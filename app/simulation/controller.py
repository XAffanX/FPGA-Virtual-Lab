"""Qt bridge: runs a BoardSimulator in a worker thread so the GUI never blocks.

All simulator calls happen in the worker thread. The GUI talks to it through
queued signals (`request_*`) and receives `update` payloads at <= ~30 Hz.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from app.board.board import Board
from app.compiler.compiler import CompileResult
from app.mapping.mapping import Mapping
from app.utils.toolchain import Toolchain

from .engine import BoardSimulator, EngineOptions
from .persistence import DigitState
from .session import SimulationError

# Target simulated clock cycles per wall-clock second.
SPEEDS = {"slow": 10, "normal": 1_000, "fast": 100_000, "max": 0}   # 0 = as fast as possible
SPEED_LABELS = {"slow": "Slow (10 cycles/s)", "normal": "Normal (1 k cycles/s)",
                "fast": "Fast (100 k cycles/s)", "max": "Maximum (unthrottled)"}
TICK_MS = 30
MAX_CHUNK_WALL_S = 0.06


class SimState:
    NO_DESIGN = "No Design Loaded"
    COMPILED = "Compilation Successful"
    COMPILE_FAILED = "Compilation Failed"
    READY = "Simulation Ready"
    RUNNING = "Simulation Running"
    PAUSED = "Simulation Paused"
    STOPPED = "Simulation Stopped"


@dataclass
class ProgramRequest:
    toolchain: Toolchain
    board: Board
    compiled: CompileResult
    mapping: Mapping
    workdir: Path
    options: EngineOptions
    initial_inputs: dict = field(default_factory=dict)


@dataclass
class BoardUpdate:
    cycles: int = 0
    time_ps: int = 0
    leds: list = field(default_factory=list)          # brightness 0..1 or None (unmapped/X)
    led_levels: list = field(default_factory=list)    # instantaneous level 0/1/None
    digits: list[DigitState] = field(default_factory=list)
    ports: list = field(default_factory=list)         # (name, dir, value, xmask, width)
    rate: float = 0.0                                 # achieved cycles per wall second
    mapped_outputs: set = field(default_factory=set)
    extra: dict = field(default_factory=dict)         # other outputs (RGB channels, Pmod pins): id -> 0..1 / None
    uart_rx: bytes = b""                              # bytes the design sent on the USB-UART since last update
    vga: bytes | None = None                          # 640x480 RGB888 frame when it changed
    vga_text: str = ""
    inputs: dict = field(default_factory=dict)        # current logic level of board input pins (Pmod inputs)


class SimWorker(QObject):
    update = Signal(object)            # BoardUpdate
    state_changed = Signal(str)
    log = Signal(str, str)             # level, message
    failed = Signal(str)
    programmed = Signal(object)        # dict: top, ports, reset_candidates, reset_port

    def __init__(self):
        super().__init__()
        self.sim: BoardSimulator | None = None
        self.running = False
        self.speed = "normal"
        self.custom_rate = 0
        self.min_press_cycles = 32
        self._timer: QTimer | None = None
        self._last_tick = 0.0
        self._carry = 0.0
        self._chunk = 1000
        self._pressed_at: dict[str, int] = {}
        self._pending_release: set[str] = set()
        self._rate_hist: list[tuple[float, int]] = []
        self._last_emit = 0.0
        self._uart_pending = b""
        self._vga_emit = 0.0

    # ------------------------------------------------------------ helpers
    def _ensure_timer(self):
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(TICK_MS)
            self._timer.timeout.connect(self._tick)

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except SimulationError as exc:
            self._fail(str(exc))
        except Exception as exc:  # pragma: no cover - unexpected backend failure
            self._fail(f"internal error: {exc!r}")
        return None

    def _fail(self, msg: str):
        self.running = False
        if self._timer:
            self._timer.stop()
        self._drain_output()
        alive = self.sim is not None and self.sim.alive
        self.failed.emit(msg)
        self.log.emit("error", msg)
        self.state_changed.emit(SimState.PAUSED if alive else SimState.STOPPED)

    def _drain_output(self):
        if self.sim:
            for line in self.sim.take_design_output():
                self.log.emit("design", line)
            info = self.sim.session.sim_info
            while info:
                line = info.pop(0)
                if line.startswith("FVL_INIT "):
                    line = (f"Power-on initialization: {line.split()[1]} register(s) without an initial "
                            "value set to 0 (FPGA configuration behaviour).")
                elif line.startswith("VCD info:"):
                    line = "Waveform recording file: " + line.split("dumpfile", 1)[-1].replace(
                        "opened for output.", "").strip()
                self.log.emit("info", line)

    def _emit(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_emit < 0.03:
            return
        self._last_emit = now
        sim = self.sim
        if sim is None or sim.snapshot is None:
            return
        u = BoardUpdate(cycles=sim.cycles, time_ps=sim.time_ps)
        u.led_levels = sim.leds()
        if self.running:
            u.leds = [sim.led_brightness(i) for i in range(16)]       # time-averaged (PWM, blinking)
        else:
            u.leds = [None if lv is None else float(lv) for lv in u.led_levels]   # paused: exact level
        u.digits = sim.seven_segment()
        for r in sim.board.of_kind("rgb", "pmod", "uart_tx", "sensor"):
            if r.id not in sim.io.output_bits:
                continue
            if self.running:
                u.extra[r.id] = sim.brightness(r.id)
            else:
                lv = sim.level(r.id)
                u.extra[r.id] = None if lv is None else float(lv)
        u.inputs = dict(sim.io.input_state)
        rx = sim.periph.take_uart()
        self._uart_pending += bytes(rx)
        u.uart_rx, self._uart_pending = bytes(self._uart_pending), b""
        vs = sim.periph.vga_state
        if sim.periph.has_vga and (vs.changed and now - self._vga_emit > 0.25 or force):
            vs.changed = False
            self._vga_emit = now
            u.vga = bytes(vs.frame)
        u.vga_text = vs.timing_text if sim.periph.has_vga else ""
        widths = {p.name: p.width for p in sim.compiled.ports}
        u.ports = [(n, d, v, x, widths[n]) for n, d, v, x in sim.port_values()]
        u.mapped_outputs = set(sim.io.output_bits)
        self._rate_hist = [(t, c) for t, c in self._rate_hist if now - t < 1.0]
        self._rate_hist.append((now, sim.cycles))
        if len(self._rate_hist) > 1 and self.running:
            (t0, c0), (t1, c1) = self._rate_hist[0], self._rate_hist[-1]
            u.rate = (c1 - c0) / (t1 - t0) if t1 > t0 else 0.0
        self.update.emit(u)
        self._drain_output()

    # ------------------------------------------------------------ slots
    @Slot(object)
    def program(self, req: ProgramRequest):
        self._ensure_timer()
        self.stop_sim(quiet=True)
        try:
            self.log.emit("info", "Programming virtual FPGA: building simulation model...")
            wanted_reset = req.options.reset
            req.options.reset = None
            sim = BoardSimulator(req.toolchain, req.board, req.compiled, req.mapping, req.workdir, req.options)
            if wanted_reset and wanted_reset.port:
                try:
                    sim.configure_reset(wanted_reset)
                except SimulationError as exc:
                    self.log.emit("warning", f"Saved reset input ignored: {exc}")
            for issue in sim.resolved.warnings:
                self.log.emit("warning", issue.message)
            snap = sim.program(req.initial_inputs)
            self.sim = sim
            if sim.session.init_status:
                self.log.emit("warning", sim.session.init_status)
            self.log.emit("success", f"Virtual FPGA programmed with '{req.compiled.top}' "
                                     f"({len(req.compiled.ports)} ports, simulator PID {sim.session._proc.pid}). "
                                     "This is a simulation - no hardware is configured.")
            if not sim.has_clock:
                self.log.emit("info", "No port is mapped to the 100 MHz clock: the design is purely "
                                      "combinational from the simulator's point of view.")
            self._pressed_at.clear()
            self._pending_release.clear()
            self._rate_hist.clear()
            self.state_changed.emit(SimState.READY)
            self._emit(force=True)
            self.programmed.emit(self._design_info())
            del snap
        except SimulationError as exc:
            self.sim = None
            self.failed.emit(str(exc))
            for d in getattr(exc, "diagnostics", [])[:20]:
                self.log.emit(d.severity if d.severity in ("error", "warning") else "info", str(d))
            self.log.emit("error", str(exc))
            self.state_changed.emit(SimState.STOPPED)
        except Exception as exc:  # pragma: no cover
            self.sim = None
            self.log.emit("error", f"internal error while programming: {exc!r}")
            self.state_changed.emit(SimState.STOPPED)

    @Slot(str, int)
    def set_input(self, rid: str, level: int):
        if self.sim is None or not self.sim.alive:
            return
        kind = self.sim.board.resources[rid].kind
        if kind == "button":
            if level:
                self._pending_release.discard(rid)
                self._pressed_at[rid] = self.sim.cycles
            elif self.running and self.sim.has_clock and \
                    self.sim.cycles - self._pressed_at.get(rid, -10**12) < self.min_press_cycles:
                # Make sure even a quick click lasts long enough for clocked logic to see it.
                self._pending_release.add(rid)
                return
        self._guard(self.sim.set_input, rid, level)
        if not self.running:          # while running, the next tick shows the change
            self._emit(force=True)

    @Slot()
    def run(self):
        if self.sim is None or not self.sim.alive:
            return
        self._ensure_timer()
        self.running = True
        self._last_tick = time.monotonic()
        self._carry = 0.0
        self._timer.start()
        self.state_changed.emit(SimState.RUNNING)

    @Slot()
    def pause(self):
        if not self.running:
            return
        self.running = False
        self._timer.stop()
        self._release_pending(force=True)
        self._guard(self.sim.settle_outputs)
        self._emit(force=True)
        self.state_changed.emit(SimState.PAUSED)

    @Slot(int)
    def step(self, cycles: int):
        if self.sim is None or not self.sim.alive:
            return
        if self.running:
            self.pause()
        if self._guard(self.sim.step, max(1, cycles)) is not None:
            self._emit(force=True)
            self.state_changed.emit(SimState.PAUSED)

    @Slot(bytes)
    def uart_send(self, data: bytes):
        if self.sim is None or not self.sim.alive:
            self.log.emit("warning", "UART: the virtual FPGA is not programmed.")
            return
        try:
            self.sim.uart_send(bytes(data))
        except SimulationError as exc:
            self.log.emit("warning", f"UART: {exc}")
            return
        if not self.running:
            self.log.emit("info", f"UART: {len(data)} byte(s) queued; they are shifted into the design "
                                  "while the clock runs (press Run).")

    @Slot(str, object)
    def set_periph(self, what: str, value):
        if self.sim is None or not self.sim.alive:
            return
        fn = {"baud": lambda v: self.sim.set_uart_baud(int(v)),
              "temp": lambda v: self.sim.set_temperature(float(v)),
              "accel": lambda v: self.sim.set_accel(*[int(x) for x in v]),
              "mic": lambda v: self.sim.set_mic(int(v[0]), int(v[1]))}.get(what)
        if fn is not None:
            self._guard(fn, value)

    def _design_info(self) -> dict:
        sim = self.sim
        return {"top": sim.compiled.top,
                "ports": [(p.name, p.direction, p.width) for p in sim.compiled.ports],
                "reset_candidates": sim.reset_candidates(),
                "reset_port": sim.reset_port_name,
                "reset": sim.options.reset,
                "peripherals": sim.periph.summary(),
                "uart": sim.periph.has_uart, "uart_rx": bool(sim.periph.uart_rx), "vga": sim.periph.has_vga,
                "sensors": {"temp": sim.periph.tmp, "acl": sim.periph.acl, "mic": sim.periph.mic},
                "pmods": sorted(rid for rid in list(sim.io.output_bits) + list(sim.resolved.inputs)
                                if sim.board.resources[rid].kind == "pmod"),
                "pmod_inputs": sorted(rid for rid in sim.resolved.inputs if sim.board.resources[rid].kind == "pmod"),
                "timing": sim.options.timing}

    @Slot()
    def reset(self):
        """Simulation reset: restart the loaded design from its initial state (time and cycles -> 0)."""
        if self.sim is None:
            self.log.emit("warning", "Reset: the virtual FPGA is not programmed, nothing to reset.")
            return
        was_running = self.running
        self.running = False
        if self._timer:
            self._timer.stop()
        if self._guard(self.sim.restart) is not None:
            self._pressed_at.clear()
            self._pending_release.clear()
            self._rate_hist.clear()
            self.log.emit("info", "Simulation reset: design restarted from its initial state "
                                  "(clock cycles and simulated time back to 0, switch positions kept).")
            self._emit(force=True)
            if was_running:
                self.run()
            else:
                self.state_changed.emit(SimState.READY)

    @Slot()
    def pulse_reset_port(self):
        """Hardware reset: assert the design's reset input for N clock cycles, then release it."""
        if self.sim is None or not self.sim.alive:
            self.log.emit("warning", "Hardware reset: the virtual FPGA is not programmed.")
            return
        rc = self.sim.options.reset
        if self.sim.reset_port_name is None or rc is None:
            self.log.emit("warning", "Hardware reset: no reset input is configured for this design "
                                     "(choose one on the Simulation page). Use 'Restart simulation' instead.")
            return
        if self._guard(self.sim.pulse_reset) is not None:
            level = "high" if rc.active_high else "low"
            self.log.emit("info", f"Hardware reset: '{rc.port}' driven {level} (asserted) for {rc.cycles} clock "
                                  f"cycle(s), then released. Now at cycle {self.sim.cycles:,}.")
            self._emit(force=True)

    @Slot(object)
    def configure_reset(self, rc):
        if self.sim is None:
            return
        try:
            self.sim.configure_reset(rc)
        except SimulationError as exc:
            self.log.emit("error", str(exc))
            return
        if rc and rc.port:
            self.log.emit("info", f"Reset input: '{rc.port}', active {'high' if rc.active_high else 'low'}, "
                                  f"held for {rc.cycles} cycle(s).")
        self.programmed.emit(self._design_info())

    @Slot()
    def stop_sim(self, quiet: bool = False):
        self.running = False
        if self._timer:
            self._timer.stop()
        if self.sim is not None:
            self.sim.stop()
            self._drain_output()
            if not quiet:
                self.log.emit("info", f"Simulation stopped after {self.sim.cycles:,} clock cycles.")
            self.sim = None
        if not quiet:
            self.state_changed.emit(SimState.STOPPED)

    @Slot(str, int)
    def set_speed(self, mode: str, custom_rate: int = 0):
        self.speed = mode
        self.custom_rate = custom_rate
        self._carry = 0.0

    @Slot(int)
    def set_min_press(self, cycles: int):
        self.min_press_cycles = max(0, cycles)

    @Slot(bool)
    def set_recording(self, on: bool):
        if self.sim is not None and self.sim.alive:
            self._guard(self.sim.set_recording, on)

    # ------------------------------------------------------------ run loop
    def _release_pending(self, force=False):
        if not self._pending_release or self.sim is None:
            return
        for rid in list(self._pending_release):
            if force or self.sim.cycles - self._pressed_at.get(rid, 0) >= self.min_press_cycles:
                self._pending_release.discard(rid)
                self._guard(self.sim.set_input, rid, 0)

    def _tick(self):
        if not self.running or self.sim is None:
            return
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.25)
        self._last_tick = now
        rate = self.custom_rate if self.speed == "custom" else SPEEDS.get(self.speed, 1000)
        if rate > 0:
            self._carry += rate * dt
            n = int(self._carry)
            self._carry -= n
            # Never block the worker for long, even when the design is slow to simulate.
            n = min(n, self._chunk)
        else:
            n = self._chunk
        if self._pending_release:
            n = min(n, max(1, self.min_press_cycles))
        if n <= 0:
            return
        rr = self._guard(self.sim.run, n)
        if rr is None:
            return
        # Adapt chunk size so one chunk takes about MAX_CHUNK_WALL_S.
        if rr.wall_s > 0:
            per_cycle = rr.wall_s / n
            target = int(MAX_CHUNK_WALL_S / max(per_cycle, 1e-9))
            self._chunk = max(1, min(target, 5_000_000))
        if rr.overflow:
            pass   # persistence model uses the events it got; outputs are still exact at chunk end
        self._release_pending()
        self._emit()


