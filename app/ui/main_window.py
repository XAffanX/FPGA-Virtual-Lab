"""Main window: virtual Digilent boards (virtual_basys3, virtual_nexys4ddr) that work alongside AMD Vivado."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QStackedWidget, QToolBar, QToolButton, QWidget)

from app import APP_NAME, __version__
from app.board.board import available_boards, get_board
from app.hardware.virtual_board import HwState, VirtualBoard
from app.ui import theme
from app.ui.pages.settings_page import SettingsPage
from app.ui.pages.sim_page import SimulationPage, fmt_sim_time
from app.ui.status_panel import ConnectVivadoDialog, StatusPanel, board_page
from app.utils.paths import resource_root, user_data_dir
from app.utils.settings import AppSettings
from app.utils.toolchain import try_find_toolchain
from app.vivado.vivado import mapping_from_pins
from app.widgets.board_view import BoardWidget
from app.widgets.console import Console
from app.widgets.waveform_view import WaveformViewer

PAGES = [("board", "▦   FPGA Board"), ("sim", "▶   Simulation"), ("waves", "∿   Waveforms"),
         ("console", "▤   Console"), ("settings", "⚙   Settings")]
STATE_COLOR = {HwState.POWER_OFF: theme.MUTED, HwState.UNPROGRAMMED: theme.WARN, HwState.PROGRAMMING: theme.ACCENT,
               HwState.PROGRAMMED: theme.ACCENT, HwState.RUNNING: theme.OK, HwState.PAUSED: theme.WARN}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = AppSettings()
        board_id = self.settings.get("board")
        try:
            self.board = get_board(board_id)
        except (OSError, KeyError, ValueError):
            self.board = get_board("basys3")
        self.toolchain = None
        self.toolchain_version = ""
        self._vcd_check = 0.0
        self._ticks = 0
        self.last_cycles = 0
        self._quiet_errors = False
        self._xvc_status = "Off"
        self.setWindowTitle(APP_NAME)
        icon = resource_root() / "assets" / "icons" / "app.png"
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1500, 940)

        self.vb = VirtualBoard(self.board, self.settings, lambda: self.toolchain, self)
        self.vb.state_changed.connect(self._on_state)
        self.vb.update.connect(self._on_update)
        self.vb.log.connect(self.log)
        self.vb.design_changed.connect(self._on_design)
        self.vb.bitstream_changed.connect(lambda _i: self.refresh_status())
        self.vb.program_failed.connect(self._on_program_failed)
        self.vb.board_changed.connect(self._on_board_changed)
        self.vb.wrong_board.connect(self._on_wrong_board)
        self.vb.xvc_changed.connect(lambda s: (setattr(self, "_xvc_status", s), self.refresh_status()))

        self._build_ui()
        self.vb.set_input_source(lambda: {**self.board_widget.switch_states(), **self.board_widget.button_states()})
        self.detect_toolchain()
        if self.vb.vivado:
            self.log("info", f"Vivado {self.vb.vivado.version} found at {self.vb.vivado.root}.")
        else:
            self.log("warning", "Vivado was not found: it is needed to load .bit files (Settings > Vivado folder).")
        self.set_min_press(self.settings.get("min_press_cycles"))
        self.set_speed(self.settings.get("speed"))
        last = self.settings.get("last_bitstream")
        if last and Path(last).is_file():
            self.vb.select_bitstream(Path(last), quiet_mismatch=True)
        if self.settings.get("xvc_autostart"):
            self.start_vivado_server()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._on_state(self.vb.state)
        self.log("info", f"{APP_NAME} {__version__} ready: {self.board.virtual_name} is powered OFF - switch it "
                         "on to begin.")

    # ================================================================== UI construction
    def _build_ui(self):
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(180)
        for key, label in PAGES:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            self.sidebar.addItem(it)
        self.sidebar.currentRowChanged.connect(self._page_changed)

        self.board_widget = BoardWidget(self.board)
        self.board_widget.input_changed.connect(self.vb.set_input)
        self.board_widget.power_clicked.connect(self.vb.toggle_power)
        self.board_widget.prog_clicked.connect(lambda: self.vb.clear_configuration("PROG button"))
        self.panel = StatusPanel()
        self.panel.set_board(self.board)
        self.sim_page = SimulationPage(self)
        self.waves = WaveformViewer()
        self.console_page = Console()
        self.settings_page = SettingsPage(self)
        self.pages = {"board": board_page(self.board_widget, self.panel), "sim": self.sim_page,
                      "waves": self.waves, "console": self.console_page, "settings": self.settings_page}
        self.stack = QStackedWidget()
        for key, _ in PAGES:
            self.stack.addWidget(self.pages[key])
        central = QWidget()
        central.setObjectName("central")
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.sidebar)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.console = Console()
        dock = QDockWidget("Console", self)
        dock.setObjectName("consoleDock")
        dock.setWidget(self.console)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [170], Qt.Vertical)
        self._build_toolbar()
        self._build_statusbar()
        self.sidebar.setCurrentRow(0)

    def _action(self, text, tip, slot, shortcut=None, checkable=False):
        a = QAction(text, self)
        a.setToolTip(tip + (f"  ({QKeySequence(shortcut).toString()})" if shortcut else ""))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        return a

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        A = self._action
        tb.addWidget(QLabel(" Board "))
        self.board_combo = QComboBox()
        for b in available_boards():
            self.board_combo.addItem(f"{b.virtual_name}  ({b.name})", b.id)
        self.board_combo.setCurrentIndex(max(0, self.board_combo.findData(self.board.id)))
        self.board_combo.setToolTip("Which virtual board is on the desk")
        self.board_combo.currentIndexChanged.connect(lambda _i: self.switch_board(self.board_combo.currentData()))
        tb.addWidget(self.board_combo)
        self.act_power = A("⏻ Power ON", "Switch the virtual board on or off", self.vb.toggle_power,
                           "Ctrl+Shift+P", checkable=True)
        tb.addAction(self.act_power)
        tb.addSeparator()
        self.act_vivado = A("Connect to Vivado", "Make this board available to Vivado's Hardware Manager and show "
                            "the Tcl to paste into Vivado", self.connect_vivado, "Ctrl+K")
        tb.addAction(self.act_vivado)
        self.act_load = A("Load Bitstream", "Select a Vivado .bit file", lambda: self.load_bitstream(), "Ctrl+O")
        self.act_program = A("⚡ Program Virtual FPGA", "Configure the virtual FPGA with the selected bitstream",
                             lambda: self.vb.program(), "F8")
        tb.addAction(self.act_load)
        tb.addAction(self.act_program)
        tb.addSeparator()
        self.act_run = A("▶ Run", "Run / pause the 100 MHz clock", self.vb.toggle_run, "F5")
        self.act_step = A("⏭ Step", "Advance the clock (step size on the Simulation page)",
                          lambda: self.vb.step(self.sim_page.step_cycles()), "F10")
        tb.addAction(self.act_run)
        tb.addAction(self.act_step)
        self.reset_menu = QMenu(self)
        self.act_restart = self.reset_menu.addAction("Restart simulation (initial state, cycle 0)",
                                                     self.vb.restart_simulation)
        self.act_restart.setShortcut(QKeySequence("Ctrl+R"))
        self.act_hwreset = self.reset_menu.addAction("Hardware reset (pulse the design's reset input)",
                                                     self.vb.hardware_reset)
        self.act_hwreset.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.reset_menu.addAction("Configure reset input...", lambda: self.goto("sim"))
        self.addActions([self.act_restart, self.act_hwreset])
        self.reset_btn = QToolButton()
        self.reset_btn.setText("⟲ Reset")
        self.reset_btn.setToolTip("Click: restart the simulation. Arrow: hardware reset of the design's reset input.")
        self.reset_btn.setMenu(self.reset_menu)
        self.reset_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.reset_btn.clicked.connect(self.vb.restart_simulation)
        tb.addWidget(self.reset_btn)
        tb.addSeparator()
        tb.addWidget(QLabel(" Speed "))
        self.speed_combo = QComboBox()
        for key, label in (("slow", "Slow"), ("normal", "Normal"), ("fast", "Fast"), ("max", "Maximum")):
            self.speed_combo.addItem(label, key)
        self.speed_combo.currentIndexChanged.connect(lambda _i: self.set_speed(self.speed_combo.currentData()))
        tb.addWidget(self.speed_combo)
        tb.addSeparator()
        tb.addAction(A("⚙ Settings", "Settings", lambda: self.goto("settings")))

    def _build_statusbar(self):
        sb = self.statusBar()
        self.st_state = QLabel()
        self.st_cycles = QLabel("Cycles: 0")
        self.st_time = QLabel("Sim time: 0 ns")
        self.st_bit = QLabel("")
        self.st_bit.setObjectName("hint")
        for w in (self.st_state, self.st_cycles, self.st_time):
            sb.addWidget(w)
        sb.addPermanentWidget(self.st_bit)

    def goto(self, key: str):
        self.sidebar.setCurrentRow([k for k, _ in PAGES].index(key))

    def _page_changed(self, row: int):
        key = PAGES[row][0]
        self.stack.setCurrentIndex(row)
        if key == "settings":
            self.settings_page.refresh()
        if key == "board":
            self.board_widget.view.setFocus()

    # ================================================================== logging & tools
    def log(self, level: str, text: str, file: str = "", line: int = 0):
        self.console.log(level, text)
        self.console_page.log(level, text)

    def detect_toolchain(self):
        self.toolchain = try_find_toolchain(self.settings.get("iverilog_dir") or None)
        self.toolchain_version = self.toolchain.version() if self.toolchain else ""
        if not self.toolchain:
            self.log("error", "The simulation engine (Icarus Verilog) was not found. Set it in Settings.")

    def _tick(self):
        self._ticks += 1
        if self._ticks % 5 == 0:
            self.vb.check_usb_conflict()          # every ~5 s while the virtual host runs

    # ================================================================== boards
    def switch_board(self, board_id: str, ask: bool = True) -> bool:
        if board_id == self.board.id:
            return False
        if ask and self.vb.configured and self.isVisible() and not self._quiet_errors:
            r = QMessageBox.question(self, "Change board", f"{self.board.virtual_name} is running a design. "
                                     "Changing the board switches it off and discards the configuration. Continue?")
            if r != QMessageBox.Yes:
                self.board_combo.blockSignals(True)
                self.board_combo.setCurrentIndex(self.board_combo.findData(self.board.id))
                self.board_combo.blockSignals(False)
                return False
        return self.vb.switch_board(get_board(board_id))

    def _on_board_changed(self, board):
        self.board = board
        self.settings.set("board", board.id)
        self.board_widget.set_board(board)
        self.panel.set_board(board)
        self.sim_page.set_design(None)
        self.waves.attach(None)
        self.board_combo.blockSignals(True)
        self.board_combo.setCurrentIndex(self.board_combo.findData(board.id))
        self.board_combo.blockSignals(False)
        self._on_state(self.vb.state)

    def _on_wrong_board(self, path: Path, board):
        if self._quiet_errors or not self.isVisible():
            return
        r = QMessageBox.question(self, "Bitstream for another board",
                                 f"{Path(path).name} was built for the {board.name} ({board.fpga_short}).\n\n"
                                 f"Switch to {board.virtual_name} and select it?")
        if r == QMessageBox.Yes and (self.board.id == board.id or self.switch_board(board.id, ask=False)):
            self.load_bitstream(Path(path))

    # ================================================================== bitstreams
    def load_bitstream(self, path: Path | None = None):
        if path is None:
            start = str(Path(self.settings.get("last_bitstream") or Path.home()).parent)
            f, _ = QFileDialog.getOpenFileName(self, "Load Vivado bitstream", start, "Xilinx bitstream (*.bit)")
            if not f:
                return
            path = Path(f)
        info = self.vb.select_bitstream(path)
        if info and self.vb.powered and self.vb.state != HwState.PROGRAMMING and not self._quiet_errors:
            r = QMessageBox.question(self, "Program virtual FPGA",
                                     f"Program {self.board.virtual_name} with {info.path.name} now?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r == QMessageBox.Yes:
                self.vb.program()
        self.refresh_status()

    def _on_program_failed(self, msg: str):
        self.statusBar().showMessage(f"Programming failed: {msg}", 10000)
        if self.isVisible() and not self._quiet_errors:
            QMessageBox.warning(self, "Programming failed", msg)

    # ================================================================== Vivado
    def start_vivado_server(self) -> bool:
        if self.vb.xvc is not None:
            return True
        host = int(self.settings.get("hw_server_port")) if self.settings.get("virtual_host") else None
        return self.vb.start_xvc(int(self.settings.get("xvc_port")), host)

    def connect_vivado(self):
        """Start the board's Vivado connection (if needed) and show the Tcl to paste into Vivado."""
        if not self.start_vivado_server():
            QMessageBox.warning(self, "Connect to Vivado", "The virtual cable could not start - see the console "
                                "(another program may be using the port; change it in Settings).")
            return
        self.refresh_status()
        dlg = self.make_connect_dialog()
        if not self._quiet_errors:
            dlg.exec()
        return dlg

    def make_connect_dialog(self) -> ConnectVivadoDialog:
        hs = self.vb.hw_server
        usb = self.vb.usb_cables
        note = ("A real USB JTAG board is connected, so the board is added to Vivado's own local server; your real "
                "board and the virtual one appear as two separate targets." if usb else "")
        bit = self.vb.selected.path if self.vb.selected is not None else None
        dlg = ConnectVivadoDialog(self.board, powered=self.vb.powered, host_port=hs.port if hs else None,
                                  xvc_port=self.vb.xvc.port, usb_note=note, bitstream=bit, parent=self)
        dlg.power_requested.connect(self.vb.power_on)
        dlg.stop_requested.connect(self.vb.stop_xvc)
        return dlg

    # ================================================================== simulation settings
    def set_speed(self, mode: str, custom: int = 0):
        self.settings.set("speed", mode)
        self.vb.sim.request_speed.emit(mode, custom)
        i = self.speed_combo.findData(mode)
        if i >= 0 and self.speed_combo.currentIndex() != i:
            self.speed_combo.blockSignals(True)
            self.speed_combo.setCurrentIndex(i)
            self.speed_combo.blockSignals(False)
        self.sim_page.set_speed(mode)

    def set_min_press(self, cycles: int):
        self.settings.set("min_press_cycles", int(cycles))
        self.vb.sim.request_min_press.emit(int(cycles))

    def set_recording(self, on: bool):
        self.settings.set("record_waveforms", bool(on))
        self.vb.sim.request_recording.emit(bool(on))

    # ================================================================== hardware callbacks
    def _on_state(self, st: str):
        active = st in HwState.ACTIVE
        self.board_widget.set_power(self.vb.powered, active)
        if not active:
            self.board_widget.clear_outputs()
        if st == HwState.PROGRAMMED and self.waves.reader is None:
            self.waves.attach(user_data_dir() / "build" / "bitstream" / "sim" / "waves.vcd",
                              int(self.board.clock_period_ns * 1000))
        elif st in (HwState.POWER_OFF, HwState.UNPROGRAMMED, HwState.PROGRAMMING):
            self.waves.detach()
            if st == HwState.PROGRAMMING:
                self.waves.attach(None)
        if st in (HwState.UNPROGRAMMED, HwState.POWER_OFF):
            self.last_cycles = 0
        self.act_power.blockSignals(True)
        self.act_power.setChecked(self.vb.powered)
        self.act_power.setText("⏻ Power OFF" if self.vb.powered else "⏻ Power ON")
        self.act_power.blockSignals(False)
        self.act_program.setEnabled(self.vb.powered and st != HwState.PROGRAMMING)
        self.board_combo.setEnabled(st != HwState.PROGRAMMING)
        self.act_run.setEnabled(active)
        self.act_run.setText("⏸ Pause" if st == HwState.RUNNING else "▶ Run")
        self.act_step.setEnabled(active)
        self.reset_btn.setEnabled(active)
        self.act_restart.setEnabled(active)
        self.act_hwreset.setEnabled(active)
        self.sim_page.set_state(st)
        self.st_state.setText(f"● {self.board.virtual_name}: {st}")
        self.st_state.setStyleSheet(f"color:{STATE_COLOR.get(st, theme.TEXT)}; font-weight:600;")
        self.refresh_status()

    def _on_design(self, info):
        design = self.vb.design
        if info and design is not None:
            mapping, _ = mapping_from_pins(design, self.board)
            mapped: dict[str, list[str]] = {}
            for key, rid in mapping.entries.items():
                mapped.setdefault(rid, []).append(key)
            self.board_widget.set_mapping({rid: ", ".join(k) for rid, k in mapped.items()})
            self.sim_page.set_design(info, design, self.board)
        else:
            self.board_widget.set_mapping({})
            self.sim_page.set_design(None)

    def _on_update(self, u):
        if self.vb.state not in HwState.ACTIVE:
            return                      # late update from a configuration that was just cleared
        self.last_cycles = u.cycles
        self.board_widget.set_outputs(u.leds, u.led_levels, u.digits, u.extra)
        self.st_cycles.setText(f"Cycles: {u.cycles:,}")
        self.st_time.setText(f"Sim time: {fmt_sim_time(u.time_ps)}")
        self.panel.set("cycles", f"{u.cycles:,}")
        self.panel.set("time", fmt_sim_time(u.time_ps))
        self.panel.set("rate", f"{u.rate:,.0f} cycles/s" if u.rate else self.speed_combo.currentText())
        if self.stack.currentWidget() is self.sim_page:
            self.sim_page.show_update(u)
        else:
            self.sim_page.remember(u)
        now = time.monotonic()
        if now - self._vcd_check > 1.0 and self.settings.get("record_waveforms") and self.waves.reader:
            self._vcd_check = now
            try:
                mb = self.waves.reader.path.stat().st_size / 1e6
            except OSError:
                mb = 0
            if mb > self.settings.get("max_vcd_mb"):
                self.log("warning", f"Waveform file reached {mb:.0f} MB: recording stopped (limit in Settings).")
                self.sim_page.record.setChecked(False)

    def refresh_status(self):
        vb, p = self.vb, self.panel
        st = vb.state
        p.set_headline("POWER ON" if vb.powered else "POWER OFF", theme.OK if vb.powered else theme.MUTED)
        p.set("power", "ON" if vb.powered else "OFF", theme.OK if vb.powered else theme.MUTED)
        cfg = {HwState.POWER_OFF: ("Unpowered", theme.MUTED), HwState.UNPROGRAMMED: ("Unprogrammed", theme.WARN),
               HwState.PROGRAMMING: ("Programming...", theme.ACCENT)}.get(st, ("Programmed (DONE)", theme.OK))
        p.set("config", *cfg)

        def short(name: str, n: int = 26) -> str:
            return name if len(name) <= n else name[: n - 1] + "…"
        if vb.programmed_bit is not None:
            p.set("bitstream", short(vb.programmed_bit.path.name), tip=str(vb.programmed_bit.path))
        elif vb.selected is not None:
            p.set("bitstream", short(vb.selected.path.name) + " (selected)", tip=str(vb.selected.path))
        else:
            p.set("bitstream", "none", theme.MUTED, tip="Load Bitstream, or program it from Vivado")
        sim = {HwState.RUNNING: ("Running", theme.OK), HwState.PAUSED: ("Paused", theme.WARN),
               HwState.PROGRAMMED: ("Ready", theme.ACCENT)}.get(st, ("Stopped", theme.MUTED))
        p.set("sim", *sim)
        if st not in HwState.ACTIVE:
            for k in ("cycles", "time", "rate"):
                p.set(k, "-")
        hs, hm = vb.hw_server, self._xvc_status
        if vb.xvc is None:
            p.set("hm", "Not connected", theme.MUTED, tip="Click Connect to Vivado")
        else:
            where = f"127.0.0.1:{hs.port}" if hs else f"cable localhost:{vb.xvc.port}"
            state = "Vivado connected" if "connected" in hm else "Ready"
            p.set("hm", f"{state} · {where}", theme.OK if "connected" in hm else theme.WARN, tip=hm)
        name = vb.programmed_bit.path.name if vb.programmed_bit else (vb.selected.path.name if vb.selected else "")
        self.st_bit.setText(name)
        self.setWindowTitle(f"{self.board.virtual_name}{' — ' + name if name else ''} — {APP_NAME}")

    # ================================================================== shutdown
    def closeEvent(self, ev):
        self._timer.stop()
        self.vb.shutdown()
        ev.accept()
