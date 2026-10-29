"""FPGA Virtual Lab project files (*.fvlproj, JSON)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.mapping.mapping import Mapping

PROJECT_EXT = ".fvlproj"
FORMAT = "fvl-project-1"


@dataclass
class ResetConfig:
    """Optional design reset port pulsed by the Reset action."""
    port: str = ""            # top-level input name, e.g. "rst" ("" = none)
    active_high: bool = True
    cycles: int = 4

    def to_dict(self) -> dict:
        return {"port": self.port, "active_high": self.active_high, "cycles": self.cycles}

    @classmethod
    def from_dict(cls, d: dict | None) -> "ResetConfig":
        d = d or {}
        return cls(str(d.get("port", "")), bool(d.get("active_high", True)), int(d.get("cycles", 4)))


@dataclass
class Project:
    name: str = "Untitled"
    board: str = "basys3"
    sources: list[Path] = field(default_factory=list)       # absolute paths in memory
    top: str = ""
    mapping: Mapping = field(default_factory=Mapping)
    xdc: Path | None = None
    language: str = "auto"
    power_on_init: bool = True
    reset: ResetConfig = field(default_factory=ResetConfig)
    speed: str = "normal"
    record_waveforms: bool = True
    description: str = ""
    bitstream: Path | None = None   # Vivado .bit file: simulate its implemented netlist instead of RTL
    checkpoint: Path | None = None  # routed .dcp used for that (None = found next to the .bit)
    path: Path | None = None        # where the project file lives (None = unsaved)
    dirty: bool = False

    @property
    def is_bitstream(self) -> bool:
        return self.bitstream is not None

    @property
    def directory(self) -> Path | None:
        return self.path.parent if self.path else None

    # --------------------------------------------------------------- serialization
    def _rel(self, p: Path, base: Path | None) -> str:
        if base is None:
            return str(p)
        try:
            return Path(os.path.relpath(p, base)).as_posix()
        except ValueError:            # different drive on Windows
            return str(p)

    def to_dict(self, base: Path | None = None) -> dict:
        return {
            "format": FORMAT,
            "name": self.name,
            "board": self.board,
            "description": self.description,
            # In bitstream mode the netlist is regenerated from the .bit, so it is not stored.
            "sources": [] if self.is_bitstream else [self._rel(s, base) for s in self.sources],
            "bitstream": self._rel(self.bitstream, base) if self.bitstream else None,
            "checkpoint": self._rel(self.checkpoint, base) if self.checkpoint else None,
            "top": self.top,
            "xdc": self._rel(self.xdc, base) if self.xdc else None,
            "language": self.language,
            "power_on_init": self.power_on_init,
            "reset": self.reset.to_dict(),
            "simulation": {"speed": self.speed, "record_waveforms": self.record_waveforms},
            "mapping": self.mapping.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict, base: Path | None = None) -> "Project":
        if d.get("format") not in (FORMAT, None):
            raise ValueError(f"unsupported project format '{d.get('format')}'")

        def absolute(s: str) -> Path:
            p = Path(s)
            return (base / p).resolve() if base is not None and not p.is_absolute() else p

        sim = d.get("simulation", {})
        return cls(
            name=str(d.get("name", "Untitled")),
            board=str(d.get("board", "basys3")),
            description=str(d.get("description", "")),
            sources=[absolute(s) for s in d.get("sources", [])],
            top=str(d.get("top", "")),
            xdc=absolute(d["xdc"]) if d.get("xdc") else None,
            bitstream=absolute(d["bitstream"]) if d.get("bitstream") else None,
            checkpoint=absolute(d["checkpoint"]) if d.get("checkpoint") else None,
            language=str(d.get("language", "auto")),
            power_on_init=bool(d.get("power_on_init", True)),
            reset=ResetConfig.from_dict(d.get("reset")),
            speed=str(sim.get("speed", "normal")),
            record_waveforms=bool(sim.get("record_waveforms", True)),
            mapping=Mapping.from_dict(d.get("mapping")),
        )

    def save(self, path: Path | None = None) -> Path:
        path = Path(path or self.path or "")
        if not str(path) or path.is_dir():
            raise ValueError("no project file path given")
        if path.suffix.lower() != PROJECT_EXT:
            path = path.with_suffix(PROJECT_EXT)
        path = path.resolve()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(path.parent), indent=2), encoding="utf-8")
        os.replace(tmp, path)
        self.path = path
        self.dirty = False
        return path

    @classmethod
    def load(cls, path: Path) -> "Project":
        path = Path(path).resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} is not a valid project file: {exc}") from exc
        p = cls.from_dict(data, path.parent)
        p.path = path
        return p

    def missing_sources(self) -> list[Path]:
        if self.is_bitstream:
            return [] if self.bitstream.is_file() else [self.bitstream]
        return [s for s in self.sources if not s.is_file()]

    def add_sources(self, files: list[Path]) -> list[Path]:
        added = []
        known = {s.resolve() for s in self.sources}
        for f in files:
            f = Path(f).resolve()
            if f not in known:
                self.sources.append(f)
                known.add(f)
                added.append(f)
        if added:
            self.dirty = True
        return added

    def remove_source(self, f: Path) -> None:
        f = Path(f).resolve()
        self.sources = [s for s in self.sources if s.resolve() != f]
        self.dirty = True
