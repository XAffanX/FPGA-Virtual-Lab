"""Back-annotate Vivado's SDF delays into its timing-simulation netlist as plain Verilog delays.

Icarus Verilog cannot use Vivado's SDF directly (its SDF reader rejects Vivado's SDF 3.0 files, and
the UNISIM timing models rely on timing-check delayed signals Icarus does not implement). So the
delays are written into the netlist instead, and the netlist is simulated with the normal UNISIM
models:

* combinational cells (LUT, CARRY4, IBUF, OBUF, BUFG ...): every input pin gets
  ``wire + assign #(d)`` = interconnect delay to that pin + the slowest IOPATH from it;
* sequential cells (FDRE/FDSE/FDCE/FDPE, latches, RAMs, SRLs): clock-to-output delays go on the
  outputs, and every input pin gets its interconnect delay (so clock skew and late data are real).

Delays use the SDF "max" corner (slow silicon), like Vivado's setup analysis. Continuous-assign
delays are inertial: pulses shorter than a path are filtered, roughly like real gates. Setup/hold
*checks* are not reported - a too-slow path simply captures stale data, as on the board.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SEQUENTIAL = re.compile(r"^(FD[A-Z]*|LD[A-Z]*|RAM.*|SRL.*|DSP.*|FIFO.*|BUFGCE.*|IDDR|ODDR|.*_DDR)$")
CLOCKISH = {"C", "CLK", "CLKARDCLK", "CLKBWRCLK", "WCLK", "G", "GE"}


@dataclass
class CellTiming:
    celltype: str
    iopaths: list[tuple[str, str, float]] = field(default_factory=list)   # (in pin, out pin, ps)


def _max_triple(tok: str) -> float:
    """'(1371.1:1441.3:1441.3)' -> 1441.3 (max corner); '()' -> 0."""
    vals = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", tok)]
    return vals[-1] if vals else 0.0


def _unescape(name: str) -> str:
    return name.replace("\\", "")


def parse_sdf(text: str):
    """Return ({instance path: CellTiming}, [(src, dst, ps)]) from a Vivado SDF."""
    ts = re.search(r"\(TIMESCALE\s+([\d.]+)\s*(ps|ns)\s*\)", text)
    scale = 1.0 if not ts else float(ts.group(1)) * (1000.0 if ts.group(2) == "ns" else 1.0)
    cells: dict[str, CellTiming] = {}
    inter: list[tuple[str, str, float]] = []
    for block in re.split(r"\(CELL\b", text)[1:]:
        ctype = re.search(r'\(CELLTYPE\s+"([^"]+)"\)', block)
        inst = re.search(r"\(INSTANCE\s*([^)]*)\)", block)
        if not ctype:
            continue
        path = _unescape(inst.group(1).strip()) if inst else ""
        ct = cells.setdefault(path, CellTiming(ctype.group(1)))
        for m in re.finditer(r"\(IOPATH\s+(\((?:posedge|negedge)\s+[^)]+\)|\S+)\s+(\S+)((?:\s*\([^()]*\))+)", block):
            src = re.sub(r"^\((?:posedge|negedge)\s+(.*)\)$", r"\1", m.group(1))
            delays = re.findall(r"\([^()]*\)", m.group(3))
            d = max((_max_triple(t) for t in delays), default=0.0) * scale
            ct.iopaths.append((_unescape(src), _unescape(m.group(2)), d))
        for m in re.finditer(r"\(INTERCONNECT\s+(\S+)\s+(\S+)((?:\s*\([^()]*\))+)", block):
            delays = re.findall(r"\([^()]*\)", m.group(3))
            d = max((_max_triple(t) for t in delays), default=0.0) * scale
            inter.append((_unescape(m.group(1)), _unescape(m.group(2)), d))
    return cells, inter


# ---------------------------------------------------------------- Verilog netlist editing
_ID = r"(?:\\\S+\s|[A-Za-z_][\w$]*)"
_KEYWORDS = {"module", "endmodule", "input", "output", "inout", "wire", "reg", "assign", "initial", "begin", "end",
             "parameter", "localparam", "always", "supply0", "supply1", "tri", "integer", "genvar", "generate",
             "endgenerate", "defparam", "specify", "endspecify", "function", "endfunction", "task", "endtask"}


def _norm(ident: str) -> str:
    ident = ident.strip()
    return ident[1:] if ident.startswith("\\") else ident


def _skip_balanced(s: str, i: int) -> int:
    """s[i] == '(' -> index just after the matching ')'."""
    depth = 0
    while i < len(s):
        c = s[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        elif c == '"':
            i = s.index('"', i + 1)
        i += 1
    raise ValueError("unbalanced parentheses in netlist")


def _split_modules(text: str):
    """Yield (start, end, name) of every module in the netlist."""
    for m in re.finditer(r"^module\s+(" + _ID + r")", text, re.M):
        end = text.index("endmodule", m.end())
        yield m.start(), end + len("endmodule"), _norm(m.group(1))


def _statements(body: str):
    """Yield (start, end) spans of ';'-terminated statements (ignores ';' inside parentheses/strings)."""
    depth, start, i = 0, 0, 0
    while i < len(body):
        c = body[i]
        if c == '"':
            i = body.index('"', i + 1)
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == ";" and depth == 0:
            yield start, i + 1
            start = i + 1
        i += 1


_INST = re.compile(r"\s*(?:\(\*.*?\*\)\s*)*(" + _ID + r")\s*", re.S)


def _parse_instance(stmt: str):
    """(celltype, instname, conn_open_index) for an instance statement, else None."""
    m = _INST.match(stmt)
    if not m:
        return None
    ctype = _norm(m.group(1))
    if ctype in _KEYWORDS:
        return None
    i = m.end()
    if stmt[i:i + 1] == "#":
        i = stmt.index("(", i)
        i = _skip_balanced(stmt, i)
    m2 = re.compile(r"\s*(" + _ID + r")\s*").match(stmt, i)
    if not m2:
        return None
    rest = m2.end()
    if stmt[rest:rest + 1] != "(":
        return None
    return ctype, _norm(m2.group(1)), rest


def _connections(stmt: str, open_idx: int):
    """[(pin, expr_start, expr_end)] for '.PIN(expr)' connections."""
    out = []
    end = _skip_balanced(stmt, open_idx)
    for m in re.finditer(r"\.(" + _ID + r")\s*\(", stmt[open_idx:end]):
        s0 = open_idx + m.end() - 1
        s1 = _skip_balanced(stmt, s0)
        out.append((_norm(m.group(1)), s0 + 1, s1 - 1))
    return out


@dataclass
class AnnotationReport:
    cells: int = 0
    pins_delayed: int = 0
    max_path_ps: float = 0.0
    unmatched_cells: list[str] = field(default_factory=list)


def annotate(netlist: str, sdf: str, top: str) -> tuple[str, AnnotationReport]:
    # Vivado's timesim netlist turns on the UNISIM timing-check models (`define XIL_TIMING), which
    # Icarus cannot run; our delays replace them. Its $sdf_annotate call (and the initial block that
    # holds it) is removed for the same reason.
    netlist = re.sub(r"^\s*`define\s+XIL_TIMING\s*$", "// (XIL_TIMING removed by FPGA Virtual Lab)", netlist,
                     flags=re.M)
    netlist = re.sub(r"^\s*\$sdf_annotate\(.*?\);\s*$", "", netlist, flags=re.M)
    netlist = re.sub(r"initial\s+begin\s+end", "", netlist)
    cells, inter = parse_sdf(sdf)
    rep = AnnotationReport()
    pin_ic: dict[tuple[str, str], float] = defaultdict(float)    # (instance path, pin) -> interconnect ps
    for src, dst, d in inter:
        if "/" in dst:
            inst, pin = dst.rsplit("/", 1)
            pin_ic[(inst, pin)] = max(pin_ic[(inst, pin)], d)
    modules = {name: (s, e) for s, e, name in _split_modules(netlist)}
    if top not in modules:
        raise ValueError(f"top module {top} not found in the timing netlist")

    # Instance tree: module -> [(instance name, celltype)] for every instance statement.
    insts: dict[str, list[tuple[str, str]]] = {}
    for name, (s, e) in modules.items():
        body = netlist[s:e]
        lst = []
        for a, b in _statements(body):
            p = _parse_instance(body[a:b])
            if p:
                lst.append((p[1], p[0]))
        insts[name] = lst
    # paths of every module instance (hierarchical netlists: submodule per instance)
    paths: dict[str, list[str]] = defaultdict(list)
    stack = [(top, "")]
    while stack:
        mod, prefix = stack.pop()
        paths[mod].append(prefix)
        for iname, ctype in insts.get(mod, []):
            if ctype in modules:
                stack.append((ctype, f"{prefix}{iname}/"))

    edits: list[tuple[int, int, str]] = []          # (start, end, replacement) in netlist coordinates
    decls: dict[str, list[str]] = defaultdict(list)  # module -> extra declarations
    uid = 0
    for mod, prefixes in paths.items():
        s, e = modules[mod]
        body = netlist[s:e]
        prefix = prefixes[0]                       # uniquified by Vivado; first path wins otherwise
        for a, b in _statements(body):
            stmt = body[a:b]
            p = _parse_instance(stmt)
            if not p or p[0] in modules:
                continue
            ctype, iname, open_idx = p
            path = prefix + iname
            ct = cells.get(path)
            if ct is None:
                continue
            rep.cells += 1
            seq = bool(SEQUENTIAL.match(ctype))
            out_delay: dict[str, float] = defaultdict(float)
            in_delay: dict[str, float] = defaultdict(float)
            for src, dst, d in ct.iopaths:
                if seq or src in CLOCKISH:
                    out_delay[dst] = max(out_delay[dst], d)
                else:
                    in_delay[src] = max(in_delay[src], d)
            for (ipath, pin), d in pin_ic.items():
                if ipath == path:
                    in_delay[pin] += d
            for pin, x0, x1 in _connections(stmt, open_idx):
                expr = stmt[x0:x1].strip()
                if not expr:
                    continue
                # an escaped identifier (\a_reg[0]_i_1_n_7 ) must be followed by whitespace
                expr += " "
                if pin in out_delay and out_delay[pin] > 0:
                    uid += 1
                    w = f"fvl_sdf_o{uid}"
                    decls[mod].append(f"  wire {w};\n  assign #({out_delay[pin]:.1f}) {expr} = {w};")
                    edits.append((s + a + x0, s + a + x1, w))
                    rep.pins_delayed += 1
                    rep.max_path_ps = max(rep.max_path_ps, out_delay[pin])
                elif pin in in_delay and in_delay[pin] > 0:
                    uid += 1
                    w = f"fvl_sdf_i{uid}"
                    decls[mod].append(f"  wire {w};\n  assign #({in_delay[pin]:.1f}) {w} = {expr};")
                    edits.append((s + a + x0, s + a + x1, w))
                    rep.pins_delayed += 1
                    rep.max_path_ps = max(rep.max_path_ps, in_delay[pin])
    for path in cells:
        mod_known = any(path.startswith(pre) for pres in paths.values() for pre in pres)
        if not mod_known:
            rep.unmatched_cells.append(path)

    out = netlist
    # apply connection edits back to front, then insert declarations before each endmodule
    for x0, x1, rep_text in sorted(edits, reverse=True):
        out = out[:x0] + rep_text + out[x1:]
    for mod in sorted(decls, key=lambda m: -modules[m][0]):
        for s, e, name in _split_modules(out):
            if name == mod:
                pos = e - len("endmodule")
                out = out[:pos] + "\n  // ---- SDF delays (FPGA Virtual Lab)\n" + "\n".join(decls[mod]) + "\n" + out[pos:]
                break
    return out, rep


def annotate_files(netlist: Path, sdf: Path, top: str, out: Path) -> AnnotationReport:
    text, rep = annotate(Path(netlist).read_text(encoding="utf-8", errors="replace"),
                         Path(sdf).read_text(encoding="utf-8", errors="replace"), top)
    Path(out).write_text(text, encoding="utf-8")
    return rep
