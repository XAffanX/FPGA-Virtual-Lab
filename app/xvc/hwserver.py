"""A dedicated AMD hw_server acting as the "virtual host" for the virtual Basys 3.

FPGA Virtual Lab starts Vivado's own hw_server on its own port with the Xilinx Virtual Cable already
attached (`set auto-open-servers xilinx-xvc:localhost:<xvc port>`). In Vivado the board is then
reached like any remote lab machine: Open New Target > Remote server > host 127.0.0.1, port 3122.
The virtual XC7A35T is listed immediately - no XVC dialog, no first-scan race.

The hw_server protocol itself (TCF with proprietary Xilinx services) is not re-implemented; the real
hw_server is used, so Vivado talks to genuine AMD software.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

from app.utils.toolchain import CREATE_NO_WINDOW

DEFAULT_PORT = 3122


class HwServerError(RuntimeError):
    pass


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


class VirtualHwServer:
    def __init__(self, vivado_root: Path, port: int, xvc_port: int):
        self.vivado_root = Path(vivado_root)
        self.port = port
        self.xvc_port = xvc_port
        self.proc: subprocess.Popen | None = None

    @property
    def executable(self) -> Path:
        bat = self.vivado_root / "bin" / ("hw_server.bat" if os.name == "nt" else "hw_server")
        return bat

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, timeout: float = 20.0) -> None:
        if self.running:
            return
        if not self.executable.is_file():
            raise HwServerError(f"hw_server not found in {self.executable.parent}")
        if port_in_use(self.port):
            raise HwServerError(f"port {self.port} is already in use (another hw_server?) - choose another "
                                "port in Settings")
        cmd = [str(self.executable), f"-stcp::{self.port}", "-p0", "-q",
               "-e", f"set auto-open-servers xilinx-xvc:localhost:{self.xvc_port}"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise HwServerError(f"hw_server exited immediately (code {self.proc.returncode})")
            if port_in_use(self.port):
                return
            time.sleep(0.2)
        self.stop()
        raise HwServerError(f"hw_server did not start listening on port {self.port}")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            # hw_server.bat starts hw_server.exe as a child: end the whole tree.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
