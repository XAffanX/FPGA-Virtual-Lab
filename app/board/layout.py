"""Geometry of the virtual boards (top view, board units).

One `BoardLayout` per board, used both by scripts/gen_board_svg.py (static artwork) and by the
interactive board view (switch handles, LEDs, segments, buttons), so they always line up.

* Basys 3: switches along the bottom edge with LEDs above, 4-digit display bottom-left of centre,
  pushbutton cross bottom-right, connectors on the edges (Basys 3 reference manual figure).
* Nexys 4 DDR: switches and LEDs along the bottom, 8-digit display above them, the pushbutton
  cross with the red CPU RESET button on the right, two tri-colour LEDs, Pmods JA-JD on the right
  edge, VGA/USB/Ethernet connectors on the top and left (Nexys 4 DDR reference manual figure).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


def _cross(cx: float, cy: float, d: float, s: float) -> dict[str, Rect]:
    return {
        "BTNU": Rect(cx - s / 2, cy - d - s / 2, s, s),
        "BTNL": Rect(cx - d - s / 2, cy - s / 2, s, s),
        "BTNC": Rect(cx - s / 2, cy - s / 2, s, s),
        "BTNR": Rect(cx + d - s / 2, cy - s / 2, s, s),
        "BTND": Rect(cx - s / 2, cy + d - s / 2, s, s),
    }


@dataclass
class BoardLayout:
    board_id: str
    width: float
    height: float
    title: str                       # silkscreen name, e.g. "BASYS 3"
    switches: dict[str, Rect]
    leds: dict[str, Rect]
    led_label_y: float
    switch_label_y: float
    buttons: dict[str, Rect]
    sevenseg: Rect
    digits: int
    digit_pitch: float
    fpga: Rect
    fpga_lines: tuple[str, ...]
    power_switch: Rect
    prog_button: Rect
    pmods: dict[str, Rect]
    connectors: list[tuple[str, str, Rect]] = field(default_factory=list)   # (kind, label, rect)
    chips: list[tuple[str, Rect]] = field(default_factory=list)            # (label, rect)
    rgb_leds: dict[str, Rect] = field(default_factory=dict)               # "LED16" -> rect
    red_buttons: tuple[str, ...] = ()                                      # drawn red (CPU RESET)
    pcb: tuple[str, str] = ("#235a3f", "#1e4d36")                         # gradient top/bottom
    title_pos: tuple[float, float] = (150, 210)

    digit_w: float = 58.0
    digit_h: float = 96.0

    # ---------------------------------------------------------------- seven-segment geometry
    def digit_rect(self, k: int) -> Rect:
        """Digit k (0 = rightmost / AN0)."""
        pos = self.digits - 1 - k
        return Rect(self.sevenseg.x + 22 + pos * self.digit_pitch, self.sevenseg.y + 16, self.digit_w, self.digit_h)

    def segment_polys(self, k: int) -> list[list[tuple[float, float]]]:
        """Polygons for segments A..G of digit k (slight italic slant like the real part)."""
        r = self.digit_rect(k)
        t = 7.0
        x0, y0, w, h = r.x + 10, r.y + 6, r.w - 22, r.h - 12
        slant = 0.10

        def P(x, y):
            return (x + (r.cy - y) * slant, y)

        def hseg(y):
            return [P(x0 + t, y), P(x0 + w - t, y), P(x0 + w - t / 2, y + t / 2), P(x0 + w - t, y + t),
                    P(x0 + t, y + t), P(x0 + t / 2, y + t / 2)]

        def vseg(x, ya, yb):
            return [P(x + t / 2, ya), P(x + t, ya + t / 2), P(x + t, yb - t / 2), P(x + t / 2, yb),
                    P(x, yb - t / 2), P(x, ya + t / 2)]

        mid = y0 + h / 2 - t / 2
        return [hseg(y0), vseg(x0 + w - t, y0 + t / 2, mid + t / 2), vseg(x0 + w - t, mid + t / 2, y0 + h - t / 2),
                hseg(y0 + h - t), vseg(x0, mid + t / 2, y0 + h - t / 2), vseg(x0, y0 + t / 2, mid + t / 2), hseg(mid)]

    def dp_center(self, k: int) -> tuple[float, float]:
        r = self.digit_rect(k)
        return (r.x + r.w - 4, r.y + r.h - 9)

    def power_led_center(self) -> tuple[float, float]:
        return (self.power_switch.x + self.power_switch.w + 18, self.power_switch.cy)

    def done_center(self) -> tuple[float, float]:
        return (self.prog_button.x + 60, self.prog_button.cy)


def _columns(x0: float, x1: float, n: int = 16):
    step = (x1 - x0) / (n - 1)
    return lambda i: x1 - i * step          # index 0 on the right, as on the boards


def _basys3() -> BoardLayout:
    col = _columns(62.0, 938.0)
    return BoardLayout(
        board_id="basys3", width=1000.0, height=720.0, title="BASYS 3",
        switches={f"SW{i}": Rect(col(i) - 14, 618, 28, 66) for i in range(16)},
        leds={f"LED{i}": Rect(col(i) - 9, 560, 18, 12) for i in range(16)},
        led_label_y=590, switch_label_y=700,
        buttons=_cross(800.0, 450.0, 62.0, 34.0),
        sevenseg=Rect(196, 400, 290, 132), digits=4, digit_pitch=66,
        fpga=Rect(420, 175, 170, 170), fpga_lines=("XILINX", "ARTIX-7", "XC7A35T", "CPG236"),
        power_switch=Rect(52, 8, 46, 26), prog_button=Rect(300, 90, 24, 24),
        pmods={"JA": Rect(958, 120, 42, 120), "JB": Rect(958, 290, 42, 120),
               "JC": Rect(0, 290, 42, 120), "JXADC": Rect(0, 120, 42, 120)},
        connectors=[("vga", "VGA", Rect(430, 0, 160, 58)), ("usbhost", "USB HOST", Rect(780, 0, 96, 70)),
                    ("microusb", "PROG / UART", Rect(146, 0, 54, 30)), ("jack", "EXT PWR", Rect(236, 0, 60, 52))],
        chips=[("FT2232", Rect(240, 120, 70, 50)), ("FLASH", Rect(640, 360, 56, 40)), ("100MHz", Rect(370, 360, 30, 22))],
    )


def _nexys4ddr() -> BoardLayout:
    col = _columns(56.0, 650.0)
    return BoardLayout(
        board_id="nexys4ddr", width=1000.0, height=860.0, title="NEXYS 4 DDR", title_pos=(225, 250),
        switches={f"SW{i}": Rect(col(i) - 12, 760, 24, 60) for i in range(16)},
        leds={f"LED{i}": Rect(col(i) - 8, 708, 16, 11) for i in range(16)},
        led_label_y=736, switch_label_y=836,
        buttons=_cross(840.0, 690.0, 56.0, 32.0),
        red_buttons=("CPU_RESETN",),
        sevenseg=Rect(60, 545, 570, 128), digits=8, digit_pitch=66,
        fpga=Rect(410, 250, 170, 170), fpga_lines=("XILINX", "ARTIX-7", "XC7A100T", "CSG324"),
        power_switch=Rect(40, 14, 46, 26), prog_button=Rect(300, 120, 24, 24),
        pmods={"JA": Rect(958, 100, 42, 120), "JB": Rect(958, 230, 42, 120),
               "JC": Rect(958, 360, 42, 120), "JD": Rect(958, 490, 42, 120), "JXADC": Rect(0, 390, 42, 120)},
        connectors=[("jack", "POWER", Rect(130, 0, 60, 52)), ("microusb", "PROG / UART", Rect(225, 0, 54, 30)),
                    ("vga", "VGA", Rect(0, 130, 58, 160)), ("usbhost", "USB HOST", Rect(400, 0, 96, 70)),
                    ("rj45", "ETHERNET", Rect(590, 0, 110, 92)), ("audio", "AUDIO", Rect(760, 0, 56, 50))],
        chips=[("DDR2", Rect(640, 270, 90, 60)), ("FT2232", Rect(230, 140, 70, 50)), ("FLASH", Rect(640, 380, 56, 40)),
               ("ADXL362", Rect(330, 470, 40, 32)), ("ADT7420", Rect(400, 470, 40, 32)), ("MIC", Rect(470, 470, 32, 32))],
        rgb_leds={"LED16": Rect(690, 600, 22, 22), "LED17": Rect(735, 600, 22, 22)},
        pcb=("#2b2f3a", "#1d2029"),
    )


_LAYOUTS: dict[str, BoardLayout] = {}


def get_layout(board_id: str) -> BoardLayout:
    if board_id not in _LAYOUTS:
        factory = {"basys3": _basys3, "nexys4ddr": _nexys4ddr}.get(board_id)
        if factory is None:
            raise KeyError(f"no layout for board '{board_id}'")
        lay = factory()
        if board_id == "nexys4ddr":
            lay.buttons["CPU_RESETN"] = Rect(860, 545, 34, 34)
        _LAYOUTS[board_id] = lay
    return _LAYOUTS[board_id]
