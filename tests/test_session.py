"""Simulation process management and protocol tests (real vvp process)."""
from __future__ import annotations

import threading
import time

import pytest

from app.compiler.compiler import compile_design
from app.simulation.harness import generate_harness
from app.simulation.session import SimulationError, SimulationSession, parse_hex

from .conftest import write


def make_session(toolchain, tmp_path, verilog: str, top: str, **kw) -> SimulationSession:
    src = write(tmp_path / f"{top}.v", verilog)
    res = compile_design(toolchain, [src], top, tmp_path / "c")
    assert res.success, [str(d) for d in res.diagnostics]
    s = SimulationSession.from_compile(toolchain, res, tmp_path / "s", **kw)
    s.build()
    return s


def test_parse_hex():
    assert parse_hex("00ff", 16) == (0xFF, 0)
    assert parse_hex("x1", 8) == (0x01, 0xF0)
    assert parse_hex("Z", 4) == (0, 0xF)


def test_harness_escapes_names():
    from app.compiler.compiler import Port
    text = generate_harness("my$top", [Port("a.b", "input", 1, 0, 0), Port("y", "output", 2, 1, 0)])
    assert ".\\a.b (fvl_p0)" in text and "my$top fvl_dut" in text


def test_session_lifecycle_and_stop(toolchain, tmp_path):
    s = make_session(toolchain, tmp_path, "module t(input a, output y); assign y = ~a; endmodule", "t")
    snap = s.start()
    assert s.alive and snap.cycles == 0
    assert s.output_by_name("y") == (1, 0)
    s.set_input(s.input_index("a"), 1)
    assert s.output_by_name("y") == (0, 0)
    s.stop()
    assert not s.alive
    with pytest.raises(SimulationError):
        s.set_input(0, 1)
    s.start()                        # can be started again
    assert s.output_by_name("y") == (0, 0)    # input value is kept across restarts
    s.stop()


def test_set_input_on_output_rejected(toolchain, tmp_path):
    s = make_session(toolchain, tmp_path, "module t(input a, output y); assign y = a; endmodule", "t")
    s.start()
    try:
        with pytest.raises(SimulationError):
            s.set_input(1, 1)
        with pytest.raises(ValueError):
            s.run(-1)
    finally:
        s.stop()


def test_design_display_output_and_finish(toolchain, tmp_path):
    s = make_session(toolchain, tmp_path, """
        module t(input clk, output reg [7:0] n);
          initial n = 0;
          always @(posedge clk) begin n <= n + 1; $display("tick %0d", n);
            if (n == 3) $finish; end
        endmodule""", "t", clock_masks={0: 1})
    s.start()
    s.run(2)
    assert s.take_design_output() == ["tick 0", "tick 1"]
    with pytest.raises(SimulationError) as e:
        s.run(5)
    assert "$finish" in str(e.value)
    assert not s.alive


def test_stop_interrupts_long_run(toolchain, tmp_path):
    s = make_session(toolchain, tmp_path, """
        module t(input clk, output reg [31:0] n = 0); always @(posedge clk) n <= n + 1; endmodule""",
                     "t", clock_masks={0: 1})
    s.start()
    errors = []

    def worker():
        try:
            s.run(2_000_000_000)
        except SimulationError as exc:
            errors.append(exc)

    th = threading.Thread(target=worker)
    th.start()
    time.sleep(0.5)
    t0 = time.perf_counter()
    s.stop()
    th.join(timeout=10)
    assert not th.is_alive()
    assert time.perf_counter() - t0 < 5
    assert errors and not s.alive


def test_event_reporting_and_overflow(toolchain, tmp_path):
    s = make_session(toolchain, tmp_path, """
        module t(input clk, output reg [3:0] n = 0); always @(posedge clk) n <= n + 1; endmodule""",
                     "t", clock_masks={0: 1})
    s.start()
    try:
        rr = s.run(10, max_events=100)
        assert [v for _, v, _ in rr.events] == list(range(1, 11))
        times = [t for t, _, _ in rr.events]
        assert all(b - a == 10_000 for a, b in zip(times, times[1:]))     # 10 ns = 10000 ps
        rr = s.run(10, max_events=3)
        assert len(rr.events) == 3 and rr.overflow
    finally:
        s.stop()


def test_build_error_is_reported(toolchain, tmp_path):
    src = write(tmp_path / "t.v", "module t(input a, output y); assign y = a; endmodule")
    s = SimulationSession(toolchain, [src], "not_the_top", [], tmp_path / "s")
    with pytest.raises(SimulationError):
        s.build()


def test_simulation_speed_is_reasonable(toolchain, tmp_path):
    s = make_session(toolchain, tmp_path, """
        module t(input clk, output reg [15:0] n = 0); always @(posedge clk) n <= n + 1; endmodule""",
                     "t", clock_masks={0: 1}, waveforms=False)
    s.start()
    try:
        rr = s.run(200_000)
        assert rr.end.cycles == 200_000
        rate = 200_000 / rr.wall_s
        print(f"\nsimulation rate: {rate:,.0f} cycles/s")
        assert rate > 20_000
    finally:
        s.stop()
