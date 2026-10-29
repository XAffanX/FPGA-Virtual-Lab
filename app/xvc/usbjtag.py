"""Detect real USB JTAG cables (a physical Basys 3, Digilent/Xilinx programmers).

Every hw_server opens all USB JTAG cables it finds, and a cable can only be used by one process at
a time. The virtual host is a second hw_server, so it must not run while a real board is plugged
in, or it could take the real board's cable away from Vivado's own local server. (hw_server's
jtag-port-filter setting would be the clean fix, but it also hides the virtual cable.)

Detected: FTDI-based JTAG (VID 0403 - Basys 3, Digilent cables and boards) and Xilinx Platform
Cable USB (VID 03FD). FTDI USB-serial adapters share VID 0403 and are reported too: a deliberate
false positive, since the plain virtual cable remains available either way.
"""
from __future__ import annotations

import os
import subprocess

from app.utils.toolchain import CREATE_NO_WINDOW

_QUERY = ("Get-CimInstance Win32_PnPEntity -Filter \"DeviceID LIKE '%VID_0403%' OR DeviceID LIKE '%VID_03FD%'\" "
          "| Where-Object { $_.Status -eq 'OK' } | ForEach-Object { $_.Name + ' [' + $_.DeviceID + ']' }")


def usb_jtag_cables(timeout: float = 10.0) -> list[str]:
    """Names of connected USB JTAG-capable devices (empty if none, or not on Windows)."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _QUERY],
                             capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]
