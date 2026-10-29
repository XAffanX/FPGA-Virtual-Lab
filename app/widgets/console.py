"""Bottom console: compilation/simulation logs, warnings, errors and design $display output."""
from __future__ import annotations

import html
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from app.ui import theme

COLORS = {"error": theme.ERR, "warning": theme.WARN, "success": theme.OK, "info": theme.TEXT,
          "design": "#9cdcfe", "cmd": theme.MUTED}
PREFIX = {"error": "ERROR", "warning": "WARN ", "success": "OK   ", "info": "INFO ", "design": "HDL  ",
          "cmd": "CMD  "}
MAX_LINES = 5000


class Console(QWidget):
    location_clicked = Signal(str, int)     # file, line

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._anchor)
        self.view.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace; font-size: 9pt;")
        self.entries: list[tuple[str, str, str]] = []
        self.filters = {k: True for k in COLORS}
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        for key, label in (("error", "Errors"), ("warning", "Warnings"), ("info", "Info"),
                           ("design", "Design output")):
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.toggled.connect(lambda on, k=key: self._set_filter(k, on))
            bar.addWidget(cb)
        bar.addStretch(1)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        bar.addWidget(clear)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)

    def _set_filter(self, key, on):
        self.filters[key] = on
        if key == "info":
            self.filters["success"] = on
            self.filters["cmd"] = on
        self._rerender()

    def _fmt(self, ts: str, level: str, text: str, file: str = "", line: int = 0) -> str:
        msg = html.escape(text)
        if file:
            msg = f'<a href="loc:{line}:{html.escape(file)}" style="color:{COLORS[level]}">{msg}</a>'
        return (f'<span style="color:{theme.MUTED}">{ts}</span> '
                f'<span style="color:{COLORS.get(level, theme.TEXT)}">{PREFIX.get(level, "     ")} {msg}</span>')

    def log(self, level: str, text: str, file: str = "", line: int = 0):
        ts = time.strftime("%H:%M:%S")
        for part in str(text).splitlines() or [""]:
            self.entries.append((ts, level, part, file, line))
            if self.filters.get(level, True):
                self.view.append(self._fmt(ts, level, part, file, line))
        if len(self.entries) > MAX_LINES:
            self.entries = self.entries[-MAX_LINES // 2:]
            self._rerender()
        self.view.moveCursor(QTextCursor.End)

    def _rerender(self):
        self.view.clear()
        self.view.setHtml("<br>".join(self._fmt(*e) for e in self.entries if self.filters.get(e[1], True)))
        self.view.moveCursor(QTextCursor.End)

    def clear(self):
        self.entries.clear()
        self.view.clear()

    def _anchor(self, url):
        s = url.toString()
        if s.startswith("loc:"):
            _, line, file = s.split(":", 2)
            self.location_clicked.emit(file, int(line))
