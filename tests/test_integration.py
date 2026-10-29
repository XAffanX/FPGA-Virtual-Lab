"""Integration tests: real Verilog, real Icarus Verilog simulator, virtual board I/O."""
from __future__ import annotations

import pytest

from app.compiler.compiler import compile_design
from app.mapping.auto import auto_map
from app.simulation.engine import BoardSimulator, EngineOptions

from .conftest import write


def test_switch_to_led_sw0_drives_led0(open_example):
    """Required acceptance test: SW0 -> LED0 through the actual simulation backend."""
    sim = open_example("switch_led")
    assert sim.alive
    assert sim.led(0) == 0
    sim.set_switch(0, True)
    assert sim.led(0) == 1
    assert sim.leds()[1:] == [0] * 15
    sim.set_switch(0, False)
    assert sim.led(0) == 0


def test_switch_to_led_all_switches(open_example):
    sim = open_example("switch_led")
    pattern = 0b1010_0101_1100_0011
    for i in range(16):
        sim.set_switch(i, bool(pattern >> i & 1))
    assert sim.leds() == [pattern >> i & 1 for i in range(16)]


def counter_value(sim) -> int:
    return sum((sim.led(i) or 0) << i for i in range(4))


def test_counter_advances_each_clock_cycle(open_example):
    """Required acceptance test: a counter advances correctly across consecutive cycles."""
    sim = open_example("counter")
    assert counter_value(sim) == 0
    for expected in range(1, 21):
        rr = sim.step(1)
        assert rr.end.cycles == expected
        assert counter_value(sim) == expected % 16
    sim.run(100)
    assert sim.cycles == 120
    assert counter_value(sim) == 120 % 16


def test_counter_button_reset_and_simulation_time(open_example):
    sim = open_example("counter")
    sim.step(5)
    assert counter_value(sim) == 5
    assert sim.time_ps >= 5 * 10_000          # 10 ns per cycle at 100 MHz
    sim.press("BTNC")
    sim.step(1)                                # synchronous reset needs a clock edge
    assert counter_value(sim) == 0
    sim.step(3)
    assert counter_value(sim) == 0             # held in reset while pressed
    sim.release("BTNC")
    sim.step(3)
    assert counter_value(sim) == 3


def test_restart_is_deterministic(open_example):
    sim = open_example("counter")
    sim.step(7)
    assert counter_value(sim) == 7
    snap = sim.reset()
    assert snap.cycles == 0
    assert counter_value(sim) == 0
    sim.step(7)
    assert counter_value(sim) == 7


def test_adder_subtractor(open_example):
    sim = open_example("adder_subtractor")

    def apply(a, b, sub):
        for i in range(2):
            sim.set_switch(i, bool(a >> i & 1))
            sim.set_switch(2 + i, bool(b >> i & 1))
        sim.set_switch(15, sub)
        return (sim.led(0) | sim.led(1) << 1, sim.led(2))

    for a in range(4):
        for b in range(4):
            res, carry = apply(a, b, False)
            assert res == (a + b) & 3 and carry == (a + b) >> 2
            res, _ = apply(a, b, True)
            assert res == (a - b) & 3
    assert sim.led(15) == 1


def test_multiplexer(open_example):
    sim = open_example("multiplexer")
    data = [0b101, 0b011, 0b110, 0b001]
    for k, d in enumerate(data):
        for i in range(3):
            sim.set_switch(3 * k + i, bool(d >> i & 1))
    for sel in range(4):
        sim.set_switch(14, bool(sel & 1))
        sim.set_switch(15, bool(sel & 2))
        assert sum(sim.led(i) << i for i in range(3)) == data[sel]
        assert [sim.led(12 + k) for k in range(4)] == [int(k == sel) for k in range(4)]


SEG_PATTERNS = {0x0: "1000000", 0x1: "1111001", 0x2: "0100100", 0x3: "0110000", 0x4: "0011001",
                0x5: "0010010", 0x6: "0000010", 0x7: "1111000", 0x8: "0000000", 0x9: "0010000",
                0xA: "0001000", 0xB: "0000011", 0xC: "1000110", 0xD: "0100001", 0xE: "0000110",
                0xF: "0001110"}


def lit_segments(hexdigit: int) -> list[bool]:
    gfedcba = SEG_PATTERNS[hexdigit]
    return [gfedcba[6 - s] == "0" for s in range(7)]       # A..G, active low


def test_seven_segment_multiplexing_and_persistence(open_example):
    sim = open_example("seven_segment")
    value = 0x3A7C
    for i in range(16):
        sim.set_switch(i, bool(value >> i & 1))
    sim.run(4 * 1024 * 3)                      # three full refresh periods (REFRESH_BITS = 12)
    digits = sim.seven_segment()
    for k in range(4):
        nib = value >> (4 * k) & 0xF
        assert digits[k].lit, k
        lit = [b > 0.5 for b in digits[k].segments[:7]]
        assert lit == lit_segments(nib), (k, hex(nib))
        assert digits[k].segments[7] == 0.0          # decimal point off
    # Only one anode is active at any instant (instantaneous outputs).
    active = [sim.level(f"AN{k}") == 0 for k in range(4)]
    assert sum(active) == 1


