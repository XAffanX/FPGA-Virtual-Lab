"""Digital waveform viewer for the simulator's VCD output."""
from __future__ import annotations

import bisect
import shutil
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollBar, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from app.waveforms.vcd import OFF, Signal as VSignal, VcdReader, format_value

HIDDEN_PREFIX = "fvl_tb.fvl_dut"
ROW_H = 26
NAME_W = 210
AXIS_H = 26

C_BG = QColor("#16181d")
C_GRID = QColor("#252a33")
C_WAVE = QColor("#3ddc84")
C_BUS = QColor("#4fb3ff")
C_X = QColor("#ff5c5c")
C_Z = QColor("#e6c84f")
C_OFF = QColor("#555b66")
C_TEXT = QColor("#d6dbe3")
C_CURSOR = QColor("#ffb347")


def display_name(full: str) -> str:
    if full.startswith(HIDDEN_PREFIX):
        rest = full[len(HIDDEN_PREFIX):].lstrip(".")
        return rest or full
    return full


def fmt_time(fs: int) -> str:
    for unit, div in (("s", 10**15), ("ms", 10**12), ("us", 10**9), ("ns", 10**6), ("ps", 10**3)):
        if abs(fs) >= div:
            v = fs / div
            return f"{v:.6g} {unit}"
    return f"{fs} fs"


