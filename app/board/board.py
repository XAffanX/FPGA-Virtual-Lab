"""Board definition model (loaded from configurations/<board>.json)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.paths import config_dir


@dataclass(frozen=True)
class Resource:
    """One physical board signal: a switch, LED, segment, button, clock..."""
    id: str
    kind: str          # clock | switch | button | led | segment | anode
    dir: str           # "in" = drives an FPGA input, "out" = driven by an FPGA output
    pin: str           # FPGA package pin (from the master XDC)
    xdc_name: str      # port name used in the official master XDC
    label: str
    active_low: bool = False   # input that reads 1 while released (e.g. Nexys 4 DDR CPU_RESETN)

    @property
    def is_input(self) -> bool:
        """Can drive an FPGA input ("io" = bidirectional header pin such as a Pmod)."""
        return self.dir in ("in", "io")

    @property
    def is_output(self) -> bool:
        return self.dir in ("out", "io")


@dataclass
class SevenSegmentDef:
    digits: int
    segments: list[str]
    dp: str
    anodes: list[str]
    segments_active_low: bool
    anodes_active_low: bool


@dataclass
class Board:
    id: str
    name: str
    fpga: str
    clock_resource: str
    clock_period_ns: float
    seven_segment: SevenSegmentDef
    resources: dict[str, Resource]
    unsupported_pins: dict[str, str] = field(default_factory=dict)
    virtual_name: str = ""     # e.g. "virtual_basys3"
    part: str = ""             # FPGA part without speed grade, e.g. "xc7a35tcpg236"
    idcode: int = 0            # JTAG IDCODE of the FPGA

    @property
    def fpga_short(self) -> str:
        """e.g. 'XC7A35T' from the part name."""
        import re
        m = re.match(r"(xc7[a-z]\d+t)", self.part)
        return m.group(1).upper() if m else self.part.upper()

    # -- lookups -------------------------------------------------------
    def by_pin(self, pin: str) -> Resource | None:
        pin = pin.upper()
        for r in self.resources.values():
            if r.pin.upper() == pin:
                return r
        return None

    def by_xdc_name(self, name: str) -> Resource | None:
        for r in self.resources.values():
            if r.xdc_name == name:
                return r
        return None

    def of_kind(self, *kinds: str) -> list[Resource]:
        return [r for r in self.resources.values() if r.kind in kinds]

    def inputs(self) -> list[Resource]:
        return [r for r in self.resources.values() if r.is_input]

    def outputs(self) -> list[Resource]:
        return [r for r in self.resources.values() if r.is_output]


def load_board(board_id: str = "basys3", path: Path | None = None) -> Board:
    path = path or (config_dir() / f"{board_id}.json")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    res = {r["id"]: Resource(**r) for r in data["resources"]}
    ss = data["seven_segment"]
    return Board(
        id=data["id"], name=data["name"], fpga=data.get("fpga", ""),
        clock_resource=data["clock"]["resource"], clock_period_ns=float(data["clock"]["period_ns"]),
        seven_segment=SevenSegmentDef(ss["digits"], ss["segments"], ss["dp"], ss["anodes"],
                                      ss["segments_active_low"], ss["anodes_active_low"]),
        resources=res, unsupported_pins=data.get("unsupported_pins", {}),
        virtual_name=data.get("virtual_name", f"virtual_{data['id']}"), part=data.get("part", ""),
        idcode=int(str(data.get("idcode", "0")), 0))


BOARD_IDS = ("basys3", "nexys4ddr")
_CACHE: dict[str, Board] = {}


def get_board(board_id: str) -> Board:
    if board_id not in _CACHE:
        _CACHE[board_id] = load_board(board_id)
    return _CACHE[board_id]


def available_boards() -> list[Board]:
    return [get_board(b) for b in BOARD_IDS if (config_dir() / f"{b}.json").is_file()]


def board_for_part(part: str) -> Board | None:
    """The supported board whose FPGA matches a bitstream part (e.g. 'xc7a100tcsg324')."""
    part = part.lower()
    part = part if part.startswith("xc") else "xc" + part
    return next((b for b in available_boards() if b.part and part.startswith(b.part)), None)
