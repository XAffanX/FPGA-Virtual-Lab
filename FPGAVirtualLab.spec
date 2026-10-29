# PyInstaller spec for FPGA Virtual Lab (one-folder Windows build).
# Build with:  pyinstaller --noconfirm FPGAVirtualLab.spec   (or scripts\build_windows.ps1)
from pathlib import Path

ROOT = Path(SPECPATH)


def tree(src, dest, exclude=(".fvl_build", "__pycache__")):
    out = []
    for p in (ROOT / src).rglob("*"):
        if p.is_file() and not any(part in exclude for part in p.parts):
            out.append((str(p), str(Path(dest) / p.relative_to(ROOT / src).parent)))
    return out


datas = (tree("configurations", "configurations") + tree("examples", "examples") +
         tree("assets", "assets") + [(str(ROOT / "native" / "fvl_init.vpi"), "native")])

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=["PySide6.QtSvg", "PySide6.QtSvgWidgets"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FPGAVirtualLab",
    icon=str(ROOT / "assets" / "icons" / "app.ico"),
    version=str(ROOT / "installer" / "exe_version.txt"),   # author / copyright in file properties
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="FPGAVirtualLab")
