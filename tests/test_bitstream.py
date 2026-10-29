"""Loading Vivado .bit files: header parsing, checkpoint matching and simulating the implemented netlist.

The fixture (tests/fixtures/vivado_counter) was produced by Vivado 2026.1 from examples/counter for
xc7a35tcpg236-1 (Basys 3). Tests that need Vivado itself are skipped when it is not installed.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from app.compiler.compiler import compile_design
from app.simulation.engine import BoardSimulator, EngineOptions
from app.vivado.bitfile import BitfileError, read_bit_header
from app.vivado.vivado import find_checkpoint, find_vivado, load_bitstream, mapping_from_pins

FIX = Path(__file__).parent / "fixtures" / "vivado_counter"
BIT = FIX / "counter.bit"


@pytest.fixture(scope="module")
def vivado():
    v = find_vivado()
    if v is None:
        pytest.skip("Vivado is not installed")
    return v


def test_bit_header():
    info = read_bit_header(BIT)
    assert info.design == "counter"
    assert info.full_part == "xc7a35tcpg236"
    assert info.vivado_version == "2026.1"
    assert info.data_length > 2_000_000          # full XC7A35T configuration
    assert "counter" in info.summary()


def test_bit_header_rejects_other_files(tmp_path):
    bad = tmp_path / "x.bit"
    bad.write_bytes(b"\x00\x01not a bitstream")
    with pytest.raises(BitfileError):
        read_bit_header(bad)
    with pytest.raises(BitfileError):
        read_bit_header(Path(__file__).parents[1] / "examples" / "counter" / "counter.v")


def test_checkpoint_lookup(tmp_path):
    dcp, notes = find_checkpoint(read_bit_header(BIT))
    assert dcp == FIX / "counter_routed.dcp" and not notes
    lonely = tmp_path / "counter.bit"
    shutil.copy(BIT, lonely)
    dcp, notes = find_checkpoint(read_bit_header(lonely))
    assert dcp is None and "No routed checkpoint" in notes[0]
    # A checkpoint written long after the bitstream means implementation was re-run.
    shutil.copy(FIX / "counter_routed.dcp", tmp_path / "counter_routed.dcp")
    t = time.time()
    os.utime(lonely, (t - 3600, t - 3600))
    _, notes = find_checkpoint(read_bit_header(lonely))
    assert any("newer" in n for n in notes)


def test_bitstream_runs_on_virtual_board(vivado, toolchain, board, tmp_path):
    d = load_bitstream(BIT, vivado)
    assert d.top == "counter" and d.part.startswith("xc7a35tcpg236")
    assert d.pins["clk"] == "W5" and d.pins["led[0]"] == "U16" and d.pins["btnC"] == "U18"
    netlist = d.netlist.read_text()
    assert "FDRE" in netlist and "LUT" in netlist        # gate-level, not the RTL source
    mapping, notes = mapping_from_pins(d, board)
    assert not notes
    assert mapping.entries == {"btnC": "BTNC", "clk": "CLK100MHZ", "led[0]": "LED0", "led[1]": "LED1",
                               "led[2]": "LED2", "led[3]": "LED3"}
    res = compile_design(toolchain, [d.netlist], d.top, tmp_path / "c", extra_args=d.compile_args(),
                         extra_roots=d.extra_roots)
    assert res.success, [str(x) for x in res.errors]
    sim = BoardSimulator(toolchain, board, res, mapping, tmp_path / "s", EngineOptions(power_on_init=False))
    sim.program()
    try:
        value = lambda: sum((sim.led(i) or 0) << i for i in range(4))  # noqa: E731
        assert sim.leds()[:4] == [0, 0, 0, 0]                 # INIT values visible at cycle 0 (no X)
        for k in range(1, 19):
            sim.step(1)
            assert value() == k % 16
        sim.press("BTNC")
        sim.step(1)
        assert value() == 0
        sim.release("BTNC")
        sim.step(3)
        assert value() == 3
    finally:
        sim.stop()
    # Second load uses the cached export (no Vivado run).
    assert load_bitstream(BIT, vivado).cached
