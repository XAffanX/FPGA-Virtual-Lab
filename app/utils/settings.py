"""Persistent application preferences (stored in the user's AppData via QSettings)."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings

DEFAULTS = {
    "iverilog_dir": "",
    "vivado_dir": "",
    "min_press_cycles": 32,
    "max_vcd_mb": 200,
    "default_speed": "normal",
    "recent_projects": [],
    "power_on_init": True,
    "board": "basys3",
    "xvc_port": 2542,
    "hw_server_port": 3122,      # virtual host: dedicated hw_server with the virtual cable attached
    "virtual_host": True,
    "xvc_autostart": False,
    "record_waveforms": True,
    "last_bitstream": "",
    "speed": "normal",
    "timing_sim": False,         # simulate with Vivado's SDF delays (timing netlist)
    "uart_baud": 9600,
    "temperature_c": 25.0,
    "accel_x": 0, "accel_y": 0, "accel_z": 1000,
    "mic_amp": 0, "mic_freq": 1000,
}


class AppSettings:
    def __init__(self):
        ini = os.environ.get("FVL_SETTINGS_FILE")      # tests: isolated settings file
        self.q = QSettings(ini, QSettings.IniFormat) if ini else QSettings("FPGAVirtualLab", "FPGAVirtualLab")

    def get(self, key: str):
        default = DEFAULTS.get(key)
        v = self.q.value(key, default)
        if isinstance(default, bool):
            return v in (True, "true", "1", 1)
        if isinstance(default, int):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        if isinstance(default, float):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default
        if isinstance(default, list):
            if v is None:
                return []
            return [v] if isinstance(v, str) else list(v)
        return v if v is not None else default

    def set(self, key: str, value) -> None:
        self.q.setValue(key, value)

    def add_recent(self, path: str) -> None:
        rec = [p for p in self.get("recent_projects") if p != path]
        rec.insert(0, path)
        self.set("recent_projects", rec[:10])
