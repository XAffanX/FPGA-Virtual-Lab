"""Experimental Vivado Hardware Manager connectivity (Xilinx Virtual Cable + emulated XC7A35T JTAG).

1. JTAG-level tests drive the emulated TAP with the sequences UG470 specifies.
2. A socket test checks the XVC 1.0 protocol framing.
3. With Vivado installed, Vivado itself (batch-mode Hardware Manager) detects and programs the
   virtual board through the app's VirtualBoard - the real acceptance test.
"""
from __future__ import annotations

import os
import socket
import struct
import subprocess
import time
from pathlib import Path

import pytest

from app.vivado.bitfile import read_bit_header
from app.xvc.jtag import IDCODE_XC7A35T, Artix7Jtag, _REV
from app.xvc.server import XvcServer

FIX = Path(__file__).parent / "fixtures"
COUNTER = FIX / "vivado_counter" / "counter.bit"
RSTN = FIX / "vivado_counter_rstn" / "counter_rstn.bit"


class Jtag:
    """Minimal JTAG host: drives an Artix7Jtag (or an XVC socket) with TMS/TDI vectors."""

    def __init__(self, shift_fn):
        self.shift_fn = shift_fn

    def clock(self, tms: list[int], tdi_bits: int = 0) -> int:
        n = len(tms)
        tms_i = sum(b << i for i, b in enumerate(tms))
        nb = (n + 7) // 8
        out = self.shift_fn(n, tms_i.to_bytes(nb, "little"), tdi_bits.to_bytes(nb, "little"))
        return int.from_bytes(out, "little")

    def reset(self):
        self.clock([1, 1, 1, 1, 1, 0])                 # Test-Logic-Reset, then Run-Test/Idle

    def ir(self, value: int) -> int:
        pre = [1, 1, 0, 0]                             # RTI -> SEL_DR -> SEL_IR -> CAP_IR -> SH_IR
        tms = pre + [0] * 5 + [1] + [1, 0]             # 6 shifts (last exits), UPDATE_IR, RTI
        tdo = self.clock(tms, value << len(pre))
        return (tdo >> len(pre)) & 0x3F                # IR capture value

    def dr(self, value: int, n: int) -> int:
        pre = [1, 0, 0]                                # RTI -> SEL_DR -> CAP_DR -> SH_DR
        tms = pre + [0] * (n - 1) + [1] + [1, 0]
        tdo = self.clock(tms, value << len(pre))
        return (tdo >> len(pre)) & ((1 << n) - 1)

    def cfg_in_bytes(self, data: bytes):
        self.ir(0x05)
        self.dr(int.from_bytes(data.translate(_REV), "little"), len(data) * 8)    # MSB of each byte first

    def idle(self, n: int):
        self.clock([0] * n)


def words(*ws: int) -> bytes:
    return b"".join(w.to_bytes(4, "big") for w in ws)


READ_STAT = words(0xFFFFFFFF, 0xAA995566, 0x20000000, 0x2800E001, 0x20000000, 0x20000000)
DESYNC = words(0x30008001, 0x0000000D, 0x20000000)


def read_stat(j: Jtag) -> int:
    j.cfg_in_bytes(READ_STAT)
    j.ir(0x04)                                         # CFG_OUT
    bits = j.dr(0, 32)
    j.cfg_in_bytes(words(0xAA995566) + DESYNC)
    return sum(((bits >> i) & 1) << (31 - i) for i in range(32))      # shifted out MSB first


def program(j: Jtag, payload: bytes):
    j.reset()
    j.ir(0x0B)                                         # JPROGRAM
    j.idle(100)
    j.cfg_in_bytes(payload)
    j.ir(0x0C)                                         # JSTART
    j.idle(2000)


def test_idcode_and_ir_capture():
    dev = Artix7Jtag()
    j = Jtag(dev.shift)
    j.reset()
    assert j.dr(0, 32) == IDCODE_XC7A35T               # IDCODE selected after Test-Logic-Reset
    assert j.ir(0x3F) == 0b010001                      # {DONE=0, INIT_COMPLETE=1, ..., 0, 1}
    assert j.dr(0b1, 1) == 0                           # BYPASS captures 0


def test_program_real_bitstream_over_jtag():
    payload = read_bit_header(COUNTER).payload()
    seen = []

    def resolver(data):
        seen.append(len(data))
        return payload in data, "match" if payload in data else "no match"

    dev = Artix7Jtag(resolver=resolver)
    j = Jtag(dev.shift)
    program(j, payload)
    assert seen and dev.done
    assert j.ir(0x3F) == 0b110101                      # configured device: DONE, INIT, ISC_DONE
    stat = read_stat(j)
    assert stat >> 14 & 1 and stat >> 12 & 1 and not stat >> 15 & 1      # DONE, INIT_B, no ID_ERROR
    j.ir(0x0B)                                         # JPROGRAM clears the configuration
    assert not dev.done and j.ir(0x3F) == 0b010001