class WaveCanvas(QWidget):
    cursor_moved = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals: list[VSignal] = []
        self.radix = "hex"
        self.t0 = 0                 # left edge time (fs)
        self.fs_per_px = 100_000    # zoom: 0.1 ns per pixel
        self.cursor: int | None = None
        self.end_time = 0
        self.clock_period_fs = 10_000_000
        self.setMinimumHeight(120)
        self.setMouseTracking(True)
        self.font_ = QFont("Consolas", 8)

    def visible_span(self) -> int:
        return int(max(1, self.width() - NAME_W) * self.fs_per_px)

    def x_of(self, t: int) -> float:
        return NAME_W + (t - self.t0) / self.fs_per_px

    def t_of(self, x: float) -> int:
        return int(self.t0 + (x - NAME_W) * self.fs_per_px)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), C_BG)
        p.setFont(self.font_)
        fm = QFontMetrics(self.font_)
        w = self.width()
        self._paint_axis(p, w)
        for row, sig in enumerate(self.signals):
            y = AXIS_H + row * ROW_H
            if y > self.height():
                break
            p.setPen(C_GRID)
            p.drawLine(0, y + ROW_H - 1, w, y + ROW_H - 1)
            name = display_name(sig.name) + (f" [{sig.width - 1}:0]" if sig.width > 1 else "")
            val = ""
            if self.cursor is not None:
                val = " = " + format_value(sig.value_at(self.cursor), sig.width, self.radix)
            p.setPen(C_TEXT)
            p.drawText(QRectF(6, y, NAME_W - 10, ROW_H), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(name + val, Qt.ElideMiddle, NAME_W - 10))
            p.save()
            p.setClipRect(QRectF(NAME_W, y, w - NAME_W, ROW_H))
            self._paint_signal(p, sig, y + 4, ROW_H - 9, fm)
            p.restore()
        p.setPen(QColor("#2f3540"))
        p.drawLine(NAME_W, 0, NAME_W, self.height())
        if self.cursor is not None:
            x = self.x_of(self.cursor)
            if x >= NAME_W:
                p.setPen(QPen(C_CURSOR, 1, Qt.DashLine))
                p.drawLine(QPointF(x, AXIS_H), QPointF(x, self.height()))
        if not self.signals:
            p.setPen(QColor("#7d8590"))
            p.drawText(self.rect().adjusted(NAME_W, 0, 0, 0), Qt.AlignCenter,
                       "Select signals on the left to display their waveforms.")
        p.end()

    def _paint_axis(self, p: QPainter, w: int):
        p.fillRect(0, 0, w, AXIS_H, QColor("#1c1f26"))
        span = self.visible_span()
        raw = span / 8
        mag = 10 ** max(0, len(str(int(raw))) - 1)
        step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
        t = (self.t0 // step) * step
        p.setPen(QColor("#8b949e"))
        while t <= self.t0 + span:
            x = self.x_of(t)
            if x >= NAME_W:
                p.setPen(C_GRID)
                p.drawLine(QPointF(x, AXIS_H), QPointF(x, self.height()))
                p.setPen(QColor("#8b949e"))
                p.drawLine(QPointF(x, AXIS_H - 6), QPointF(x, AXIS_H))
                p.drawText(QPointF(x + 3, AXIS_H - 9), fmt_time(t))
            t += step
        if self.cursor is not None:
            p.setPen(C_CURSOR)
            p.drawText(QRectF(6, 0, NAME_W - 10, AXIS_H), Qt.AlignVCenter,
                       f"cursor {fmt_time(self.cursor)}  (cycle {self.cursor // self.clock_period_fs})")

    def _paint_signal(self, p: QPainter, sig: VSignal, y: float, h: float, fm: QFontMetrics):
        if not sig.times:
            return
        t_left, t_right = self.t0, self.t0 + self.visible_span()
        i = max(0, bisect.bisect_right(sig.times, t_left) - 1)
        end = min(self.end_time, t_right)
        last_x = None
        while i < len(sig.times) and sig.times[i] <= t_right:
            ts = max(sig.times[i], t_left)
            te = sig.times[i + 1] if i + 1 < len(sig.times) else max(end, sig.times[i])
            te = min(te, t_right)
            v = sig.values[i]
            x0, x1 = self.x_of(ts), self.x_of(te)
            if x1 - x0 < 1.0 and i + 1 < len(sig.times):
                # Too many transitions for the pixel resolution: draw a dense block.
                j = i
                while j + 1 < len(sig.times) and self.x_of(sig.times[j + 1]) - x0 < 2.0:
                    j += 1
                xe = self.x_of(min(sig.times[j + 1], t_right)) if j + 1 < len(sig.times) else x1
                p.fillRect(QRectF(x0, y, max(1.0, xe - x0), h), C_BUS if sig.width > 1 else C_WAVE)
                i = j + 1
                last_x = xe
                continue
            self._draw_segment(p, sig, v, x0, x1, y, h, fm, last_x is not None)
            last_x = x1
            i += 1

    def _draw_segment(self, p, sig, v, x0, x1, y, h, fm, edge):
        if v is OFF:
            p.fillRect(QRectF(x0, y + h / 2 - 1, x1 - x0, 2), C_OFF)
            return
        if sig.width == 1 and v in ("0", "1"):
            yy = y if v == "1" else y + h
            p.setPen(QPen(C_WAVE, 1.4))
            if edge:
                p.drawLine(QPointF(x0, y), QPointF(x0, y + h))
            p.drawLine(QPointF(x0, yy), QPointF(x1, yy))
            if v == "1":
                c = QColor(C_WAVE)
                c.setAlpha(28)
                p.fillRect(QRectF(x0, y, x1 - x0, h), c)
            return
        bad = any(c in "xX" for c in v)
        hz = any(c in "zZ" for c in v) and not bad
        col = C_X if bad else C_Z if hz else C_BUS
        if sig.width == 1:
            p.setPen(QPen(col, 1.4))
            p.drawLine(QPointF(x0, y + h / 2), QPointF(x1, y + h / 2))
            return
        d = min(3.0, (x1 - x0) / 2)
        poly = QPolygonF([QPointF(x0, y + h / 2), QPointF(x0 + d, y), QPointF(x1 - d, y), QPointF(x1, y + h / 2),
                          QPointF(x1 - d, y + h), QPointF(x0 + d, y + h)])
        c = QColor(col)
        c.setAlpha(40)
        p.setBrush(c)
        p.setPen(QPen(col, 1.2))
        p.drawPolygon(poly)
        txt = format_value(v, sig.width, self.radix)
        if x1 - x0 > fm.horizontalAdvance(txt) + 8:
            p.setPen(C_TEXT)
            p.drawText(QRectF(x0 + 4, y, x1 - x0 - 8, h), Qt.AlignCenter, txt)

    def mousePressEvent(self, ev):
        if ev.position().x() > NAME_W:
            self.cursor = max(0, self.t_of(ev.position().x()))
            self.cursor_moved.emit(self.cursor)
            self.update()

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.LeftButton and ev.position().x() > NAME_W:
            self.cursor = max(0, self.t_of(ev.position().x()))
            self.update()

    def wheelEvent(self, ev):
        anchor = self.t_of(ev.position().x()) if ev.position().x() > NAME_W else self.t0
        self.zoom(1 / 1.25 if ev.angleDelta().y() > 0 else 1.25, anchor)

    def zoom(self, factor: float, anchor: int | None = None):
        anchor = self.t0 + self.visible_span() // 2 if anchor is None else anchor
        new = max(1_000, min(self.fs_per_px * factor, 10**13))
        self.t0 = max(0, int(anchor - (anchor - self.t0) * new / self.fs_per_px))
        self.fs_per_px = new
        self.update()
        self.parent_sync()

    def parent_sync(self):
        pass


class WaveformViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.reader: VcdReader | None = None
        self.selected: list[str] = []
        self.follow = True

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Signals"])
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.itemChanged.connect(self._item_changed)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter signals...")
        self.filter.textChanged.connect(self._apply_filter)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.filter)
        ll.addWidget(self.tree, 1)
        row = QHBoxLayout()
        b_all = QPushButton("Ports")
        b_all.setToolTip("Trace all top-level ports")
        b_all.clicked.connect(self.select_ports)
        b_none = QPushButton("None")
        b_none.clicked.connect(self.select_none)
        row.addWidget(b_all)
        row.addWidget(b_none)
        ll.addLayout(row)

        self.canvas = WaveCanvas()
        self.canvas.parent_sync = self._sync_scroll
        self.scroll = QScrollBar(Qt.Horizontal)
        self.scroll.valueChanged.connect(self._scrolled)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        tb = QHBoxLayout()
        self.info = QLabel("No waveform data. Program the virtual FPGA with recording enabled.")
        self.info.setObjectName("hint")
        tb.addWidget(self.info, 1)
        tb.addWidget(QLabel("Radix"))
        self.radix = QComboBox()
        self.radix.addItems(["hex", "dec", "sdec", "bin"])
        self.radix.currentTextChanged.connect(self._set_radix)
        tb.addWidget(self.radix)
        for text, tip, fn in (("Zoom +", "Zoom in", lambda: self.canvas.zoom(1 / 1.6)),
                              ("Zoom −", "Zoom out", lambda: self.canvas.zoom(1.6)),
                              ("Fit", "Show the whole recording", self.zoom_fit),
                              ("Export VCD...", "Save the recorded waveform as a VCD file", self.export_vcd)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            tb.addWidget(b)
        self.follow_cb = QCheckBox("Follow")
        self.follow_cb.setToolTip("Keep the latest simulation time in view")
        self.follow_cb.setChecked(True)
        self.follow_cb.toggled.connect(lambda v: setattr(self, "follow", v))
        tb.addWidget(self.follow_cb)
        rl.addLayout(tb)
        rl.addWidget(self.canvas, 1)
        rl.addWidget(self.scroll)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 900])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(split)

        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.refresh)

    # ------------------------------------------------------------- data
    def attach(self, vcd_path: Path | None, clock_period_ps: int = 10_000):
        self.reader = VcdReader(vcd_path) if vcd_path else None
        self.canvas.clock_period_fs = clock_period_ps * 1000
        self.tree.clear()
        self.canvas.signals = []
        self.canvas.cursor = None
        self.canvas.t0 = 0
        self.canvas.end_time = 0
        if self.reader:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()
        self.canvas.update()

    def detach(self):
        """Simulation ended: keep the data but stop polling."""
        if self.reader:
            self.reader.poll()
            self.refresh()
        self.timer.stop()

    def refresh(self):
        if not self.reader:
            return
        changed = self.reader.poll()
        if self.reader.header_done and self.tree.topLevelItemCount() == 0 and self.reader.by_name:
            self._build_tree()
        if not changed:
            return
        self.canvas.end_time = self.reader.end_time_fs
        if self.follow:
            span = self.canvas.visible_span()
            if self.canvas.end_time > self.canvas.t0 + span:
                self.canvas.t0 = max(0, self.canvas.end_time - int(span * 0.9))
        self._sync_scroll()
        try:
            size = self.reader.path.stat().st_size
        except OSError:
            size = 0
        self.info.setText(f"{len(self.reader.by_name)} signals · recorded to {fmt_time(self.canvas.end_time)} · "
                          f"VCD {size / 1e6:.1f} MB")
        self.canvas.update()

    def _build_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        scopes: dict[str, QTreeWidgetItem] = {}
        for sig in self.reader.all_signals():
            name = display_name(sig.name)
            scope = name.rsplit(".", 1)[0] if "." in name else ""
            parent = self.tree.invisibleRootItem()
            if scope:
                path = ""
                for part in scope.split("."):
                    path = f"{path}.{part}" if path else part
                    if path not in scopes:
                        it = QTreeWidgetItem([part])
                        it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
                        parent.addChild(it)
                        scopes[path] = it
                    parent = scopes[path]
            leaf = name.rsplit(".", 1)[-1] + (f" [{sig.width - 1}:0]" if sig.width > 1 else "")
            it = QTreeWidgetItem([leaf])
            it.setData(0, Qt.UserRole, sig.name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Unchecked)
            parent.addChild(it)
        self.tree.expandToDepth(0)
        self.tree.blockSignals(False)
        if self.selected:
            self._restore_selection()
        else:
            self.select_ports()

    def _items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, Qt.UserRole):
                yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def _restore_selection(self):
        self.tree.blockSignals(True)
        for it in self._items():
            it.setCheckState(0, Qt.Checked if it.data(0, Qt.UserRole) in self.selected else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_signals()

    def select_ports(self):
        if not self.reader:
            return
        names = [s.name for s in self.reader.all_signals()
                 if s.name.count(".") == HIDDEN_PREFIX.count(".") + 1]
        self.selected = names
        self._restore_selection()

    def select_none(self):
        self.selected = []
        self._restore_selection()

    def _item_changed(self, item, col):
        name = item.data(0, Qt.UserRole)
        if not name:
            return
        if item.checkState(0) == Qt.Checked and name not in self.selected:
            self.selected.append(name)
        elif item.checkState(0) != Qt.Checked and name in self.selected:
            self.selected.remove(name)
        self._update_signals()

    def _update_signals(self):
        if not self.reader:
            return
        self.canvas.signals = [self.reader.by_name[n] for n in self.selected if n in self.reader.by_name]
        self.canvas.update()

    def _apply_filter(self, text: str):
        text = text.lower().strip()
        for it in self._items():
            it.setHidden(bool(text) and text not in display_name(it.data(0, Qt.UserRole)).lower())

    def _set_radix(self, r: str):
        self.canvas.radix = r
        self.canvas.update()

    def zoom_fit(self):
        end = max(self.canvas.end_time, 1)
        self.canvas.t0 = 0
        self.canvas.fs_per_px = max(1_000, end / max(100, self.canvas.width() - NAME_W - 10))
        self._sync_scroll()
        self.canvas.update()

    def _sync_scroll(self):
        span = self.canvas.visible_span()
        total = max(self.canvas.end_time, self.canvas.t0 + span)
        # QScrollBar is int-based: scroll in units of 1/1000 of a span.
        unit = max(1, span // 1000)
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, max(0, (total - span) // unit))
        self.scroll.setPageStep(1000)
        self.scroll.setSingleStep(100)
        self.scroll.setValue(self.canvas.t0 // unit)
        self.scroll.blockSignals(False)
        self._unit = unit

    def _scrolled(self, v: int):
        self.canvas.t0 = v * getattr(self, "_unit", 1)
        at_end = v >= self.scroll.maximum()
        if not at_end and self.follow:
            self.follow_cb.setChecked(False)
        self.canvas.update()

    def export_vcd(self):
        if not self.reader or not self.reader.path.is_file():
            QMessageBox.information(self, "Export VCD", "There is no recorded waveform to export yet.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Export VCD", "waveform.vcd", "VCD files (*.vcd)")
        if dest:
            shutil.copyfile(self.reader.path, dest)
            self.info.setText(f"Exported to {dest}")
