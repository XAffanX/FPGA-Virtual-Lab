"""Signal-to-board-resource mapping, validation and resolution."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.board.board import Board
from app.compiler.compiler import Port

CONST0 = "CONST0"   # tie an input bit to logic 0
CONST1 = "CONST1"   # tie an input bit to logic 1
CONSTANTS = (CONST0, CONST1)

_KEY_RE = re.compile(r"^\s*([A-Za-z_\\][^\[\]\s]*)\s*(?:\[\s*(-?\d+)\s*\])?\s*$")


def make_key(port: str, index: int | None) -> str:
    return f"{port}[{index}]" if index is not None else port


def parse_key(key: str) -> tuple[str, int | None]:
    m = _KEY_RE.match(key)
    if not m:
        raise ValueError(f"invalid signal reference '{key}' (expected name or name[index])")
    return m.group(1), (int(m.group(2)) if m.group(2) is not None else None)


@dataclass
class Mapping:
    """Maps top-level port bits ("sw[3]", "clk") to board resource ids ("SW3", "CLK100MHZ")."""
    entries: dict[str, str] = field(default_factory=dict)

    def set(self, port: str, index: int | None, target: str | None) -> None:
        key = make_key(port, index)
        if target:
            self.entries[key] = target
        else:
            self.entries.pop(key, None)

    def get(self, port: str, index: int | None) -> str | None:
        return self.entries.get(make_key(port, index))

    def clear(self) -> None:
        self.entries.clear()

    def copy(self) -> "Mapping":
        return Mapping(dict(self.entries))

    def to_dict(self) -> dict[str, str]:
        return dict(sorted(self.entries.items(), key=lambda kv: _sort_key(kv[0])))

    @classmethod
    def from_dict(cls, d: dict[str, str] | None) -> "Mapping":
        return cls({str(k): str(v) for k, v in (d or {}).items()})

    def save(self, path: Path, board_id: str = "basys3") -> None:
        Path(path).write_text(json.dumps({"format": "fvl-mapping-1", "board": board_id,
                                          "mapping": self.to_dict()}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Mapping":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data.get("mapping", data))


def _sort_key(key: str):
    try:
        name, idx = parse_key(key)
    except ValueError:
        return (key, 0)
    return (name, idx if idx is not None else -1)


def normalize_index(port: Port, index: int | None) -> int | None:
    """Scalar ports are always addressed without an index."""
    return index if port.is_vector else None


@dataclass
class Issue:
    severity: str  # error | warning | info
    message: str
    key: str = ""

    def __str__(self) -> str:
        return f"{self.severity.upper()}: {self.message}"


@dataclass
class BitRef:
    port: int   # index into ResolvedMapping.ports
    pos: int    # bit position in the packed vector


@dataclass
class ResolvedMapping:
    """Mapping bound to concrete ports and board: the form used by the simulator."""
    ports: list[Port]
    inputs: dict[str, list[BitRef]] = field(default_factory=dict)    # input resource -> port bits
    clock_bits: list[BitRef] = field(default_factory=list)
    constants: list[tuple[BitRef, int]] = field(default_factory=list)
    outputs: dict[str, BitRef] = field(default_factory=dict)         # output resource -> port bit
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def input_ports(self) -> list[int]:
        return [i for i, p in enumerate(self.ports) if p.direction == "input"]

    def output_ports(self) -> list[int]:
        return [i for i, p in enumerate(self.ports) if p.direction == "output"]


def resolve(mapping: Mapping, ports: list[Port], board: Board) -> ResolvedMapping:
    """Validate a mapping against the compiled ports and the board definition."""
    rm = ResolvedMapping(list(ports))
    by_name = {p.name: i for i, p in enumerate(ports)}
    mapped: set[tuple[int, int]] = set()
    out_owner: dict[str, str] = {}
    in_owner: dict[str, list[str]] = {}

    for key, target in mapping.to_dict().items():
        try:
            name, index = parse_key(key)
        except ValueError as exc:
            rm.issues.append(Issue("error", str(exc), key))
            continue
        if name not in by_name:
            rm.issues.append(Issue("warning", f"'{key}' is mapped to {target} but the top module has "
                                   f"no port named '{name}' (mapping ignored)", key))
            continue
        pi = by_name[name]
        port = ports[pi]
        if not port.is_vector and index is not None and index != port.lsb:
            rm.issues.append(Issue("error", f"'{key}': port '{name}' is a scalar", key))
            continue
        try:
            pos = port.position(index if port.is_vector else None)
        except ValueError as exc:
            rm.issues.append(Issue("error", str(exc), key))
            continue
        ref = BitRef(pi, pos)
        if port.direction == "inout":
            rm.issues.append(Issue("warning", f"'{key}': inout ports are not simulated in V1 "
                                   "(mapping ignored)", key))
            continue
        if target in CONSTANTS:
            if port.direction != "input":
                rm.issues.append(Issue("error", f"'{key}' is an output and cannot be tied to a constant", key))
                continue
            rm.constants.append((ref, 1 if target == CONST1 else 0))
            mapped.add((pi, pos))
            continue
        res = board.resources.get(target)
        if res is None:
            rm.issues.append(Issue("error", f"'{key}' is mapped to unknown board resource '{target}'", key))
            continue
        if port.direction == "input" and not res.is_input:
            rm.issues.append(Issue("error", f"invalid direction: input '{key}' is mapped to {res.id}, "
                                   f"which is an output ({res.kind}) of the FPGA", key))
            continue
        if port.direction == "output" and res.is_input:
            rm.issues.append(Issue("error", f"invalid direction: output '{key}' is mapped to {res.id}, "
                                   f"which is an input ({res.kind}) of the FPGA", key))
            continue
        mapped.add((pi, pos))
        if port.direction == "input":
            in_owner.setdefault(res.id, []).append(key)
            if res.id == board.clock_resource:
                rm.clock_bits.append(ref)
            else:
                rm.inputs.setdefault(res.id, []).append(ref)
        else:
            if res.id in out_owner:
                rm.issues.append(Issue("error", f"duplicate output assignment: {res.id} is driven by both "
                                       f"'{out_owner[res.id]}' and '{key}'", key))
                continue
            out_owner[res.id] = key
            rm.outputs[res.id] = ref

    for rid, keys in in_owner.items():
        if len(keys) > 1 and rid != board.clock_resource:
            rm.issues.append(Issue("info", f"{rid} drives several inputs: {', '.join(keys)}"))

    for pi, port in enumerate(ports):
        missing = [idx for idx in port.indices() if (pi, port.position(idx if port.is_vector else None)) not in mapped]
        if not missing or port.direction == "inout":
            if port.direction == "inout":
                rm.issues.append(Issue("warning", f"inout port '{port.name}' is left unconnected"))
            continue
        label = port.name + port.range_text()
        if len(missing) == port.width:
            what = f"port '{label}'"
        else:
            what = f"{len(missing)} bit(s) of '{label}' ({_compact(port, missing)})"
        if port.direction == "input":
            rm.issues.append(Issue("warning", f"missing input mapping: {what} is unmapped and held at 0"))
        else:
            rm.issues.append(Issue("info", f"unmapped output: {what} is not shown on the board "
                                   "(visible in the I/O monitor and waveforms)"))
    if not rm.clock_bits and any(p.direction == "input" and p.name.lower() in
                                 ("clk", "clock", "clk100mhz", "sysclk", "sys_clk", "clk_100mhz")
                                 for p in ports):
        rm.issues.append(Issue("warning", "the design has a clock-like input that is not mapped to "
                               f"{board.clock_resource}; sequential logic will not advance"))
    return rm


def _compact(port: Port, idxs: list[int]) -> str:
    idxs = sorted(idxs)
    parts, start, prev = [], idxs[0], idxs[0]
    for i in idxs[1:] + [None]:
        if i is not None and i == prev + 1:
            prev = i
            continue
        parts.append(f"{port.name}[{start}]" if start == prev else f"{port.name}[{prev}:{start}]")
        if i is not None:
            start = prev = i
    return ", ".join(parts)
