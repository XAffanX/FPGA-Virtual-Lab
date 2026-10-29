"""Board-level simulation engine: virtual Basys 3 peripherals driving a real HDL simulation.

`BoardSimulator` is independent of Qt. The GUI runs it inside a worker thread
(see controller.py); the integration tests use it directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.board.board import Board
from app.compiler.compiler import CompileResult
from app.mapping.mapping import Mapping, ResolvedMapping, resolve
from app.project.project import ResetConfig
from app.utils.toolchain import Toolchain

from .board_io import BoardIO
from .persistence import DigitState, PersistenceModel
from .session import RunResult, SimulationError, SimulationSession, Snapshot


@dataclass
class EngineOptions:
    power_on_init: bool = True
    waveforms: bool = True
    record: bool = True
    language: str = "auto"
    max_events_per_chunk: int = 20_000
    reset: ResetConfig | None = None
    timing: bool = False               # netlist carries real (SDF) delays: sample at end of cycle
    uart_baud: int = 9600
    temperature_c: float = 25.0
    accel_mg: tuple = (0, 0, 1000)     # board lying flat: +1 g on Z
    mic: tuple = (0, 1000)             # (amplitude 0..1000, tone frequency Hz)


class BoardSimulator:
    def __init__(self, toolchain: Toolchain, board: Board, compiled: CompileResult, mapping: Mapping,
                 workdir: Path, options: EngineOptions | None = None):
        if not compiled.success:
            raise SimulationError("compile the design successfully before programming the virtual FPGA")
        self.board = board
        self.compiled = compiled
        self.options = options or EngineOptions()
        self.resolved: ResolvedMapping = resolve(mapping, compiled.ports, board)
        if not self.resolved.ok:
            raise SimulationError("the pin mapping has errors: " +
                                  "; ".join(i.message for i in self.resolved.errors[:5]))
        self.io = BoardIO(board, self.resolved)
        from .peripherals import Peripherals
        self.periph = Peripherals(board, self.resolved)
        self.session = SimulationSession(
            toolchain, compiled.sources, compiled.top, compiled.ports, workdir,
            clock_masks=self.io.clock_masks(), language=self.options.language,
            power_on_init=self.options.power_on_init, waveforms=self.options.waveforms,
            record_initially=self.options.record,
            extra_args=compiled.extra_args, extra_roots=compiled.extra_roots,
            extras=self.periph.extras(), timing=self.options.timing)
        self.persistence = PersistenceModel(self.session.out_width)
        self.has_clock = bool(self.resolved.clock_bits)
        self._reset_port: int | None = None
        self.configure_reset(self.options.reset)

    # ---------------------------------------------------------------- design reset input
    RESET_HIGH = ("rst", "reset", "rst_i", "i_rst", "reset_i", "areset", "srst", "clr", "clear")
    RESET_LOW = ("rst_n", "rstn", "reset_n", "resetn", "nrst", "n_rst", "rst_b", "reset_b", "aresetn", "nreset")

    def reset_candidates(self) -> list[tuple[str, bool]]:
        """1-bit inputs that can act as a design reset: [(name, suggested_active_high)], likely ones first."""
        out = []
        for p in self.compiled.ports:
            if p.direction != "input" or p.width != 1 or self._is_clock(p.name):
                continue
            out.append((0 if self.looks_like_reset(p.name) else 1, p.name, not self.active_low_name(p.name)))
        return [(n, hi) for _, n, hi in sorted(out)]

    @classmethod
    def looks_like_reset(cls, name: str) -> bool:
        low = name.lower()
        return low in cls.RESET_HIGH + cls.RESET_LOW or "rst" in low or "reset" in low

    @classmethod
    def active_low_name(cls, name: str) -> bool:
        """rst_n, rstn, CPU_RESETN, reset_b ... are active low."""
        low = name.lower()
        return low in cls.RESET_LOW or (("rst" in low or "reset" in low) and low.endswith(("n", "_b")))

    def _is_clock(self, name: str) -> bool:
        idx = next(i for i, p in enumerate(self.compiled.ports) if p.name == name)
        return any(ref.port == idx for ref in self.resolved.clock_bits)

    def configure_reset(self, rc: ResetConfig | None) -> None:
        """Select (or clear, with None / empty port) the design input pulsed by pulse_reset()."""
        if not rc or not rc.port:
            self.options.reset = None
            self._reset_port = None
            return
        idx = next((i for i, p in enumerate(self.compiled.ports)
                    if p.name == rc.port and p.direction == "input" and p.width == 1), None)
        if idx is None:
            raise SimulationError(f"reset input '{rc.port}' is not a 1-bit input of '{self.compiled.top}'")
        self.options.reset = rc
        self._reset_port = idx

    @property
    def reset_port_name(self) -> str | None:
        return self.compiled.ports[self._reset_port].name if self._reset_port is not None else None

    # ---------------------------------------------------------------- lifecycle
    def program(self, initial_inputs: dict[str, int] | None = None) -> Snapshot:
        """Build the simulation model and start the simulator (power-on).

        initial_inputs: current switch/button levels {resource id: 0/1}, applied from time 0.
        """
        for rid, active in (initial_inputs or {}).items():
            if rid in self.io.input_state:
                self.io.set_input(rid, active)          # handles active-low inputs
        self.session.build()
        self.session.inputs = dict(self.io.port_values())
        snap = self.session.start()
        snap = self._configure_peripherals() or snap
        self.persistence.reset(snap.time_ps, snap.out_value, snap.out_xmask)
        return snap

    def _configure_peripherals(self) -> Snapshot | None:
        o = self.options
        snap = None
        for reg, val in self.periph.initial_writes(o.uart_baud, o.temperature_c, tuple(o.accel_mg), tuple(o.mic)):
            snap = self.session.periph_write(reg, val)
        return snap

    @property
    def alive(self) -> bool:
        return self.session.alive

    def stop(self) -> None:
        self.session.stop()

    def restart(self) -> Snapshot:
        """Restart the loaded design from its initial (power-on) state: cycle counter and simulated
        time return to 0, registers to their INIT values. Current switch positions are kept."""
        self.session.inputs = dict(self.io.port_values())
        snap = self.session.restart()
        snap = self._configure_peripherals() or snap
        self.periph.take_uart()
        self.persistence.reset(snap.time_ps, snap.out_value, snap.out_xmask)
        return snap

    def reset(self) -> Snapshot:
        """Restart, then pulse the configured design reset input (if any)."""
        snap = self.restart()
        if self._reset_port is not None:
            snap = self.pulse_reset()
        return snap

    def pulse_reset(self) -> Snapshot:
        """Assert the design's reset input for the configured number of cycles, then release it."""
        if self._reset_port is None:
            raise SimulationError("no reset port configured")
        rc = self.options.reset
        base = self.io.port_values().get(self._reset_port, 0)
        self._apply(self.session.set_input(self._reset_port, 1 if rc.active_high else 0))
        if rc.cycles > 0:
            self.run(rc.cycles)
        snap = self.session.set_input(self._reset_port, base)
        self._apply(snap)
        return snap

    # ---------------------------------------------------------------- inputs
    def set_input(self, resource_id: str, level: int) -> Snapshot:
        """Switch or button change from the board."""
        ports = self.io.set_input(resource_id, level)
        vals = self.io.port_values()
        snap = self.session.snapshot
        for p in sorted(ports):
            snap = self.session.set_input(p, vals[p])
            self._apply(snap)
        return snap or self.session.query()

    def set_switch(self, index: int, on: bool) -> Snapshot:
        return self.set_input(f"SW{index}", 1 if on else 0)

    def press(self, button: str) -> Snapshot:
        return self.set_input(button, 1)

    def release(self, button: str) -> Snapshot:
        return self.set_input(button, 0)

    # ---------------------------------------------------------------- time
    def run(self, cycles: int, max_events: int | None = None) -> RunResult:
        if max_events is None:
            max_events = self.options.max_events_per_chunk
        rr = self.session.run(cycles, max_events)
        self.persistence.add_run(rr.start.time_ps, rr.events, rr.end.time_ps, rr.end.out_value,
                                 rr.end.out_xmask)
        self.periph.handle(self.session.take_periph_lines())
        return rr

    # ---------------------------------------------------------------- peripherals
    def uart_send(self, data: bytes) -> None:
        """Queue bytes for the design's UART RX pin (they are shifted in while the clock runs)."""
        if not self.periph.uart_rx:
            raise SimulationError("the design has no UART RX input (RsRx / UART_TXD_IN) on the USB-UART pin")
        for b in data:
            self.session.periph_write(1, b)

    def set_uart_baud(self, baud: int) -> None:
        if not 300 <= baud <= 12_500_000:
            raise SimulationError(f"unsupported baud rate {baud}")
        self.options.uart_baud = baud
        self.session.periph_write(0, max(2, round(100_000_000 / baud)))

    def set_temperature(self, celsius: float) -> None:
        from .peripherals import temp_register
        self.options.temperature_c = celsius
        self.session.periph_write(2, temp_register(celsius))

    def set_accel(self, x_mg: int, y_mg: int, z_mg: int) -> None:
        self.options.accel_mg = (x_mg, y_mg, z_mg)
        for i, v in enumerate((x_mg, y_mg, z_mg)):
            self.session.periph_write(3 + i, int(v) & 0xFFFFFFFF)

    def set_mic(self, amplitude: int, freq_hz: int) -> None:
        self.options.mic = (amplitude, freq_hz)
        self.session.periph_write(6, max(0, min(1000, int(amplitude))))
        self.session.periph_write(7, max(1, int(freq_hz)))

    def step(self, cycles: int = 1) -> RunResult:
        """Advance `cycles` clock cycles, then stop the clock (outputs settle in timing simulation)."""
        rr = self.run(cycles)
        self.settle_outputs()
        return rr

    def settle_outputs(self) -> None:
        """Clock stopped: with real delays the outputs need ~10-20 ns to reach their final value."""
        if self.options.timing and self.session.alive:
            self._apply(self.session.settle())

    def set_recording(self, on: bool) -> None:
        self.session.set_recording(on)

    def _apply(self, snap: Snapshot) -> None:
        self.persistence.set_state(snap.time_ps, snap.out_value, snap.out_xmask, external=True)

    # ---------------------------------------------------------------- outputs
    @property
    def snapshot(self) -> Snapshot | None:
        return self.session.snapshot

    @property
    def cycles(self) -> int:
        return self.session.snapshot.cycles if self.session.snapshot else 0

    @property
    def time_ps(self) -> int:
        return self.session.snapshot.time_ps if self.session.snapshot else 0

    def level(self, resource_id: str) -> int | None:
        """Instantaneous logic level of an output resource (None = unmapped or X/Z)."""
        s = self.session.snapshot
        return None if s is None else self.io.resource_level(resource_id, s.out_value, s.out_xmask)

    def led(self, index: int) -> int | None:
        return self.level(f"LED{index}")

    def leds(self) -> list[int | None]:
        return [self.led(i) for i in range(16)]

    def led_brightness(self, index: int) -> float | None:
        return self.persistence.duty(self.io.output_bits.get(f"LED{index}"))

    def brightness(self, resource_id: str) -> float | None:
        """Time-averaged level of any output resource (RGB LED channels ...); None when unmapped."""
        return self.persistence.duty(self.io.output_bits.get(resource_id))

    def seven_segment(self) -> list[DigitState]:
        ss = self.board.seven_segment
        bits = self.io.output_bits
        return self.persistence.seven_segment([bits.get(s) for s in ss.segments], bits.get(ss.dp),
                                              [bits.get(a) for a in ss.anodes],
                                              ss.segments_active_low, ss.anodes_active_low)

    def port_values(self) -> list[tuple[str, str, int, int]]:
        """(name, direction, value, unknown_mask) for every port, for the I/O monitor."""
        out = []
        snap = self.session.snapshot
        for i, p in enumerate(self.compiled.ports):
            if p.direction == "input":
                out.append((p.name, "input", self.session.inputs.get(i, 0), 0))
            elif p.direction == "output" and snap is not None:
                v, x = self.session.output_port(snap, i)
                out.append((p.name, "output", v, x))
        return out

    def take_design_output(self) -> list[str]:
        return self.session.take_design_output()
