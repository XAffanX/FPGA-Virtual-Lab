"""Read the header of a Xilinx .bit bitstream file (as written by Vivado's write_bitstream).

Layout: a fixed 13-byte preamble, then tagged fields
  'a' design name (and ;UserID=...;Version=... properties)   'b' part   'c' date   'd' time
  'e' 4-byte length of the raw configuration data that follows.
Only the header is interpreted; the configuration frames themselves are not
decoded (their format is not publicly documented).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

PREAMBLE = bytes.fromhex("0009 0ff00ff00ff00ff0 00 0001".replace(" ", ""))


class BitfileError(ValueError):
    pass


@dataclass
class BitInfo:
    path: Path
    design: str                 # top-level design name
    part: str                   # e.g. "7a35tcpg236"
    date: str
    time: str
    data_length: int
    properties: dict[str, str] = field(default_factory=dict)   # UserID, Version, ...
    header_length: int = 0      # bytes before the configuration data

    @property
    def expected_size(self) -> int:
        return self.header_length + self.data_length

    def is_complete(self) -> bool:
        """True when the file holds all the configuration data its header announces."""
        try:
            return self.path.stat().st_size >= self.expected_size
        except OSError:
            return False

    def payload(self) -> bytes:
        """The configuration data (what is shifted into the FPGA)."""
        with open(self.path, "rb") as f:
            f.seek(self.header_length)
            return f.read(self.data_length)

    @property
    def vivado_version(self) -> str:
        return self.properties.get("Version", "")

    @property
    def full_part(self) -> str:
        return self.part if self.part.startswith("xc") else "xc" + self.part

    def summary(self) -> str:
        v = f", Vivado {self.vivado_version}" if self.vivado_version else ""
        return f"design '{self.design}', part {self.full_part}, built {self.date} {self.time}{v}"


def read_bit_header(path: Path) -> BitInfo:
    path = Path(path)
    data = path.read_bytes()[:4096]
    if not data.startswith(PREAMBLE):
        raise BitfileError(f"{path.name} is not a Xilinx .bit file (unexpected header). Note: .bin files "
                           "have no header; select the .bit file Vivado wrote.")
    pos = len(PREAMBLE)
    fields: dict[str, str] = {}
    length = 0
    while pos < len(data):
        tag = chr(data[pos])
        pos += 1
        if tag == "e":
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            pos += 4
            break
        if tag not in "abcd" or pos + 2 > len(data):
            raise BitfileError(f"{path.name}: malformed header field '{tag}'")
        n = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        fields[tag] = data[pos:pos + n].rstrip(b"\0").decode("ascii", errors="replace")
        pos += n
    if "a" not in fields or "b" not in fields:
        raise BitfileError(f"{path.name}: header has no design/part information")
    parts = fields["a"].split(";")
    props = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
    return BitInfo(path, parts[0], fields["b"], fields.get("c", ""), fields.get("d", ""), length, props, pos)
