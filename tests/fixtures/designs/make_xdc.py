"""Write each test design's .xdc by copying pin lines from Digilent's master XDC files.

Run:  python tests/fixtures/designs/make_xdc.py
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
XDC = HERE.parents[2] / "configurations" / "xdc"
LINE = re.compile(r"PACKAGE_PIN\s+([A-Z0-9]+)\s+IOSTANDARD\s+(\w+).*get_ports\s+(?:\{\s*([^ }]+)\s*\}|([^ \]]+))")


def pins(master: str) -> dict[str, tuple[str, str]]:
    out = {}
    for raw in (XDC / master).read_text(encoding="utf-8").splitlines():
        m = LINE.search(raw)
        if m:
            out[m.group(3) or m.group(4)] = (m.group(1), m.group(2))
    return out


def rng(prefix, n, master_prefix=None, offset=0):
    master_prefix = master_prefix or prefix
    return {f"{prefix}[{i}]": f"{master_prefix}[{i + offset}]" for i in range(n)}


DESIGNS = {
    "uart_echo": ("Basys-3-Master.xdc", "clk",
                  {"clk": "clk", "RsRx": "RsRx", "RsTx": "RsTx", "btnC": "btnC", **rng("led", 16)}),
    "vga_test": ("Basys-3-Master.xdc", "clk",
                 {"clk": "clk", **rng("sw", 16), **rng("vgaRed", 4), **rng("vgaGreen", 4), **rng("vgaBlue", 4),
                  "Hsync": "Hsync", "Vsync": "Vsync"}),
    "pmod_test": ("Basys-3-Master.xdc", "clk",
                  {"clk": "clk", **rng("sw", 16), **rng("led", 16), **rng("JA_out", 4, "JA"),
                   **rng("JA_in", 4, "JA", 4), "JB_tog": "JB[0]", "JB_in": "JB[1]"}),
    "timing_fail": ("Basys-3-Master.xdc", "clk", {"clk": "clk", **rng("led", 16)}),
    "nexys_sensors": ("Nexys-4-DDR-Master.xdc", "CLK100MHZ",
                      {"CLK100MHZ": "CLK100MHZ", **{p: p for p in ("TMP_SCL", "TMP_SDA", "ACL_SCLK", "ACL_MOSI",
                                                                    "ACL_CSN", "ACL_MISO", "M_CLK", "M_DATA",
                                                                    "M_LRSEL", "CA", "CB", "CC", "CD", "CE", "CF",
                                                                    "CG", "DP")},
                       **rng("LED", 16), **rng("AN", 8)}),
}

for name, (master, clk, ports) in DESIGNS.items():
    table = pins(master)
    lines = []
    for port, mport in ports.items():
        pin, std = table[mport]
        target = port if "[" not in port else "{" + port + "}"
        lines.append(f"set_property -dict {{ PACKAGE_PIN {pin} IOSTANDARD {std} }} [get_ports {target}]; # {mport}")
    lines.append(f"create_clock -add -name sys_clk_pin -period 10.00 -waveform {{0 5}} [get_ports {clk}]")
    lines += ["set_property CONFIG_VOLTAGE 3.3 [current_design]", "set_property CFGBVS VCCO [current_design]"]
    (HERE / name / f"{name}.xdc").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(name, len(ports), "pins")
