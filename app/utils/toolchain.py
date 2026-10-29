"""Locate the Icarus Verilog executables (iverilog / vvp)."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import install_root, resource_root

EXE = ".exe" if os.name == "nt" else ""

# Hide console windows for child processes when running as a GUI app on Windows.
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class ToolchainError(RuntimeError):
    pass


@dataclass
class Toolchain:
    iverilog: Path
    vvp: Path

    @property
    def bin_dir(self) -> Path:
        return self.iverilog.parent

    def version(self) -> str:
        try:
            out = subprocess.run([str(self.iverilog), "-V"], capture_output=True, text=True,
                                 timeout=15, creationflags=CREATE_NO_WINDOW)
            return (out.stdout or out.stderr).splitlines()[0].strip()
        except Exception as exc:  # pragma: no cover - diagnostic only
            return f"unknown ({exc})"


def _candidate_dirs(configured: str | None) -> list[Path]:
    dirs: list[Path] = []
    if configured:
        p = Path(configured)
        dirs += [p, p / "bin"]
    env = os.environ.get("FVL_IVERILOG_DIR")
    if env:
        dirs += [Path(env), Path(env) / "bin"]
    for root in {install_root(), resource_root()}:
        dirs.append(root / "tools" / "iverilog" / "bin")
    found = shutil.which("iverilog")
    if found:
        dirs.append(Path(found).parent)
    if os.name == "nt":
        dirs += [Path(r"C:\iverilog\bin"), Path(r"C:\Program Files\iverilog\bin"),
                 Path(r"C:\Program Files (x86)\iverilog\bin")]
    else:
        dirs += [Path("/usr/local/bin"), Path("/usr/bin"), Path("/opt/homebrew/bin")]
    return dirs


def find_toolchain(configured: str | None = None) -> Toolchain:
    for d in _candidate_dirs(configured):
        iv, vvp = d / f"iverilog{EXE}", d / f"vvp{EXE}"
        if iv.is_file() and vvp.is_file():
            return Toolchain(iv, vvp)
    raise ToolchainError(
        "Icarus Verilog was not found. Install it (Windows: `winget install Icarus.Verilog`, "
        "or https://bleyer.org/icarus/) or set its folder in Settings > Simulator.")


def try_find_toolchain(configured: str | None = None) -> Toolchain | None:
    try:
        return find_toolchain(configured)
    except ToolchainError:
        return None


__all__ = ["Toolchain", "ToolchainError", "find_toolchain", "try_find_toolchain",
           "CREATE_NO_WINDOW"]
