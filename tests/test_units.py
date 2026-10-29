"""Unit tests for compiler wrapper, module discovery, mapping, XDC, projects and VCD."""
from __future__ import annotations

import json

import pytest

from app.compiler.compiler import Port, compile_design, parse_diagnostics
from app.compiler.discovery import discover_modules, missing_modules, top_candidates
from app.mapping.auto import auto_map
from app.mapping.mapping import Mapping, resolve
from app.mapping.xdc import export_xdc, parse_xdc_text
from app.project.project import Project, ResetConfig
from app.waveforms.vcd import VcdReader, format_value

from .conftest import EXAMPLES, write


# ------------------------------------------------------------------ compiler
def test_compile_reports_ports_with_declared_ranges(toolchain, tmp_path):
    src = write(tmp_path / "p.v", """
        module p(input [0:3] a, input [8:1] b, input c, output reg [3:0] q, output y);
          always @(*) q = a; assign y = c & b[1]; endmodule""")
    r = compile_design(toolchain, [src], "p", tmp_path / "w")
    assert r.success
    ports = {p.name: p for p in r.ports}
    assert (ports["a"].msb, ports["a"].lsb, ports["a"].width) == (0, 3, 4)
    assert (ports["b"].msb, ports["b"].lsb) == (8, 1)
    assert ports["c"].width == 1 and not ports["c"].is_vector
    assert ports["q"].direction == "output"
    assert ports["a"].position(3) == 0 and ports["a"].position(0) == 3
    with pytest.raises(ValueError):
        ports["b"].position(0)


def test_compile_syntax_error_reports_file_and_line(toolchain, tmp_path):
    src = write(tmp_path / "bad.v", "module bad(input a, output b);\n assign b = a &;\nendmodule\n")
    r = compile_design(toolchain, [src], "bad", tmp_path / "w")
    assert not r.success
    assert any(d.line == 2 and d.severity == "error" for d in r.diagnostics)


def test_compile_missing_module_and_bad_top(toolchain, tmp_path):
    src = write(tmp_path / "m.v", "module m(input a, output b);\n foo u1(.x(a));\nendmodule\n")
    r = compile_design(toolchain, [src], "m", tmp_path / "w")
    assert not r.success
    assert any("foo" in d.message for d in r.errors)
    r = compile_design(toolchain, [src], "nosuch", tmp_path / "w")
    assert not r.success and r.errors


def test_compile_invalid_inputs(toolchain, tmp_path):
    assert not compile_design(toolchain, [], "x", tmp_path).success
    assert not compile_design(toolchain, [tmp_path / "missing.v"], "x", tmp_path).success
    src = write(tmp_path / "ok.v", "module ok(input a, output b); assign b = a; endmodule")
    r = compile_design(toolchain, [src], "", tmp_path)
    assert not r.success and "top-level" in r.errors[0].message


def test_parse_diagnostics_sorry_is_error():
    d = parse_diagnostics("x.v:3: sorry: constant selects in always_* are not supported\n")
    assert d[0].severity == "error" and d[0].line == 3


# ------------------------------------------------------------------ discovery
def test_discovery_finds_modules_and_top(tmp_path):
    a = write(tmp_path / "a.v", """
        // module commented_out(input x);
        module leaf #(parameter W = 2) (input [W-1:0] x, output y); assign y = ^x; endmodule
        /* module also_commented; endmodule */
        module top(input [1:0] s, output o);
          leaf #(.W(2)) u_leaf (.x(s), .y(o));
          missing_mod u_missing ();
        endmodule""")
    mods = discover_modules([a])
    assert set(mods) == {"leaf", "top"}
    assert top_candidates(mods) == ["top"]
    assert missing_modules(mods) == {"missing_mod": {"top"}}


def test_discovery_examples_tops():
    for folder, top in [("fsm", "fsm_lock"), ("multiplexer", "mux_top"), ("seven_segment", "seven_segment")]:
        mods = discover_modules(sorted((EXAMPLES / folder).glob("*.v")))
        assert top_candidates(mods) == [top]


