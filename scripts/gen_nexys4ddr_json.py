"""Generate configurations/nexys4ddr.json from Digilent's Nexys-4-DDR-Master.xdc.

Every pin comes from the master XDC (configurations/xdc/Nexys-4-DDR-Master.xdc, Digilent
digilent-xdc repository, Rev. C board); nothing is typed in by hand. Pins of peripherals the
virtual board does not model (Pmods, VGA, audio, accelerometer, Ethernet, DDR2, ...) are listed
as unsupported so XDC/bitstream mapping can name them in warnings.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
XDC = ROOT / "configurations" / "xdc" / "Nexys-4-DDR-Master.xdc"
LINE = re.compile(r"PACKAGE_PIN\s+([A-Z0-9]+).*get_ports\s+\{\s*([^ }]+)\s*\}\];\s*(?:#(.*))?")


def main():
    pins: dict[str, str] = {}
    comments: dict[str, str] = {}
    for raw in XDC.read_text(encoding="utf-8").splitlines():
        m = LINE.search(raw)
        if m:
            pins[m.group(2)] = m.group(1).upper()
            comments[m.group(2)] = (m.group(3) or "").strip()
    used = set()

    def res(rid, kind, d, port, label, **extra):
        used.add(port)
        r = {"id": rid, "kind": kind, "dir": d, "pin": pins[port], "xdc_name": port, "label": label}
        r.update(extra)
        return r

    R = [res("CLK100MHZ", "clock", "in", "CLK100MHZ", "100 MHz oscillator")]
    R += [res(f"SW{i}", "switch", "in", f"SW[{i}]", f"SW{i}") for i in range(16)]
    R += [res(f"LED{i}", "led", "out", f"LED[{i}]", f"LD{i}") for i in range(16)]
    for n in (16, 17):
        for c, name in (("R", "red"), ("G", "green"), ("B", "blue")):
            R.append(res(f"LED{n}_{c}", "rgb", "out", f"LED{n}_{c}", f"LD{n} {name} (tri-color LED)"))
    for c in "ABCDEFG":
        R.append(res(f"SEG_{c}", "segment", "out", f"C{c}", f"C{c} (segment {c}, active low)"))
    R.append(res("SEG_DP", "segment", "out", "DP", "DP (decimal point, active low)"))
    R += [res(f"AN{i}", "anode", "out", f"AN[{i}]", f"AN{i} (digit {i} enable, active low)") for i in range(8)]
    for b in ("BTNC", "BTNU", "BTNL", "BTNR", "BTND"):
        R.append(res(b, "button", "in", b, b))
    R.append(res("CPU_RESETN", "button", "in", "CPU_RESETN", "CPU RESET (active low: reads 1 until pressed)",
                 active_low=True))
    unsupported = {pins[p]: f"{p} ({comments[p] or 'not simulated'})" for p in pins if p not in used}
    cfg = {
        "id": "nexys4ddr", "name": "Digilent Nexys 4 DDR", "virtual_name": "virtual_nexys4ddr",
        "fpga": "Xilinx Artix-7 XC7A100T-1CSG324C", "part": "xc7a100tcsg324", "idcode": "0x03631093",
        "source": "Pin data from Digilent Nexys-4-DDR-Master.xdc (Rev. C)",
        "clock": {"resource": "CLK100MHZ", "frequency_hz": 100_000_000, "period_ns": 10.0},
        "seven_segment": {"digits": 8, "segments": [f"SEG_{c}" for c in "ABCDEFG"], "dp": "SEG_DP",
                          "anodes": [f"AN{i}" for i in range(8)], "segments_active_low": True,
                          "anodes_active_low": True},
        "leds_active_high": True,
        "resources": R,
        "unsupported_pins": unsupported,
    }
    out = ROOT / "configurations" / "nexys4ddr.json"
    out.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print("wrote", out, len(R), "resources,", len(unsupported), "unsupported pins")


if __name__ == "__main__":
    main()