def test_unknown_configuration_keeps_done_low():
    dev = Artix7Jtag(resolver=lambda d: (False, "unknown"))
    program(Jtag(dev.shift), read_bit_header(COUNTER).payload())
    assert not dev.done and dev.last_result.message == "unknown"


def test_wrong_idcode_is_reported():
    payload = bytearray(read_bit_header(COUNTER).payload())
    i = payload.find(words(0x30018001))                # type-1 write IDCODE
    payload[i + 4:i + 8] = words(0x03631093)           # XC7A100T id
    dev = Artix7Jtag(resolver=lambda d: (True, "would match"))
    j = Jtag(dev.shift)
    program(j, bytes(payload))
    assert not dev.done and "IDCODE" in dev.last_result.message
    assert read_stat(j) >> 15 & 1                      # STAT.ID_ERROR


def test_wrong_board_bitstream_names_the_board(qapp_hw):
    """A Nexys 4 DDR bitstream shifted into virtual_basys3 fails start-up and names the right board."""
    from tests.test_gui import wait_for
    from PySide6.QtWidgets import QApplication
    vb = qapp_hw
    vb.power_on()
    msgs = []
    vb.log.connect(lambda level, m: msgs.append(m))
    assert vb.start_xvc(free_port())
    try:
        program(Jtag(vb.xvc_device.shift), read_bit_header(FIX / "vivado_nexys4ddr" / "nexys_demo.bit").payload())
        assert not vb.xvc_device.done and vb.xvc_device.id_error
        assert wait_for(QApplication.instance(), lambda: any("virtual_nexys4ddr" in m for m in msgs), 10)
    finally:
        vb.stop_xvc()


def test_unpowered_device_is_invisible():
    dev = Artix7Jtag()
    dev.powered = False
    j = Jtag(dev.shift)
    j.reset()
    assert j.dr(0, 32) == 0


def test_xvc_socket_protocol():
    dev = Artix7Jtag()
    srv = XvcServer(dev, port=0)
    srv.port = 0
    srv.start()
    port = srv._sock.getsockname()[1]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
            c.sendall(b"getinfo:")
            assert c.recv(64).startswith(b"xvcServer_v1.0:")
            c.sendall(b"settck:" + struct.pack("<I", 100))
            assert struct.unpack("<I", c.recv(4))[0] == 100

            def shift(n, tms, tdi):
                c.sendall(b"shift:" + struct.pack("<I", n) + tms + tdi)
                buf = b""
                while len(buf) < len(tms):
                    buf += c.recv(len(tms) - len(buf))
                return buf
            j = Jtag(shift)
            j.reset()
            assert j.dr(0, 32) == IDCODE_XC7A35T
    finally:
        srv.stop()


# ------------------------------------------------------------------------------- real Vivado
HM_TCL = r"""
set bit [lindex $argv 0]
set port [lindex $argv 1]
set hwport [lindex $argv 2]
open_hw_manager
connect_hw_server -url localhost:$hwport -allow_non_jtag
if {[catch {open_hw_target -xvc_url localhost:$port}]} {
    after 3000
    close_hw_target -quiet
    open_hw_target [lindex [get_hw_targets *$port*] 0]
}
set d [lindex [get_hw_devices] 0]
puts "HM: device=$d idcode=[get_property IDCODE_HEX $d]"
current_hw_device $d
refresh_hw_device -update_hw_probes false $d
puts "HM: before_done=[get_property REGISTER.IR.BIT5_DONE $d]"
set_property PROGRAM.FILE $bit $d
if {[catch {program_hw_devices $d} err]} { puts "HM: program_error=$err" } else { puts "HM: program_ok" }
refresh_hw_device -update_hw_probes false $d
puts "HM: after_done=[get_property REGISTER.IR.BIT5_DONE $d]"
"""


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def vivado_hm(tmp_path, toolchain, monkeypatch):
    from app.vivado.vivado import find_vivado
    v = find_vivado()
    if v is None:
        pytest.skip("Vivado is not installed")
    tcl = tmp_path / "hm.tcl"
    tcl.write_text(HM_TCL)
    # A private hw_server, so the tests never share (or disturb) a Vivado session the user has open.
    hwport = free_port()
    hs = subprocess.Popen([str(v.root / "bin" / "hw_server.bat"), f"-stcp::{hwport}", "-p0", "-q"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(tmp_path))
    time.sleep(3)

    def run(bit: Path, port: int) -> str:
        p = subprocess.run([str(v.launcher), "-mode", "batch", "-nojournal", "-nolog", "-source", str(tcl),
                            "-tclargs", bit.as_posix(), str(port), str(hwport)], capture_output=True, text=True,
                           errors="replace", timeout=600, cwd=str(tmp_path))
        return p.stdout + p.stderr
    yield run
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(hs.pid)], capture_output=True)