# ------------------------------------------------------------------ mapping
PORTS = [Port("clk", "input", 1, 0, 0), Port("sw", "input", 16, 15, 0), Port("btnC", "input", 1, 0, 0),
         Port("led", "output", 16, 15, 0), Port("seg", "output", 7, 6, 0), Port("an", "output", 4, 3, 0),
         Port("dp", "output", 1, 0, 0)]


def test_auto_map_master_xdc_names(board):
    m, _ = auto_map(PORTS, board)
    assert m.get("clk", None) == "CLK100MHZ"
    assert m.get("sw", 7) == "SW7"
    assert m.get("led", 15) == "LED15"
    assert m.get("seg", 0) == "SEG_A" and m.get("seg", 6) == "SEG_G"
    assert m.get("an", 3) == "AN3" and m.get("dp", None) == "SEG_DP"
    assert m.get("btnC", None) == "BTNC"
    rm = resolve(m, PORTS, board)
    assert rm.ok and not rm.warnings


def test_mapping_validation_messages(board):
    m = Mapping({"sw[0]": "LED0",            # invalid direction
                 "led[0]": "LED3", "led[1]": "LED3",   # duplicate output
                 "led[2]": "SW2",            # output to input resource
                 "sw[99]": "SW1",            # out of range
                 "nosuch": "SW0",            # unknown port
                 "sw[1]": "BOGUS"})          # unknown resource
    rm = resolve(m, PORTS, board)
    text = " | ".join(i.message for i in rm.issues)
    assert "invalid direction" in text
    assert "duplicate output assignment" in text
    assert "outside" in text
    assert "no port named 'nosuch'" in text
    assert "unknown board resource" in text
    assert "missing input mapping" in text
    assert "not mapped to CLK100MHZ" in text
    assert not rm.ok


def test_mapping_constants_and_bus(board):
    m = Mapping({"sw[0]": "CONST1", "sw[1]": "SW5", "led[0]": "CONST0"})
    rm = resolve(m, PORTS, board)
    assert any("cannot be tied" in i.message for i in rm.errors)
    assert rm.inputs["SW5"][0].pos == 1


def test_mapping_save_load(tmp_path, board):
    m, _ = auto_map(PORTS, board)
    m.save(tmp_path / "map.json")
    m2 = Mapping.load(tmp_path / "map.json")
    assert m2.entries == m.entries


# ------------------------------------------------------------------ XDC
MASTER_STYLE = """
## Clock signal
set_property -dict { PACKAGE_PIN W5   IOSTANDARD LVCMOS33 } [get_ports clk]
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports clk]
## Switches
set_property PACKAGE_PIN V17 [get_ports {sw[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {sw[0]}]
#set_property -dict { PACKAGE_PIN V16   IOSTANDARD LVCMOS33 } [get_ports {sw[1]}]
set_property -dict { PACKAGE_PIN U16   IOSTANDARD LVCMOS33 } [get_ports {led[0]}]
set_property -dict { PACKAGE_PIN J1   IOSTANDARD LVCMOS33 } [get_ports {led[1]}]; # Pmod JA
set_property -dict { PACKAGE_PIN Z99   IOSTANDARD LVCMOS33 } [get_ports {led[2]}]
set_property -dict { PACKAGE_PIN U18   IOSTANDARD LVCMOS33 } [get_ports btnC]
"""


def test_xdc_parse(board):
    r = parse_xdc_text(MASTER_STYLE, board, PORTS)
    e = r.mapping.entries
    assert e["clk"] == "CLK100MHZ" and e["sw[0]"] == "SW0" and e["led[0]"] == "LED0" and e["btnC"] == "BTNC"
    assert "sw[1]" not in e                      # commented out
    msgs = " | ".join(i.message for i in r.issues)
    assert "Pmod JA" in msgs                     # unsupported but known pin
    assert any(i.severity == "error" and "Z99" in i.message for i in r.issues)


def test_xdc_roundtrip(board):
    m, _ = auto_map(PORTS, board)
    text = export_xdc(m, board, PORTS)
    assert "create_clock" in text
    assert parse_xdc_text(text, board, PORTS).mapping.entries == m.entries


