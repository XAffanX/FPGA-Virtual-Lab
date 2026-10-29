"""Right-hand hardware status panel and the Connect-to-Vivado dialog (Tcl for Vivado's Tcl Console)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from app.ui import theme


def vivado_tcl(board, *, host_port: int | None, xvc_port: int, bitstream: Path | None) -> str:
    """Tcl that connects Vivado's Hardware Manager to the virtual board (and programs `bitstream`).

    host_port: the virtual host (dedicated hw_server) - connect as a remote server.
    None: real USB board present / virtual host off - add the virtual cable to Vivado's local server.
    """
    dev = board.fpga_short.lower()
    lines = [f"# FPGA Virtual Lab - {board.virtual_name} ({board.name}, {board.fpga_short})",
             "# Paste into Vivado's Tcl Console (Window > Tcl Console) and press Enter.",
             "open_hw_manager"]
    if host_port:
        lines += ["# Vivado keeps one hardware-server connection at a time: leave the local one first.",
                  "catch {close_hw_target}",
                  "catch {disconnect_hw_server}",
                  f"connect_hw_server -url 127.0.0.1:{host_port} -allow_non_jtag",
                  "open_hw_target [lindex [get_hw_targets] 0]"]
    else:
        lines += ["# Uses Vivado's own local server, next to any real board you have plugged in.",
                  "catch {connect_hw_server -allow_non_jtag}",
                  f"if {{[catch {{open_hw_target -xvc_url localhost:{xvc_port}}}]}} {{",
                  "    # hw_server needs ~2 s to scan a new cable: close the half-open target and retry",
                  "    for {set i 0} {$i < 3} {incr i} {",
                  "        catch {close_hw_target}",
                  "        after 3000",
                  f"        if {{![catch {{open_hw_target [lindex [get_hw_targets *{xvc_port}*] 0]}}]}} break",
                  "    }",
                  "}"]
    lines.append(f"current_hw_device [lindex [get_hw_devices -of_objects [current_hw_target] {dev}*] 0]")
    lines.append(f'puts "Connected to {board.virtual_name}: [current_hw_device]"')
    if bitstream is not None:
        lines += [f"set_property PROGRAM.FILE {{{Path(bitstream).as_posix()}}} [current_hw_device]",
                  "program_hw_devices [current_hw_device]"]
    else:
        lines += ["# Program: right-click the device > Program Device, or:",
                  "# set_property PROGRAM.FILE {C:/path/to/project.runs/impl_1/top.bit} [current_hw_device]",
                  "# program_hw_devices [current_hw_device]"]
    return "\n".join(lines) + "\n"


class StatusPanel(QFrame):
    ROWS = [("board", "Board"), ("fpga", "FPGA"), ("power", "Power"), ("config", "Configuration"),
            ("bitstream", "Bitstream"), ("sim", "Simulation"), ("cycles", "Clock cycles"),
            ("time", "Simulated time"), ("rate", "Speed"), ("timing", "Timing"), ("hm", "Vivado")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFixedWidth(340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        self.big = QLabel("POWER OFF")
        self.big.setAlignment(Qt.AlignCenter)
        self.big.setStyleSheet("font-size:15pt; font-weight:700; padding:6px; border-radius:6px;")
        lay.addWidget(self.big)
        grid = QGridLayout()
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(1, 1)
        self.values: dict[str, QLabel] = {}
        for r, (key, title) in enumerate(self.ROWS):
            t = QLabel(title)
            t.setObjectName("hint")
            v = QLabel("-")
            v.setWordWrap(False)       # one line per value; full text in the tooltip
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(t, r, 0, Qt.AlignTop)
            grid.addWidget(v, r, 1)
            self.values[key] = v
        lay.addLayout(grid)
        lay.addStretch(1)

    def set(self, key: str, text: str, color: str | None = None, tip: str = ""):
        lbl = self.values[key]
        lbl.setText(text)
        lbl.setToolTip(tip or text)
        lbl.setStyleSheet(f"color:{color};" if color else "")

    def set_headline(self, text: str, color: str):
        self.big.setText(text)
        self.big.setStyleSheet(f"font-size:15pt; font-weight:700; padding:6px; border-radius:6px; "
                               f"color:{color}; border:1px solid {color};")

    def set_board(self, board):
        self.set("board", board.virtual_name, tip=board.name)
        self.set("fpga", board.fpga.replace("Xilinx Artix-7 ", ""), tip=board.fpga)


class ConnectVivadoDialog(QDialog):
    """Shown by 'Connect to Vivado': the Tcl to paste, plus the state of the virtual board."""
    power_requested = Signal()
    stop_requested = Signal()

    def __init__(self, board, *, powered: bool, host_port: int | None, xvc_port: int, usb_note: str,
                 bitstream: Path | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Connect Vivado to {board.virtual_name}")
        lay = QVBoxLayout(self)
        if host_port:
            how = (f"<span style='color:{theme.OK}'><b>Ready.</b> {board.virtual_name} is served at "
                   f"<code>127.0.0.1:{host_port}</code> (virtual host).</span> Paste this into Vivado's Tcl Console. "
                   f"<i>GUI alternative:</i> close Vivado's <b>localhost</b> server, then Open New Target &gt; "
                   f"Remote server, host <code>127.0.0.1</code>, port <code>{host_port}</code>.")
        else:
            how = (f"<span style='color:{theme.OK}'><b>Ready.</b> {board.virtual_name} is on the virtual cable "
                   f"<code>localhost:{xvc_port}</code>.</span> {usb_note} Paste this into Vivado's Tcl Console.")
        if not powered:
            how += (f"<br><span style='color:{theme.WARN}'>The board is powered OFF - Vivado will see no device "
                    "until you switch it on.</span>")
        info = QLabel(how)
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        lay.addWidget(info)
        self.tcl = QPlainTextEdit(vivado_tcl(board, host_port=host_port, xvc_port=xvc_port, bitstream=bitstream))
        self.tcl.setReadOnly(True)
        self.tcl.setStyleSheet("font-family: Consolas, monospace; font-size: 9.5pt;")
        self.tcl.setMinimumHeight(260)
        lay.addWidget(self.tcl, 1)
        note = QLabel("Vivado can program any .bit for this board that still sits in its Vivado run folder next to "
                      "its <code>_routed.dcp</code>; it does not have to be loaded here first. Check that the "
                      "target is the virtual one before programming if a real board is also connected.")
        note.setWordWrap(True)
        note.setObjectName("hint")
        lay.addWidget(note)
        row = QHBoxLayout()
        copy = QPushButton("Copy Tcl")
        copy.setObjectName("primary")
        copy.clicked.connect(self._copy)
        row.addWidget(copy)
        if not powered:
            on = QPushButton("Power ON board")
            on.clicked.connect(lambda: (self.power_requested.emit(), on.setEnabled(False)))
            row.addWidget(on)
        row.addStretch(1)
        stop = QPushButton("Disconnect (stop server)")
        stop.clicked.connect(lambda: (self.stop_requested.emit(), self.accept()))
        row.addWidget(stop)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.resize(780, 520)

    def _copy(self):
        QGuiApplication.clipboard().setText(self.tcl.toPlainText())
        self.setWindowTitle(self.windowTitle().split("  ·")[0] + "  ·  copied")


def board_page(board_widget: QWidget, panel: StatusPanel) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 8, 8)
    row.addWidget(board_widget, 1)
    row.addWidget(panel)
    return w
