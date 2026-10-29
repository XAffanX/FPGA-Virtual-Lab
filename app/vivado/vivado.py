"""Load a Vivado bitstream into the simulator.

The configuration frames of a .bit file are not publicly documented, so they
are not decoded. Instead, Vivado's routed checkpoint (<top>_routed.dcp) from
the same implementation run is located next to the .bit and exported, via
Vivado in batch mode, as a functional simulation netlist made of 7-series
primitives (LUT, FDRE, CARRY4, ...). That netlist is the implemented design
the bitstream was generated from, together with the exact package pins it uses.
It is simulated with Xilinx's own UNISIM primitive models.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.paths import user_data_dir
from app.utils.toolchain import CREATE_NO_WINDOW

from .bitfile import BitInfo, read_bit_header

EXPORT_FORMAT = 3          # 3: also timing netlist + SDF + worst slack
GSR_PULSE_PS = 1     # global set/reset pulse at start-up (glbl.ROC_WIDTH); flip-flops load their INIT


class VivadoError(RuntimeError):
    pass


@dataclass
class Vivado:
    root: Path          # .../Vivado/<version> or .../<version>/Vivado

    @property
    def launcher(self) -> Path:
        return self.root / "bin" / ("vivado.bat" if os.name == "nt" else "vivado")

    @property
    def unisims(self) -> Path:
        return self.root / "data" / "verilog" / "src" / "unisims"

    @property
    def version(self) -> str:
        for part in reversed(self.root.parts):
            if part[:4].isdigit():
                return part
        return "?"


def _candidates(configured: str | None) -> list[Path]:
    out: list[Path] = []
    if configured:
        p = Path(configured)
        out += [p, p / "Vivado", p.parent]
    env = os.environ.get("FVL_VIVADO_DIR")
    if env:
        out.append(Path(env))
    found = shutil.which("vivado")
    if found:
        out.append(Path(found).resolve().parent.parent)
    for base in (Path("C:/AMDDesignTools"), Path("C:/Xilinx"), Path("D:/AMDDesignTools"), Path("D:/Xilinx"),
                 Path("/tools/Xilinx"), Path("/opt/Xilinx")):
        if not base.is_dir():
            continue
        # <base>/<ver>/Vivado (2025+ layout) and <base>/Vivado/<ver> (older layout), newest first
        out += sorted((d / "Vivado" for d in base.iterdir() if (d / "Vivado").is_dir()), reverse=True)
        if (base / "Vivado").is_dir():
            out += sorted((base / "Vivado").iterdir(), reverse=True)
    return out


def find_vivado(configured: str | None = None) -> Vivado | None:
    for c in _candidates(configured):
        v = Vivado(c)
        if v.launcher.is_file() and v.unisims.is_dir():
            return v
    return None


def find_checkpoint(bit: BitInfo) -> tuple[Path | None, list[str]]:
    """Locate the routed checkpoint of the implementation run that wrote `bit`."""
    folder = bit.path.parent
    notes: list[str] = []
    preferred = folder / f"{bit.design}_routed.dcp"
    cands = [preferred] if preferred.is_file() else sorted(folder.glob("*_routed.dcp"))
    if not cands:
        return None, [f"No routed checkpoint (*_routed.dcp) next to {bit.path.name}. Load the .bit from "
                      "its Vivado run folder (<project>.runs/impl_1) or choose the checkpoint manually."]
    dcp = cands[0]
    if dcp.name != preferred.name:
        notes.append(f"using {dcp.name} (expected {preferred.name})")
    dt = dcp.stat().st_mtime - bit.path.stat().st_mtime
    if dt > 60:
        notes.append(f"{dcp.name} is newer than {bit.path.name}: implementation was re-run after the "
                     "bitstream was written. Regenerate the bitstream to make sure they match.")
    return dcp, notes


_EXPORT_TCL = r"""
set dcp [lindex $argv 0]
set out [lindex $argv 1]
open_checkpoint $dcp
write_verilog -force -mode funcsim [file join $out netlist.v]
write_verilog -force -mode timesim -sdf_anno false [file join $out netlist_ts.v]
write_sdf -force -mode timesim [file join $out netlist.sdf]
set wns "null"
set tp [get_timing_paths -max_paths 1 -nworst 1 -setup -quiet]
if {[llength $tp] > 0} { set s [get_property SLACK $tp]; if {$s ne ""} { set wns $s } }
set f [open [file join $out pins.json] w]
puts $f "\{"
puts $f "  \"wns\": $wns,"
puts $f "  \"top\": \"[get_property TOP [current_design]]\","
puts $f "  \"part\": \"[get_property PART [current_design]]\","
puts $f "  \"ports\": \["
set first 1
foreach p [get_ports] {
  if {!$first} { puts $f "," }
  set first 0
  puts -nonewline $f "    \[\"[get_property NAME $p]\", \"[get_property DIRECTION $p]\", \"[get_property PACKAGE_PIN $p]\"\]"
}
puts $f "\n  \]"
puts $f "\}"
close $f
puts "FVL_EXPORT_OK"
"""


@dataclass
class BitstreamDesign:
    """A bitstream turned into something the simulator can run."""
    bit: BitInfo
    checkpoint: Path
    netlist: Path
    top: str
    part: str
    pins: dict[str, str]                 # port bit ("led[0]") -> package pin
    directions: dict[str, str]
    unisims: Path
    notes: list[str] = field(default_factory=list)
    cached: bool = False
    timing: bool = False                 # netlist carries Vivado's SDF delays
    wns_ns: float | None = None          # worst setup slack from Vivado (negative = fails timing)

    def compile_args(self) -> list[str]:
        """Extra iverilog arguments: UNISIM library search + short start-up reset."""
        return ["-y", str(self.unisims), "-Y", ".v", f"-Pglbl.ROC_WIDTH={GSR_PULSE_PS}"]

    @property
    def extra_roots(self) -> list[str]:
        return ["glbl"]      # Xilinx global set/reset module, defined inside the exported netlist


def _cache_dir(dcp: Path) -> Path:
    h = hashlib.sha1()
    with open(dcp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    d = user_data_dir() / "bitstreams" / h.hexdigest()[:16]
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_bitstream(bit_path: Path, vivado: Vivado, checkpoint: Path | None = None,
                   log=lambda msg: None, timeout: float = 900, timing: bool = False) -> BitstreamDesign:
    """Validate a .bit, find its checkpoint and export the implemented netlist (cached)."""
    bit = read_bit_header(Path(bit_path).resolve())
    log(f"Bitstream {bit.path.name}: {bit.summary()}")
    notes: list[str] = []
    if checkpoint is None:
        checkpoint, notes = find_checkpoint(bit)
        if checkpoint is None:
            raise VivadoError(notes[0])
    checkpoint = Path(checkpoint).resolve()
    for n in notes:
        log(n)
    out = _cache_dir(checkpoint)
    netlist, meta = out / "netlist.v", out / "pins.json"
    stamp = out / "export.json"
    cached = False
    if netlist.is_file() and meta.is_file() and stamp.is_file() and \
            json.loads(stamp.read_text()).get("format") == EXPORT_FORMAT:
        cached = True
        log(f"Using cached netlist export of {checkpoint.name}")
    else:
        tcl = out / "export.tcl"
        tcl.write_text(_EXPORT_TCL, encoding="utf-8")
        cmd = [str(vivado.launcher), "-mode", "batch", "-nojournal", "-nolog", "-notrace",
               "-source", str(tcl), "-tclargs", checkpoint.as_posix(), out.as_posix()]
        log(f"Running Vivado {vivado.version} in batch mode to export {checkpoint.name} "
            "(about 20-60 seconds)...")
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
                                  cwd=str(out), creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise VivadoError("Vivado did not finish the export in time") from exc
        except OSError as exc:
            raise VivadoError(f"cannot start Vivado: {exc}") from exc
        text = (proc.stdout or "") + (proc.stderr or "")
        (out / "vivado.log").write_text(text, encoding="utf-8")
        if "FVL_EXPORT_OK" not in text or not netlist.is_file():
            errs = [l for l in text.splitlines() if l.startswith(("ERROR", "CRITICAL"))]
            raise VivadoError("Vivado export failed: " + ("; ".join(errs[:4]) or f"exit code {proc.returncode}"))
        stamp.write_text(json.dumps({"format": EXPORT_FORMAT, "checkpoint": str(checkpoint),
                                     "vivado": vivado.version}))
        log(f"Vivado export finished in {time.monotonic() - t0:.0f}s")
    info = json.loads(meta.read_text(encoding="utf-8"))
    top, part = info["top"], info["part"]
    if top != bit.design:
        raise VivadoError(f"checkpoint {checkpoint.name} contains design '{top}' but the bitstream was "
                          f"generated for '{bit.design}'; they are from different runs")
    if bit.full_part not in part:
        notes.append(f"part mismatch: bitstream {bit.full_part}, checkpoint {part}")
    pins = {name: pin for name, _d, pin in info["ports"] if pin}
    dirs = {name: d for name, d, _p in info["ports"]}
    wns = info.get("wns")
    if timing:
        from .sdf import annotate_files
        ts, sdf, timed = out / "netlist_ts.v", out / "netlist.sdf", out / "netlist_timed.v"
        if not (ts.is_file() and sdf.is_file()):
            raise VivadoError("the timing netlist/SDF is missing from the export")
        if not timed.is_file() or timed.stat().st_mtime < max(ts.stat().st_mtime, sdf.stat().st_mtime):
            rep = annotate_files(ts, sdf, top, timed)
            log(f"Timing simulation: {rep.pins_delayed} pin delays from {rep.cells} cells annotated "
                f"(longest {rep.max_path_ps / 1000:.2f} ns)")
        netlist = timed
        if wns is not None and wns < 0:
            notes.append(f"Vivado timing: this design FAILS timing at 100 MHz (worst negative slack "
                         f"{wns:.3f} ns) - expect wrong values, as on the real board")
    return BitstreamDesign(bit, checkpoint, netlist, top, part, pins, dirs, vivado.unisims, notes, cached,
                           timing, wns)


def mapping_from_pins(design: BitstreamDesign, board) -> tuple["Mapping", list[str]]:
    """Board mapping from the package pins placed by Vivado (the pins the bitstream really uses)."""
    from app.mapping.mapping import Mapping
    m = Mapping()
    notes: list[str] = []
    for key, pin in sorted(design.pins.items()):
        r = board.by_pin(pin)
        if r is not None:
            m.entries[key] = r.id
        elif pin.upper() in board.unsupported_pins:
            notes.append(f"{key} uses pin {pin} ({board.unsupported_pins[pin.upper()]}), not simulated")
        else:
            notes.append(f"{key} uses pin {pin}, which is not a {board.name} I/O")
    return m, notes