def test_seven_segment_decimal_point_button(open_example):
    sim = open_example("seven_segment")
    sim.press("BTNC")
    sim.run(4 * 1024 * 2)
    digits = sim.seven_segment()
    assert digits[0].segments[7] > 0.9
    assert all(d.segments[7] == 0 for d in digits[1:])


def test_fsm_lock_with_pushbuttons(open_example):
    sim = open_example("fsm")

    def click(btn):
        sim.press(btn)
        sim.step(4)
        sim.release(btn)
        sim.step(4)

    for btn, progress in [("BTNU", 1), ("BTNL", 2), ("BTNR", 3)]:
        click(btn)
        assert sum(sim.led(i) for i in range(4)) == progress
    click("BTND")
    assert sim.led(15) == 1
    # Wrong code -> error state; BTNC returns to idle.
    sim.press("BTNC"); sim.step(2); sim.release("BTNC"); sim.step(2)
    assert sim.led(15) == 0 and sim.led(0) == 0
    click("BTND")
    assert sim.led(14) == 1


def test_independent_design_with_automap(toolchain, board, tmp_path):
    """A user-written design with its own naming is imported, auto-mapped and simulated."""
    src = write(tmp_path / "src" / "my_alu.v", """
        module my_alu(input clk, input btnU, input [15:0] sw, output reg [15:0] led,
                      output [6:0] seg, output [3:0] an, output dp);
          reg [7:0] acc;
          always @(posedge clk) if (btnU) acc <= acc + sw[7:0];
          always @(*) led = {acc, sw[15:8] & sw[7:0]};
          assign seg = 7'h7f; assign an = 4'hf; assign dp = 1'b1;
        endmodule
    """)
    res = compile_design(toolchain, [src], "my_alu", tmp_path / "c")
    assert res.success
    mapping, _ = auto_map(res.ports, board)
    sim = BoardSimulator(toolchain, board, res, mapping, tmp_path / "s", EngineOptions())
    sim.program()
    try:
        assert sim.leds()[8:] == [0] * 8                     # acc zeroed by power-on init
        for i in (0, 1, 8, 9):
            sim.set_switch(i, True)
        assert sim.leds()[:8] == [1, 1, 0, 0, 0, 0, 0, 0]    # 0x03 & 0x03
        sim.press("BTNU")
        sim.step(5)
        sim.release("BTNU")
        sim.step(2)
        acc = sum(sim.led(8 + i) << i for i in range(8))
        assert acc == 5 * 3
    finally:
        sim.stop()


def test_x_without_power_on_init(toolchain, board, tmp_path):
    src = write(tmp_path / "c.v", """
        module c(input clk, output [3:0] led); reg [3:0] q;
          always @(posedge clk) q <= q + 1; assign led = q; endmodule""")
    res = compile_design(toolchain, [src], "c", tmp_path / "c")
    mapping, _ = auto_map(res.ports, board)
    sim = BoardSimulator(toolchain, board, res, mapping, tmp_path / "s", EngineOptions(power_on_init=False))
    sim.program()
    try:
        sim.step(3)
        assert sim.led(0) is None                          # stays X like a pure simulator
    finally:
        sim.stop()
    sim = BoardSimulator(toolchain, board, res, mapping, tmp_path / "s2", EngineOptions(power_on_init=True))
    sim.program()
    try:
        sim.step(3)
        assert counter_from(sim) == 3                      # FPGA-like power-on state
    finally:
        sim.stop()


def counter_from(sim):
    return sum((sim.led(i) or 0) << i for i in range(4))


def test_reset_port_pulse(toolchain, board, tmp_path):
    from app.project.project import ResetConfig
    src = write(tmp_path / "r.v", """
        module r(input clk, input rst_n, output [3:0] led); reg [3:0] q = 4'd9;
          always @(posedge clk or negedge rst_n) if (!rst_n) q <= 0; else q <= q + 1;
          assign led = q; endmodule""")
    res = compile_design(toolchain, [src], "r", tmp_path / "c")
    mapping, _ = auto_map(res.ports, board)
    mapping.set("rst_n", None, "CONST1")
    sim = BoardSimulator(toolchain, board, res, mapping, tmp_path / "s",
                         EngineOptions(reset=ResetConfig("rst_n", active_high=False, cycles=2)))
    sim.program()
    try:
        sim.step(2)
        assert counter_from(sim) == 11
        sim.reset()                    # restart + pulse rst_n low for 2 cycles
        assert counter_from(sim) == 0
        sim.step(1)
        assert counter_from(sim) == 1
    finally:
        sim.stop()


@pytest.mark.parametrize("folder", ["switch_led", "counter", "adder_subtractor", "multiplexer",
                                    "seven_segment", "fsm"])
def test_every_example_compiles_and_runs(open_example, folder):
    sim = open_example(folder)
    sim.run(50)
    assert sim.alive and sim.cycles == 50
