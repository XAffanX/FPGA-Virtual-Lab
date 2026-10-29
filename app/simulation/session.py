"""Persistent, interactive Icarus Verilog simulation of a user design.

`SimulationSession` is GUI-independent: it is used directly by the tests and,
wrapped in a worker thread, by the application.

Life cycle::

    s = SimulationSession(toolchain, sources, top, ports, workdir)
    s.build()                    # generate harness + compile with iverilog
    s.start()                    # launch one persistent vvp process
    s.set_input(port_index, val) # returns a Snapshot of all outputs
    s.run(cycles)                # advance clock cycles, returns RunResult
    s.restart()                  # deterministic power-on reset (new process)
    s.stop()
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.compiler.compiler import (CompileResult, Diagnostic, Port, common_args, generation_flag,
                                   parse_diagnostics, run_iverilog)
from app.utils.paths import native_dir
from app.utils.toolchain import CREATE_NO_WINDOW, Toolchain

from .harness import (OP_DUMP, OP_PERIPH, OP_QUERY, OP_QUIT, OP_RUN, OP_SET, OP_SETTLE, PROTOCOL_PREFIX,
                      HarnessExtras,
                      OutputLayout, generate_harness, output_layout)

INIT_VPI = "fvl_init"
MAX_CYCLES_PER_COMMAND = 2**31 - 1


class SimulationError(RuntimeError):
    def __init__(self, message: str, diagnostics: list[Diagnostic] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


def parse_hex(text: str, width: int) -> tuple[int, int]:
    """Parse Icarus %h output into (value, unknown_mask). x/z digits mark 4 unknown bits."""
    value = xmask = 0
    for ch in text.strip():
        value <<= 4
        xmask <<= 4
        c = ch.lower()
        if c in "xz?":
            xmask |= 0xF
        else:
            value |= int(c, 16)
    full = (1 << width) - 1
    return value & full, xmask & full


@dataclass
class Snapshot:
    """State of all outputs at one simulated instant."""
    cycles: int
    time_ps: int
    out_value: int
    out_xmask: int
    overflow: bool = False


@dataclass
class RunResult:
    start: Snapshot
    end: Snapshot
    events: list[tuple[int, int, int]] = field(default_factory=list)  # (time_ps, value, xmask)
    overflow: bool = False
    wall_s: float = 0.0


def native_init_module() -> Path | None:
    """The bundled power-on-init VPI module, if present."""
    p = native_dir() / f"{INIT_VPI}.vpi"
    return p if p.is_file() else None


class SimulationSession:
    def __init__(self, toolchain: Toolchain, sources: list[Path], top: str, ports: list[Port],
                 workdir: Path, *, clock_masks: dict[int, int] | None = None,
                 language: str = "auto", include_dirs: list[Path] | None = None,
                 defines: dict[str, str] | None = None, power_on_init: bool = True,
                 waveforms: bool = True, record_initially: bool = False,
                 extra_args: list[str] | None = None, extra_roots: list[str] | None = None,
                 extras: HarnessExtras | None = None, timing: bool = False):
        self.toolchain = toolchain
        self.sources = [Path(s) for s in sources]
        self.top = top
        self.ports = list(ports)
        self.workdir = Path(workdir)
        self.clock_masks = dict(clock_masks or {})
        self.language = language
        self.include_dirs = include_dirs
        self.defines = defines
        self.power_on_init = power_on_init
        self.waveforms = waveforms
        self.recording = record_initially
        self.extra_args = list(extra_args or [])
        self.extra_roots = list(extra_roots or [])
        self.layout: list[OutputLayout] = output_layout(self.ports)
        self.out_width = max(1, sum(l.width for l in self.layout))
        self.harness_path = self.workdir / "fvl_harness.v"
        self.vvp_path = self.workdir / "fvl_sim.vvp"
        self.vcd_path = self.workdir / "waves.vcd"
        self.build_log = ""
        self.build_diagnostics: list[Diagnostic] = []
        self.init_module: Path | None = None
        self.init_status = ""
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.RLock()
        self._abort = threading.Event()
        self.design_output: list[str] = []     # lines printed by the design ($display ...)
        self.sim_info: list[str] = []          # simulator status lines (VCD info, power-on init)
        self.periph_lines: list[str] = []      # "@@FVL <letter> ..." lines from peripheral models
        self.extras = extras
        self.timing = timing
        self.exit_message = ""
        self.snapshot: Snapshot | None = None
        self.inputs: dict[int, int] = {}       # last value written to each input port

    # ------------------------------------------------------------------ build
    @classmethod
    def from_compile(cls, toolchain: Toolchain, result: CompileResult, workdir: Path, **kw) -> "SimulationSession":
        if not result.success:
            raise SimulationError("the design has not been compiled successfully")
        kw.setdefault("extra_args", result.extra_args)
        kw.setdefault("extra_roots", result.extra_roots)
        return cls(toolchain, result.sources, result.top, result.ports, workdir, **kw)

    def build(self) -> None:
        """Generate the harness and compile it together with the design."""
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.init_module = native_init_module() if self.power_on_init else None
        if self.power_on_init and self.init_module is None:
            self.init_status = ("power-on register initialization unavailable (fvl_init.vpi not found); "
                                "registers without reset/initial value start as X")
        harness = generate_harness(self.top, self.ports, self.clock_masks,
                                   vcd_path=self.vcd_path if self.waveforms else None,
                                   power_on_init=self.init_module is not None,
                                   record_initially=self.recording,
                                   startup_ns=20.0 if self.timing else (0.2 if self.extra_roots else 0.001),
                                   extras=self.extras, timing=self.timing)
        self.harness_path.write_text(harness, encoding="utf-8")
        if self.vvp_path.exists():
            self.vvp_path.unlink()
        flag = generation_flag(self.language, self.sources)
        args = common_args(flag, self.sources, self.include_dirs, self.defines)
        # Harness first: its `timescale 1ns/1ps also becomes the default for user files
        # without a timescale directive (same default as Vivado).
        args += self.extra_args
        args += ["-Wno-timescale", "-s", "fvl_tb"] + [a for r in self.extra_roots for a in ("-s", r)]
        args += ["-o", str(self.vvp_path), str(self.harness_path)]
        args += [str(s) for s in self.sources]
        rc, text = run_iverilog(self.toolchain, args)
        self.build_log = text
        self.build_diagnostics = parse_diagnostics(text)
        if rc != 0 or not self.vvp_path.is_file():
            errs = [d for d in self.build_diagnostics if d.severity == "error"]
            raise SimulationError("building the simulation model failed: " +
                                  ("; ".join(str(e) for e in errs[:5]) or f"iverilog exit code {rc}"),
                                  self.build_diagnostics)

    # ---------------------------------------------------------------- process
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> Snapshot:
        with self._lock:
            if not self.vvp_path.is_file():
                raise SimulationError("simulation model not built; call build() first")
            self._kill()
            self._abort.clear()
            self.exit_message = ""
            cmd = [str(self.toolchain.vvp), "-n"]
            if self.init_module is not None:
                cmd += ["-M", str(self.init_module.parent), "-m", INIT_VPI]
            cmd.append(str(self.vvp_path))
            # Power-on input values (switch positions, constants) as plusargs.
            cmd += [f"+fvl_i{idx}={val:x}" for idx, val in sorted(self.inputs.items())]
            env = dict(os.environ)
            # vvp needs its own DLLs (and our VPI module) to be found.
            env["PATH"] = os.pathsep.join([str(self.toolchain.bin_dir), env.get("PATH", "")])
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, cwd=str(self.workdir), env=env,
                                          text=True, encoding="utf-8", errors="replace", bufsize=1,
                                          creationflags=CREATE_NO_WINDOW)
            self._lines = queue.Queue()
            self._reader = threading.Thread(target=self._read_loop, args=(self._proc, self._lines),
                                            daemon=True, name="vvp-reader")
            self._reader.start()
            self.snapshot, _, _ = self._wait_status(timeout=30.0)
            return self.snapshot

    def restart(self) -> Snapshot:
        """Deterministic power-on reset: fresh simulator, same compiled model, same inputs."""
        return self.start()

    def stop(self) -> None:
        """Terminate the simulator. Safe to call from any thread, even during a long run."""
        self._abort.set()
        if self._lock.acquire(timeout=0.5):
            try:
                if self.alive:
                    try:
                        self._proc.stdin.write(f"{OP_QUIT} 0 0\n")
                        self._proc.stdin.flush()
                        self._proc.wait(timeout=2.0)
                    except (OSError, ValueError, subprocess.TimeoutExpired):
                        pass
                self._kill()
            finally:
                self._lock.release()
        else:
            # A run in another thread holds the lock: killing the process makes it return.
            self._kill()

    def _kill(self) -> None:
        p = self._proc
        if p is not None:
            if p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for s in (p.stdin, p.stdout):
                try:
                    if s:
                        s.close()
                except OSError:
                    pass
        self._proc = None

    @staticmethod
    def _read_loop(proc: subprocess.Popen, q: queue.Queue) -> None:
        try:
            for line in proc.stdout:
                q.put(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        q.put(None)

    # --------------------------------------------------------------- protocol
    def _send(self, op: int, a: int, b: int) -> None:
        if not self.alive:
            raise SimulationError(self.exit_message or "the simulator is not running")
        try:
            self._proc.stdin.write(f"{op} {a} {b:x}\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise SimulationError(f"lost connection to the simulator: {exc}") from exc

    def _wait_status(self, timeout: float | None = None, events: list | None = None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._abort.is_set() and not self.alive:
                raise SimulationError("simulation stopped")
            remaining = 0.1 if deadline is None else max(0.0, min(0.1, deadline - time.monotonic()))
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    self._kill()
                    raise SimulationError("the simulator did not respond (timeout)")
                continue
            if line is None:
                if self._abort.is_set():
                    self._kill()
                    raise SimulationError("simulation stopped")
                self._on_exit()
                raise SimulationError(self.exit_message)
            if not line.startswith(PROTOCOL_PREFIX):
                if line.startswith(("VCD info:", "FVL_INIT ")):
                    self.sim_info.append(line)       # simulator/VPI status, not design output
                else:
                    self.design_output.append(line)
                continue
            parts = line.split()
            if len(parts) >= 6 and parts[1] == "S":
                v, x = parse_hex(parts[4], self.out_width)
                snap = Snapshot(int(parts[2]), int(parts[3]), v, x, parts[5] == "1")
                return snap, events, snap.overflow
            if len(parts) >= 4 and parts[1] == "E":
                if events is not None:
                    v, x = parse_hex(parts[3], self.out_width)
                    events.append((int(parts[2]), v, x))
            elif len(parts) >= 2:
                self.periph_lines.append(line[len(PROTOCOL_PREFIX) + 1:])   # e.g. "U 41", "P 12 3 f00"

    def _on_exit(self) -> None:
        tail = [l for l in self.design_output[-5:] if l.strip()]
        rc = self._proc.poll() if self._proc else None
        if any("$finish" in l for l in tail):
            msg = "the design called $finish; simulation ended"
        else:
            msg = f"the simulator exited unexpectedly (exit code {rc})"
        if tail:
            msg += ": " + " | ".join(tail[-3:])
        self.exit_message = msg
        self._kill()

    # ---------------------------------------------------------------- actions
    def query(self) -> Snapshot:
        with self._lock:
            self._send(OP_QUERY, 0, 0)
            self.snapshot, _, _ = self._wait_status(timeout=30.0)
            return self.snapshot

    def set_input(self, port_index: int, value: int) -> Snapshot:
        port = self.ports[port_index]
        if port.direction != "input":
            raise SimulationError(f"port '{port.name}' is not an input")
        value &= (1 << port.width) - 1
        with self._lock:
            self._send(OP_SET, port_index, value)
            self.inputs[port_index] = value
            self.snapshot, _, _ = self._wait_status(timeout=30.0)
            return self.snapshot

    def run(self, cycles: int, max_events: int = 0) -> RunResult:
        """Advance `cycles` full clock periods (rising then falling edge)."""
        if cycles < 0:
            raise ValueError("cycles must be >= 0")
        with self._lock:
            t0 = time.perf_counter()
            start = self.snapshot or self.query()
            events: list = []
            overflow = False
            remaining = cycles
            while True:
                n = min(remaining, MAX_CYCLES_PER_COMMAND)
                self._send(OP_RUN, n, max(0, max_events - len(events)))
                self.snapshot, events, ovf = self._wait_status(timeout=None, events=events)
                overflow |= ovf
                remaining -= n
                if remaining <= 0:
                    break
            return RunResult(start, self.snapshot, events, overflow, time.perf_counter() - t0)

    def periph_write(self, reg: int, value: int) -> Snapshot:
        """Write a peripheral register in the harness (UART byte to send, baud rate, sensor value ...)."""
        with self._lock:
            self._send(OP_PERIPH, int(reg), int(value) & 0xFFFFFFFF)
            self.snapshot, _, _ = self._wait_status(timeout=30.0)
            return self.snapshot

    def settle(self) -> Snapshot:
        """Clock stopped: give outputs 20 ns to settle (only meaningful with real delays)."""
        with self._lock:
            self._send(OP_SETTLE, 0, 0)
            self.snapshot, _, _ = self._wait_status(timeout=30.0)
            return self.snapshot

    def take_periph_lines(self) -> list[str]:
        out, self.periph_lines = self.periph_lines, []
        return out

    def set_recording(self, on: bool) -> Snapshot:
        with self._lock:
            self.recording = on
            if not self.waveforms:
                return self.snapshot or self.query()
            self._send(OP_DUMP, 1 if on else 0, 0)
            self.snapshot, _, _ = self._wait_status(timeout=30.0)
            return self.snapshot

    def take_design_output(self) -> list[str]:
        out, self.design_output = self.design_output, []
        return out

    # ---------------------------------------------------------------- helpers
    def output_port(self, snap: Snapshot, port_index: int) -> tuple[int, int]:
        """(value, unknown_mask) of one output port in a snapshot or event."""
        return self.extract(snap.out_value, snap.out_xmask, port_index)

    def extract(self, value: int, xmask: int, port_index: int) -> tuple[int, int]:
        for l in self.layout:
            if l.port_index == port_index:
                m = (1 << l.width) - 1
                return (value >> l.offset) & m, (xmask >> l.offset) & m
        raise KeyError(f"port #{port_index} is not an output")

    def output_by_name(self, name: str, snap: Snapshot | None = None) -> tuple[int, int]:
        snap = snap or self.snapshot
        idx = next(i for i, p in enumerate(self.ports) if p.name == name)
        return self.output_port(snap, idx)

    def input_index(self, name: str) -> int:
        return next(i for i, p in enumerate(self.ports) if p.name == name and p.direction == "input")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
