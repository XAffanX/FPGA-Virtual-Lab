"""Generate assets/boards/<board>.svg - the static artwork of each virtual board.

Interactive parts (switch handles, LEDs, segments, buttons, power/DONE LEDs) are drawn live by the
board view on top of this image, using the same coordinates (app/board/layout.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.board.layout import BoardLayout, Rect, get_layout  # noqa: E402

SILK = "#e9efe9"
COPPER = "#b9975b"
BLACK = "#16181a"


def rect(r: Rect, fill, stroke="none", sw=0, rx=0):
    return (f'<rect x="{r.x:.1f}" y="{r.y:.1f}" width="{r.w:.1f}" height="{r.h:.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def text(x, y, s, size=11, anchor="middle", fill=SILK, weight="normal", rotate=None):
    tr = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate is not None else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, Helvetica, sans-serif" font-size="{size}" '
            f'fill="{fill}" text-anchor="{anchor}" font-weight="{weight}"{tr}>{s}</text>')


def pmod(name: str, r: Rect, left: bool):
    out = [rect(r, BLACK, "#000", 1, 3)]
    for row in range(2):
        for c in range(6):
            cx = r.x + (12 if left else r.w - 12) + (row * (14 if left else -14))
            cy = r.y + 12 + c * 19.2
            out.append(f'<rect x="{cx - 4:.1f}" y="{cy - 4:.1f}" width="8" height="8" fill="{COPPER}"/>')
    lx = r.x + r.w + 16 if left else r.x - 16
    out.append(text(lx, r.cy, name, 13, weight="bold", rotate=-90))
    return out


def connector(kind: str, label: str, r: Rect, board_h: float):
    s = []
    below = r.y + r.h + 15
    if kind == "vga":
        s.append(rect(r, "#2a3d8f", "#141e48", 2, 4))
        if r.w > r.h:
            s.append(f'<path d="M {r.x + 22} {r.y + 14} L {r.x + r.w - 22} {r.y + 14} L {r.x + r.w - 34} {r.y + 44} '
                     f'L {r.x + 34} {r.y + 44} Z" fill="#0f163a"/>')
            s.append(text(r.cx, below, label, 12, weight="bold"))
        else:
            s.append(f'<path d="M {r.x + 14} {r.y + 22} L {r.x + 14} {r.y + r.h - 22} L {r.x + 44} {r.y + r.h - 34} '
                     f'L {r.x + 44} {r.y + 34} Z" fill="#0f163a"/>')
            s.append(text(r.x + r.w + 14, r.cy, label, 12, weight="bold", rotate=-90))
    elif kind == "usbhost":
        s.append(rect(r, "url(#metal)", "#5d6369", 2, 3))
        s.append(f'<rect x="{r.x + 16}" y="{r.y + 18}" width="{r.w - 32}" height="18" fill="#1a1c1e"/>')
        s.append(text(r.cx, below, label, 11, weight="bold"))
    elif kind == "rj45":
        s.append(rect(r, "url(#metal)", "#5d6369", 2, 3))
        s.append(f'<rect x="{r.x + 22}" y="{r.y + 20}" width="{r.w - 44}" height="{r.h - 40}" fill="#141618"/>')
        for k, col in enumerate(("#3ddc84", "#f0b429")):
            s.append(f'<rect x="{r.x + 8 + k * (r.w - 22)}" y="{r.y + r.h - 14}" width="6" height="6" fill="{col}" opacity="0.6"/>')
        s.append(text(r.cx, below, label, 11, weight="bold"))
    elif kind == "audio":
        s.append(rect(r, BLACK, "#000", 1, 4))
        s.append(f'<circle cx="{r.cx}" cy="{r.cy}" r="10" fill="#2a2d30" stroke="#555"/>')
        s.append(text(r.cx, below, label, 10))
    elif kind == "microusb":
        s.append(rect(r, "url(#metal)", "#5d6369", 1.5, 3))
        s.append(text(r.cx, r.y + r.h + 14, label, 10))
    else:  # jack
        s.append(rect(r, BLACK, "#000", 1, 4))
        s.append(f'<circle cx="{r.cx}" cy="{r.cy}" r="12" fill="#2a2d30"/>')
        s.append(text(r.cx, r.y + r.h + 14, label, 10))
    return s


def render(L: BoardLayout) -> str:
    W, H = L.width, L.height
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:g} {H:g}" width="{W:g}" height="{H:g}">',
         '<defs>',
         f'<linearGradient id="pcb" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{L.pcb[0]}"/>'
         f'<stop offset="1" stop-color="{L.pcb[1]}"/></linearGradient>',
         '<linearGradient id="chip" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2d3136"/>'
         '<stop offset="1" stop-color="#15171a"/></linearGradient>',
         '<linearGradient id="metal" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d5d9dd"/>'
         '<stop offset="1" stop-color="#8a9096"/></linearGradient>', '</defs>']
    s.append(f'<rect x="1" y="1" width="{W - 2:g}" height="{H - 2:g}" rx="18" fill="url(#pcb)" stroke="#111" stroke-width="3"/>')
    for x, y in [(22, 22), (W - 22, 22), (22, H - 22), (W - 22, H - 22)]:
        s.append(f'<circle cx="{x}" cy="{y}" r="11" fill="{COPPER}"/><circle cx="{x}" cy="{y}" r="6" fill="#0b0d0c"/>')
    trace = "#2b6a4b" if L.board_id == "basys3" else "#3a4152"
    f = L.fpga
    for i in range(14):
        y = f.y - 25 + i * 14
        s.append(f'<path d="M {f.x - 300} {y} L {f.x - 120} {y} L {f.x - 90} {y + 25} L {f.x} {y + 25}" '
                 f'stroke="{trace}" stroke-width="2" fill="none" opacity="0.55"/>')
        s.append(f'<path d="M {f.x + f.w} {y + 10} L {f.x + f.w + 90} {y + 10} L {f.x + f.w + 120} {y - 15} '
                 f'L {f.x + f.w + 300} {y - 15}" stroke="{trace}" stroke-width="2" fill="none" opacity="0.55"/>')
    tx, ty = L.title_pos
    s.append(text(tx, ty, L.title, 34, weight="bold"))
    s.append(text(tx, ty + 22, "FPGA Virtual Lab model", 11))
    s.append(text(tx, ty + 40, f"virtual_{L.board_id}", 11, fill="#9fd3b8"))
    s.append(rect(f, "url(#chip)", "#0a0a0a", 2, 6))
    s.append(f'<circle cx="{f.x + 16}" cy="{f.y + 16}" r="5" fill="#3b4046"/>')
    for k, (line, size, col) in enumerate(zip(L.fpga_lines, (18, 16, 12, 10), ("#cfd3d6", "#cfd3d6", "#9aa0a6", "#9aa0a6"))):
        s.append(text(f.cx, f.cy - 22 + k * 22 - (6 if k > 1 else 0), line, size, fill=col, weight="bold" if k == 0 else "normal"))
    for label, r in L.chips:
        s.append(f'<rect x="{r.x}" y="{r.y}" width="{r.w}" height="{r.h}" rx="3" fill="url(#chip)"/>')
        s.append(text(r.cx, r.cy + 4, label, 9, fill="#aeb4b9"))
    for kind, label, r in L.connectors:
        s += connector(kind, label, r, H)
    ps = L.power_switch
    s.append(rect(ps, "#2a2d30", "#000", 1, 3))
    s.append(text(ps.cx, ps.y + ps.h + 14, "POWER", 10))
    px, py = L.power_led_center()
    s.append(f'<circle cx="{px}" cy="{py}" r="5" fill="#4a2420" stroke="#2a0d08"/>')
    s.append(text(px, py + 18, "PWR", 8))
    pb = L.prog_button
    s.append(rect(pb, "#c9ccd0", "#666", 1, 4))
    s.append(text(pb.cx, pb.y + pb.h + 13, "PROG", 9))
    dx, dy = L.done_center()
    s.append(f'<circle cx="{dx}" cy="{dy}" r="5" fill="#1d3a26" stroke="#0c1a10"/>')
    s.append(text(dx, pb.y + pb.h + 13, "DONE", 9))
    for name, r in L.pmods.items():
        s += pmod(name, r, left=r.x < W / 2)
    ss = L.sevenseg
    s.append(rect(ss, "#111214", "#000", 2, 6))
    for k in range(L.digits):
        d = L.digit_rect(k)
        s.append(rect(d, "#16171a", "#25272b", 1, 3))
        s.append(text(d.cx, ss.y + ss.h + 15, f"AN{k}", 10))
        for poly in L.segment_polys(k):
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in poly)
            s.append(f'<polygon points="{pts}" fill="#2a1c1c"/>')
        cx, cy = L.dp_center(k)
        s.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="#2a1c1c"/>')
    for name, r in L.rgb_leds.items():
        s.append(f'<rect x="{r.x - 3}" y="{r.y - 3}" width="{r.w + 6}" height="{r.h + 6}" rx="4" fill="#d7d7d7"/>')
        s.append(text(r.cx, r.y + r.h + 16, "LD" + name[3:], 9))
    for name, r in L.buttons.items():
        s.append(f'<rect x="{r.x - 6:.1f}" y="{r.y - 6:.1f}" width="{r.w + 12:.1f}" height="{r.h + 12:.1f}" '
                 f'rx="4" fill="url(#metal)" stroke="#555" stroke-width="1"/>')
        lx, ly = r.cx, r.y + r.h + 22
        if name == "BTNL":
            lx, ly = r.x - 8, r.y - 10
        elif name == "BTNR":
            lx, ly = r.x + r.w + 8, r.y - 10
        elif name == "BTNU":
            lx, ly = r.cx + 48, r.cy + 4
        elif name == "BTNC":
            lx, ly = r.cx + 2, r.y - 12
        elif name == "CPU_RESETN":
            lx, ly = r.cx, r.y - 12
        s.append(text(lx, ly, "CPU RESET" if name == "CPU_RESETN" else name, 10, weight="bold"))
    for i in range(16):
        r = L.leds[f"LED{i}"]
        s.append(rect(Rect(r.x - 2, r.y - 2, r.w + 4, r.h + 4), "#d7d7d7", rx=2))
        s.append(text(r.cx, L.led_label_y, f"LD{i}", 9))
        sw = L.switches[f"SW{i}"]
        s.append(rect(Rect(sw.x - 4, sw.y - 4, sw.w + 8, sw.h + 8), "#1a1b1e", "#000", 1, 3))
        s.append(rect(sw, "#2b2d31", rx=2))
        s.append(text(sw.cx, L.switch_label_y, f"SW{i}", 9))
    s.append(text(L.switches["SW15"].x - 10, L.switches["SW15"].cy, "ON", 8, rotate=-90))
    s.append(text(W - 52, H - 40, "DIGILENT", 10, anchor="end", fill="#cfe3d6", weight="bold"))
    s.append("</svg>")
    return "\n".join(s)


def main():
    out_dir = ROOT / "assets" / "boards"
    out_dir.mkdir(parents=True, exist_ok=True)
    for board_id in ("basys3", "nexys4ddr"):
        out = out_dir / f"{board_id}.svg"
        out.write_text(render(get_layout(board_id)), encoding="utf-8")
        print("wrote", out)


if __name__ == "__main__":
    main()
