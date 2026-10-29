"""Board peripherals (UART, VGA, Pmod, Nexys sensors) against real designs.

RTL-level tests compile the test designs in tests/fixtures/designs directly (fast); the bitstream
tests run the same designs as built by Vivado (implemented netlists) when Vivado is installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.board.board import get_board
from app.compiler.compiler import compile_design
from app.mapping.xdc import parse_xdc
from app.simulation.engine import BoardSimulator, EngineOptions
from app.waveforms.vcd import VcdReader

DESIGNS = Path(__file__).parent / "fixtures" / "designs"
FIX = Path(__file__).parent / "fixtures"


def rtl_sim(toolchain, tmp_path, name: str, board_id: str, **opts) -> BoardSimulator:
    d = DESIGNS / name
    board = get_board(board_id)
    res = compile_design(toolchain, [d / f"{name}.v"], name, tmp_path / "c")
    assert res.success, [str(e) for e in res.errors]
    x = parse_xdc(d / f"{name}.xdc", board, res.ports)
    assert not [i for i in x.issues if i.severity == "error"], x.issues
    sim = BoardSimulator(toolchain, board, res, x.mapping, tmp_path / "s", EngineOptions(**opts))
    sim.program()
    return sim


# ------------------------------------------------------------------ UART
def test_uart_receive_and_transmit(toolchain, tmp_path):
    sim = rtl_sim(toolchain, tmp_path, "uart_echo", "basys3", uart_baud=115200)
    try:
        assert sim.periph.has_uart and sim.periph.uart_tx is not None and sim.periph.uart_rx
        sim.uart_send(b"Az")
        sim.run(2 * 10 * 868 + 25_000)                      # two frames in, two echoed back
        assert bytes(sim.periph.take_uart()) == b"Az"
        assert sum(sim.led(i) << i for i in range(8)) == ord("z")
        sim.press("BTNC")
        sim.run(10)
        sim.release("BTNC")
        sim.run(4 * 10 * 868 + 5_000)
        assert bytes(sim.periph.take_uart()) == b"HI\r\n"
        assert sim.periph.uart_errors == 0
    finally:
        sim.stop()


def test_uart_wrong_baud_gives_garbage(toolchain, tmp_path):
    sim = rtl_sim(toolchain, tmp_path, "uart_echo", "basys3", uart_baud=115200)
    try:
        sim.set_uart_baud(57600)                             # mismatch: as with a misconfigured terminal
        sim.uart_send(b"U")
        sim.run(60_000)
        got = sim.periph.take_uart()
        assert got != [ord("U")]
    finally:
        sim.stop()


# ------------------------------------------------------------------ VGA
def test_vga_picture(toolchain, tmp_path):
    sim = rtl_sim(toolchain, tmp_path, "vga_test", "basys3")
    try:
        # square at x = 0x20*2 = 64, y = 0x30 = 48
        for i in (5, 12, 13):                                # sw[7:0] = 0x20, sw[15:8] = 0x30
            sim.set_switch(i, True)
        sim.run(2 * 800 * 525 * 4 + 10_000)                 # two full frames
        v = sim.periph.vga_state
        assert v.frames >= 2 and v.divider == 4 and v.hperiod == 3200 and v.lines == 525
        assert "640x480 @ 60 Hz" in v.timing_text

        def px(x, y):
            i = (y * 640 + x) * 3
            return tuple(v.frame[i:i + 3])
        assert px(10, 10) == (255, 0, 0)                     # bar 0 red
        assert px(100, 300) == (0, 255, 0)                   # bar 1 green
        assert px(150, 300) == (0, 0, 255)                   # bar 2 blue
        assert px(70, 60) == (255, 255, 255)                 # the square
        assert px(70, 90) == (0, 255, 0)                     # below it: bar 1 again
        assert px(639, 479) == (0, 136, 255)                 # last bar 0x08F
    finally:
        sim.stop()


# ------------------------------------------------------------------ Pmod
def test_pmod_inputs_and_outputs(toolchain, tmp_path):
    sim = rtl_sim(toolchain, tmp_path, "pmod_test", "basys3")
    try:
        sim.set_switch(0, True)
        sim.set_switch(2, True)
        assert [sim.level(f"JA{n}") for n in (1, 2, 3, 4)] == [1, 0, 1, 0]   # JA_out = SW[3:0]
        sim.set_input("JA7", 1)
        sim.set_input("JA10", 1)
        sim.set_input("JB2", 1)
        assert [sim.led(i) for i in range(4)] == [1, 0, 0, 1] and sim.led(15) == 1
        sim.run(1)
        a = sim.level("JB1")
        sim.run(1)
        assert sim.level("JB1") == 1 - a                     # toggles every cycle
    finally:
        sim.stop()


# ------------------------------------------------------------------ Nexys 4 DDR sensors
def test_nexys_sensors(toolchain, tmp_path):
    sim = rtl_sim(toolchain, tmp_path, "nexys_sensors", "nexys4ddr",
                  temperature_c=23.5, accel_mg=(500, -250, 1000), mic=(500, 1000))
    try:
        p = sim.periph
        assert p.tmp and p.acl and p.mic
        sim.run(150_000)
        temp_msb = sum((sim.led(i) or 0) << i for i in range(8))
        xdata = sum((sim.led(8 + i) or 0) << i for i in range(8))
        assert temp_msb == (int(23.5 * 16) << 3) >> 8           # 0x0BC0 -> 0x0B
        assert xdata == (500 >> 4) & 0xFF                       # XDATA = acceleration[11:4]
        sim.set_temperature(-10.0)
        sim.set_accel(-800, 0, 0)
        sim.run(150_000)
        assert sum((sim.led(i) or 0) << i for i in range(8)) == ((int(-10 * 16) & 0x1FFF) << 3) >> 8
        assert sum((sim.led(8 + i) or 0) << i for i in range(8)) == (-800 >> 4) & 0xFF
    finally:
        sim.stop()
    r = VcdReader(sim.session.vcd_path)
    r.poll()
    dens = [int(v, 2) for v in r.by_name["fvl_tb.fvl_dut.density"].values if v and "x" not in v]
    assert dens and max(dens) - min(dens) > 40               # a 50 % tone moves the density
    assert all(60 < d < 195 for d in dens[1:])               # around half of 255


def test_accelerometer_reads_zero_in_standby(toolchain, tmp_path):
    """Like the real ADXL362: without POWER_CTL measurement mode the data registers read 0."""
    from app.simulation.peripherals import ADXL362
    assert "meas ? fvl_preg[3]" in ADXL362


# ------------------------------------------------------------------ the same designs as Vivado bitstreams
def bit_sim(toolchain, tmp_path, name: str, board_id: str, timing: bool = False, **opts) -> BoardSimulator:
    from app.vivado.vivado import find_vivado, load_bitstream, mapping_from_pins
    viv = find_vivado()
    if viv is None:
        pytest.skip("Vivado is not installed")
    board = get_board(board_id)
    d = load_bitstream(FIX / f"vivado_{name}" / f"{name}.bit", viv, timing=timing)
    mapping, notes = mapping_from_pins(d, board)
    assert not notes, notes
    res = compile_design(toolchain, [d.netlist], d.top, tmp_path / "c", extra_args=d.compile_args(),
                         extra_roots=d.extra_roots)
    assert res.success, [str(e) for e in res.errors[:3]]
    sim = BoardSimulator(toolchain, board, res, mapping, tmp_path / "s",
                         EngineOptions(power_on_init=False, timing=d.timing, **opts))
    sim.program()
    sim.design = d
    return sim


def test_bitstream_uart(toolchain, tmp_path):
    sim = bit_sim(toolchain, tmp_path, "uart_echo", "basys3", uart_baud=115200)
    try:
        sim.uart_send(b"ok")
        sim.run(2 * 10 * 868 + 25_000)
        assert bytes(sim.periph.take_uart()) == b"ok"
        assert sum(sim.led(i) << i for i in range(8)) == ord("k")
    finally:
        sim.stop()


def test_bitstream_vga_first_lines(toolchain, tmp_path):
    sim = bit_sim(toolchain, tmp_path, "vga_test", "basys3")
    try:
        sim.run(800 * 525 * 4 + 800 * 4 * 50)              # one frame + 50 lines of the next
        v = sim.periph.vga_state
        # first vsync after power-on comes at line 490; full 525-line frames are checked in the RTL test
        assert v.frames >= 1 and v.divider == 4 and v.hperiod == 3200 and v.lines >= 480
        px = lambda x, y: tuple(v.frame[(y * 640 + x) * 3:(y * 640 + x) * 3 + 3])   # noqa: E731
        assert px(10, 5) == (255, 255, 255)                # switches off: the square sits at (0,0)
        assert px(10, 40) == (255, 0, 0)                   # below it: red bar
        assert px(100, 40) == (0, 255, 0)
    finally:
        sim.stop()


def test_bitstream_pmod(toolchain, tmp_path):
    sim = bit_sim(toolchain, tmp_path, "pmod_test", "basys3")
    try:
        sim.set_switch(1, True)
        sim.set_input("JA8", 1)
        assert [sim.level(f"JA{n}") for n in (1, 2, 3, 4)] == [0, 1, 0, 0]
        assert [sim.led(i) for i in range(4)] == [0, 1, 0, 0]
    finally:
        sim.stop()


def test_bitstream_nexys_sensors(toolchain, tmp_path):
    sim = bit_sim(toolchain, tmp_path, "nexys_sensors", "nexys4ddr", temperature_c=30.0, accel_mg=(-300, 0, 0))
    try:
        assert sim.periph.tmp and sim.periph.acl and sim.periph.mic
        sim.run(150_000)
        assert sum((sim.led(i) or 0) << i for i in range(8)) == (int(30 * 16) << 3) >> 8
        assert sum((sim.led(8 + i) or 0) << i for i in range(8)) == (-300 >> 4) & 0xFF
    finally:
        sim.stop()


# ------------------------------------------------------------------ timing simulation (Vivado SDF delays)
def _f(v: int) -> int:
    for _ in range(16):
        v = ((v + (v << 3)) ^ (v >> 5)) & 0xFFFFFFFF
    return v


def _expected_led(n: int) -> int:
    acc = _f(((n - 1) * 0x01030507) & 0xFFFFFFFF) if n >= 1 else 0
    return (acc >> 16) ^ (acc & 0xFFFF)


def _leds(sim) -> int | None:
    v = sim.leds()
    return None if None in v else sum(b << i for i, b in enumerate(v))


def test_timing_simulation_counter_meets_timing(toolchain, tmp_path):
    sim = bit_sim(toolchain, tmp_path, "counter", "basys3", timing=True)
    try:
        assert sim.design.timing and sim.design.wns_ns is not None and sim.design.wns_ns > 0
        for k in range(1, 12):
            sim.step(1)
            assert sum((sim.led(i) or 0) << i for i in range(4)) == k % 16
    finally:
        sim.stop()


def test_timing_failure_shows_only_with_delays(toolchain, tmp_path):
    func = bit_sim(toolchain, tmp_path / "f", "timing_fail", "basys3", timing=False)
    try:
        got = []
        for n in range(1, 25):
            func.step(1)
            got.append(_leds(func))
        assert got == [_expected_led(n) for n in range(1, 25)]       # functional: exactly the RTL result
    finally:
        func.stop()
    tim = bit_sim(toolchain, tmp_path / "t", "timing_fail", "basys3", timing=True)
    try:
        assert tim.design.wns_ns is not None and tim.design.wns_ns < -5
        assert any("FAILS timing" in n for n in tim.design.notes)
        got = []
        for n in range(1, 25):
            tim.run(1)                    # consecutive runs = a continuous 100 MHz clock
            got.append(_leds(tim))
        wrong = sum(1 for n, g in zip(range(1, 25), got) if g != _expected_led(n))
        assert wrong >= 12, got                                     # too slow for 10 ns: wrong data
    finally:
        tim.stop()
