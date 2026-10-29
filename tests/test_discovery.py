"""Finding the bitstream Vivado programs from Vivado's own records (recent projects, PROGRAM.FILE history)."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from app.vivado.discovery import candidate_bitstreams, programmed_files, recent_projects

FIX = Path(__file__).parent / "fixtures"


def make_vivado_dir(tmp: Path, xprs: list[Path], log_lines: list[str]) -> Path:
    user = tmp / "Xilinx" / "Vivado"
    (user / "2026.1").mkdir(parents=True)
    rec = "\n".join(f'<recent index="{i}" path="{x.as_posix()}$Artix-7, xc7a35tcpg236-1"/>' for i, x in enumerate(xprs))
    (user / "2026.1" / "newvivado.xml").write_text(f"<x><recentProjects>{rec}</recentProjects></x>")
    (user / "vivado.log").write_text("\n".join(log_lines))
    return user


def make_project(tmp: Path, name: str, bit: Path) -> tuple[Path, Path]:
    impl = tmp / name / f"{name}.runs" / "impl_1"
    impl.mkdir(parents=True)
    shutil.copy(bit, impl / bit.name)
    shutil.copy(bit.with_name(bit.stem + "_routed.dcp"), impl)
    xpr = tmp / name / f"{name}.xpr"
    xpr.write_text("<Project/>")
    return xpr, impl / bit.name


def test_recent_projects_and_program_file_history(tmp_path):
    xpr_a, bit_a = make_project(tmp_path, "proj_a", FIX / "vivado_counter" / "counter.bit")
    xpr_b, bit_b = make_project(tmp_path, "proj_b", FIX / "vivado_counter_rstn" / "counter_rstn.bit")
    old = time.time() - 600
    os.utime(bit_a, (old, old))
    user = make_vivado_dir(tmp_path, [xpr_a, xpr_b, tmp_path / "gone" / "gone.xpr"], [
        "set_property PROGRAM.FILE {C:/nowhere/missing.bit} [get_hw_devices xc7a35t_0]",
        f"set_property PROGRAM.FILE {{{bit_a.as_posix()}}} [get_hw_devices xc7a35t_0]",
        "program_hw_devices [get_hw_devices xc7a35t_0]"])
    assert recent_projects(user) == [xpr_a, xpr_b]            # missing projects skipped
    assert programmed_files(user)[0] == bit_a                 # last PROGRAM.FILE first
    cands = candidate_bitstreams(user)
    assert cands[0] == bit_a.resolve()                        # what Vivado programmed beats newer files
    assert bit_b.resolve() in cands and len(cands) == 2       # non-existent paths dropped


def test_no_vivado_records(tmp_path):
    assert candidate_bitstreams(tmp_path / "nothing") == []
