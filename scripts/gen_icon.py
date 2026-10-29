"""Generate assets/icons/app.png and app.ico (a stylised FPGA chip with a green LED)."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

ROOT = Path(__file__).resolve().parents[1]


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    p.setBrush(QColor("#1e4d36"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 40 * s, 40 * s)
    p.setPen(QPen(QColor("#b9975b"), 8 * s))
    for k in range(5):
        o = (60 + k * 34) * s
        p.drawLine(o, 30 * s, o, 62 * s)
        p.drawLine(o, 194 * s, o, 226 * s)
        p.drawLine(30 * s, o, 62 * s, o)
        p.drawLine(194 * s, o, 226 * s, o)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#17191c"))
    p.drawRoundedRect(QRectF(56 * s, 56 * s, 144 * s, 144 * s), 14 * s, 14 * s)
    p.setPen(QColor("#e9efe9"))
    f = QFont("Segoe UI", max(6, int(40 * s)))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(56 * s, 66 * s, 144 * s, 80 * s), Qt.AlignCenter, "FPGA")
    p.setBrush(QColor("#46ff78"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(112 * s, 150 * s, 32 * s, 32 * s))
    p.end()
    return img


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 - needed for fonts
    out = ROOT / "assets" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    draw(256).save(str(out / "app.png"))
    draw(256).save(str(out / "app.ico"))
    print("wrote", out / "app.png", out / "app.ico")


if __name__ == "__main__":
    main()
