"""Drive Icarus Verilog to compile user designs and extract exact top-level port data."""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.toolchain import CREATE_NO_WINDOW, Toolchain


@dataclass(frozen=True)
class Port:
    name: str
    direction: str  # input | output | inout
    width: int
    msb: int        # left index of the declared range
    lsb: int        # right index of the declared range

    @property
    def is_vector(self) -> bool:
        return self.width > 1 or self.msb != self.lsb

    def indices(self) -> list[int]:
        """Declared bit indices, rightmost (vector position 0) first."""
        step = 1 if self.msb >= self.lsb else -1
        return [self.lsb + k * step for k in range(self.width)]

    def position(self, index: int | None) -> int:
        """Bit position in the packed vector (0 = rightmost) of a declared index."""
        if index is None:
            if self.is_vector:
                raise ValueError(f"port '{self.name}' is a vector; a bit index is required")
            return 0
        if not self.is_vector and index == self.lsb:
            return 0
        if index not in self.indices():
            raise ValueError(f"bit {index} is outside {self.range_text() or 'the scalar'} of port '{self.name}'")
        return abs(index - self.lsb)

    def range_text(self) -> str:
        return f"[{self.msb}:{self.lsb}]" if self.is_vector else ""

    def bit_label(self, index: int | None) -> str:
        return f"{self.name}[{index}]" if index is not None and self.is_vector else self.name

    def to_dict(self) -> dict:
        return {"name": self.name, "direction": self.direction, "width": self.width,
                "msb": self.msb, "lsb": self.lsb}


@dataclass
class Diagnostic:
    severity: str   # error | warning | info
    message: str
    file: str = ""
    line: int = 0

    def __str__(self) -> str:
        loc = f"{self.file}:{self.line}: " if self.file else ""
        return f"{loc}{self.severity}: {self.message}"


@dataclass
class CompileResult:
    success: bool
    top: str
    ports: list[Port] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    log: str = ""
    command: list[str] = field(default_factory=list)
    output: Path | None = None
    duration_s: float = 0.0
    sources: list[Path] = field(default_factory=list)
    language_flag: str = "-g2005"
    extra_args: list[str] = field(default_factory=list)    # e.g. UNISIM library search for netlists
    extra_roots: list[str] = field(default_factory=list)   # additional root modules (glbl)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]

    def port(self, name: str) -> Port | None:
        return next((p for p in self.ports if p.name == name), None)


LANGUAGE_FLAGS = {
    "auto": None,
    "verilog-2001": "-g2001",
    "verilog-2005": "-g2005",
    "systemverilog-2012": "-g2012",
}


def generation_flag(language: str, sources: list[Path]) -> str:
    flag = LANGUAGE_FLAGS.get(language)
    if flag:
        return flag
    return "-g2012" if any(Path(s).suffix.lower() in (".sv", ".svh") for s in sources) else "-g2005"


_DIAG_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):\s*(?P<rest>.*)$")


def parse_diagnostics(text: str) -> list[Diagnostic]:
    """Turn iverilog's console output into structured diagnostics."""
    diags: list[Diagnostic] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        i += 1
        if not raw.strip():
            continue
        if raw.startswith("*** These modules were missing"):
            while i < len(lines) and not lines[i].startswith("***"):
                name = lines[i].strip().split(" referenced")[0]
                if name:
                    diags.append(Diagnostic("error", f"missing module definition '{name}' "
                                            "(add the source file that defines it)"))
                i += 1
            i += 1
            continue
        m = _DIAG_RE.match(raw)
        if m:
            rest = m.group("rest").strip()
            low = rest.lower()
            if low.startswith("error:"):
                sev, msg = "error", rest[6:].strip()
            elif low.startswith("warning:"):
                sev, msg = "warning", rest[8:].strip()
            elif low.startswith("sorry:"):
                sev, msg = "error", "not supported by Icarus Verilog: " + rest[6:].strip()
            elif "syntax error" in low:
                sev, msg = "error", rest
            elif low.startswith(":") and diags:
                diags[-1].message += " " + rest.lstrip(": ").strip()
                continue
            else:
                sev, msg = "info", rest
            diags.append(Diagnostic(sev, msg, m.group("file"), int(m.group("line"))))
            continue
        low = raw.lower()
        if re.match(r"^\d+ error\(s\)", raw):
            continue
        msg = re.sub(r"^(error|warning)\s*:\s*", "", raw.strip(), flags=re.I)
        if "error" in low or "no top level modules" in low or "unable to" in low or "unknown" in low:
            diags.append(Diagnostic("error", msg))
        elif "warning" in low:
            diags.append(Diagnostic("warning", msg))
    # Several files may report the same "syntax error" twice (generic + specific); de-duplicate.
    seen, unique = set(), []
    for d in diags:
        key = (d.severity, d.file, d.line, d.message)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


