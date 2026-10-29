"""Find the .bit files Vivado is likely to program, without any setup in FPGA Virtual Lab.

Sources, all written by Vivado itself:
  * Vivado's logs/journals (%APPDATA%\\Xilinx\\Vivado\\vivado*.log|jou, and the same files in recent
    project folders): every `set_property PROGRAM.FILE {...}` names the exact file Hardware Manager
    programs - newest first.
  * Vivado's recent-projects list (%APPDATA%\\Xilinx\\Vivado\\<version>\\newvivado.xml / vivado.xml):
    the bitstreams in <project>/<name>.runs/impl_*/ of each recent project.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_PROGRAM_FILE = re.compile(r"PROGRAM\.FILE\s+(?:\{([^}]+)\}|\"([^\"]+)\"|(\S+\.bit))", re.I)
_RECENT = re.compile(r"<recent\b[^>]*\bpath=\"([^\"$]+\.xpr)", re.I)
LOG_TAIL_BYTES = 4 * 1024 * 1024


def vivado_user_dir() -> Path:
    override = os.environ.get("FVL_VIVADO_USER_DIR")      # tests
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "Xilinx" / "Vivado"


def _read_tail(path: Path, limit: int = LOG_TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def recent_projects(user_dir: Path | None = None) -> list[Path]:
    user_dir = user_dir or vivado_user_dir()
    out: list[Path] = []
    if not user_dir.is_dir():
        return out
    for xml in sorted(user_dir.glob("*/*vivado*.xml"), key=lambda p: p.stat().st_mtime, reverse=True):
        for m in _RECENT.finditer(_read_tail(xml, 8 * 1024 * 1024)):
            p = Path(m.group(1))
            if p not in out and p.is_file():
                out.append(p)
    return out


def _log_files(user_dir: Path, projects: list[Path]) -> list[Path]:
    logs = []
    for folder in [user_dir] + [p.parent for p in projects]:
        if folder.is_dir():
            logs += list(folder.glob("vivado*.log")) + list(folder.glob("vivado*.jou"))
    return sorted(set(logs), key=lambda p: p.stat().st_mtime, reverse=True)


def programmed_files(user_dir: Path | None = None, projects: list[Path] | None = None) -> list[Path]:
    """Bitstreams named in PROGRAM.FILE commands, most recently used first."""
    user_dir = user_dir or vivado_user_dir()
    projects = recent_projects(user_dir) if projects is None else projects
    out: list[Path] = []
    for log in _log_files(user_dir, projects):
        hits = [next(g for g in m.groups() if g) for m in _PROGRAM_FILE.finditer(_read_tail(log))]
        for h in reversed(hits):                          # last command in the log first
            p = Path(h.strip())
            if p.suffix.lower() == ".bit" and p not in out:
                out.append(p)
    return out


def project_bitstreams(projects: list[Path]) -> list[Path]:
    out: list[Path] = []
    for xpr in projects:
        runs = xpr.parent / f"{xpr.stem}.runs"
        if runs.is_dir():
            out += sorted(runs.glob("impl_*/*.bit"), key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def candidate_bitstreams(user_dir: Path | None = None) -> list[Path]:
    """Existing .bit files Vivado may be programming, most likely first."""
    user_dir = user_dir or vivado_user_dir()
    projects = recent_projects(user_dir)
    seen, out = set(), []
    for p in programmed_files(user_dir, projects) + project_bitstreams(projects):
        try:
            key = p.resolve()
        except OSError:
            continue
        if key not in seen and p.is_file():
            seen.add(key)
            out.append(key)
    return out
