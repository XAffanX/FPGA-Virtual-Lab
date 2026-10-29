"""Incremental Value Change Dump (VCD) reader.

The simulator appends to the VCD while it runs; `VcdReader.poll()` parses only
the newly written, complete lines, so the waveform viewer can follow a live
simulation cheaply.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from pathlib import Path

_UNITS = {"s": 10**15, "ms": 10**12, "us": 10**9, "ns": 10**6, "ps": 10**3, "fs": 1}

OFF = None   # value while recording was switched off ($dumpoff)


@dataclass
class Signal:
    code: str
    name: str            # full hierarchical name, e.g. "fvl_dut.u_dec.seg"
    width: int
    kind: str            # wire | reg | integer | real | ...
    times: list[int] = field(default_factory=list)     # in femtoseconds
    values: list[str | None] = field(default_factory=list)

    @property
    def short_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    @property
    def scope(self) -> str:
        return self.name.rsplit(".", 1)[0] if "." in self.name else ""

    def value_at(self, t_fs: int) -> str | None:
        i = bisect.bisect_right(self.times, t_fs) - 1
        return self.values[i] if i >= 0 else OFF

    def append(self, t: int, v: str | None) -> None:
        if self.times and self.times[-1] == t:
            self.values[-1] = v
            return
        if self.values and self.values[-1] == v:
            return
        self.times.append(t)
        self.values.append(v)


def format_value(v: str | None, width: int, radix: str = "hex") -> str:
    """Human-readable value of a VCD bit string."""
    if v is OFF:
        return "-"
    if width == 1 or radix == "bin":
        return v
    if any(c in "xXzZ" for c in v):
        if radix == "hex" and len(v) >= 4:
            v = v.rjust((len(v) + 3) // 4 * 4, "0")
            out = ""
            for k in range(0, len(v), 4):
                nib = v[k:k + 4]
                if all(c in "01" for c in nib):
                    out += f"{int(nib, 2):x}"
                elif all(c in "zZ" for c in nib):
                    out += "z"
                else:
                    out += "x"
            return out
        return v
    n = int(v, 2)
    if radix == "dec":
        return str(n)
    if radix == "sdec":
        bits = max(width, len(v))
        return str(n - (1 << bits) if n >> (bits - 1) & 1 else n)
    return f"{n:0{max(1, (width + 3) // 4)}x}"


class VcdReader:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.signals: dict[str, list[Signal]] = {}     # code -> signals sharing it
        self.by_name: dict[str, Signal] = {}
        self.timescale_fs = 1000                        # default 1 ps
        self.time = 0                                   # last timestamp (fs)
        self.header_done = False
        self._offset = 0
        self._partial = ""
        self._header_text = ""
        self._scopes: list[str] = []
        self._in_dumpoff = False
        self._in_dump_block = False

    # -------------------------------------------------------------- public API
    def poll(self) -> bool:
        """Read newly appended data. Returns True if anything changed."""
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if size < self._offset:          # file was recreated (simulator restarted)
            self.__init__(self.path)
        if size == self._offset:
            return False
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self._offset)
            chunk = f.read()
            self._offset = f.tell()
        text = self._partial + chunk
        cut = text.rfind("\n")
        if cut < 0:
            self._partial = text
            return False
        self._partial = text[cut + 1:]
        self._feed(text[:cut + 1])
        return True

    def all_signals(self) -> list[Signal]:
        return sorted(self.by_name.values(), key=lambda s: s.name)

    @property
    def end_time_fs(self) -> int:
        return self.time

    # -------------------------------------------------------------- parsing
    def _feed(self, text: str) -> None:
        if not self.header_done:
            self._header_text += text
            idx = self._header_text.find("$enddefinitions")
            if idx < 0:
                return
            end = self._header_text.find("$end", idx + len("$enddefinitions"))
            if end < 0:
                return
            self._parse_header(self._header_text[:end + 4])
            rest = self._header_text[end + 4:]
            self._header_text = ""
            self.header_done = True
            text = rest
        for tok_line in text.splitlines():
            self._parse_line(tok_line.strip())

    def _parse_header(self, text: str) -> None:
        tokens = text.split()
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t == "$timescale":
                j = tokens.index("$end", i)
                ts = "".join(tokens[i + 1:j])
                m = re.match(r"(\d+)\s*([munpf]?s)", ts)
                if m:
                    self.timescale_fs = int(m.group(1)) * _UNITS[m.group(2)]
                i = j + 1
            elif t == "$scope":
                self._scopes.append(tokens[i + 2])
                i = tokens.index("$end", i) + 1
            elif t == "$upscope":
                if self._scopes:
                    self._scopes.pop()
                i = tokens.index("$end", i) + 1
            elif t == "$var":
                j = tokens.index("$end", i)
                kind, width, code, ref = tokens[i + 1], int(tokens[i + 2]), tokens[i + 3], tokens[i + 4]
                if j - i > 5 and tokens[i + 5].startswith("["):   # "name [3:0]"
                    ref += tokens[i + 5]
                name = ".".join(self._scopes + [ref.split("[")[0] if width > 1 and ref.endswith("]")
                                                and ":" in ref else ref])
                sig = Signal(code, name, width, kind)
                self.signals.setdefault(code, []).append(sig)
                self.by_name.setdefault(name, sig)
                i = j + 1
            elif t.startswith("$") and t != "$end":
                try:
                    i = tokens.index("$end", i) + 1
                except ValueError:
                    i = len(tokens)
            else:
                i += 1

    def _set(self, code: str, value: str | None) -> None:
        for s in self.signals.get(code, ()):
            if value is not OFF and s.width > 1 and len(value) < s.width:
                pad = value[0] if value[0] in "xXzZ" else "0"
                value = value.rjust(s.width, pad)
            s.append(self.time, value)

    def _parse_line(self, line: str) -> None:
        if not line:
            return
        c = line[0]
        if c == "#":
            try:
                self.time = int(line[1:]) * self.timescale_fs
            except ValueError:
                pass
            return
        if c == "$":
            word = line.split()[0]
            if word == "$dumpoff":
                self._in_dumpoff = True
            elif word in ("$dumpon", "$dumpvars", "$dumpall"):
                self._in_dumpoff = False
                self._in_dump_block = True
            elif word == "$end":
                self._in_dump_block = False
            return
        if self._in_dumpoff:
            # Values listed after $dumpoff are placeholders: mark the signals as not recorded.
            if c in "01xXzZ" and len(line) > 1:
                self._set(line[1:], OFF)
            elif c in "bBrR":
                parts = line.split()
                if len(parts) == 2:
                    self._set(parts[1], OFF)
            elif line == "$end":
                pass
            return
        if c in "01xXzZ":
            self._set(line[1:], c.lower())
        elif c in "bB":
            parts = line[1:].split()
            if len(parts) == 2:
                self._set(parts[1], parts[0].lower())
        elif c in "rR":
            parts = line[1:].split()
            if len(parts) == 2:
                self._set(parts[1], parts[0])
        elif line == "$end":
            self._in_dump_block = False