def test_basys3_pins_match_master_xdc(board):
    # Spot checks against Digilent Basys-3-Master.xdc.
    expect = {"CLK100MHZ": "W5", "SW0": "V17", "SW15": "R2", "LED0": "U16", "LED15": "L1",
              "BTNC": "U18", "BTNU": "T18", "BTNL": "W19", "BTNR": "T17", "BTND": "U17",
              "SEG_A": "W7", "SEG_G": "U7", "SEG_DP": "V7", "AN0": "U2", "AN3": "W4"}
    for rid, pin in expect.items():
        assert board.resources[rid].pin == pin


# ------------------------------------------------------------------ project
def test_project_save_and_reopen(tmp_path, board):
    src = write(tmp_path / "proj" / "rtl" / "a.v", "module a(input [15:0] sw, output [15:0] led); endmodule")
    m, _ = auto_map(PORTS, board)
    p = Project(name="Demo", sources=[src], top="a", mapping=m, speed="fast",
                reset=ResetConfig("rst", False, 8), power_on_init=False)
    path = p.save(tmp_path / "proj" / "demo")
    assert path.suffix == ".fvlproj"
    data = json.loads(path.read_text())
    assert data["sources"] == ["rtl/a.v"]          # stored relative
    q = Project.load(path)
    assert q.name == "Demo" and q.top == "a" and q.speed == "fast"
    assert q.sources == [src.resolve()]
    assert q.mapping.entries == m.entries
    assert q.reset == ResetConfig("rst", False, 8) and q.power_on_init is False
    assert not q.missing_sources()


def test_project_invalid_file(tmp_path):
    bad = write(tmp_path / "x.fvlproj", "{not json")
    with pytest.raises(ValueError):
        Project.load(bad)


def test_all_example_projects_load():
    for f in EXAMPLES.glob("*/*.fvlproj"):
        p = Project.load(f)
        assert p.sources and not p.missing_sources() and p.top and p.mapping.entries


# ------------------------------------------------------------------ VCD
def test_vcd_reader_incremental(tmp_path):
    path = tmp_path / "w.vcd"
    path.write_text("$timescale 1ps $end\n$scope module top $end\n$var wire 1 ! clk $end\n"
                    "$var wire 4 \" q [3:0] $end\n$upscope $end\n$enddefinitions $end\n"
                    "#0\n$dumpvars\n0!\nb0 \"\n$end\n#5000\n1!\nb11 \"\n")
    r = VcdReader(path)
    assert r.poll()
    clk, q = r.by_name["top.clk"], r.by_name["top.q"]
    assert q.width == 4 and clk.value_at(5_000_000) == "1" and q.value_at(5_000_000) == "0011"
    with open(path, "a") as f:
        f.write("#10000\n$dumpoff\nx!\nbx \"\n$end\n#20000\n$dumpon\n0!\nb101 \"\n$end\n")
    assert r.poll()
    assert clk.value_at(15_000_000) is None                  # recording was off
    assert q.value_at(20_000_000) == "0101"
    assert format_value("0101", 4) == "5" and format_value("x1", 8) == "x1"
    assert format_value("11111111", 8, "sdec") == "-1"


def test_waveform_recording_from_simulation(open_example):
    sim = open_example("counter", record=True)
    sim.step(6)
    r = VcdReader(sim.session.vcd_path)
    r.poll()
    names = [s.name for s in r.all_signals()]
    assert "fvl_tb.fvl_dut.count" in names and "fvl_tb.fvl_dut.clk" in names
    cnt = r.by_name["fvl_tb.fvl_dut.count"]
    assert int(cnt.values[-1], 2) == 6
    # Rising edges every 10 ns (10_000_000 fs).
    clk = r.by_name["fvl_tb.fvl_dut.clk"]
    rises = [t for t, v in zip(clk.times, clk.values) if v == "1"]
    assert all(b - a == 10_000_000 for a, b in zip(rises, rises[1:]))
    sim.set_recording(False)
    sim.step(4)
    sim.set_recording(True)
    sim.step(1)
    r.poll()
    assert int(cnt.values[-1], 2) == 11
    assert None in cnt.values                               # gap while recording was off