_SCOPE_RE = re.compile(r'^(S_\w+)\s+\.scope\s+module,\s+"((?:[^"\\]|\\.)*)"\s+"((?:[^"\\]|\\.)*)"(.*);\s*$')
_PORT_RE = re.compile(r'^\s*\.port_info\s+(\d+)\s+/(INPUT|OUTPUT|INOUT)\s+(\d+)\s+"((?:[^"\\]|\\.)*)"')
_NET_RE = re.compile(r'^\S+\s+\.(?:net|var)\S*\s+"((?:[^"\\]|\\.)*)",\s*(-?\d+)\s+(-?\d+)')


def parse_vvp_ports(vvp_text: str, top: str) -> list[Port]:
    """Extract the elaborated top-level port list from an iverilog .vvp output file.

    `.port_info` lines give order, direction and elaborated width; the `.net`/`.var`
    declarations in the same scope give the declared [left:right] range.
    """
    lines = vvp_text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        m = _SCOPE_RE.match(ln)
        if m and m.group(3) == top and "S_" not in m.group(4):   # root scope has no parent
            start = i
            break
    if start is None:
        return []
    raw_ports: list[tuple[int, str, int, str]] = []
    ranges: dict[str, tuple[int, int]] = {}
    for ln in lines[start + 1:]:
        if ln.startswith("S_"):
            break
        pm = _PORT_RE.match(ln)
        if pm:
            raw_ports.append((int(pm.group(1)), pm.group(2).lower(), int(pm.group(3)), pm.group(4)))
            continue
        nm = _NET_RE.match(ln)
        if nm and nm.group(1) not in ranges:
            ranges[nm.group(1)] = (int(nm.group(2)), int(nm.group(3)))
    ports = []
    for _, d, w, name in sorted(raw_ports):
        msb, lsb = ranges.get(name, (w - 1, 0))
        if abs(msb - lsb) + 1 != w:
            msb, lsb = w - 1, 0
        ports.append(Port(name, d, w, msb, lsb))
    return ports


def run_iverilog(toolchain: Toolchain, args: list[str], timeout: float = 120) -> tuple[int, str]:
    proc = subprocess.run([str(toolchain.iverilog), *args], capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW, errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def common_args(language_flag: str, sources: list[Path], include_dirs: list[Path] | None,
                defines: dict[str, str] | None) -> list[str]:
    args = [language_flag]
    for d in include_dirs or sorted({Path(s).resolve().parent for s in sources}):
        args += ["-I", str(d)]
    for k, v in (defines or {}).items():
        args.append(f"-D{k}={v}" if v != "" else f"-D{k}")
    return args


def compile_design(toolchain: Toolchain, sources: list[Path], top: str, workdir: Path, *,
                   language: str = "auto", include_dirs: list[Path] | None = None,
                   defines: dict[str, str] | None = None, extra_args: list[str] | None = None,
                   extra_roots: list[str] | None = None) -> CompileResult:
    """Compile and elaborate the user's design with `top` as root and extract its ports."""
    t0 = time.perf_counter()
    workdir.mkdir(parents=True, exist_ok=True)
    sources = [Path(s) for s in sources]
    res = CompileResult(False, top, sources=sources, extra_args=list(extra_args or []),
                        extra_roots=list(extra_roots or []))
    if not sources:
        res.diagnostics.append(Diagnostic("error", "no Verilog source files in the project"))
        return res
    missing = [s for s in sources if not s.is_file()]
    if missing:
        res.diagnostics += [Diagnostic("error", f"source file not found: {s}") for s in missing]
        return res
    if not top:
        res.diagnostics.append(Diagnostic("error", "no top-level module selected"))
        return res
    res.language_flag = generation_flag(language, sources)
    out = workdir / "design_check.vvp"
    if out.exists():
        out.unlink()
    args = common_args(res.language_flag, sources, include_dirs, defines)
    # -Wall only for user RTL: vendor primitive libraries would flood the diagnostics.
    args += res.extra_args + ([] if res.extra_args else ["-Wall"])
    args += ["-Wno-timescale", "-s", top] + [a for r in res.extra_roots for a in ("-s", r)]
    args += ["-o", str(out)] + [str(s) for s in sources]
    res.command = [str(toolchain.iverilog), *args]
    try:
        rc, text = run_iverilog(toolchain, args)
    except subprocess.TimeoutExpired:
        res.diagnostics.append(Diagnostic("error", "iverilog timed out"))
        return res
    except OSError as exc:
        res.diagnostics.append(Diagnostic("error", f"cannot run iverilog: {exc}"))
        return res
    res.log = text
    res.diagnostics = parse_diagnostics(text)
    res.duration_s = time.perf_counter() - t0
    if rc != 0 or not out.is_file():
        if not res.errors:
            res.diagnostics.append(Diagnostic("error", f"iverilog failed with exit code {rc}"))
        return res
    res.ports = parse_vvp_ports(out.read_text(encoding="utf-8", errors="replace"), top)
    if not res.ports:
        res.diagnostics.append(Diagnostic("warning", f"top module '{top}' has no ports; nothing can "
                                          "be connected to the board"))
    res.output = out
    res.success = True
    return res