def test_vivado_hardware_manager_programs_virtual_board(vivado_hm, qapp_hw, tmp_path, monkeypatch):
    """Vivado detects the virtual XC7A35T over XVC, programs counter.bit, and the virtual board runs it."""
    from app.hardware.virtual_board import HwState
    from tests.test_gui import wait_for
    vb = qapp_hw
    vb.power_on()
    vb.add_known(COUNTER)                       # the app knows this .bit (as if loaded or watched)
    port = free_port()
    assert vb.start_xvc(port)
    try:
        import threading
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("log", vivado_hm(COUNTER, port)))
        th.start()
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        assert wait_for(app, lambda: not th.is_alive(), 600)
        log = out["log"]
        assert "HM: device=xc7a35t_0 idcode=0362D093" in log, log[-3000:]
        assert "HM: before_done=0" in log
        lines = log.splitlines()
        assert "End of startup status: HIGH" in log and "HM: program_ok" in lines
        assert "HM: after_done=1" in lines
        assert wait_for(app, lambda: vb.state == HwState.PROGRAMMED, 240), vb.last_error
        assert vb.programmed_bit.path == COUNTER.resolve()
        assert vb.programming_source == "Vivado Hardware Manager"
        vb.step(3)
        assert wait_for(app, lambda: vb.sim.worker.sim is not None and vb.sim.worker.sim.cycles == 3)
        assert [vb.sim.worker.sim.led(i) for i in range(4)] == [1, 1, 0, 0]
    finally:
        vb.stop_xvc()


def test_vivado_unknown_bitstream_leaves_done_low(vivado_hm, qapp_hw, tmp_path, monkeypatch):
    from app.hardware.virtual_board import HwState
    from tests.test_gui import wait_for
    monkeypatch.setenv("FVL_VIVADO_USER_DIR", str(tmp_path / "no_vivado_records"))
    vb = qapp_hw
    vb.power_on()                               # counter_rstn.bit is NOT known to the app
    port = free_port()
    assert vb.start_xvc(port)
    try:
        import threading
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("log", vivado_hm(RSTN, port)))
        th.start()
        from PySide6.QtWidgets import QApplication
        assert wait_for(QApplication.instance(), lambda: not th.is_alive(), 600)
        log = out["log"]
        assert "HM: device=xc7a35t_0" in log
        assert "End of startup status: LOW" in log and "HM: program_error=" in log   # Vivado: start-up failed
        lines = log.splitlines()
        assert "HM: program_ok" not in lines and "HM: after_done=0" in lines
        assert vb.state == HwState.UNPROGRAMMED
    finally:
        vb.stop_xvc()


@pytest.fixture
def qapp_hw(tmp_path, monkeypatch, toolchain):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("FVL_SETTINGS_FILE", str(tmp_path / "s.ini"))
    from app.board.board import load_board
    from app.hardware.virtual_board import VirtualBoard
    from app.utils.settings import AppSettings
    vb = VirtualBoard(load_board(), AppSettings(), lambda: toolchain)
    yield vb
    vb.shutdown()


def test_vivado_programs_bitstream_the_app_was_never_told_about(vivado_hm, qapp_hw, tmp_path, monkeypatch):
    """Nothing loaded or watched: the .bit is found through Vivado's PROGRAM.FILE history."""
    from app.hardware.virtual_board import HwState
    from tests.test_gui import wait_for
    from tests.test_discovery import make_vivado_dir
    user = make_vivado_dir(tmp_path, [], [f"set_property PROGRAM.FILE {{{RSTN.as_posix()}}} [get_hw_devices xc7a35t_0]"])
    monkeypatch.setenv("FVL_VIVADO_USER_DIR", str(user))
    vb = qapp_hw
    vb.power_on()
    port = free_port()
    assert vb.start_xvc(port)
    try:
        import threading
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("log", vivado_hm(RSTN, port)))
        th.start()
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        assert wait_for(app, lambda: not th.is_alive(), 600)
        lines = out["log"].splitlines()
        assert "End of startup status: HIGH" in out["log"] and "HM: program_ok" in lines
        assert wait_for(app, lambda: vb.state == HwState.PROGRAMMED, 240), vb.last_error
        assert vb.programmed_bit.path == RSTN.resolve()
    finally:
        vb.stop_xvc()


