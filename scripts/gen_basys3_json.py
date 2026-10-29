"""Generates configurations/basys3.json.

Package pins are taken from Digilent's Basys-3-Master.xdc (Rev. C board),
https://github.com/Digilent/digilent-xdc/blob/master/Basys-3-Master.xdc
"""
import json, pathlib

SW = ["V17", "V16", "W16", "W17", "W15", "V15", "W14", "W13",
      "V2", "T3", "T2", "R3", "W2", "U1", "T1", "R2"]
LED = ["U16", "E19", "U19", "V19", "W18", "U15", "U14", "V14",
       "V13", "V3", "W3", "U3", "P3", "N3", "P1", "L1"]
SEG = ["W7", "W6", "U8", "V8", "U5", "V5", "U7"]          # seg[0]=CA ... seg[6]=CG
AN = ["U2", "U4", "V4", "W4"]
BTN = [("BTNC", "U18", "btnC"), ("BTNU", "T18", "btnU"), ("BTNL", "W19", "btnL"),
       ("BTNR", "T17", "btnR"), ("BTND", "U17", "btnD")]

res = [{"id": "CLK100MHZ", "kind": "clock", "dir": "in", "pin": "W5", "xdc_name": "clk",
        "label": "100 MHz oscillator"}]
for i, p in enumerate(SW):
    res.append({"id": f"SW{i}", "kind": "switch", "dir": "in", "pin": p, "xdc_name": f"sw[{i}]", "label": f"SW{i}"})
for i, p in enumerate(LED):
    res.append({"id": f"LED{i}", "kind": "led", "dir": "out", "pin": p, "xdc_name": f"led[{i}]", "label": f"LD{i}"})
for rid, p, n in BTN:
    res.append({"id": rid, "kind": "button", "dir": "in", "pin": p, "xdc_name": n, "label": rid})
for i, p in enumerate(SEG):
    c = "ABCDEFG"[i]
    res.append({"id": f"SEG_{c}", "kind": "segment", "dir": "out", "pin": p, "xdc_name": f"seg[{i}]",
                "label": f"C{c} (segment {c}, active low)"})
res.append({"id": "SEG_DP", "kind": "segment", "dir": "out", "pin": "V7", "xdc_name": "dp",
            "label": "DP (decimal point, active low)"})
for i, p in enumerate(AN):
    res.append({"id": f"AN{i}", "kind": "anode", "dir": "out", "pin": p, "xdc_name": f"an[{i}]",
                "label": f"AN{i} (digit {i} enable, active low)"})

# Pins that exist on the board but whose peripherals are not simulated in V1.
unsupported = {}
for name, pins in {"JA": ["J1", "L2", "J2", "G2", "H1", "K2", "H2", "G3"],
                   "JB": ["A14", "A16", "B15", "B16", "A15", "A17", "C15", "C16"],
                   "JC": ["K17", "M18", "N17", "P18", "L17", "M19", "P17", "R18"],
                   "JXADC": ["J3", "L3", "M2", "N2", "K3", "M3", "M1", "N1"]}.items():
    for i, p in enumerate(pins):
        unsupported[p] = f"Pmod {name} pin {i + 1 if i < 4 else i + 3}"
for i, p in enumerate(["G19", "H19", "J19", "N19"]): unsupported[p] = f"VGA vgaRed[{i}]"
for i, p in enumerate(["N18", "L18", "K18", "J18"]): unsupported[p] = f"VGA vgaBlue[{i}]"
for i, p in enumerate(["J17", "H17", "G17", "D17"]): unsupported[p] = f"VGA vgaGreen[{i}]"
unsupported.update({"P19": "VGA Hsync", "R19": "VGA Vsync", "B18": "USB-UART RsRx",
                    "A18": "USB-UART RsTx", "C17": "USB HID PS2Clk", "B17": "USB HID PS2Data"})

cfg = {
    "id": "basys3", "name": "Digilent Basys 3", "virtual_name": "virtual_basys3",
    "fpga": "Xilinx Artix-7 XC7A35T-1CPG236C", "part": "xc7a35tcpg236", "idcode": "0x0362D093",
    "source": "Pin data from Digilent Basys-3-Master.xdc",
    "clock": {"resource": "CLK100MHZ", "frequency_hz": 100_000_000, "period_ns": 10.0},
    "seven_segment": {"digits": 4, "segments": [f"SEG_{c}" for c in "ABCDEFG"], "dp": "SEG_DP",
                      "anodes": [f"AN{i}" for i in range(4)], "segments_active_low": True,
                      "anodes_active_low": True},
    "leds_active_high": True,
    "resources": res,
    "unsupported_pins": unsupported,
}
out = pathlib.Path(__file__).resolve().parent.parent / "configurations" / "basys3.json"
out.write_text(json.dumps(cfg, indent=2))
print("wrote", out, len(res), "resources")
