"""The virtual Basys 3 as a piece of hardware: power, configuration, programming, reset, JTAG.

State machine (one source of truth for the whole UI):

    POWER OFF --power on--> UNPROGRAMMED --program--> PROGRAMMING --ok--> PROGRAMMED <-> RUNNING / PAUSED
        ^                        ^   ^                     |  fail                |
        |                        |   +---------------------+                      |
        +------ power off -------+------ PROG button / JPROGRAM / power cycle -----+

The FPGA configuration is volatile: powering off (or pressing PROG, or Vivado sending JPROGRAM)
discards it and stops the simulator. Powering on again leaves the FPGA unprogrammed (no
configuration-flash boot is modelled); the last bitstream stays selected for one-click reprogramming.

Programming always goes through the existing engine: Vivado export of the routed checkpoint
(cached) -> netlist compile -> SimController.program. "Programmed" is reported only after the
simulator has actually started the design.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal

from app.board.board import Board
from app.compiler.compiler import compile_design
from app.project.project import ResetConfig
from app.simulation.controller import ProgramRequest, SimController, SimState
from app.simulation.engine import EngineOptions
from app.utils.paths import user_data_dir
from app.vivado.bitfile import BitfileError, BitInfo
from app.vivado.vivado import VivadoError, find_vivado, load_bitstream, mapping_from_pins
from app.vivado.validate import WrongBoardError, validate_bitstream


class HwState:
    POWER_OFF = "POWER OFF"
    UNPROGRAMMED = "UNPROGRAMMED"
    PROGRAMMING = "PROGRAMMING"
    PROGRAMMED = "PROGRAMMED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"

    ACTIVE = (PROGRAMMED, RUNNING, PAUSED)


class _JobSignals(QObject):
    done = Signal(object)
    progress = Signal(str)


class _Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _JobSignals()
        self.setAutoDelete(False)

    def run(self):
        try:
            res = self.fn(self.signals.progress.emit)
        except Exception as exc:
            res = exc
        self.signals.done.emit(res)


class VirtualBoard(QObject):
    state_changed = Signal(str)
    log = Signal(str, str)
    update = Signal(object)                  # BoardUpdate from the simulator
    design_changed = Signal(object)          # dict with top/ports/reset info, or None
    bitstream_changed = Signal(object)       # selected BitInfo or None
    program_failed = Signal(str)
    xvc_changed = Signal(str)                # Hardware Manager server status text
    board_changed = Signal(object)           # the new Board after switch_board()
    wrong_board = Signal(object, object)     # (path, Board it fits) when a .bit is for the other board
    _xvc_configured = Signal(object)
    _xvc_jprogram = Signal()

    def __init__(self, board: Board, settings, toolchain_fn, parent=None):
        super().__init__(parent)
        self.board = board
        self.settings = settings
        self._toolchain_fn = toolchain_fn
        self.vivado = None
        self.powered = False
        self.state = HwState.POWER_OFF
        self.selected: BitInfo | None = None          # bitstream chosen for programming
        self.programmed_bit: BitInfo | None = None    # bitstream currently configured
        self.design = None                            # BitstreamDesign of the configured bitstream
        self.design_info: dict | None = None
        self.last_error = ""
        self.programming_source = ""
        self._gen = 0                                 # invalidates stale programming jobs
        self._jobs: set = set()
        self._pool = QThreadPool.globalInstance()
        self._known_lock = threading.Lock()
        self._known: dict[Path, float] = {}           # bitstreams Vivado might program via JTAG
        self._payload_cache: dict[Path, tuple[float, bytes]] = {}
        self.xvc = None
        self.xvc_device = None
        self.hw_server = None                        # VirtualHwServer ("remote server" for Vivado)
        self.usb_cables: list[str] = []              # real USB JTAG cables seen when the host was refused

        self.sim = SimController(self)
        w = self.sim.worker
        w.state_changed.connect(self._on_sim_state)
        w.update.connect(self.update)
        w.log.connect(self.log)
        w.failed.connect(self._on_sim_failed)
        w.programmed.connect(self._on_programmed)
        self._xvc_configured.connect(self._on_xvc_configured, Qt.QueuedConnection)
        self._xvc_jprogram.connect(self._on_xvc_jprogram, Qt.QueuedConnection)
        self.detect_vivado()

    # ================================================================== helpers
    def detect_vivado(self):
        self.vivado = find_vivado(self.settings.get("vivado_dir") or None)
        return self.vivado

    def _set_state(self, st: str):
        if st != self.state:
            self.state = st
            self.state_changed.emit(st)
        if self.xvc_device is not None:
            with self.xvc_device.lock:
                self.xvc_device.powered = self.powered
                if st in HwState.ACTIVE:
                    self.xvc_device.done = True
                elif st in (HwState.UNPROGRAMMED, HwState.POWER_OFF):
                    self.xvc_device.done = False

    def _start_job(self, fn, on_done, on_progress=None):
        job = _Job(fn)
        self._jobs.add(job)

        def finished(res, job=job):
            self._jobs.discard(job)
            on_done(res)
        job.signals.done.connect(finished, Qt.QueuedConnection)
        if on_progress:
            job.signals.progress.connect(on_progress, Qt.QueuedConnection)
        self._pool.start(job)

    @property
    def configured(self) -> bool:
        return self.state in HwState.ACTIVE

    # ================================================================== power
    def power_on(self):
        if self.powered:
            return
        self.powered = True
        self.log.emit("success", "POWER ON - Basys 3 powered. The FPGA is unprogrammed (configuration memory "
                                 "is volatile); program it with a bitstream.")
        self._set_state(HwState.UNPROGRAMMED)

    def power_off(self):
        if not self.powered:
            return
        self.powered = False
        self._gen += 1                    # discard any programming still in progress
        self.sim.request_stop.emit()
        self.programmed_bit = None
        self.design = None
        self.design_info = None
        self.design_changed.emit(None)
        self.log.emit("info", "POWER OFF - clock stopped, simulator terminated, FPGA configuration lost.")
        self._set_state(HwState.POWER_OFF)

    def toggle_power(self):
        self.power_off() if self.powered else self.power_on()

    def switch_board(self, board: Board) -> bool:
        """Swap the virtual board (like unplugging one board and plugging in another)."""
        if board.id == self.board.id:
            return False
        was_on = self.powered
        if was_on:
            self.power_off()
        self._gen += 1
        self.board = board
        if self.selected is not None and not self.selected.full_part.lower().startswith(board.part):
            self.selected = None
            self.bitstream_changed.emit(None)
        if self.xvc_device is not None:
            with self.xvc_device.lock:
                self.xvc_device.idcode = board.idcode
                self.xvc_device.done = False
        self.log.emit("info", f"Board: {board.virtual_name} ({board.name}, {board.fpga}). It starts powered OFF.")
        self.board_changed.emit(board)
        self._set_state(HwState.POWER_OFF)
        return True

    def clear_configuration(self, reason: str):
        """PROG button / JPROGRAM: the FPGA loses its configuration but stays powered."""
        if not self.powered:
            return
        self._gen += 1
        was = self.configured or self.state == HwState.PROGRAMMING
        self.sim.request_stop.emit()
        self.programmed_bit = None
        self.design = None
        self.design_info = None
        self.design_changed.emit(None)
        if was:
            self.log.emit("info", f"{reason}: FPGA configuration cleared (DONE low).")
        self._set_state(HwState.UNPROGRAMMED)

    # ================================================================== bitstream selection
    def select_bitstream(self, path: Path, quiet_mismatch: bool = False) -> BitInfo | None:
        try:
            path = Path(path).resolve()       # absolute: also used in the Tcl given to Vivado
        except OSError:
            path = Path(path)
        try:
            info = validate_bitstream(Path(path), self.board)
        except WrongBoardError as exc:
            self.last_error = str(exc)
            if quiet_mismatch:            # e.g. last session's bitstream was for the other board
                return None
            self.log.emit("error", f"Bitstream rejected: {exc}")
            if exc.board is not None:
                self.wrong_board.emit(Path(path), exc.board)
            self.program_failed.emit(str(exc))
            return None
        except BitfileError as exc:
            self.last_error = str(exc)
            self.log.emit("error", f"Bitstream rejected: {exc}")
            self.program_failed.emit(str(exc))
            return None
        self.selected = info
        self.add_known(info.path)
        self.settings.set("last_bitstream", str(info.path))
        dcp = info.path.parent / f"{info.design}_routed.dcp"
        note = "" if dcp.is_file() or list(info.path.parent.glob("*_routed.dcp")) else \
            " Warning: no routed checkpoint (*_routed.dcp) next to it - programming will ask for one."
        self.log.emit("info", f"Bitstream selected: {info.path.name} ({info.summary()}).{note}")
        self.bitstream_changed.emit(info)
        return info

    def add_known(self, path: Path):
        with self._known_lock:
            self._known[Path(path).resolve()] = 0.0

    # ================================================================== programming
    def program(self, path: Path | None = None, checkpoint: Path | None = None, source: str = "FPGA Virtual Lab"):
        """Program the virtual FPGA. Returns False when the request was rejected immediately."""
        if not self.powered:
            return self._reject("The board is powered OFF - switch power on before programming.")
        if self.state == HwState.PROGRAMMING and source == "FPGA Virtual Lab":
            self.log.emit("warning", "Programming already in progress.")
            return False
        if path is not None:
            if self.select_bitstream(path) is None:
                return False
        if self.selected is None:
            return self._reject("No bitstream selected - use Load Bitstream first.")
        info = self.selected
        try:                              # file may have changed since it was selected
            info = validate_bitstream(info.path, self.board)
        except BitfileError as exc:
            return self._reject(f"Bitstream rejected: {exc}")
        tc = self._toolchain_fn()
        if tc is None:
            return self._reject("Icarus Verilog (the simulation engine) was not found - set it in Settings.")
        if self.vivado is None and self.detect_vivado() is None:
            return self._reject("Vivado was not found. It is needed to read the implemented design behind the "
                                ".bit (set the Vivado folder in Settings).")
        # Programming starts by clearing the old configuration, like the real device (no silent
        # fallback to the previous design if the new one fails).
        self._gen += 1
        gen = self._gen
        self.sim.request_stop.emit()
        self.programmed_bit = None
        self.design = None
        self.design_changed.emit(None)
        self.programming_source = source
        self._set_state(HwState.PROGRAMMING)
        self.log.emit("cmd", f"Programming virtual FPGA with {info.path.name} (requested by {source})...")
        viv = self.vivado
        timing = bool(self.settings.get("timing_sim"))
        workdir = user_data_dir() / "build" / "bitstream"

        def work(progress):
            design = load_bitstream(info.path, viv, checkpoint, log=progress, timing=timing)
            res = compile_design(tc, [design.netlist], design.top, workdir / "compile",
                                 extra_args=design.compile_args(), extra_roots=design.extra_roots)
            return design, res

        def done(result):
            if gen != self._gen:
                return                    # powered off / superseded meanwhile
            if isinstance(result, Exception):
                self._fail(str(result))
                return
            design, res = result
            if not res.success:
                self._fail("the implemented netlist could not be compiled for simulation: " +
                           "; ".join(str(e) for e in res.errors[:3]))
                return
            for n in design.notes:
                self.log.emit("warning", n)
            mapping, notes = mapping_from_pins(design, self.board)
            for n in notes:
                self.log.emit("warning", f"Pin: {n}")
            self.design = design
            self._pending_bit = info
            rc = self.reset_config(design.top)
            st = self.settings
            opts = EngineOptions(power_on_init=False, waveforms=True, record=bool(st.get("record_waveforms")),
                                 reset=rc, timing=design.timing, uart_baud=int(st.get("uart_baud")),
                                 temperature_c=float(st.get("temperature_c")),
                                 accel_mg=(int(st.get("accel_x")), int(st.get("accel_y")), int(st.get("accel_z"))),
                                 mic=(int(st.get("mic_amp")), int(st.get("mic_freq"))))
            self.timing_info = (design.timing, design.wns_ns)
            self.sim.request_program.emit(ProgramRequest(tc, self.board, res, mapping, workdir / "sim", opts,
                                                         dict(self._inputs_fn())))

        self._start_job(work, done, lambda m: self.log.emit("info", m))
        return True

    def _reject(self, msg: str) -> bool:
        """A programming request refused before anything changed (current configuration untouched)."""
        self.last_error = msg
        self.log.emit("error", msg)
        self.program_failed.emit(msg)
        return False

    _inputs_fn = staticmethod(lambda: {})
    _pending_bit = None

    def set_input_source(self, fn):
        """fn() -> {resource id: level} of the physical switches/buttons (applied at configuration)."""
        self._inputs_fn = fn

    def _fail(self, msg: str):
        self.last_error = msg
        self.programmed_bit = None
        self.design = None
        self.log.emit("error", f"Programming FAILED: {msg}")
        self.program_failed.emit(msg)
        if self.xvc_device is not None:
            self.xvc_device.done = False
        self._set_state(HwState.UNPROGRAMMED if self.powered else HwState.POWER_OFF)

    def _on_programmed(self, info: dict):
        self.design_info = info
        self.design_changed.emit(info)

    def _on_sim_failed(self, msg: str):
        if self.state == HwState.PROGRAMMING:
            self._fail(msg)

    def _on_sim_state(self, st: str):
        if st == SimState.READY:
            if self.state == HwState.PROGRAMMING and self._pending_bit is not None:
                # The simulator has started the new design: only now is the FPGA "programmed".
                self.programmed_bit = self._pending_bit
                self._pending_bit = None
                self.log.emit("success", f"PROGRAMMED: {self.programmed_bit.path.name} is configured on the "
                                         f"virtual FPGA (DONE high, via {self.programming_source}).")
                self._set_state(HwState.PROGRAMMED)
            elif self.state in HwState.ACTIVE:
                self._set_state(HwState.PROGRAMMED)
        elif st == SimState.RUNNING:
            self._set_state(HwState.RUNNING)
        elif st == SimState.PAUSED:
            self._set_state(HwState.PAUSED)
        elif st == SimState.STOPPED:
            if self.state == HwState.PROGRAMMING:
                return                    # the old configuration being cleared, or a failure (handled)
            if not self.powered:
                self._set_state(HwState.POWER_OFF)
            elif self.state in HwState.ACTIVE:
                # The simulator ended by itself (crash or $finish): configuration is no longer running.
                self.programmed_bit = None
                self.design_changed.emit(None)
                self._set_state(HwState.UNPROGRAMMED)

    # ================================================================== execution / reset
    def _require_configured(self, what: str) -> bool:
        if not self.powered:
            self.log.emit("warning", f"{what}: the board is powered off.")
            return False
        if not self.configured:
            self.log.emit("warning", f"{what}: the FPGA is not programmed.")
            return False
        return True

    def run(self):
        if self._require_configured("Run"):
            self.sim.request_run.emit()

    def pause(self):
        if self.state == HwState.RUNNING:
            self.sim.request_pause.emit()

    def toggle_run(self):
        self.pause() if self.state == HwState.RUNNING else self.run()

    def step(self, cycles: int = 1):
        if self._require_configured("Step"):
            self.sim.request_step.emit(max(1, int(cycles)))

    def restart_simulation(self):
        if self._require_configured("Reset"):
            self.sim.request_reset.emit()

    def hardware_reset(self):
        if self._require_configured("Hardware reset"):
            self.sim.request_pulse_reset.emit()

    # ---- board peripherals (USB-UART, sensors)
    def uart_send(self, data: bytes):
        if self._require_configured("UART"):
            self.sim.request_uart_send.emit(bytes(data))

    def set_peripheral(self, what: str, value):
        """what: 'baud' | 'temp' | 'accel' (x,y,z mg) | 'mic' (amplitude 0..1000, Hz). Remembered in settings."""
        st = self.settings
        if what == "baud":
            st.set("uart_baud", int(value))
        elif what == "temp":
            st.set("temperature_c", float(value))
        elif what == "accel":
            for k, v in zip(("accel_x", "accel_y", "accel_z"), value):
                st.set(k, int(v))
        elif what == "mic":
            st.set("mic_amp", int(value[0]))
            st.set("mic_freq", int(value[1]))
        if self.configured:
            self.sim.request_periph.emit(what, value)

    def set_input(self, rid: str, level: int):
        if self.configured:
            self.sim.request_input.emit(rid, level)

    # ---- reset input configuration (remembered per design top module)
    def reset_config(self, top: str) -> ResetConfig | None:
        raw = self.settings.get(f"reset/{top}")
        if not raw:
            return None
        try:
            d = json.loads(raw)
            return ResetConfig(d.get("port", ""), bool(d.get("active_high", True)), int(d.get("cycles", 4)))
        except (ValueError, TypeError):
            return None

    def configure_reset(self, rc: ResetConfig | None):
        top = (self.design_info or {}).get("top")
        if top:
            self.settings.set(f"reset/{top}", json.dumps(rc.to_dict() if rc else {}))
        if self.configured:
            self.sim.request_configure_reset.emit(rc)

    # ================================================================== Vivado Hardware Manager (XVC)
    def start_xvc(self, port: int, host_port: int | None = None) -> bool:
        """Start the virtual cable; with host_port also the virtual host (dedicated hw_server)."""
        if not self._start_cable(port):
            return False
        if host_port:
            self.start_virtual_host(host_port)
        return True

    def start_virtual_host(self, host_port: int) -> bool:
        from app.xvc.hwserver import HwServerError, VirtualHwServer
        if self.xvc is None:
            return False
        if self.vivado is None and self.detect_vivado() is None:
            self.log.emit("warning", "Virtual host not started: Vivado's hw_server was not found. The virtual cable "
                                     "is still available (Add Xilinx Virtual Cable in Vivado).")
            return False
        from app.xvc import usbjtag
        cables = usbjtag.usb_jtag_cables()
        self.usb_cables = cables
        if cables:
            self.log.emit("warning", "Virtual host NOT started: a real USB JTAG cable is connected ("
                          + "; ".join(cables[:3]) + "). A second hw_server could take it away from Vivado's "
                          "local server. The virtual cable is still available: in Vivado's local server use "
                          f"Add Xilinx Virtual Cable (localhost:{self.xvc.port}) - your real board and the "
                          "virtual board then appear as two separate targets.")
            self.xvc_changed.emit(f"Experimental - listening on localhost:{self.xvc.port}")
            return False
        hs = VirtualHwServer(self.vivado.root, host_port, self.xvc.port)
        try:
            hs.start()
        except HwServerError as exc:
            self.log.emit("error", f"Virtual host could not start: {exc}. The virtual cable is still available.")
            return False
        self.hw_server = hs
        self.log.emit("success", f"Virtual host ready: in Vivado use Open New Target > Remote server, host "
                                 f"127.0.0.1, port {host_port}. The virtual xc7a35t_0 is listed immediately.")
        self.xvc_changed.emit(f"Experimental - virtual host 127.0.0.1:{host_port}")
        return True

    def stop_virtual_host(self, reason: str = ""):
        if self.hw_server is None:
            return
        self.hw_server.stop()
        self.hw_server = None
        self.log.emit("warning", f"Virtual host stopped{': ' + reason if reason else ''}. The virtual cable is "
                                 f"still available on localhost:{self.xvc.port if self.xvc else '?'}.")
        if self.xvc is not None:
            self.xvc_changed.emit(f"Experimental - listening on localhost:{self.xvc.port}")

    def check_usb_conflict(self):
        """While the virtual host runs, stop it as soon as a real USB JTAG cable is plugged in."""
        if self.hw_server is None or getattr(self, "_usb_check_busy", False):
            return
        from app.xvc import usbjtag
        self._usb_check_busy = True

        def done(cables):
            self._usb_check_busy = False
            if isinstance(cables, list) and cables and self.hw_server is not None:
                self.stop_virtual_host("a real USB JTAG cable was connected (" + "; ".join(cables[:3]) +
                                       ") - it belongs to Vivado's local server")
        self._start_job(lambda _p: usbjtag.usb_jtag_cables(), done)

    def _start_cable(self, port: int) -> bool:
        from app.xvc.jtag import Artix7Jtag
        from app.xvc.server import XvcServer
        self.stop_xvc()
        dev = Artix7Jtag(idcode=self.board.idcode, resolver=self._xvc_resolve,
                         on_jprogram=lambda: self._xvc_jprogram.emit(),
                         on_configured=lambda res: self._xvc_configured.emit(res))
        dev.powered = self.powered
        dev.done = self.configured
        srv = XvcServer(dev, port=port, on_event=lambda kind, msg: self._xvc_event(kind, msg))
        try:
            srv.start()
        except OSError as exc:
            self.log.emit("error", f"Hardware Manager server could not listen on localhost:{port}: {exc}")
            self.xvc_changed.emit("Unavailable (port in use)")
            return False
        self.xvc, self.xvc_device = srv, dev
        self.log.emit("success", f"Hardware Manager server (Xilinx Virtual Cable) listening on localhost:{port}. "
                                 f"In Vivado: Open Target > Open New Target > Add Xilinx Virtual Cable, "
                                 f"host localhost, port {port}.")
        self.xvc_changed.emit(f"Experimental - listening on localhost:{port}")
        return True

    def stop_xvc(self):
        if self.hw_server is not None:
            self.hw_server.stop()
            self.hw_server = None
        if self.xvc is not None:
            self.xvc.stop()
            self.log.emit("info", "Hardware Manager server stopped.")
        self.xvc = None
        self.xvc_device = None
        self.hw_server = None                        # VirtualHwServer ("remote server" for Vivado)
        self.usb_cables: list[str] = []              # real USB JTAG cables seen when the host was refused
        self.xvc_changed.emit("Off")

    def _xvc_event(self, kind: str, msg: str):
        # called from the server thread: only emit signals
        self.log.emit("info", msg)
        port = self.xvc.port if self.xvc else 0
        where = f"virtual host 127.0.0.1:{self.hw_server.port}" if self.hw_server else f"localhost:{port}"
        if kind == "connected":
            self.xvc_changed.emit(f"Experimental - Vivado connected ({where})")
            if not self.powered:
                self.log.emit("warning", "Vivado connected to the virtual cable, but the board is powered OFF, so "
                                         "Vivado will see no device. Switch power ON, then refresh the target in "
                                         "Vivado.")
        elif kind == "disconnected":
            self.xvc_changed.emit(f"Experimental - listening ({where})")

    def _payload(self, path: Path) -> bytes | None:
        from app.vivado.bitfile import read_bit_header
        try:
            m = path.stat().st_mtime
            hit = self._payload_cache.get(path)
            if hit and hit[0] == m:
                return hit[1]
            data = read_bit_header(path).payload()
            self._payload_cache[path] = (m, data)
            return data
        except (OSError, BitfileError):
            return None

    def _xvc_resolve(self, data: bytes) -> tuple[bool, str]:
        """Called in the JTAG thread at JSTART: which .bit was just shifted in?

        First the bitstreams the app already knows (loaded / watched folder), then the ones Vivado
        itself points at: PROGRAM.FILE commands in its logs and the run folders of its recent
        projects. A match must be byte-for-byte."""
        from app.vivado.discovery import candidate_bitstreams
        with self._known_lock:
            known = list(self._known)
        found = self._match(data, known)
        if found is None:
            try:
                discovered = [p for p in candidate_bitstreams() if p not in known]
            except OSError:
                discovered = []
            found = self._match(data, discovered)
            if isinstance(found, Path):
                self.add_known(found)
                self.log.emit("info", f"Recognised the bitstream Vivado is programming: {found} "
                                      "(found through Vivado's project/log records).")
        if isinstance(found, str):
            return False, found
        if found is not None:
            self._xvc_match = found
            return True, f"configuration matches {found}"
        return False, (f"received {len(data):,} bytes of configuration data that match no .bit file FPGA Virtual "
                       "Lab can find (loaded bitstreams, the watched folder, Vivado's recent projects and "
                       "Vivado's PROGRAM.FILE history). Load the .bit with Load Bitstream, then program again.")

    def _match(self, data: bytes, paths) -> Path | str | None:
        """Path of the bitstream whose payload is in `data`; an error string when it matches but cannot run."""
        for path in paths:
            payload = self._payload(path)
            if payload and payload in data:
                if not list(path.parent.glob("*_routed.dcp")):
                    return (f"configuration matches {path.name}, but its routed checkpoint (*_routed.dcp) is "
                            "missing next to it, so the design cannot be simulated")
                return path
        return None

    _xvc_match: Path | None = None
    timing_info: tuple = (False, None)       # (timing simulation on, worst slack ns) of the programmed design

    def _on_xvc_jprogram(self):
        self.clear_configuration("Vivado Hardware Manager (JPROGRAM)")

    def _on_xvc_configured(self, res):
        if not res.ok:
            msg = res.message
            written = self.xvc_device.regs.get(12) if self.xvc_device is not None else None   # IDCODE register
            if "IDCODE" in msg and written:
                from app.board.board import available_boards
                other = next((b for b in available_boards() if b.idcode == written), None)
                if other is not None:
                    msg = (f"this bitstream is for the {other.name} ({other.fpga_short}); select "
                           f"{other.virtual_name} in FPGA Virtual Lab, then refresh the target in Vivado")
            self.log.emit("error", f"Vivado Hardware Manager programming: DONE stays LOW - {msg}")
            return
        path = self._xvc_match
        self.log.emit("info", f"Vivado Hardware Manager shifted in a complete configuration; {res.message}.")
        if not self.program(path, source="Vivado Hardware Manager"):
            if self.xvc_device is not None:
                self.xvc_device.done = False

    # ================================================================== shutdown
    def shutdown(self):
        self._gen += 1
        self.stop_xvc()
        self.sim.shutdown()
