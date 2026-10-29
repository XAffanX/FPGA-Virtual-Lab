"""Interactive virtual board: SVG artwork plus live switches, buttons, LEDs and display.

Board-independent: geometry comes from app.board.layout, resources from the board definition, so
the same widget shows the Basys 3 and the Nexys 4 DDR (set_board() swaps boards at runtime).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QLinearGradient, QPainter, QPen, QPolygonF, QRadialGradient
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsObject, QGraphicsScene, QGraphicsView, QHBoxLayout,
                               QLabel, QToolButton, QVBoxLayout, QWidget)

from app.board.board import Board
from app.board.layout import BoardLayout, Rect, get_layout
from app.utils.paths import resource_root

# Keyboard shortcuts: momentary buttons and switch toggles.
BUTTON_KEYS = {Qt.Key_Up: "BTNU", Qt.Key_Down: "BTND", Qt.Key_Left: "BTNL", Qt.Key_Right: "BTNR",
               Qt.Key_Space: "BTNC", Qt.Key_Escape: "CPU_RESETN"}
SWITCH_KEYS = {getattr(Qt, f"Key_{i}"): i for i in range(10)}
SWITCH_KEYS.update({Qt.Key_Q: 10, Qt.Key_W: 11, Qt.Key_E: 12, Qt.Key_R: 13, Qt.Key_T: 14, Qt.Key_Y: 15})

LED_ON = QColor(70, 255, 120)
SEG_ON = QColor(255, 45, 35)
ACCENT = QColor(80, 170, 255)


def _grow(r: Rect, d: float) -> Rect:
    return Rect(r.x - d, r.y - d, r.w + 2 * d, r.h + 2 * d)


class BoardOverlay(QGraphicsObject):
    input_changed = Signal(str, int)
    power_clicked = Signal()          # the board's POWER slide switch
    prog_clicked = Signal()           # the board's PROG button (clears the FPGA configuration)

    def __init__(self, board: Board, layout: BoardLayout):
        super().__init__()
        self.board = board
        self.L = layout
        self.switches = [0] * 16
        self.buttons = {b: 0 for b in layout.buttons}
        self.latched: set[str] = set()
        self.led_brightness: list = [None] * 16
        self.led_known: list = [True] * 16
        self.extra: dict = {}                   # RGB LED channels: id -> 0..1 / None
        self.digits = []                        # list[DigitState]
        self.mapped: dict[str, str] = {}        # resource id -> signal name(s)
        self.show_names = True
        self.powered = False
        self.done = False
        self._prog_down = False
        self._mouse_button: str | None = None
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsFocusable, True)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.L.width, self.L.height)

    # ------------------------------------------------------------- painting
    def paint(self, p: QPainter, option, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        self._paint_power(p)
        self._paint_leds(p)
        self._paint_rgb(p)
        self._paint_switches(p)
        self._paint_buttons(p)
        self._paint_display(p)
        if self.show_names and self.mapped:
            self._paint_names(p)

    def _glow(self, p: QPainter, cx: float, cy: float, col: QColor, radius: float, alpha: float):
        g = QRadialGradient(QPointF(cx, cy), radius)
        c = QColor(col)
        c.setAlphaF(max(0.0, min(1.0, alpha)))
        g.setColorAt(0, c)
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

    def _paint_power(self, p: QPainter):
        ps = self.L.power_switch
        hx = ps.x + 24 if self.powered else ps.x + 6
        g = QLinearGradient(QPointF(hx, ps.y + 5), QPointF(hx, ps.y + 21))
        g.setColorAt(0, QColor(250, 250, 250))
        g.setColorAt(1, QColor(180, 183, 187))
        p.setPen(QPen(QColor(60, 60, 60), 1))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(hx, ps.y + 5, 16, 16), 2, 2)
        p.setFont(QFont("Segoe UI", 5, QFont.Bold))
        p.setPen(QColor(200, 220, 205))
        p.drawText(QRectF(ps.x - 2, ps.y - 9, ps.w + 4, 8), Qt.AlignCenter, "OFF        ON")
        for (cx, cy), on, col in ((self.L.power_led_center(), self.powered, QColor(255, 70, 50)),
                                  (self.L.done_center(), self.done, QColor(60, 255, 120))):
            if on:
                self._glow(p, cx, cy, col, 18, 0.6)
                p.setBrush(col.lighter(130))
                p.drawEllipse(QPointF(cx, cy), 5, 5)
        pb = self.L.prog_button
        d = 1 if self._prog_down else 0
        p.setPen(QPen(QColor(90, 20, 15), 1))
        p.setBrush(QColor(170, 40, 30) if self._prog_down else QColor(225, 75, 59))
        p.drawEllipse(QPointF(pb.cx + d, pb.cy + d), 7, 7)

    def _paint_leds(self, p: QPainter):
        for i in range(16):
            r = self.L.leds[f"LED{i}"]
            b = self.led_brightness[i]
            rect = QRectF(r.x, r.y, r.w, r.h)
            if b is not None and b > 0.02:
                self._glow(p, rect.center().x(), rect.center().y(), LED_ON, 34, 0.65 * b)
                body = QColor(LED_ON).lighter(100 + int(60 * b))
                body.setAlphaF(0.35 + 0.65 * b)
                p.setBrush(body)
                p.drawRoundedRect(rect, 2, 2)
            if not self.led_known[i] and f"LED{i}" in self.mapped:
                p.setPen(QPen(QColor(255, 180, 40), 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 3, 3)
                p.setFont(QFont("Segoe UI", 6, QFont.Bold))
                p.drawText(QRectF(r.x - 6, r.y - 15, r.w + 12, 12), Qt.AlignCenter, "X")

    def _paint_rgb(self, p: QPainter):
        for name, r in self.L.rgb_leds.items():
            ch = [self.extra.get(f"{name}_{c}") or 0.0 for c in "RGB"]
            if max(ch) <= 0.02:
                continue
            col = QColor(int(255 * ch[0]), int(255 * ch[1]), int(255 * ch[2]))
            self._glow(p, r.cx, r.cy, col, 30, 0.75 * max(ch))
            p.setBrush(col.lighter(120))
            p.drawRoundedRect(QRectF(r.x, r.y, r.w, r.h), 4, 4)

    def _paint_switches(self, p: QPainter):
        for i in range(16):
            r = self.L.switches[f"SW{i}"]
            on = self.switches[i]
            hh = min(26.0, r.h * 0.42)
            hy = r.y + 4 if on else r.y + r.h - hh - 4
            handle = QRectF(r.x + 3, hy, r.w - 6, hh)
            g = QLinearGradient(handle.topLeft(), handle.bottomLeft())
            g.setColorAt(0, QColor(250, 250, 250))
            g.setColorAt(1, QColor(185, 188, 192))
            p.setPen(QPen(QColor(90, 90, 90), 1))
            p.setBrush(g)
            p.drawRoundedRect(handle, 2, 2)
            for k in range(3):
                y = handle.y() + hh * 0.3 + k * 5
                p.setPen(QPen(QColor(140, 140, 145), 1))
                p.drawLine(QPointF(handle.x() + 4, y), QPointF(handle.right() - 4, y))
            if f"SW{i}" in self.mapped:
                p.setPen(QPen(ACCENT, 1.4))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(r.x - 5, r.y - 5, r.w + 10, r.h + 10), 4, 4)

    def _paint_buttons(self, p: QPainter):
        for name, r in self.L.buttons.items():
            down = self.buttons[name]
            rect = QRectF(r.x, r.y, r.w, r.h)
            off = 2 if down else 0
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawEllipse(rect.center() + QPointF(1.5, 2.5), r.w / 2.6, r.w / 2.6)
            g = QRadialGradient(rect.center() + QPointF(-4 + off, -5 + off), r.w / 2)
            if name in self.L.red_buttons:
                base = QColor(200, 40, 35) if not down else QColor(140, 25, 22)
            else:
                base = QColor(60, 66, 75) if not down else QColor(38, 42, 48)
            g.setColorAt(0, base.lighter(170))
            g.setColorAt(1, base)
            p.setBrush(g)
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.drawEllipse(rect.center() + QPointF(off / 2, off / 2), r.w / 2.6, r.w / 2.6)
            if name in self.latched:
                p.setPen(QPen(QColor(255, 190, 60), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(rect.center(), r.w / 2 + 4, r.w / 2 + 4)
            elif name in self.mapped:
                p.setPen(QPen(ACCENT, 1.4))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-8, -8, 8, 8), 5, 5)

    def _paint_display(self, p: QPainter):
        for k, d in enumerate(self.digits[: self.L.digits]):
            if not d.lit:
                continue
            for s, poly in enumerate(self.L.segment_polys(k)):
                b = d.segments[s]
                if b <= 0.03:
                    continue
                c = QColor(SEG_ON)
                c.setAlphaF(0.25 + 0.75 * min(1.0, b))
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in poly]))
            if d.segments[7] > 0.03:
                c = QColor(SEG_ON)
                c.setAlphaF(0.25 + 0.75 * min(1.0, d.segments[7]))
                p.setBrush(c)
                cx, cy = self.L.dp_center(k)
                p.drawEllipse(QPointF(cx, cy), 4, 4)

    def _paint_names(self, p: QPainter):
        p.setFont(QFont("Consolas", 6))
        p.setPen(QColor(150, 210, 255))
        for i in range(16):
            for rid, r, y in ((f"LED{i}", self.L.leds[f"LED{i}"], self.L.led_label_y + 2),
                              (f"SW{i}", self.L.switches[f"SW{i}"], self.L.switch_label_y + 4)):
                name = self.mapped.get(rid)
                if name:
                    p.drawText(QRectF(r.cx - 28, y, 56, 11), Qt.AlignCenter, name)
        ss = self.L.sevenseg
        segs = [self.mapped.get(x) for x in ("SEG_A", "AN0")]
        if any(segs):
            p.drawText(QRectF(ss.x, ss.y + ss.h - 15, ss.w, 12), Qt.AlignCenter,
                       "display: " + ", ".join(sorted({n.split("[")[0] for n in segs if n})))

    # ------------------------------------------------------------- interaction
    def hit(self, pos: QPointF) -> str | None:
        x, y = pos.x(), pos.y()
        if _grow(self.L.power_switch, 4).contains(x, y):
            return "POWER"
        if _grow(self.L.prog_button, 4).contains(x, y):
            return "PROG"
        for rid, r in self.L.switches.items():
            if _grow(r, 6).contains(x, y):
                return rid
        for rid, r in self.L.buttons.items():
            if _grow(r, 6).contains(x, y):
                return rid
        return None

    def toggle_switch(self, index: int):
        self.switches[index] ^= 1
        self.input_changed.emit(f"SW{index}", self.switches[index])
        self.update()

    def set_button(self, name: str, level: int):
        if name not in self.buttons or self.buttons[name] == level:
            return
        self.buttons[name] = level
        self.input_changed.emit(name, level)
        self.update()

    def mousePressEvent(self, ev):
        rid = self.hit(ev.pos())
        if rid is None:
            ev.ignore()
            return
        ev.accept()
        if rid == "POWER":
            if ev.button() == Qt.LeftButton:
                self.power_clicked.emit()
            return
        if rid == "PROG":
            if ev.button() == Qt.LeftButton:
                self._prog_down = True
                self.update()
            return
        if rid.startswith("SW") and ev.button() == Qt.LeftButton:
            self.toggle_switch(int(rid[2:]))
        elif rid in self.buttons:
            if ev.button() == Qt.RightButton or ev.modifiers() & Qt.ShiftModifier:
                if rid in self.latched:
                    self.latched.discard(rid)
                    self.set_button(rid, 0)
                else:
                    self.latched.add(rid)
                    self.set_button(rid, 1)
            elif ev.button() == Qt.LeftButton:
                self._mouse_button = rid
                self.set_button(rid, 1)

    def mouseReleaseEvent(self, ev):
        if self._prog_down:
            self._prog_down = False
            self.update()
            self.prog_clicked.emit()
        if self._mouse_button and self._mouse_button not in self.latched:
            self.set_button(self._mouse_button, 0)
        self._mouse_button = None

    def hoverMoveEvent(self, ev):
        x, y = ev.pos().x(), ev.pos().y()
        rid = self.hit(ev.pos())
        if rid == "POWER":
            self.setToolTip("<b>POWER switch</b><br>Click to switch the board on or off. Turning it off "
                            "stops the clock and erases the FPGA configuration.")
            self.setCursor(Qt.PointingHandCursor)
            return
        if rid == "PROG":
            self.setToolTip("<b>PROG button</b><br>Clears the FPGA configuration (DONE goes low), like pulling "
                            "PROG_B low on the real board.")
            self.setCursor(Qt.PointingHandCursor)
            return
        if rid:
            res = self.board.resources.get(rid)
            pin = res.pin if res else "?"
            if rid.startswith("SW"):
                extra = "Click to toggle."
            else:
                extra = "Hold to press (momentary). Right-click or Shift+click to latch."
                if res is not None and res.active_low:
                    extra += "<br>Active low: the FPGA pin reads 1 until the button is pressed."
            self.setToolTip(f"<b>{rid}</b> &nbsp; pin {pin}<br>signal: <code>{self.mapped.get(rid, 'not mapped')}"
                            f"</code><br>{extra}")
            self.setCursor(Qt.PointingHandCursor)
            return
        tip = ""
        for led, r in self.L.leds.items():
            if _grow(r, 6).contains(x, y):
                res = self.board.resources[led]
                tip = (f"<b>{led}</b> ({res.label}) &nbsp; pin {res.pin}<br>"
                       f"signal: <code>{self.mapped.get(led, 'not mapped')}</code>")
        for name, r in self.L.rgb_leds.items():
            if _grow(r, 6).contains(x, y):
                pins = ", ".join(f"{c}: {self.board.resources[f'{name}_{c}'].pin}" for c in "RGB")
                sig = ", ".join(self.mapped.get(f"{name}_{c}", "-") for c in "RGB")
                tip = f"<b>{name}</b> tri-color LED &nbsp; {pins}<br>signals: <code>{sig}</code>"
        if not tip and self.L.sevenseg.contains(x, y):
            tip = (f"{self.L.digits}-digit seven-segment display (common anode, active-low segments "
                   f"CA..CG/DP and anodes AN0..AN{self.L.digits - 1})")
        self.setToolTip(tip)
        self.unsetCursor()


class BoardGraphicsView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.overlay: BoardOverlay | None = None
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setBackgroundBrush(QColor("#15171c"))
        self.setFocusPolicy(Qt.StrongFocus)
        self._zoom = 1.0
        self._fitted = True

    def wheelEvent(self, ev):
        self.zoom_by(1.15 if ev.angleDelta().y() > 0 else 1 / 1.15)

    def zoom_by(self, factor: float):
        new = max(0.3, min(6.0, self._zoom * factor))
        factor = new / self._zoom
        self._zoom = new
        self._fitted = False
        self.scale(factor, factor)

    def fit(self):
        self.resetTransform()
        self.fitInView(self.sceneRect().adjusted(-10, -10, 10, 10), Qt.KeepAspectRatio)
        self._zoom = self.transform().m11()
        self._fitted = True

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._fitted:
            self.fit()

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.isAutoRepeat() or self.overlay is None:
            return
        if ev.key() in BUTTON_KEYS:
            self.overlay.set_button(BUTTON_KEYS[ev.key()], 1)
        elif ev.key() in SWITCH_KEYS and not ev.modifiers() & Qt.ControlModifier:
            self.overlay.toggle_switch(SWITCH_KEYS[ev.key()])
        elif ev.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(1.2)
        elif ev.key() == Qt.Key_Minus:
            self.zoom_by(1 / 1.2)
        else:
            super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev: QKeyEvent):
        if ev.isAutoRepeat() or self.overlay is None:
            return
        if ev.key() in BUTTON_KEYS:
            name = BUTTON_KEYS[ev.key()]
            if name not in self.overlay.latched:
                self.overlay.set_button(name, 0)
        else:
            super().keyReleaseEvent(ev)


class BoardWidget(QWidget):
    """Board view with a small zoom toolbar."""
    input_changed = Signal(str, int)
    power_clicked = Signal()
    prog_clicked = Signal()

    def __init__(self, board: Board, parent=None):
        super().__init__(parent)
        self.view = BoardGraphicsView()
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        self.title = QLabel()
        self.title.setObjectName("boardTitle")
        bar.addWidget(self.title)
        bar.addStretch(1)
        self.hint = QLabel("Keys: 0-9, Q-Y switches · arrows/Space buttons · wheel zoom · drag pan")
        self.hint.setObjectName("hint")
        bar.addWidget(self.hint)
        for text, tip, fn in (("−", "Zoom out", lambda: self.view.zoom_by(1 / 1.2)),
                              ("+", "Zoom in", lambda: self.view.zoom_by(1.2)),
                              ("Fit", "Fit board to window", self.view.fit),
                              ("Names", "Show mapped signal names on the board", None)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            if fn is None:
                b.setCheckable(True)
                b.setChecked(True)
                b.toggled.connect(self._toggle_names)
            else:
                b.clicked.connect(fn)
            bar.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)
        self.overlay: BoardOverlay | None = None
        self.set_board(board)

    def set_board(self, board: Board):
        """Show another board (the physical inputs start in their default positions)."""
        self.board = board
        layout = get_layout(board.id)
        old = self.view.scene()
        scene = QGraphicsScene(self)
        scene.setSceneRect(0, 0, layout.width, layout.height)
        bg = QGraphicsSvgItem(str(resource_root() / "assets" / "boards" / f"{board.id}.svg"))
        bg.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        bg.setZValue(0)
        scene.addItem(bg)
        ov = BoardOverlay(board, layout)
        ov.setZValue(1)
        scene.addItem(ov)
        ov.input_changed.connect(self.input_changed)
        ov.power_clicked.connect(self.power_clicked)
        ov.prog_clicked.connect(self.prog_clicked)
        if self.overlay is not None:
            ov.show_names = self.overlay.show_names
        self.overlay = ov
        self.view.overlay = ov
        self.view.setScene(scene)
        if old is not None:
            old.deleteLater()
        self.title.setText(f"{board.virtual_name}  ·  {board.name}  ·  {board.fpga_short}")
        self.view.fit()

    def _toggle_names(self, on: bool):
        self.overlay.show_names = on
        self.overlay.update()

    # ------------------------------------------------------------- API
    def set_mapping(self, mapped: dict[str, str]):
        self.overlay.mapped = dict(mapped)
        self.overlay.update()

    def set_power(self, powered: bool, done: bool):
        self.overlay.powered = powered
        self.overlay.done = done
        self.overlay.update()

    def switch_states(self) -> dict[str, int]:
        return {f"SW{i}": v for i, v in enumerate(self.overlay.switches)}

    def button_states(self) -> dict[str, int]:
        return dict(self.overlay.buttons)

    def set_outputs(self, leds: list, led_levels: list, digits: list, extra: dict | None = None):
        self.overlay.led_brightness = list(leds)
        self.overlay.led_known = [lv is not None for lv in led_levels]
        self.overlay.digits = list(digits)
        self.overlay.extra = dict(extra or {})
        self.overlay.update()

    def clear_outputs(self):
        self.overlay.led_brightness = [None] * 16
        self.overlay.led_known = [True] * 16
        self.overlay.digits = []
        self.overlay.extra = {}
        self.overlay.update()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.view.fit()
