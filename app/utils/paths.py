"""Filesystem locations that work both from source and from a PyInstaller bundle."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_root() -> Path:
    """Folder holding read-only bundled data (configurations/, examples/, assets/)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def install_root() -> Path:
    """Folder containing the executable (or the source checkout)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return resource_root() / "configurations"


def native_dir() -> Path:
    """Prebuilt helper binaries (the fvl_init.vpi module)."""
    return resource_root() / "native"


def examples_dir() -> Path:
    return resource_root() / "examples"


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "FPGAVirtualLab"
    p.mkdir(parents=True, exist_ok=True)
    return p


def work_dir_for(project_dir: Path | None) -> Path:
    """Build folder used for compiler output and waveform files.

    Uses <project>/.fvl_build when the project folder is writable, otherwise a
    per-project folder under the user's AppData.
    """
    if project_dir is not None:
        p = Path(project_dir) / ".fvl_build"
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".write_test"
            probe.write_text("ok")
            probe.unlink()
            return p
        except OSError:
            import hashlib
            tag = hashlib.sha1(str(Path(project_dir).resolve()).encode()).hexdigest()[:10]
            p = user_data_dir() / "build" / f"{Path(project_dir).name}-{tag}"
    else:
        p = user_data_dir() / "build" / "untitled"
    p.mkdir(parents=True, exist_ok=True)
    return p
