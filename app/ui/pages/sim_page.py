"""Simulation page: clock control, the two kinds of reset, live I/O monitor and the pin view."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from app.project.project import ResetConfig
from app.simulation.controller import SPEED_LABELS
from app.ui import theme


def fmt_sim_time(ps: int) -> str:
    if ps >= 10**12:
        return f"{ps / 1e12:.6f} s"
    if ps >= 10**9:
        return f"{ps / 1e9:.4f} ms"
    if ps >= 10**6:
        return f"{ps / 1e6:.3f} µs"
    return f"{ps / 1e3:.3f} ns"


def hint(text: str) -> QLabel:
    l = QLabel(text)
    l.setWordWrap(True)
    l.setObjectName("hint")
    return l


class SimulationPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        vb = win.vb

        clk = QGroupBox("Clock")
        cf = QFormLayout(clk)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(vb.toggle_run)
        self.step_btn = QPushButton("Step")
        self.step_btn.clicked.connect(lambda: vb.step(self.step_cycles()))
        self.step_size = QComboBox()
        self.step_size.setEditable(True)
        self.step_size.addItems(["1", "10", "100", "1000", "10000", "100000", "1000000"])
        row.addWidget(self.run_btn)
        row.addWidget(self.step_btn)
        row.addWidget(QLabel("cycles"))
        row.addWidget(self.step_size)
        cf.addRow(row)
        self.speed = QComboBox()
        for k, label in SPEED_LABELS.items():
            self.speed.addItem(label, k)
        self.speed.addItem("Custom", "custom")
        self.speed.currentIndexChanged.connect(self._speed_changed)
        self.custom = QSpinBox()
        self.custom.setRange(1, 100_000_000)
        self.custom.setValue(5000)
        self.custom.setSuffix(" cycles/s")
        self.custom.valueChanged.connect(self._speed_changed)
        cf.addRow("Speed", self.speed)
        cf.addRow("Custom rate", self.custom)
        cf.addRow(hint("100 MHz clock in simulated time; speed = cycles per real second."))

        rst = QGroupBox("Reset")
        rf = QFormLayout(rst)
        self.restart_btn = QPushButton("Restart simulation")
        self.restart_btn.clicked.connect(vb.restart_simulation)
        rf.addRow(self.restart_btn)
        rf.addRow(hint("Restarts the design from its initial state; cycles and time return to 0."))
        self.reset_port = QComboBox()
        self.reset_level = QComboBox()
        self.reset_level.addItems(["active high", "active low"])
        self.reset_cycles = QSpinBox()
        self.reset_cycles.setRange(1, 10_000_000)
        self.reset_cycles.setValue(4)
        for w in (self.reset_port, self.reset_level, self.reset_cycles):
            (w.currentIndexChanged if hasattr(w, "currentIndexChanged") else w.valueChanged).connect(self._reset_edited)
        self.reset_port.currentIndexChanged.connect(self._port_picked)
        rf.addRow("Design reset input", self.reset_port)
        rf.addRow("Polarity", self.reset_level)
        rf.addRow("Hold for", self.reset_cycles)
        self.pulse_btn = QPushButton("Hardware reset (pulse input)")
        self.pulse_btn.clicked.connect(vb.hardware_reset)
        rf.addRow(self.pulse_btn)
        self.reset_note = hint("")
        rf.addRow(self.reset_note)

        opt = QGroupBox("Options")
        of = QFormLayout(opt)
        self.record = QCheckBox("Record waveforms (VCD)")
        self.record.setChecked(bool(win.settings.get("record_waveforms")))
        self.record.toggled.connect(win.set_recording)
        self.min_press = QSpinBox()
        self.min_press.setRange(0, 10_000_000)
        self.min_press.setSuffix(" cycles")
        self.min_press.setValue(win.settings.get("min_press_cycles"))
        self.min_press.valueChanged.connect(win.set_min_press)
        of.addRow(self.record)
        of.addRow("Minimum button press", self.min_press)

        self.io = QTableWidget(0, 5)
        self.io.setHorizontalHeaderLabels(["Port", "Dir", "Width", "Hex", "Binary"])
        self.io.verticalHeader().setVisible(False)
        self.io.setEditTriggers(QTableWidget.NoEditTriggers)
        self.io.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        iob = QGroupBox("I/O monitor (live top-level port values)")
        QVBoxLayout(iob).addWidget(self.io)
        self.pins = QTableWidget(0, 4)
        self.pins.setHorizontalHeaderLabels(["Port bit", "Package pin", "Board part", "Direction"])
        self.pins.verticalHeader().setVisible(False)
        self.pins.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pins.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        pb = QGroupBox("Pins (from Vivado's placement in the bitstream's routed checkpoint)")
        QVBoxLayout(pb).addWidget(self.pins)

        left = QVBoxLayout()
        for g in (clk, rst, opt):
            left.addWidget(g)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(440)
        split = QSplitter(Qt.Vertical)
        split.addWidget(iob)
        split.addWidget(pb)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(lw)
        lay.addWidget(split, 1)
        self._filling = False
        self.set_design(None)

    # ---------------------------------------------------------------- helpers
    def step_cycles(self) -> int:
        try:
            return max(1, int(self.step_size.currentText().replace(",", "").replace("_", "")))
        except ValueError:
            return 1

    def set_speed(self, mode: str):
        i = self.speed.findData(mode)
        if i >= 0:
            self.speed.blockSignals(True)
            self.speed.setCurrentIndex(i)
            self.speed.blockSignals(False)
        self.custom.setEnabled(mode == "custom")

    def _speed_changed(self, *_):
        mode = self.speed.currentData()
        self.custom.setEnabled(mode == "custom")
        self.win.set_speed(mode, self.custom.value())

    def set_state(self, st: str):
        from app.hardware.virtual_board import HwState
        active = st in HwState.ACTIVE
        self.run_btn.setText("Pause" if st == HwState.RUNNING else "Run")
        for b in (self.run_btn, self.step_btn, self.restart_btn):
            b.setEnabled(active)
        self.pulse_btn.setEnabled(active and bool(self.reset_port.currentData()))

    def set_design(self, info: dict | None, design=None, board=None):
        """Fill reset choices and the pin table for the programmed design (None = unprogrammed)."""
        self._filling = True
        self.reset_port.clear()
        self.reset_port.addItem("(none - use Restart simulation)", "")
        self.pins.setRowCount(0)
        self.io.setRowCount(0)
        self._last = None
        if info:
            for name, _hi in info["reset_candidates"]:
                self.reset_port.addItem(name, name)
            rc = info.get("reset")
            if rc and rc.port:
                self.reset_port.setCurrentIndex(max(0, self.reset_port.findData(rc.port)))
                self.reset_level.setCurrentIndex(0 if rc.active_high else 1)
                self.reset_cycles.setValue(rc.cycles)
            n = len(info["reset_candidates"])
            self.reset_note.setText(
                "Asserts the input for N cycles, then releases it." if n else
                "No 1-bit input to pulse in this design - use Restart simulation.")
        else:
            self.reset_note.setText("Program the FPGA to choose a reset input.")
        if design is not None and board is not None:
            rows = sorted(design.pins.items())
            self.pins.setRowCount(len(rows))
            for r, (key, pin) in enumerate(rows):
                res = board.by_pin(pin)
                part = res.id + f"  ({res.label})" if res else board.unsupported_pins.get(pin, "not on the board I/O map")
                for c, text in enumerate((key, pin, part, design.directions.get(key, ""))):
                    self.pins.setItem(r, c, QTableWidgetItem(text))
        self._filling = False
        self.pulse_btn.setEnabled(bool(self.reset_port.currentData()))

    def _port_picked(self, *_):
        if self._filling:
            return
        name = (self.reset_port.currentData() or "").lower()
        if name:
            from app.simulation.engine import BoardSimulator
            self.reset_level.blockSignals(True)
            self.reset_level.setCurrentIndex(1 if BoardSimulator.active_low_name(name) else 0)
            self.reset_level.blockSignals(False)
        self._reset_edited()

    def _reset_edited(self, *_):
        if self._filling:
            return
        port = self.reset_port.currentData() or ""
        rc = ResetConfig(port, self.reset_level.currentIndex() == 0, self.reset_cycles.value()) if port else None
        self.pulse_btn.setEnabled(bool(port))
        self.win.vb.configure_reset(rc)

    def showEvent(self, ev):
        super().showEvent(ev)
        if getattr(self, "_last", None) is not None:
            self.show_update(self._last)

    def remember(self, u):
        self._last = u

    def show_update(self, u):
        self._last = u
        self.io.setRowCount(len(u.ports))
        for r, (name, d, v, x, w) in enumerate(u.ports):
            bits = "".join("x" if (x >> b) & 1 else str((v >> b) & 1) for b in range(w - 1, -1, -1))
            hexs = "".join("x" if ((x >> (4 * k)) & 0xF) else f"{(v >> (4 * k)) & 0xF:x}"
                           for k in range((w + 3) // 4 - 1, -1, -1))
            for c, text in enumerate((name, d, str(w), hexs, bits)):
                item = self.io.item(r, c)
                if item is None:
                    item = QTableWidgetItem()
                    self.io.setItem(r, c, item)
                if item.text() != text:
                    item.setText(text)
                if c >= 3:
                    item.setForeground(QColor(theme.ERR) if "x" in bits else QColor(theme.TEXT))
