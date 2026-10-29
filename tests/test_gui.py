"""End-to-end tests of the virtual-hardware UI (offscreen Qt) against real Vivado bitstreams.

Uses the Vivado-built fixtures in tests/fixtures, the real Vivado export (cached after the first
run) and the real Icarus Verilog simulator. Skipped when Vivado is not installed.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:/Windows/Fonts")

from PySide6.QtWidgets import QApplication  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
COUNTER = FIX / "vivado_counter" / "counter.bit"
RSTN = FIX / "vivado_counter_rstn" / "counter_rstn.bit"
SHOTS = os.environ.get("FVL_SCREENSHOTS")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    from app.ui import theme
    theme.apply(app)
    return app


@pytest.fixture(scope="module")
def appdata(tmp_path_factory):
    # One AppData for the module, so Vivado's netlist export is done once per bitstream.
    return tmp_path_factory.mktemp("appdata")


def wait_for(app, cond, timeout=30.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def settle(app, seconds):
    wait_for(app, lambda: False, seconds)


def shot(win, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        win.grab().save(str(Path(SHOTS) / f"{name}.png"))


@pytest.fixture
def win(qapp, toolchain, appdata, tmp_path, monkeypatch):
    from app.vivado.vivado import find_vivado
    if find_vivado() is None:
        pytest.skip("Vivado is not installed")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("FVL_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    from app.ui.main_window import MainWindow
    w = MainWindow()
    w._quiet_errors = True              # no modal dialogs in tests
    w.resize(1500, 950)
    w.show()
    yield w
    w.close()
    qapp.processEvents()


def counter_value(w):
    return sum(int((w.board_widget.overlay.led_brightness[i] or 0) > 0.5) << i for i in range(4))


def program(qapp, w, bit, timeout=240):
    from app.hardware.virtual_board import HwState as H
    assert w.vb.program(bit) is not False, w.vb.last_error
    assert wait_for(qapp, lambda: w.vb.state != H.PROGRAMMING, timeout), w.vb.state
    assert w.vb.state == H.PROGRAMMED, w.vb.last_error


# ---------------------------------------------------------------------------- power
def test_power_cycling(qapp, win):
    from app.hardware.virtual_board import HwState as H
    ov = win.board_widget.overlay
    assert win.vb.state == H.POWER_OFF and not ov.powered
    shot(win, "01_power_off")
    for _ in range(3):
        win.vb.toggle_power()
        assert wait_for(qapp, lambda: win.vb.state == H.UNPROGRAMMED)
        assert ov.powered and not ov.done
        assert win.panel.values["power"].text() == "ON"
        win.vb.toggle_power()
        assert wait_for(qapp, lambda: win.vb.state == H.POWER_OFF)
        assert not ov.powered and win.panel.values["power"].text() == "OFF"
    win.board_widget.power_clicked.emit()                     # the board's own POWER switch
    assert wait_for(qapp, lambda: win.vb.state == H.UNPROGRAMMED)


def test_programming_requires_power_and_power_off_clears_configuration(qapp, win):
    from app.hardware.virtual_board import HwState as H
    ov = win.board_widget.overlay
    assert win.vb.program(COUNTER) is False                 # powered off: rejected
    assert win.vb.state == H.POWER_OFF and "powered OFF" in win.vb.last_error
    win.vb.power_on()
    program(qapp, win, COUNTER)
    assert ov.done and win.vb.programmed_bit.path.name == "counter.bit"
    win.set_speed("fast")
    win.vb.run()
    assert wait_for(qapp, lambda: win.vb.state == H.RUNNING and win.last_cycles > 1000)
    assert wait_for(qapp, lambda: any((b or 0) > 0 for b in ov.led_brightness[:4]))
    win.goto("board")
    shot(win, "02_running")
    win.vb.power_off()                                        # power off while running
    assert wait_for(qapp, lambda: win.vb.state == H.POWER_OFF)
    assert wait_for(qapp, lambda: win.vb.sim.worker.sim is None)      # simulator process terminated
    assert all(b is None for b in ov.led_brightness) and not ov.digits and not ov.done
    win.vb.run()                                              # no execution while unpowered
    settle(qapp, 0.3)
    assert win.vb.state == H.POWER_OFF
    win.vb.power_on()                                         # power cycle: unprogrammed again
    assert wait_for(qapp, lambda: win.vb.state == H.UNPROGRAMMED)
    assert win.vb.programmed_bit is None and win.vb.selected.path.name == "counter.bit"
    program(qapp, win, None)                                  # one-click reprogram of the last bitstream
    win.vb.step(3)
    assert wait_for(qapp, lambda: counter_value(win) == 3)


def test_prog_button_clears_configuration(qapp, win):
    from app.hardware.virtual_board import HwState as H
    win.vb.power_on()
    program(qapp, win, COUNTER)
    win.board_widget.prog_clicked.emit()
    assert wait_for(qapp, lambda: win.vb.state == H.UNPROGRAMMED)
    assert not win.board_widget.overlay.done and win.vb.powered


# ---------------------------------------------------------------------------- reset
def test_restart_running_and_paused_counter(qapp, win):
    from app.hardware.virtual_board import HwState as H
    win.vb.power_on()
    program(qapp, win, COUNTER)
    win.set_speed("normal")
    win.vb.run()
    assert wait_for(qapp, lambda: win.last_cycles > 300)
    before = win.last_cycles
    win.vb.restart_simulation()                               # reset a running counter
    assert wait_for(qapp, lambda: win.last_cycles < before)
    assert wait_for(qapp, lambda: win.vb.state == H.RUNNING)  # keeps running from cycle 0
    win.vb.pause()
    assert wait_for(qapp, lambda: win.vb.state == H.PAUSED)
    win.vb.step(5)
    assert wait_for(qapp, lambda: counter_value(win) == (win.last_cycles % 16))
    win.vb.restart_simulation()                               # reset while paused
    assert wait_for(qapp, lambda: win.last_cycles == 0 and win.vb.state == H.PROGRAMMED)
    assert counter_value(win) == 0
    win.vb.step(2)
    assert wait_for(qapp, lambda: counter_value(win) == 2)


def test_reset_without_bitstream(qapp, win):
    from app.hardware.virtual_board import HwState as H
    win.vb.restart_simulation()                               # powered off
    win.vb.hardware_reset()
    win.vb.power_on()
    assert wait_for(qapp, lambda: win.vb.state == H.UNPROGRAMMED)
    win.vb.restart_simulation()                               # powered, nothing programmed
    win.vb.hardware_reset()
    settle(qapp, 0.2)
    assert win.vb.state == H.UNPROGRAMMED
    msgs = [e[2] for e in win.console.entries]
    assert any("not programmed" in m for m in msgs) and any("powered off" in m for m in msgs)


def test_hardware_reset_active_high(qapp, win):
    from app.project.project import ResetConfig
    win.vb.power_on()
    program(qapp, win, COUNTER)
    info = win.vb.design_info
    assert ("btnC", True) in info["reset_candidates"]         # clk excluded, btnC offered
    win.vb.configure_reset(ResetConfig("btnC", True, 2))
    settle(qapp, 0.2)
    win.vb.step(5)
    assert wait_for(qapp, lambda: counter_value(win) == 5)
    win.vb.hardware_reset()                                   # btnC high for 2 cycles, then low
    assert wait_for(qapp, lambda: win.last_cycles == 7)
    assert wait_for(qapp, lambda: counter_value(win) == 0)
    win.vb.step(3)
    assert wait_for(qapp, lambda: counter_value(win) == 3)


def test_hardware_reset_active_low(qapp, win):
    from app.project.project import ResetConfig
    ov = win.board_widget.overlay
    ov.toggle_switch(15)                                      # rst_n (SW15) high = not in reset
    win.vb.power_on()
    program(qapp, win, RSTN)
    info = win.vb.design_info
    assert ("rst_n", False) in info["reset_candidates"]       # recognised as active-low
    win.vb.step(6)
    assert wait_for(qapp, lambda: counter_value(win) == 6)
    win.vb.configure_reset(ResetConfig("rst_n", False, 3))
    settle(qapp, 0.2)
    win.vb.hardware_reset()                                   # rst_n low for 3 cycles, then back to SW15
    assert wait_for(qapp, lambda: win.last_cycles == 9)
    assert wait_for(qapp, lambda: counter_value(win) == 0)
    win.vb.step(4)
    assert wait_for(qapp, lambda: counter_value(win) == 4)
    ov.toggle_switch(15)                                      # SW15 off: the design is held in reset
    win.vb.step(2)
    assert wait_for(qapp, lambda: counter_value(win) == 0)
    win.goto("sim")
    shot(win, "03_simulation_page")


# ---------------------------------------------------------------------------- bitstream validation
def test_invalid_and_unsupported_bitstreams_rejected(qapp, win, tmp_path):
    from app.hardware.virtual_board import HwState as H
    win.vb.power_on()
    program(qapp, win, COUNTER)
    bad = tmp_path / "garbage.bit"
    bad.write_bytes(os.urandom(5000))
    assert win.vb.program(bad) is False and "not a Xilinx .bit" in win.vb.last_error
    other = tmp_path / "other_part.bit"
    data = bytearray(COUNTER.read_bytes())
    i = data.find(b"7a35tcpg236")
    data[i:i + 11] = b"7a50tcsg324"                           # same length: valid header, other device
    other.write_bytes(bytes(data))
    assert win.vb.program(other) is False and "XC7A35TCPG236" in win.vb.last_error
    trunc = tmp_path / "counter.bit"
    trunc.write_bytes(COUNTER.read_bytes()[:500_000])
    assert win.vb.program(trunc) is False and "incomplete" in win.vb.last_error
    # Rejections never touch the running configuration.
    assert win.vb.state == H.PROGRAMMED and win.vb.programmed_bit.path == COUNTER.resolve()


def test_missing_checkpoint_reports_error(qapp, win, tmp_path):
    from app.hardware.virtual_board import HwState as H
    lonely = tmp_path / "counter.bit"
    shutil.copy(COUNTER, lonely)
    win.vb.power_on()
    win.vb.program(lonely)
    assert wait_for(qapp, lambda: win.vb.state == H.UNPROGRAMMED and win.vb.last_error, 60)
    assert "routed checkpoint" in win.vb.last_error and win.vb.programmed_bit is None


# ---------------------------------------------------------------------------- Nexys 4 DDR
NEXYS = FIX / "vivado_nexys4ddr" / "nexys_demo.bit"


def test_nexys4ddr_board(qapp, win):
    """virtual_nexys4ddr: 8-digit display, RGB LED, active-low CPU_RESETN, board-specific validation."""
    from app.hardware.virtual_board import HwState as H
    assert win.vb.program(NEXYS) is False                                  # Basys 3 selected, powered off
    win.vb.power_on()
    assert win.vb.program(NEXYS) is False and "virtual_nexys4ddr" in win.vb.last_error   # wrong board
    assert win.switch_board("nexys4ddr", ask=False)
    assert win.board.id == "nexys4ddr" and win.vb.state == H.POWER_OFF     # switching = new board, off
    assert win.board_widget.overlay.L.digits == 8
    ov = win.board_widget.overlay
    for i in (0, 2, 8, 15):
        ov.toggle_switch(i)
    win.vb.power_on()
    program(qapp, win, NEXYS)
    assert win.vb.design_info["top"] == "nexys_demo"
    assert ("CPU_RESETN", False) in win.vb.design_info["reset_candidates"]   # suggested as active low
    win.vb.step(5)
    assert wait_for(qapp, lambda: counter_value(win) == 5)
    assert [ov.led_brightness[i] for i in (8, 15, 9)] == [1.0, 1.0, 0.0]   # LED[15:8] = SW[15:8]
    assert wait_for(qapp, lambda: [ov.extra.get(f"LED16_{c}") for c in "RGB"] == [1.0, 0.0, 1.0])
    ov.set_button("CPU_RESETN", 1)                                         # pressed = pin low = reset
    win.vb.step(2)
    assert wait_for(qapp, lambda: counter_value(win) == 0)
    ov.set_button("CPU_RESETN", 0)
    win.vb.step(3)
    assert wait_for(qapp, lambda: counter_value(win) == 3)
    win.set_speed("max")
    win.vb.run()
    assert wait_for(qapp, lambda: win.last_cycles > 20000, 60)
    win.vb.pause()
    assert wait_for(qapp, lambda: win.vb.state == H.PAUSED)
    assert len(ov.digits) == 8 and all(d.lit for d in ov.digits)
    assert [x > 0.5 for x in ov.digits[1].segments[:7]] == [False, True, True, False, False, False, False]  # "1"
    win.goto("board")
    shot(win, "05_nexys4ddr")
    assert win.switch_board("basys3", ask=False) and win.vb.state == H.POWER_OFF
    assert win.board_widget.overlay.L.digits == 4 and win.vb.selected is None     # Nexys .bit dropped


def test_connect_to_vivado_dialog(qapp, win, monkeypatch):
    from app.xvc import usbjtag
    monkeypatch.setattr(usbjtag, "usb_jtag_cables", lambda timeout=10.0: [])
    win.settings.set("xvc_port", 26541)
    win.settings.set("hw_server_port", 26542)
    win.vb.select_bitstream(COUNTER)
    dlg = win.connect_vivado()
    try:
        assert win.vb.xvc is not None and win.vb.hw_server is not None
        tcl = dlg.tcl.toPlainText()
        assert "connect_hw_server -url 127.0.0.1:26542" in tcl and "xc7a35t*" in tcl
        assert "{" + COUNTER.resolve().as_posix() + "}" in tcl          # absolute path of the selected .bit
        assert win.panel.values["hm"].text().startswith("Ready")
        dlg.stop_requested.emit()
        assert win.vb.xvc is None and win.vb.hw_server is None
    finally:
        win.vb.stop_xvc()
    # With a real USB board present the dialog targets Vivado's local server instead.
    monkeypatch.setattr(usbjtag, "usb_jtag_cables", lambda timeout=10.0: ["Digilent USB Device"])
    dlg = win.connect_vivado()
    try:
        tcl = dlg.tcl.toPlainText()
        assert win.vb.hw_server is None and "open_hw_target -xvc_url localhost:26541" in tcl
    finally:
        win.vb.stop_xvc()


# ---------------------------------------------------------------------------- Devices page
def test_devices_uart_terminal(qapp, win):
    from app.hardware.virtual_board import HwState as H
    win.vb.set_peripheral("baud", 115200)
    win.vb.power_on()
    program(qapp, win, FIX / "vivado_uart_echo" / "uart_echo.bit")
    assert win.vb.design_info["uart"] and win.vb.design_info["uart_rx"]
    win.goto("devices")
    t = win.devices.uart
    t.line.setText("hello")
    t.ending.setCurrentIndex(0)
    t.send()
    win.set_speed("max")
    win.vb.run()
    assert wait_for(qapp, lambda: "hello" in t.out.toPlainText(), 120), t.out.toPlainText()
    win.vb.pause()
    assert wait_for(qapp, lambda: win.vb.state == H.PAUSED)
    shot(win, "06_uart_terminal")


def test_devices_vga_screen_and_timing_option(qapp, win):
    from app.hardware.virtual_board import HwState as H
    win.vb.power_on()
    program(qapp, win, FIX / "vivado_vga_test" / "vga_test.bit")
    assert win.vb.design_info["vga"]
    win.goto("devices")
    win.devices.setCurrentIndex(1)
    win.set_speed("max")
    win.vb.run()
    vga = win.devices.vga
    assert wait_for(qapp, lambda: vga._img is not None and "frame" in vga.status.text(), 240)
    assert wait_for(qapp, lambda: vga._img.pixelColor(10, 40).name() == "#ff0000", 120)
    win.vb.pause()
    assert wait_for(qapp, lambda: win.vb.state == H.PAUSED)
    shot(win, "07_vga_screen")
    assert win.panel.values["timing"].text().startswith("Functional")


def test_devices_nexys_sensors_tab(qapp, win):
    win.switch_board("nexys4ddr", ask=False)
    assert win.devices.isTabVisible(3)
    win.devices.sensors.temp.setValue(321)                # 32.1 C, remembered for programming
    win.vb.power_on()
    program(qapp, win, FIX / "vivado_nexys_sensors" / "nexys_sensors.bit")
    info = win.vb.design_info
    assert info["sensors"] == {"temp": True, "acl": True, "mic": True}
    win.vb.step(150_000)
    ov = win.board_widget.overlay
    assert wait_for(qapp, lambda: counter_byte(ov, 0) == (int(32.1 * 16) << 3) >> 8, 120)
    win.goto("devices")
    win.devices.setCurrentIndex(3)
    shot(win, "08_sensors")


def counter_byte(ov, base):
    return sum(int((ov.led_brightness[base + i] or 0) > 0.5) << i for i in range(8))


def test_devices_pmod_tab(qapp, win):
    win.vb.power_on()
    program(qapp, win, FIX / "vivado_pmod_test" / "pmod_test.bit")
    pm = win.devices.pmod
    assert "JA7" in pm.inputs and "JA1" in pm.used
    pm._toggle("JA7")                                       # click the input pin
    win.vb.step(1)
    assert wait_for(qapp, lambda: (win.board_widget.overlay.led_brightness[0] or 0) == 1.0)
    win.goto("devices")
    win.devices.setCurrentIndex(2)
    shot(win, "09_pmod")
