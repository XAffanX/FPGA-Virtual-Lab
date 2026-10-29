from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.board.board import load_board  # noqa: E402
from app.compiler.compiler import compile_design  # noqa: E402
from app.mapping.xdc import parse_xdc  # noqa: E402
from app.project.project import Project  # noqa: E402
from app.simulation.engine import BoardSimulator, EngineOptions  # noqa: E402
from app.utils.toolchain import try_find_toolchain  # noqa: E402

EXAMPLES = ROOT / "examples"


@pytest.fixture(scope="session")
def toolchain():
    tc = try_find_toolchain()
    if tc is None:
        pytest.skip("Icarus Verilog is not installed")
    return tc


@pytest.fixture(scope="session")
def board():
    return load_board("basys3")


@pytest.fixture
def open_example(toolchain, board, tmp_path):
    """Compile an example project, map it via its XDC and program the virtual FPGA."""
    sims = []

    def _open(folder: str, **opts) -> BoardSimulator:
        proj_file = next((EXAMPLES / folder).glob("*.fvlproj"))
        proj = Project.load(proj_file)
        res = compile_design(toolchain, proj.sources, proj.top, tmp_path / "compile")
        assert res.success, [str(d) for d in res.diagnostics]
        xdc = parse_xdc(proj.xdc, board, res.ports)
        assert not [i for i in xdc.issues if i.severity == "error"]
        sim = BoardSimulator(toolchain, board, res, xdc.mapping, tmp_path / "sim",
                             EngineOptions(**opts))
        sim.program()
        sims.append(sim)
        return sim

    yield _open
    for s in sims:
        s.stop()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
