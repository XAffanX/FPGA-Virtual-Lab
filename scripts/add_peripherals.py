"""Add UART, VGA, Pmod and (Nexys) sensor resources to the board JSON files.

All pins are read from Digilent's master XDC files in configurations/xdc/. Pins that become
resources are removed from the board's "unsupported_pins" list. Safe to run repeatedly.
Run after gen_basys3_json.py / gen_nexys4ddr_json.py:  python scripts/add_peripherals.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE = re.compile(r"PACKAGE_PIN\s+([A-Z0-9]+).*get_ports\s+(?:\{\s*([^ }]+)\s*\}|([^ \]]+))")


def xdc_pins(name: str) -> dict[str, str]:
    out = {}
    for raw in (ROOT / "configurations" / "xdc" / name).read_text(encoding="utf-8").splitlines():
        m = LINE.search(raw)
        if m:
            out[m.group(2) or m.group(3)] = m.group(1).upper()
    return out


PMOD_POS = [1, 2, 3, 4, 7, 8, 9, 10]        # header pin numbers of the 8 signal pins


def basys3(p: dict[str, str]) -> list[dict]:
    R = [("UART_RX", "uart_rx", "in", "RsRx", "USB-UART RX: data from the PC into the FPGA"),
         ("UART_TX", "uart_tx", "out", "RsTx", "USB-UART TX: data from the FPGA to the PC")]
    for c, port in (("R", "vgaRed"), ("G", "vgaGreen"), ("B", "vgaBlue")):
        R += [(f"VGA_{c}{i}", "vga", "out", f"{port}[{i}]", f"VGA {port}[{i}]") for i in range(4)]
    R += [("VGA_HS", "vga", "out", "Hsync", "VGA horizontal sync"), ("VGA_VS", "vga", "out", "Vsync", "VGA vertical sync")]
    for hdr in ("JA", "JB", "JC", "JXADC"):
        R += [(f"{hdr}{n}", "pmod", "io", f"{hdr}[{k}]", f"Pmod {hdr} pin {n}") for k, n in enumerate(PMOD_POS)]
    return R


def nexys4ddr(p: dict[str, str]) -> list[dict]:
    R = [("UART_RX", "uart_rx", "in", "UART_TXD_IN", "USB-UART RX: data from the PC into the FPGA"),
         ("UART_TX", "uart_tx", "out", "UART_RXD_OUT", "USB-UART TX: data from the FPGA to the PC")]
    for c in "RGB":
        R += [(f"VGA_{c}{i}", "vga", "out", f"VGA_{c}[{i}]", f"VGA_{c}[{i}]") for i in range(4)]
    R += [("VGA_HS", "vga", "out", "VGA_HS", "VGA horizontal sync"), ("VGA_VS", "vga", "out", "VGA_VS", "VGA vertical sync")]
    for hdr in ("JA", "JB", "JC", "JD"):
        R += [(f"{hdr}{n}", "pmod", "io", f"{hdr}[{n}]", f"Pmod {hdr} pin {n}") for n in PMOD_POS]
    # JXADC: pins 1-4 = XA1_P..XA4_P, pins 7-10 = XA1_N..XA4_N
    R += [(f"JXADC{n}", "pmod", "io", f"XA_{'P' if n < 5 else 'N'}[{(n - 1) % 6 + 1 if n < 5 else n - 6}]",
           f"Pmod JXADC pin {n}") for n in PMOD_POS]
    R += [("TMP_SCL", "sensor", "out", "TMP_SCL", "ADT7420 temperature sensor I2C SCL"),
          ("TMP_SDA", "sensor", "io", "TMP_SDA", "ADT7420 temperature sensor I2C SDA (open drain)"),
          ("TMP_INT", "sensor", "in", "TMP_INT", "ADT7420 INT (open drain, idle high)"),
          ("TMP_CT", "sensor", "in", "TMP_CT", "ADT7420 CT (open drain, idle high)"),
          ("ACL_SCLK", "sensor", "out", "ACL_SCLK", "ADXL362 accelerometer SPI SCLK"),
          ("ACL_MOSI", "sensor", "out", "ACL_MOSI", "ADXL362 accelerometer SPI MOSI"),
          ("ACL_CSN", "sensor", "out", "ACL_CSN", "ADXL362 accelerometer SPI CS (active low)"),
          ("ACL_MISO", "sensor", "in", "ACL_MISO", "ADXL362 accelerometer SPI MISO"),
          ("ACL_INT1", "sensor", "in", "ACL_INT[1]", "ADXL362 INT1"),
          ("ACL_INT2", "sensor", "in", "ACL_INT[2]", "ADXL362 INT2"),
          ("M_CLK", "sensor", "out", "M_CLK", "PDM microphone clock"),
          ("M_DATA", "sensor", "in", "M_DATA", "PDM microphone data"),
          ("M_LRSEL", "sensor", "out", "M_LRSEL", "PDM microphone channel select")]
    return R


def update(board_file: str, xdc: str, builder) -> None:
    path = ROOT / "configurations" / board_file
    cfg = json.loads(path.read_text(encoding="utf-8"))
    pins = xdc_pins(xdc)
    have = {r["id"] for r in cfg["resources"]}
    added = 0
    for rid, kind, d, port, label in builder(pins):
        pin = pins[port]
        cfg["unsupported_pins"].pop(pin, None)
        if rid in have:
            continue
        cfg["resources"].append({"id": rid, "kind": kind, "dir": d, "pin": pin, "xdc_name": port, "label": label})
        added += 1
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"{board_file}: +{added} resources, {len(cfg['resources'])} total, "
          f"{len(cfg['unsupported_pins'])} unsupported pins left")


if __name__ == "__main__":
    update("basys3.json", "Basys-3-Master.xdc", basys3)
    update("nexys4ddr.json", "Nexys-4-DDR-Master.xdc", nexys4ddr)
