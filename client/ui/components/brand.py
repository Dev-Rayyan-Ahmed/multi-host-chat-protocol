# client/ui/components/brand.py
"""The single Horizon Relay identity: a faceted hexagon whose negative space forms
two relay pillars joined by a horizon link. Rendered from assets/logo.svg everywhere."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from client.ui import theme

LOGO_PATH = theme.ASSETS / "logo.svg"


@lru_cache(maxsize=1)
def _renderer() -> QSvgRenderer:
    return QSvgRenderer(str(LOGO_PATH))


def logo_pixmap(size: int, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _renderer().render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        ic.addPixmap(logo_pixmap(s, 1.0))
    return ic


class LogoMark(QWidget):
    def __init__(self, size: int = 32, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._size, self._size)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _renderer().render(p, QRectF(0, 0, self.width(), self.height()))


class BrandLockup(QWidget):
    """Logo + two-line wordmark. `scale` sizes the whole lockup consistently."""

    def __init__(self, scale: float = 1.0, stacked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        mark = LogoMark(round(34 * scale))
        top = QLabel("HORIZON")
        bottom = QLabel("RELAY")
        for lbl, color in ((top, theme.TEXT), (bottom, theme.ACCENT_HOVER)):
            f = theme.font(11.5 * scale, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6 * scale)
            lbl.setFont(f)
            lbl.setStyleSheet(f"color: {color};")
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(0)
        words.addWidget(top)
        words.addWidget(bottom)

        if stacked:
            lay = QVBoxLayout(self)
            lay.setSpacing(round(14 * scale))
            lay.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
            top.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            bottom.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        else:
            lay = QHBoxLayout(self)
            lay.setSpacing(round(11 * scale))
            lay.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(words)
