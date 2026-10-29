"""Suggest a mapping from top-level port names (Basys 3 master-XDC naming conventions)."""
from __future__ import annotations

import re

from app.board.board import Board
from app.compiler.compiler import Port

from .mapping import Mapping, make_key

CLOCK_NAMES = {"clk", "clock", "clk100mhz", "clk_100mhz", "sysclk", "sys_clk", "clk_in", "i_clk", "clk_i"}
_BUTTON_ORDER = ["BTNC", "BTNU", "BTNL", "BTNR", "BTND"]   # order of btn[4:0] in common designs
_ALIASES = {
    "sw": "sw", "sws": "sw", "switch": "sw", "switches": "sw", "i_sw": "sw", "sw_i": "sw",
    "led": "led", "leds": "led", "o_led": "led", "led_o": "led",
    "seg": "seg", "segs": "seg", "segment": "seg", "segments": "seg", "a_to_g": "seg", "o_seg": "seg",
    "an": "an", "anode": "an", "anodes": "an", "o_an": "an",
    "dp": "dp", "o_dp": "dp",
}
_BTN_RE = re.compile(r"^(?:i_)?(?:btn|button|bt)_?([culrd])(?:_i)?$", re.I)
_BTN_LONG = {"center": "C", "centre": "C", "up": "U", "left": "L", "right": "R", "down": "D"}
_RESET_NAMES = {"rst", "reset", "rst_i", "i_rst", "reset_i", "areset"}


def _family(board: Board, base: str) -> dict[int, str]:
    """{bit index: resource id} for resources whose master-XDC name is base[...]"""
    out = {}
    for r in board.resources.values():
        m = re.match(rf"^{base}\[(\d+)\]$", r.xdc_name)
        if m:
            out[int(m.group(1))] = r.id
        elif r.xdc_name == base:
            out[-1] = r.id
    return out


def auto_map(ports: list[Port], board: Board) -> tuple[Mapping, list[str]]:
    """Return (mapping, notes) for ports whose names follow the master-XDC conventions."""
    m = Mapping()
    notes: list[str] = []
    used_inputs: set[str] = set()
    for p in ports:
        if p.direction == "inout":
            continue
        low = p.name.lower()
        if p.direction == "input" and low in CLOCK_NAMES and not p.is_vector:
            m.set(p.name, None, board.clock_resource)
            notes.append(f"{p.name} -> {board.clock_resource}")
            continue
        bm = _BTN_RE.match(low)
        letter = bm.group(1).upper() if bm else None
        if letter is None:
            for word, l in _BTN_LONG.items():
                if low in (f"btn_{word}", f"btn{word}", f"button_{word}"):
                    letter = l
        if p.direction == "input" and letter and not p.is_vector:
            rid = f"BTN{letter}"
            if rid in board.resources:
                m.set(p.name, None, rid)
                used_inputs.add(rid)
                notes.append(f"{p.name} -> {rid}")
            continue
        if p.direction == "input" and low in ("btn", "btns", "buttons") and p.is_vector:
            for k, idx in enumerate(p.indices()):
                if k < len(_BUTTON_ORDER):
                    m.set(p.name, idx, _BUTTON_ORDER[k])
                    used_inputs.add(_BUTTON_ORDER[k])
            notes.append(f"{p.name}[{p.width - 1}:0] -> buttons {', '.join(_BUTTON_ORDER[:p.width])}")
            continue
        base = _ALIASES.get(low)
        if base is None:
            continue
        fam = _family(board, base)
        want_dir = "in" if p.direction == "input" else "out"
        if not fam or board.resources[next(iter(fam.values()))].dir != want_dir:
            continue
        if not p.is_vector and -1 in fam:
            m.set(p.name, None, fam[-1])
            notes.append(f"{p.name} -> {fam[-1]}")
            continue
        count = 0
        for k, idx in enumerate(p.indices()):
            # Map by declared index when it fits (sw[15:0], seg[6:0]); else by position.
            rid = fam.get(idx) if p.indices() and min(p.indices()) >= 0 and max(p.indices()) in fam else fam.get(k)
            if rid:
                m.set(p.name, idx if p.is_vector else None, rid)
                count += 1
        if count:
            notes.append(f"{p.name}{p.range_text()} -> {count} x {base}")
    # Active-high reset ports default to the centre button when it is still free.
    for p in ports:
        if p.direction == "input" and not p.is_vector and p.name.lower() in _RESET_NAMES \
                and m.get(p.name, None) is None and "BTNC" not in used_inputs:
            m.set(p.name, None, "BTNC")
            used_inputs.add("BTNC")
            notes.append(f"{p.name} -> BTNC (reset)")
    return m, notes