class SimController(QObject):
    """GUI-side handle owning the worker thread."""
    request_program = Signal(object)
    request_input = Signal(str, int)
    request_run = Signal()
    request_pause = Signal()
    request_step = Signal(int)
    request_reset = Signal()
    request_pulse_reset = Signal()
    request_configure_reset = Signal(object)
    request_uart_send = Signal(bytes)
    request_periph = Signal(str, object)      # ("baud", 115200) / ("temp", 23.5) / ("accel", (x,y,z)) / ("mic", (a,f))
    request_stop = Signal()
    request_speed = Signal(str, int)
    request_min_press = Signal(int)
    request_recording = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = QThread()
        self.thread.setObjectName("simulation-worker")
        self.worker = SimWorker()
        self.worker.moveToThread(self.thread)
        self.request_program.connect(self.worker.program)
        self.request_input.connect(self.worker.set_input)
        self.request_run.connect(self.worker.run)
        self.request_pause.connect(self.worker.pause)
        self.request_step.connect(self.worker.step)
        self.request_reset.connect(self.worker.reset)
        self.request_pulse_reset.connect(self.worker.pulse_reset_port)
        self.request_configure_reset.connect(self.worker.configure_reset)
        self.request_uart_send.connect(self.worker.uart_send)
        self.request_periph.connect(self.worker.set_periph)
        self.request_stop.connect(self.worker.stop_sim)
        self.request_speed.connect(self.worker.set_speed)
        self.request_min_press.connect(self.worker.set_min_press)
        self.request_recording.connect(self.worker.set_recording)
        self.thread.start()

    def shutdown(self):
        # Kill the simulator directly (thread-safe) so a long chunk cannot delay exit.
        sim = self.worker.sim
        if sim is not None:
            sim.stop()
        self.thread.quit()
        self.thread.wait(5000)
