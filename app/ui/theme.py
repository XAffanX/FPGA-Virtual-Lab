"""Dark engineering-tool theme."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

ACCENT = "#3d8bfd"
BG0 = "#121418"     # deepest (sidebar)
BG1 = "#181b21"     # panels
BG2 = "#1f232b"     # inputs / cards
BORDER = "#2b303a"
TEXT = "#d6dbe3"
MUTED = "#8b94a3"
OK = "#3ddc84"
WARN = "#f0b429"
ERR = "#ff5c5c"

STATE_COLORS = {
    "No Design Loaded": MUTED, "Compilation Successful": OK, "Compilation Failed": ERR,
    "Simulation Ready": ACCENT, "Simulation Running": OK, "Simulation Paused": WARN,
    "Simulation Stopped": MUTED,
}

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QWidget#central {{ background: {BG1}; }}
QWidget {{ background: {BG1}; }}
QToolTip {{ background: {BG2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QListWidget#sidebar {{ background: {BG0}; border: none; border-right: 1px solid {BORDER}; padding-top: 6px;
    outline: 0; }}
QListWidget#sidebar::item {{ padding: 10px 14px; border-left: 3px solid transparent; color: {MUTED}; }}
QListWidget#sidebar::item:hover {{ background: #1a1e25; color: {TEXT}; }}
QListWidget#sidebar::item:selected {{ background: #1d2430; color: #ffffff; border-left: 3px solid {ACCENT}; }}
QToolBar {{ background: {BG0}; border: none; border-bottom: 1px solid {BORDER}; spacing: 2px; padding: 3px; }}
QToolBar QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 5px;
    padding: 4px 8px; }}
QToolBar QToolButton:hover {{ background: {BG2}; border-color: {BORDER}; }}
QToolBar QToolButton:pressed, QToolBar QToolButton:checked {{ background: #22314a; border-color: {ACCENT}; }}
QToolBar QToolButton:disabled {{ color: #535a66; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 4px 6px; }}
QPushButton {{ background: {BG2}; border: 1px solid {BORDER}; border-radius: 5px; padding: 5px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #22314a; }}
QPushButton:disabled {{ color: #59606b; border-color: #23272e; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #5a9dfd; }}
QPushButton#primary:disabled {{ background: #2a3a52; color: #7a8699; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget, QTableWidget {{
    background: {BG2}; border: 1px solid {BORDER}; border-radius: 4px; selection-background-color: #264f78; }}
QLineEdit, QComboBox, QSpinBox {{ padding: 4px 6px; }}
QComboBox QAbstractItemView {{ background: {BG2}; border: 1px solid {BORDER}; }}
QHeaderView::section {{ background: #1b1f26; border: none; border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER}; padding: 4px 6px; color: {MUTED}; }}
QTableWidget {{ gridline-color: {BORDER}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; top: -1px; }}
QTabBar::tab {{ background: {BG0}; padding: 6px 14px; border: 1px solid {BORDER}; border-bottom: none;
    color: {MUTED}; }}
QTabBar::tab:selected {{ background: {BG1}; color: {TEXT}; border-top: 2px solid {ACCENT}; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {BG0}; padding: 4px 8px; border-top: 1px solid {BORDER}; }}
QStatusBar {{ background: {BG0}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ padding: 0 8px; background: transparent; }}
QSplitter::handle {{ background: {BORDER}; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {BG1}; border: none; width: 11px; height: 11px; }}
QScrollBar::handle {{ background: #3a404b; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}
QLabel#pageTitle {{ font-size: 16pt; font-weight: 600; }}
QLabel#hint, QLabel#boardTitle {{ color: {MUTED}; }}
QLabel#boardTitle {{ font-weight: 600; }}
QFrame#card {{ background: {BG2}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#card QLabel {{ background: transparent; }}
QLabel#bigValue {{ font-size: 18pt; font-weight: 600; font-family: Consolas, monospace; }}
QCheckBox::indicator {{ width: 15px; height: 15px; }}
"""


def apply(app) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in ((QPalette.Window, BG1), (QPalette.WindowText, TEXT), (QPalette.Base, BG2),
                      (QPalette.AlternateBase, "#1c2027"), (QPalette.Text, TEXT), (QPalette.Button, BG2),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, "#264f78"),
                      (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, BG2),
                      (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, MUTED)):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
