"""FPGA Virtual Lab - entry point.

    python main.py              start the GUI
    python main.py --selftest   headless check: compile and simulate the switch-to-LED
                                example with the real simulator; exit code 0 = OK
    python main.py --version
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path


def selftest() -> int:
    """Verify the toolchain and simulation backend without opening a window."""
    from app.board.board import load_board
    from app.compiler.compiler import compile_design
    from app.mapping.xdc import parse_xdc
    from app.project.project import Project
    from app.simulation.engine import BoardSimulator, EngineOptions
    from app.simulation.session import native_init_module
    from app.utils.paths import examples_dir, user_data_dir
    from app.utils.toolchain import find_toolchain

    report: dict = {"ok": False}
    try:
        tc = find_toolchain()
        report["simulator"] = tc.version()
        report["simulator_path"] = str(tc.iverilog)
        report["power_on_init_module"] = str(native_init_module() or "missing")
        board = load_board()
        proj = Project.load(examples_dir() / "switch_led" / "switch_led.fvlproj")
        with tempfile.TemporaryDirectory() as tmp:
            res = compile_design(tc, proj.sources, proj.top, Path(tmp) / "c")
            report["compile"] = res.success
            if not res.success:
                report["errors"] = [str(d) for d in res.errors]
                raise RuntimeError("compile failed")
            mapping = parse_xdc(proj.xdc, board, res.ports).mapping
            sim = BoardSimulator(tc, board, res, mapping, Path(tmp) / "s", EngineOptions())
            sim.program()
            try:
                sim.set_switch(0, True)
                on = sim.led(0)
                sim.set_switch(0, False)
                off = sim.led(0)
            finally:
                sim.stop()
        report["sw0_on_led0"] = on
        report["sw0_off_led0"] = off
        report["ok"] = on == 1 and off == 0
    except Exception as exc:
        report["error"] = f"{exc!r}"
        report["trace"] = traceback.format_exc()
    text = json.dumps(report, indent=2)
    try:
        (user_data_dir() / "selftest.json").write_text(text, encoding="utf-8")
    except OSError:
        pass
    if sys.stdout:
        print(text)
    return 0 if report["ok"] else 1


def selftest_bit(bit: str) -> int:
    """Headless check of the .bit path: header -> Vivado export -> netlist simulation -> 5 clock cycles."""
    from app.board.board import load_board
    from app.compiler.compiler import compile_design
    from app.simulation.engine import BoardSimulator, EngineOptions
    from app.utils.paths import user_data_dir
    from app.utils.toolchain import find_toolchain
    from app.vivado.vivado import find_vivado, load_bitstream, mapping_from_pins
    from app.vivado.validate import validate_bitstream
    from app.board.board import board_for_part

    report: dict = {"ok": False, "bitstream": bit}
    try:
        from app.vivado.bitfile import read_bit_header
        target = board_for_part(read_bit_header(Path(bit)).full_part)
        if target is None:
            raise RuntimeError("no supported virtual board for this bitstream's FPGA")
        report["board"] = target.virtual_name
        info = validate_bitstream(Path(bit), target)
        report["header"] = info.summary()
        viv = find_vivado()
        if viv is None:
            raise RuntimeError("Vivado not found")
        report["vivado"] = str(viv.root)
        tc = find_toolchain()
        report["simulator_path"] = str(tc.iverilog)
        d = load_bitstream(Path(bit), viv)
        board = target
        mapping, _ = mapping_from_pins(d, board)
        with tempfile.TemporaryDirectory() as tmp:
            res = compile_design(tc, [d.netlist], d.top, Path(tmp) / "c", extra_args=d.compile_args(),
                                 extra_roots=d.extra_roots)
            if not res.success:
                raise RuntimeError("; ".join(str(e) for e in res.errors[:3]))
            sim = BoardSimulator(tc, board, res, mapping, Path(tmp) / "s", EngineOptions(power_on_init=False))
            sim.program()
            try:
                report["leds_cycle0"] = sim.leds()
                sim.step(5)
                report["leds_cycle5"] = sim.leds()
                report["cycles"] = sim.cycles
            finally:
                sim.stop()
        report["ok"] = report["cycles"] == 5
    except Exception as exc:
        report["error"] = f"{exc!r}"
        report["trace"] = traceback.format_exc()
    text = json.dumps(report, indent=2)
    try:
        (user_data_dir() / "selftest_bit.json").write_text(text, encoding="utf-8")
    except OSError:
        pass
    if sys.stdout:
        print(text)
    return 0 if report["ok"] else 1


def main() -> int:
    if "--version" in sys.argv:
        from app import __version__
        print(__version__)
        return 0
    if "--selftest-bit" in sys.argv:
        return selftest_bit(sys.argv[sys.argv.index("--selftest-bit") + 1])
    if "--selftest" in sys.argv:
        return selftest()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    from app import APP_NAME
    from app.ui import theme
    from app.ui.main_window import MainWindow
    from app.utils.paths import resource_root

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("FPGAVirtualLab")
    icon = resource_root() / "assets" / "icons" / "app.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    theme.apply(app)
    win = MainWindow()
    win.show()
    for arg in sys.argv[1:]:
        if arg.lower().endswith(".bit") and Path(arg).is_file():
            win.vb.select_bitstream(Path(arg))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
