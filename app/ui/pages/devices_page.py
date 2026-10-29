"""Devices page: USB-UART terminal, VGA screen, Pmod headers and (Nexys 4 DDR) on-board sensors."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPixmap, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from app.ui import theme

BAUDS = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]
PMOD_ORDER = [1, 2, 3, 4, 7, 8, 9, 10]


def hint(text: str) -> QLabel:
    l = QLabel(text)
    l.setWordWrap(True)
    l.setObjectName("hint")
    return l


class UartTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.status = hint("")
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        f = QFont("Consolas", 10)
        f.setStyleHint(QFont.Monospace)
        self.out.setFont(f)
        self.out.setMaximumBlockCount(5000)
        top = QHBoxLayout()
        top.addWidget(QLabel("Baud"))
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItems(BAUDS)
        self.baud.setCurrentText(str(win.settings.get("uart_baud")))
        self.baud.currentTextChanged.connect(self._baud)
        top.addWidget(self.baud)
        top.addWidget(QLabel("8N1"))
        self.hex = QCheckBox("Hex view")
        top.addWidget(self.hex)
        top.addStretch(1)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.out.clear)
        top.addWidget(clear)
        row = QHBoxLayout()
        self.line = QLineEdit()
        self.line.setPlaceholderText("Type text to send to the FPGA (Enter to send)")
        self.line.returnPressed.connect(self.send)
        self.ending = QComboBox()
        self.ending.addItems(["No line ending", "LF (\\n)", "CR (\\r)", "CR+LF"])
        self.ending.setCurrentIndex(3)
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.line, 1)
        row.addWidget(self.ending)
        row.addWidget(self.send_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addLayout(top)
        lay.addWidget(self.out, 1)
        lay.addLayout(row)
        self.set_design(None)

    def _baud(self, text: str):
        try:
            baud = int(text.strip())
        except ValueError:
            return
        if 300 <= baud <= 12_500_000:
            self.win.vb.set_peripheral("baud", baud)

    def set_design(self, info):
        self.baud.blockSignals(True)
        self.baud.setCurrentText(str(self.win.settings.get("uart_baud")))
        self.baud.blockSignals(False)
        if not info:
            self.status.setText("Program the FPGA to use the USB-UART. The terminal connects to the design's "
                                "UART pins (Basys 3: RsRx/RsTx, Nexys 4 DDR: UART_TXD_IN/UART_RXD_OUT).")
            self.send_btn.setEnabled(False)
            return
        if not info.get("uart"):
            self.status.setText("This design does not use the USB-UART pins.")
        else:
            self.status.setText("Connected to the design's USB-UART. Set the baud rate your design uses "
                                "(clock 100 MHz). Characters are exchanged while the clock runs.")
        self.send_btn.setEnabled(bool(info.get("uart_rx")))

    def send(self):
        text = self.line.text()
        data = text.encode("utf-8") + [b"", b"\n", b"\r", b"\r\n"][self.ending.currentIndex()]
        if data:
            self.win.vb.uart_send(data)
            self.line.clear()

    def received(self, data: bytes):
        if not data:
            return
        if self.hex.isChecked():
            text = " ".join(f"{b:02X}" for b in data) + " "
        else:
            text = data.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")
        self.out.moveCursor(QTextCursor.End)
        self.out.insertPlainText(text)
        self.out.moveCursor(QTextCursor.End)


class VgaTab(QWidget):
    def __init__(self):
        super().__init__()
        self.status = hint("")
        self.screen = QLabel()
        self.screen.setAlignment(Qt.AlignCenter)
        self.screen.setMinimumSize(320, 240)
        self.screen.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.screen.setStyleSheet("background:#000; border:1px solid #333;")
        self._img: QImage | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(self.screen, 1)
        lay.addWidget(hint("The picture builds up line by line at simulation speed - a full 640x480 frame is "
                           "1.68 million clock cycles. Use Maximum speed."))
        self.set_design(None)

    def set_design(self, info):
        self._img = None
        self.screen.clear()
        if not info:
            self.status.setText("Program the FPGA to see its VGA output (640x480 @ 60 Hz, 12-bit colour).")
        elif not info.get("vga"):
            self.status.setText("This design does not use the VGA pins.")
            self.screen.setText("no VGA output")
        else:
            self.status.setText("Waiting for sync...")
            self.screen.setText("no signal")

    def frame(self, data: bytes | None, text: str):
        if text:
            self.status.setText(text)
        if data:
            self._img = QImage(data, 640, 480, 640 * 3, QImage.Format_RGB888).copy()
            self._show()

    def _show(self):
        if self._img is not None:
            self.screen.setPixmap(QPixmap.fromImage(self._img).scaled(self.screen.size(), Qt.KeepAspectRatio,
                                                                     Qt.SmoothTransformation))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._show()


class PmodTab(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.body = QWidget()
        self.setWidget(self.body)
        self.lay = QVBoxLayout(self.body)
        self.pins: dict[str, QPushButton] = {}
        self.inputs: set[str] = set()
        self.used: set[str] = set()
        self.levels: dict[str, int] = {}

    def set_board(self, board):
        for i in reversed(range(self.lay.count())):
            w = self.lay.itemAt(i).widget()
            if w is not None:
                w.setParent(None)
        self.pins.clear()
        self.lay.addWidget(hint("Pmod header pins. Pins the design drives show their level (green = 1); "
                                "pins the design reads are buttons you click to toggle (blue = 1)."))
        headers: dict[str, list] = {}
        for r in board.of_kind("pmod"):
            hdr = r.id.rstrip("0123456789")
            headers.setdefault(hdr, []).append(r)
        for hdr, res in headers.items():
            box = QGroupBox(f"Pmod {hdr}")
            g = QGridLayout(box)
            byn = {int(r.id[len(hdr):]): r for r in res}
            for col, (top, bottom) in enumerate(zip((1, 2, 3, 4, 5, 6), (7, 8, 9, 10, 11, 12))):
                for row, n in ((0, top), (1, bottom)):
                    if n in byn:
                        r = byn[n]
                        b = QPushButton(f"{n}\n{r.pin}")
                        b.setFixedSize(64, 44)
                        b.setToolTip(f"{r.id} - package pin {r.pin}")
                        b.clicked.connect(lambda _c=False, rid=r.id: self._toggle(rid))
                        self.pins[r.id] = b
                        g.addWidget(b, row, col)
                    else:
                        lab = QLabel("GND" if n in (5, 11) else "3V3")
                        lab.setAlignment(Qt.AlignCenter)
                        lab.setFixedWidth(48)
                        lab.setObjectName("hint")
                        g.addWidget(lab, row, col)
            g.setColumnStretch(6, 1)                 # keep the header compact, like the real connector
            self.lay.addWidget(box)
        self.lay.addStretch(1)
        self.set_design(None)

    def set_design(self, info):
        self.used = set(info.get("pmods", [])) if info else set()
        self.inputs = set(info.get("pmod_inputs", [])) if info else set()
        self.levels = {}
        self._paint({})

    def _toggle(self, rid: str):
        if rid in self.inputs:
            self.win.vb.set_input(rid, 0 if self.levels.get(rid) else 1)

    def update_levels(self, extra: dict, inputs: dict):
        self.levels = {rid: inputs.get(rid, 0) for rid in self.inputs}
        self._paint(extra)

    def _paint(self, extra: dict):
        for rid, b in self.pins.items():
            if rid in self.inputs:
                on = self.levels.get(rid, 0)
                b.setEnabled(True)
                b.setStyleSheet(f"background:{'#1f5fbf' if on else theme.BG2}; border:1px solid {theme.ACCENT};")
            elif rid in self.used:
                v = extra.get(rid)
                col = "#555" if v is None else QColor(int(40 + 20 * v), int(44 + 186 * v), int(52 + 30 * v)).name()
                b.setEnabled(False)
                b.setStyleSheet(f"background:{col}; color:white; border:1px solid #3ddc84;")
            else:
                b.setEnabled(False)
                b.setStyleSheet("")


class SensorsTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        s = win.settings
        self.status = hint("")
        tbox = QGroupBox("ADT7420 temperature sensor (I2C, address 0x4B)")
        tf = QFormLayout(tbox)
        self.temp = QSlider(Qt.Horizontal)
        self.temp.setRange(-400, 1250)         # 0.1 degC steps
        self.temp.setValue(int(float(s.get("temperature_c")) * 10))
        self.temp_lbl = QLabel()
        self.temp.valueChanged.connect(self._temp)
        tf.addRow("Temperature", self.temp)
        tf.addRow("", self.temp_lbl)
        abox = QGroupBox("ADXL362 accelerometer (SPI) - values in mg (1000 = 1 g)")
        af = QFormLayout(abox)
        self.acc = []
        for axis, key in (("X", "accel_x"), ("Y", "accel_y"), ("Z", "accel_z")):
            sp = QSpinBox()
            sp.setRange(-2000, 2000)
            sp.setSingleStep(50)
            sp.setValue(int(s.get(key)))
            sp.setSuffix(" mg")
            sp.valueChanged.connect(self._acc)
            self.acc.append(sp)
            af.addRow(axis, sp)
        pre = QHBoxLayout()
        for name, v in (("Flat", (0, 0, 1000)), ("Tilt left", (-700, 0, 700)), ("Tilt right", (700, 0, 700)),
                        ("Upside down", (0, 0, -1000)), ("Free fall", (0, 0, 0))):
            b = QPushButton(name)
            b.clicked.connect(lambda _c=False, v=v: self._preset(v))
            pre.addWidget(b)
        af.addRow(pre)
        mbox = QGroupBox("PDM microphone")
        mf = QFormLayout(mbox)
        self.amp = QSlider(Qt.Horizontal)
        self.amp.setRange(0, 100)
        self.amp.setValue(int(s.get("mic_amp")) // 10)
        self.freq = QSpinBox()
        self.freq.setRange(20, 20000)
        self.freq.setValue(int(s.get("mic_freq")))
        self.freq.setSuffix(" Hz")
        self.amp_lbl = QLabel()
        self.amp.valueChanged.connect(self._mic)
        self.freq.valueChanged.connect(self._mic)
        mf.addRow("Tone level", self.amp)
        mf.addRow("", self.amp_lbl)
        mf.addRow("Tone frequency", self.freq)
        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(tbox)
        lay.addWidget(abox)
        lay.addWidget(mbox)
        lay.addStretch(1)
        self._temp(self.temp.value(), send=False)
        self._mic(send=False)

    def set_design(self, info):
        if not info:
            self.status.setText("Program the FPGA to use the on-board sensors. Values set here are applied "
                                "when it is programmed.")
            return
        sens = info.get("sensors", {})
        used = [n for n, k in (("temperature sensor", "temp"), ("accelerometer", "acl"), ("microphone", "mic"))
                if sens.get(k)]
        self.status.setText("Design uses: " + (", ".join(used) if used else "none of the on-board sensors"))

    def _temp(self, v, send=True):
        c = v / 10.0
        self.temp_lbl.setText(f"{c:.1f} °C  (register 0x{(int(round(c * 16)) & 0x1FFF) << 3:04X})")
        if send:
            self.win.vb.set_peripheral("temp", c)

    def _acc(self, *_):
        self.win.vb.set_peripheral("accel", tuple(sp.value() for sp in self.acc))

    def _preset(self, v):
        for sp, x in zip(self.acc, v):
            sp.blockSignals(True)
            sp.setValue(x)
            sp.blockSignals(False)
        self._acc()

    def _mic(self, *_, send=True):
        self.amp_lbl.setText(f"{self.amp.value()} % of full scale")
        if send:
            self.win.vb.set_peripheral("mic", (self.amp.value() * 10, self.freq.value()))


class DevicesPage(QTabWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.uart = UartTab(win)
        self.vga = VgaTab()
        self.pmod = PmodTab(win)
        self.sensors = SensorsTab(win)
        self.addTab(self.uart, "UART Terminal")
        self.addTab(self.vga, "VGA Screen")
        self.addTab(self.pmod, "Pmod")
        self.addTab(self.sensors, "Sensors")

    def set_board(self, board):
        self.pmod.set_board(board)
        self.setTabVisible(3, board.id == "nexys4ddr")

    def set_design(self, info):
        self.uart.set_design(info)
        self.vga.set_design(info)
        self.pmod.set_design(info)
        self.sensors.set_design(info)

    def show_update(self, u):
        self.uart.received(u.uart_rx)
        self.vga.frame(u.vga, u.vga_text)
        self.pmod.update_levels(u.extra, u.inputs)
