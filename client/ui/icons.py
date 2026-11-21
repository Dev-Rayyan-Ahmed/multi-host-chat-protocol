# client/ui/icons.py
"""A small, consistent 24px stroke icon set (1.8px strokes, round caps), rendered via QtSvg."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS: dict[str, str] = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/>',
    "hash": '<path d="M5 9h15M4 15h15M10 3.5 8 20.5M16 3.5l-2 17"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/>'
             '<path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18.2 14.8c1.8.8 2.9 2.5 3.3 5.2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><path d="M12 7.6v.01" stroke-width="2.4"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/>',
    "send": '<path d="M21.5 2.5 10.5 13.5"/><path d="M21.5 2.5 15 21l-4.5-7.5L3 9z"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c.8-4.2 4-6.5 8-6.5s7.2 2.3 8 6.5"/>',
    "lock": '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2.2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M10.6 5.1A10.4 10.4 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-2.4 3.3M6.6 6.6C3.9 8.4 2 12 2 12s3.6 7 10 7a9.9 9.9 0 0 0 5.4-1.6"/>'
               '<path d="M9.9 9.9a3 3 0 0 0 4.2 4.2M3 3l18 18"/>',
    "server": '<rect x="3" y="3.5" width="18" height="7" rx="2"/><rect x="3" y="13.5" width="18" height="7" rx="2"/>'
              '<path d="M7 7h.01M7 17h.01" stroke-width="2.6"/>',
    "port": '<path d="M7 3v5M17 3v5"/><path d="M5 8h14v4a7 7 0 0 1-14 0z"/><path d="M12 19v2.5"/>',
    "check": '<path d="M4.5 12.5l5 5 10-11"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "shield": '<path d="M12 2.8 4.5 5.6v6.1c0 4.6 3.2 8.4 7.5 9.6 4.3-1.2 7.5-5 7.5-9.6V5.6z"/>',
    "shield-check": '<path d="M12 2.8 4.5 5.6v6.1c0 4.6 3.2 8.4 7.5 9.6 4.3-1.2 7.5-5 7.5-9.6V5.6z"/><path d="M8.8 12l2.3 2.3 4.3-4.6"/>',
    "copy": '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>',
    "zap": '<path d="M13 2.5 4.5 13.5H12l-1 8 8.5-11H12z"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    "arrow-down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "alert": '<path d="M10.3 3.9 2.4 17.6A2 2 0 0 0 4.1 20.6h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4"/><path d="M12 17v.01" stroke-width="2.4"/>',
    "alert-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><path d="M12 16.4v.01" stroke-width="2.4"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 3.5V8h4.5"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20.5V16h-4.5"/>',
    "message": '<path d="M20.5 14.5a2 2 0 0 1-2 2H8l-4.5 4V5.5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/>',
    "wifi-off": '<path d="M3 3l18 18"/><path d="M8.5 16.4a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 4.6-2.6M19 12.9a10 10 0 0 0-2.5-1.8M2 9.3a15 15 0 0 1 4.3-2.8M10.7 5.1A15 15 0 0 1 22 9.3"/><path d="M12 20h.01" stroke-width="2.6"/>',
    "at": '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-3.9 7.9"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
    "activity": '<path d="M22 12h-4l-3 8.5L9 3.5 6 12H2"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.7 5.6 3.7 9s-1.2 6.4-3.7 9c-2.5-2.6-3.7-5.6-3.7-9S9.5 5.6 12 3z"/>',
    "key": '<circle cx="7.5" cy="15.5" r="4"/><path d="M10.3 12.7 20 3M16.5 6.5l3 3M14.5 8.5l2 2"/>',
    "layers": '<path d="M12 2.5 2.5 7.5 12 12.5l9.5-5z"/><path d="M2.5 12.2 12 17.2l9.5-5M2.5 16.8 12 21.8l9.5-5"/>',
    "hourglass": '<path d="M6 2.5h12M6 21.5h12M7 2.5c0 4.5 5 6 5 9.5s-5 5-5 9.5M17 2.5c0 4.5-5 6-5 9.5s5 5 5 9.5"/>',
}


def svg(name: str, color: str = "#EDE8F3", stroke: float = 1.8) -> bytes:
    body = _PATHS[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    ).encode()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = "#EDE8F3", size: int = 18, stroke: float = 1.8) -> QPixmap:
    app = QGuiApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    dpr = max(dpr, 2.0)  # render crisp on any screen
    pm = QPixmap(QSize(round(size * dpr), round(size * dpr)))
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, color, stroke)))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str = "#A39AAA", hover: str | None = None, size: int = 18) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, hover or color, size), QIcon.Mode.Active)
    ic.addPixmap(pixmap(name, "#5A5266", size), QIcon.Mode.Disabled)
    return ic
