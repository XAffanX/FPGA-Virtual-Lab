"""Settings: tool locations, Vivado integration, defaults, shortcuts."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from app import APP_NAME, __version__
from app.ui import theme


def _path_row(edit: QLineEdit, browse, apply) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    for text, fn in (("Browse...", browse), ("Apply / detect", apply)):
        b = QPushButton(text)
        b.clicked.connect(fn)
        row.addWidget(b)
    return row


class SettingsPage(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        s = win.settings

        tools = QGroupBox("Tools")
        tf = QFormLayout(tools)
        self.vivado_dir = QLineEdit()
        self.vivado_dir.setPlaceholderText("Auto-detect (C:\\AMDDesignTools\\<version>\\Vivado, C:\\Xilinx\\Vivado\\<version>)")
        tf.addRow("Vivado folder", _path_row(self.vivado_dir, self._browse_vivado, self._apply_vivado))
        self.vivado_status = QLabel()
        self.vivado_status.setWordWrap(True)
        tf.addRow("", self.vivado_status)
        self.iverilog_dir = QLineEdit()
        self.iverilog_dir.setPlaceholderText("Auto-detect (bundled tools\\iverilog, PATH, C:\\iverilog)")
        tf.addRow("Simulation engine", _path_row(self.iverilog_dir, self._browse_iverilog, self._apply_iverilog))
        self.iverilog_status = QLabel()
        self.iverilog_status.setWordWrap(True)
        tf.addRow("", self.iverilog_status)

        viv = QGroupBox("Vivado integration")
        vf = QFormLayout(viv)
        self.xvc_port = QSpinBox()
        self.xvc_port.setRange(1024, 65535)
        self.xvc_port.valueChanged.connect(lambda v: s.set("xvc_port", v))
        vf.addRow("Virtual cable (XVC) port", self.xvc_port)
        self.vhost = QCheckBox("Start a virtual host (dedicated hw_server) - connect as 'Remote server'")
        self.vhost.toggled.connect(lambda v: s.set("virtual_host", v))
        vf.addRow(self.vhost)
        self.hw_port = QSpinBox()
        self.hw_port.setRange(1024, 65535)
        self.hw_port.valueChanged.connect(lambda v: s.set("hw_server_port", v))
        vf.addRow("Virtual host port", self.hw_port)
        self.xvc_auto = QCheckBox("Connect to Vivado automatically when the application starts")
        self.xvc_auto.toggled.connect(lambda v: s.set("xvc_autostart", v))
        vf.addRow(self.xvc_auto)

        d = QGroupBox("Simulation defaults")
        df = QFormLayout(d)
        self.max_vcd = QSpinBox()
        self.max_vcd.setRange(10, 10_000)
        self.max_vcd.setSuffix(" MB")
        self.max_vcd.valueChanged.connect(lambda v: s.set("max_vcd_mb", v))
        df.addRow("Stop waveform recording above", self.max_vcd)

        keys = QGroupBox("Keyboard shortcuts (click the board first for switch/button keys)")
        QVBoxLayout(keys).addWidget(QLabel(
            "<table cellspacing=4>"
            "<tr><td><b>Ctrl+Shift+P</b></td><td>Power ON / OFF</td></tr>"
            "<tr><td><b>Ctrl+O</b> / <b>F8</b></td><td>Load bitstream / Program virtual FPGA</td></tr>"
            "<tr><td><b>F5</b> / <b>F10</b></td><td>Run-Pause / Step clock</td></tr>"
            "<tr><td><b>Ctrl+R</b> / <b>Ctrl+Shift+R</b></td><td>Restart simulation / Hardware reset</td></tr>"
            "<tr><td><b>0-9, Q-Y</b></td><td>toggle SW0-SW9, SW10-SW15</td></tr>"
            "<tr><td><b>Arrows, Space</b></td><td>hold BTNU/BTND/BTNL/BTNR, BTNC</td></tr>"
            "<tr><td><b>Right-click / Shift+click</b></td><td>latch a pushbutton</td></tr></table>"))

        about = QLabel(f"<b>{APP_NAME} {__version__}</b> - a virtual Digilent Basys 3 for bitstreams built in "
                       "AMD Vivado. The design behind a .bit is taken from its routed checkpoint (exported by "
                       "Vivado) and simulated with Icarus Verilog using Xilinx's UNISIM primitive models.")
        about.setWordWrap(True)
        about.setObjectName("hint")

        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 12, 16, 12)
        t = QLabel("Settings")
        t.setObjectName("pageTitle")
        lay.addWidget(t)
        for w in (tools, viv, d, keys, about):
            lay.addWidget(w)
        lay.addStretch(1)

    def refresh(self):
        s, win = self.win.settings, self.win
        self.vivado_dir.setText(s.get("vivado_dir") or "")
        self.iverilog_dir.setText(s.get("iverilog_dir") or "")
        for w, key in ((self.xvc_auto, "xvc_autostart"), (self.vhost, "virtual_host")):
            w.blockSignals(True)
            w.setChecked(bool(s.get(key)))
            w.blockSignals(False)
        for w, key in ((self.xvc_port, "xvc_port"), (self.hw_port, "hw_server_port"), (self.max_vcd, "max_vcd_mb")):
            w.blockSignals(True)
            w.setValue(s.get(key))
            w.blockSignals(False)
        v = win.vb.vivado
        self.vivado_status.setText(f"Vivado {v.version} at {v.root}" if v else
                                   "Not found - needed to load .bit files")
        self.vivado_status.setStyleSheet(f"color:{theme.OK if v else theme.ERR}")
        tc = win.toolchain
        self.iverilog_status.setText(f"{win.toolchain_version} ({tc.bin_dir})" if tc else
                                     "Icarus Verilog not found - it runs the hardware simulation")
        self.iverilog_status.setStyleSheet(f"color:{theme.OK if tc else theme.ERR}")

    def _browse_vivado(self):
        d = QFileDialog.getExistingDirectory(self, "Vivado folder (containing bin\\vivado.bat)")
        if d:
            self.vivado_dir.setText(d)
            self._apply_vivado()

    def _apply_vivado(self):
        self.win.settings.set("vivado_dir", self.vivado_dir.text().strip())
        self.win.vb.detect_vivado()
        self.refresh()
        self.win.refresh_status()

    def _browse_iverilog(self):
        d = QFileDialog.getExistingDirectory(self, "Icarus Verilog folder (containing bin\\iverilog.exe)")
        if d:
            self.iverilog_dir.setText(d)
            self._apply_iverilog()

    def _apply_iverilog(self):
        self.win.settings.set("iverilog_dir", self.iverilog_dir.text().strip())
        self.win.detect_toolchain()
        self.refresh()