def test_jtag_programming_resolves_through_discovery(qapp_hw, tmp_path, monkeypatch):
    """Same path without Vivado: JTAG sequence from the test host, bitstream found via a recent project."""
    from app.hardware.virtual_board import HwState
    from tests.test_gui import wait_for
    from tests.test_discovery import make_project, make_vivado_dir
    xpr, bit = make_project(tmp_path, "proj", COUNTER)
    monkeypatch.setenv("FVL_VIVADO_USER_DIR", str(make_vivado_dir(tmp_path, [xpr], [])))
    vb = qapp_hw
    vb.power_on()
    assert vb.start_xvc(free_port())
    try:
        program(Jtag(vb.xvc_device.shift), read_bit_header(COUNTER).payload())
        assert vb.xvc_device.done
        from PySide6.QtWidgets import QApplication
        assert wait_for(QApplication.instance(), lambda: vb.state == HwState.PROGRAMMED, 240), vb.last_error
        assert vb.programmed_bit.path == bit.resolve()
    finally:
        vb.stop_xvc()


def test_virtual_host_as_remote_server(vivado_hm, qapp_hw, tmp_path):
    """Vivado reaches the board as a 'Remote server' (the app's own hw_server), alongside a local server,
    using exactly the Tcl shown in the app's help dialog."""
    from app.hardware.virtual_board import HwState
    from app.board.board import get_board
    from app.ui.status_panel import vivado_tcl
    from app.vivado.vivado import find_vivado
    from tests.test_gui import wait_for
    from PySide6.QtWidgets import QApplication
    vb = qapp_hw
    vb.power_on()
    vb.add_known(COUNTER)
    host = free_port()
    assert vb.start_xvc(free_port(), host)
    assert vb.hw_server is not None and vb.hw_server.running
    try:
        local = free_port()      # stands in for Vivado's own local server named "localhost"
        v = find_vivado()
        hs = subprocess.Popen([str(v.root / "bin" / "hw_server.bat"), f"-stcp::{local}", "-p0", "-q"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        snippet = vivado_tcl(get_board("basys3"), host_port=host, xvc_port=0, bitstream=COUNTER.resolve())
        snippet = snippet.replace("program_hw_devices [current_hw_device]",
                                  'if {[catch {program_hw_devices [current_hw_device]} e]} {puts "HM: err=$e"} '
                                  'else {puts "HM: program_ok"}')
        tcl = tmp_path / "remote.tcl"
        tcl.write_text(f"open_hw_manager\nconnect_hw_server -url localhost:{local} -allow_non_jtag\n"
                       f'puts "HM: servers=[get_hw_servers]"\n' + snippet)
        p = subprocess.run([str(v.launcher), "-mode", "batch", "-nojournal", "-nolog", "-source", str(tcl)],
                           capture_output=True, text=True, errors="replace", timeout=600, cwd=str(tmp_path))
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(hs.pid)], capture_output=True)
        log = p.stdout + p.stderr
        lines = log.splitlines()
        assert f"127.0.0.1:{host}" in log and "End of startup status: HIGH" in log, log[-2500:]
        assert "HM: program_ok" in lines
        assert wait_for(QApplication.instance(), lambda: vb.state == HwState.PROGRAMMED, 240), vb.last_error
    finally:
        vb.stop_xvc()
    assert vb.hw_server is None


def test_virtual_host_refuses_to_start_next_to_a_real_usb_cable(qapp_hw, monkeypatch):
    from app.xvc import usbjtag
    monkeypatch.setattr(usbjtag, "usb_jtag_cables", lambda timeout=10.0: ["Digilent USB Device (VID_0403 PID_6010)"])
    vb = qapp_hw
    try:
        assert vb.start_xvc(free_port(), free_port())      # the virtual cable itself still starts
        assert vb.xvc is not None and vb.hw_server is None  # ...but no second hw_server
    finally:
        vb.stop_xvc()


