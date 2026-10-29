"""(Re)generate the .xdc constraint and .fvlproj project file of every built-in example.

Ports come from compiling each example with Icarus Verilog; pins come from the
Basys 3 board definition (Digilent master XDC). Run from the repository root:

    python scripts/make_examples.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.board.board import load_board  # noqa: E402
from app.compiler.compiler import compile_design  # noqa: E402
from app.mapping.auto import auto_map  # noqa: E402
from app.mapping.mapping import resolve  # noqa: E402
from app.mapping.xdc import export_xdc  # noqa: E402
from app.project.project import Project, ResetConfig  # noqa: E402
from app.utils.toolchain import find_toolchain  # noqa: E402

EXAMPLES = [
    ("switch_led", "Project A - Switch to LED", ["switch_led.v"], "switch_led", "normal",
     "Each switch drives the LED above it."),
    ("counter", "Project B - 4-bit counter", ["counter.v"], "counter", "slow",
     "Counts clock cycles on LED3..LED0. BTNC resets. Use Step Clock or Slow speed."),
    ("adder_subtractor", "Project C - 2-bit adder/subtractor", ["full_adder.v", "adder_subtractor.v"],
     "adder_subtractor", "normal", "A = SW1..0, B = SW3..2, SW15 = subtract. Result on LED1..0, carry LED2, overflow LED3."),
    ("multiplexer", "Project D - 4-to-1 multiplexer", ["mux4.v", "mux_top.v"], "mux_top", "normal",
     "Four 3-bit inputs on SW11..SW0, select on SW15..SW14, output on LED2..LED0."),
    ("seven_segment", "Project E - seven-segment display", ["hex_to_7seg.v", "seven_segment.v"],
     "seven_segment", "fast", "Switches shown as four multiplexed hex digits. BTNC lights a decimal point."),
    ("fsm", "Project F - FSM combination lock", ["button_sync.v", "hex_to_7seg.v", "fsm_lock.v"],
     "fsm_lock", "normal", "Press BTNU, BTNL, BTNR, BTND in order to unlock (LED15). BTNC resets."),
]


def main() -> int:
    tc = find_toolchain()
    board = load_board("basys3")
    ok = True
    for folder, title, files, top, speed, desc in EXAMPLES:
        d = ROOT / "examples" / folder
        srcs = [d / f for f in files]
        with tempfile.TemporaryDirectory() as tmp:
            res = compile_design(tc, srcs, top, Path(tmp))
        if not res.success:
            print(f"[FAIL] {folder}: " + "; ".join(map(str, res.errors)))
            ok = False
            continue
        mapping, _ = auto_map(res.ports, board)
        rm = resolve(mapping, res.ports, board)
        xdc = d / f"{top}.xdc"
        xdc.write_text(export_xdc(mapping, board, res.ports), encoding="utf-8")
        proj = Project(name=title, sources=srcs, top=top, mapping=mapping, xdc=xdc, speed=speed,
                       description=desc, reset=ResetConfig())
        proj.save(d / f"{folder}.fvlproj")
        issues = [str(i) for i in rm.issues if i.severity != "info"]
        print(f"[ OK ] {folder}: {len(mapping.entries)} mapped bits" + (f"  {issues}" if issues else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