def test_virtual_host_stops_when_a_real_board_is_plugged_in(qapp_hw, monkeypatch):
    from app.vivado.vivado import find_vivado
    from app.xvc import usbjtag
    from tests.test_gui import wait_for
    from PySide6.QtWidgets import QApplication
    if find_vivado() is None:
        pytest.skip("Vivado is not installed")
    cables: list[str] = []
    monkeypatch.setattr(usbjtag, "usb_jtag_cables", lambda timeout=10.0: list(cables))
    vb = qapp_hw
    try:
        assert vb.start_xvc(free_port(), free_port()) and vb.hw_server is not None
        proc = vb.hw_server.proc
        vb.check_usb_conflict()
        assert wait_for(QApplication.instance(), lambda: not vb._usb_check_busy, 20)
        assert vb.hw_server is not None                       # nothing plugged in: keeps running
        cables.append("Digilent USB Device (VID_0403 PID_6010)")
        vb.check_usb_conflict()
        assert wait_for(QApplication.instance(), lambda: vb.hw_server is None, 20)
        assert proc.poll() is not None                        # the hw_server process really ended
        assert vb.xvc is not None                             # virtual cable still available
    finally:
        vb.stop_xvc()


NEXYS = FIX / "vivado_nexys4ddr" / "nexys_demo.bit"


def _run_dialog_tcl(v, tcl_text: str, tmp_path: Path) -> list[str]:
    tcl = tmp_path / "dialog.tcl"
    tcl.write_text(tcl_text)
    p = subprocess.run([str(v.launcher), "-mode", "batch", "-nojournal", "-nolog", "-source", str(tcl)],
                       capture_output=True, text=True, errors="replace", timeout=600, cwd=str(tmp_path))
    return (p.stdout + p.stderr).splitlines()


def test_vivado_programs_virtual_nexys4ddr(qapp_hw, tmp_path):
    """The Connect-to-Vivado Tcl for virtual_nexys4ddr: Vivado finds xc7a100t_0 and programs it."""
    from app.board.board import get_board
    from app.hardware.virtual_board import HwState
    from app.ui.status_panel import vivado_tcl
    from app.vivado.vivado import find_vivado
    from tests.test_gui import wait_for
    from PySide6.QtWidgets import QApplication
    v = find_vivado()
    if v is None:
        pytest.skip("Vivado is not installed")
    vb = qapp_hw
    vb.switch_board(get_board("nexys4ddr"))
    vb.power_on()
    assert vb.select_bitstream(NEXYS) is not None          # as when the dialog's Tcl names the loaded .bit
    host = free_port()
    assert vb.start_xvc(free_port(), host) and vb.hw_server is not None
    try:
        lines = _run_dialog_tcl(v, vivado_tcl(vb.board, host_port=host, xvc_port=0, bitstream=NEXYS.resolve()),
                                tmp_path)
        assert "Connected to virtual_nexys4ddr: xc7a100t_0" in lines, "\n".join(lines[-40:])
        assert any("End of startup status: HIGH" in l for l in lines)
        assert wait_for(QApplication.instance(), lambda: vb.state == HwState.PROGRAMMED, 240), vb.last_error
        assert vb.programmed_bit.path == NEXYS.resolve() and vb.design_info["top"] == "nexys_demo"
    finally:
        vb.stop_xvc()


def test_vivado_local_server_tcl_next_to_real_board(qapp_hw, tmp_path, monkeypatch):
    """The cable-only Tcl (used when a real USB board is plugged in) against a stand-in local hw_server."""
    from app.hardware.virtual_board import HwState
    from app.ui.status_panel import vivado_tcl
    from app.vivado.vivado import find_vivado
    from app.xvc import usbjtag
    from tests.test_gui import wait_for
    from PySide6.QtWidgets import QApplication
    v = find_vivado()
    if v is None:
        pytest.skip("Vivado is not installed")
    monkeypatch.setattr(usbjtag, "usb_jtag_cables", lambda timeout=10.0: ["Digilent USB Device"])
    vb = qapp_hw
    vb.power_on()
    assert vb.select_bitstream(COUNTER) is not None
    xvc = free_port()
    assert vb.start_xvc(xvc, free_port()) and vb.hw_server is None        # refused: real cable "present"
    local = free_port()
    hs = subprocess.Popen([str(v.root / "bin" / "hw_server.bat"), f"-stcp::{local}", "-p0", "-q"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    try:
        tcl = vivado_tcl(vb.board, host_port=None, xvc_port=xvc, bitstream=COUNTER.resolve())
        tcl = tcl.replace("catch {connect_hw_server -allow_non_jtag}",
                          "catch {connect_hw_server -url localhost:%d -allow_non_jtag}" % local)
        lines = _run_dialog_tcl(v, tcl, tmp_path)
        assert "Connected to virtual_basys3: xc7a35t_0" in lines, "\n".join(lines[-40:])
        assert any("End of startup status: HIGH" in l for l in lines)
        assert wait_for(QApplication.instance(), lambda: vb.state == HwState.PROGRAMMED, 240), vb.last_error
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(hs.pid)], capture_output=True)
        vb.stop_xvc()
