# client/ui/components/backdrop.py
"""
Cinematic backgrounds.

VideoBackdrop paints, in this order and *underneath* its child widgets:
    1. the current frame of the ping-pong black-hole video (cover-fit)
    2. a uniform black overlay (≈64 %)
    3. optional extra black radial / linear gradients ("shades") behind text
Child widgets (logo, cards, text) are painted afterwards by Qt, so the overlay never
darkens them.

All backdrops share one decoder (BlackHoleSource). It only plays while a backdrop is
visible, so the chat workspace never pays for video decoding.

The processed video is built by `python -m tools.make_pingpong` (FFmpeg). If it, or
Qt Multimedia, is missing, a static poster frame is shown and the reason is printed
once — the app never falls back to a plain looping video with a visible jump.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

ROOT = Path(__file__).resolve().parents[3]
BG_DIR = ROOT / "assets" / "backgrounds"
PINGPONG = BG_DIR / "purple_black_hole_pingpong.mp4"
POSTER = BG_DIR / "purple_black_hole_poster.jpg"

# Centre of the black hole's dark disk in the source video, as fractions of the frame.
# Measured over all 478 frames (dark-core / bright-ring contrast); it does not move.
HOLE_U, HOLE_V = 0.497, 0.461

OVERLAY_ALPHA = 0.68  # base black overlay; tuned on the per-pixel brightest frame of the whole loop
ON_VIDEO_TEXT = "#CFC7DA"  # secondary text that sits directly on the video (brighter than TEXT_MUTED)


def asset(*names: str) -> Path | None:
    """First existing file among candidate names in assets/backgrounds."""
    for n in names:
        p = BG_DIR / n
        if p.exists():
            return p
    return None


def focal_dest(img_w: int, img_h: int, w: float, h: float, fx: float) -> QRectF:
    """Destination rectangle that covers (w, h) with the image centre placed at x = fx * w."""
    if img_w <= 0 or img_h <= 0:
        return QRectF(0, 0, w, h)
    half = max(fx, 1 - fx) * w  # widest half that must be covered
    scale = max(2 * half / img_w, h / img_h)
    dw, dh = img_w * scale, img_h * scale
    return QRectF(fx * w - dw / 2, (h - dh) / 2, dw, dh)


def cover_rect(img_w: int, img_h: int, w: float, h: float) -> QRectF:
    """Source rectangle that fills (w, h) while preserving aspect ratio (CSS object-fit: cover)."""
    if img_w <= 0 or img_h <= 0 or w <= 0 or h <= 0:
        return QRectF(0, 0, max(img_w, 1), max(img_h, 1))
    scale = max(w / img_w, h / img_h)
    sw, sh = w / scale, h / scale
    return QRectF((img_w - sw) / 2, (img_h - sh) / 2, sw, sh)


class BlackHoleSource(QObject):
    """Single shared decoder for the ping-pong video."""

    frameReady = Signal()
    _instance: "BlackHoleSource | None" = None

    @classmethod
    def get(cls) -> "BlackHoleSource":
        if cls._instance is None:
            cls._instance = BlackHoleSource()
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self.image: QImage | None = None
        self.player = None
        self._users = 0
        self.status = ""
        poster = POSTER if POSTER.exists() else asset("empty_state_black_hole.png",
                                                      "Horizon_Relay_Empty_State_Black_Hole.png")
        if poster:
            self.image = QImage(str(poster))
        if not PINGPONG.exists():
            self.status = (f"Background video not built: {PINGPONG.name} is missing. "
                           "Run `python -m tools.make_pingpong` (requires FFmpeg). Showing a still image.")
        else:
            try:
                from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
            except ImportError:
                self.status = ("Qt Multimedia is not installed (pip install PySide6-Addons). "
                               "Showing a still image instead of the animated background.")
            else:
                self._can_play = True
        if self.status:
            print(f"[Horizon Relay] {self.status}", file=sys.stderr)

    _can_play = False

    def prepare(self) -> None:
        """Create the player up front (called before the main window is shown): loading the
        media backend takes a few hundred ms once, and must not hitch the visible animation."""
        if self.player is None and self._can_play:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

            self.player = QMediaPlayer(self)
            self.sink = QVideoSink(self)
            self.player.setVideoSink(self.sink)  # no QAudioOutput attached -> silent
            self.player.setSource(QUrl.fromLocalFile(str(PINGPONG)))
            self.player.setLoops(QMediaPlayer.Loops.Infinite)  # forward-only playback of the fwd+rev file
            self.sink.videoFrameChanged.connect(self._on_frame)
            self.player.errorOccurred.connect(self._on_error)

    def _on_frame(self, frame) -> None:
        img = frame.toImage()
        if not img.isNull():
            self.image = img
            self.frameReady.emit()

    def _on_error(self, _err, text: str) -> None:
        print(f"[Horizon Relay] Background video error: {text}", file=sys.stderr)

    def acquire(self) -> None:
        self._users += 1
        if self._users == 1:
            self.prepare()
            if self.player is not None:
                self.player.play()

    def release(self) -> None:
        self._users = max(0, self._users - 1)
        if self.player is not None and self._users == 0:
            self.player.pause()


class Shade:
    """An extra darkening gradient. Coordinates are fractions of the widget size,
    or a callable returning a QRectF/QPointF in pixels."""

    def __init__(self, kind: str, alpha: float, center=None, radius: float = 0.4,
                 x0: float = 0, x1: float = 1, y0: float = 0, y1: float = 0, rect=None) -> None:
        self.kind, self.alpha = kind, alpha
        self.center, self.radius = center, radius
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1
        self.rect = rect


def halo(w: QWidget, alpha: int = 230, blur: int = 14) -> QWidget:
    """Faint black halo around text drawn on the video: keeps glyph edges readable
    even when a bright streak passes directly behind them."""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    fx = QGraphicsDropShadowEffect(w)
    fx.setBlurRadius(blur)
    fx.setOffset(0, 0)
    fx.setColor(QColor(0, 0, 0, alpha))
    w.setGraphicsEffect(fx)
    return w


class VideoBackdrop(QWidget):
    def __init__(self, parent: QWidget | None = None, overlay: float = OVERLAY_ALPHA,
                 shades: list[Shade] | None = None, tint: QColor | None = None,
                 focus_x: float = 0.5) -> None:
        super().__init__(parent)
        self.source = BlackHoleSource.get()
        self.overlay = overlay
        self.shades = shades or []
        self.tint = tint
        self.focus_x = focus_x
        self._active = False
        self.source.frameReady.connect(self._repaint)

    def _repaint(self) -> None:
        if self._active:
            self.update()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if not self._active:
            self._active = True
            self.source.acquire()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        if self._active:
            self._active = False
            self.source.release()

    def video_rect(self) -> QRectF:
        """Where the video frame is drawn, in widget coordinates (same maths as painting)."""
        img = self.source.image
        iw, ih = (img.width(), img.height()) if img is not None and not img.isNull() else (1280, 720)
        w, h = self.width(), self.height()
        if abs(self.focus_x - 0.5) < 1e-3:
            src = cover_rect(iw, ih, w, h)
            sx, sy = w / src.width(), h / src.height()
            return QRectF(-src.x() * sx, -src.y() * sy, iw * sx, ih * sy)
        return focal_dest(iw, ih, w, h, self.focus_x)

    def video_point(self, u: float, v: float) -> QPointF:
        r = self.video_rect()
        return QPointF(r.x() + u * r.width(), r.y() + v * r.height())

    def paint_background(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(8, 6, 12))
        img = self.source.image
        if img is not None and not img.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if abs(self.focus_x - 0.5) < 1e-3:
                p.drawImage(QRectF(0, 0, w, h), img, cover_rect(img.width(), img.height(), w, h))
            else:
                p.drawImage(focal_dest(img.width(), img.height(), w, h, self.focus_x), img)
        p.fillRect(self.rect(), QColor(0, 0, 0, round(255 * self.overlay)))
        for s in self.shades:
            self._paint_shade(p, s, w, h)
        if self.tint is not None:
            p.fillRect(self.rect(), self.tint)

    def _paint_shade(self, p: QPainter, s: Shade, w: int, h: int) -> None:
        a = round(255 * s.alpha)
        if s.kind == "radial":
            if callable(s.center):
                c = s.center()
            else:
                cx, cy = s.center or (0.5, 0.5)
                c = QPointF(cx * w, cy * h)
            r = s.radius * max(w, h) if s.radius <= 2 else s.radius
            g = QRadialGradient(c, r)
            g.setColorAt(0.0, QColor(0, 0, 0, a))
            g.setColorAt(0.55, QColor(0, 0, 0, round(a * 0.6)))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), g)
        else:  # linear, from (x0,y0) transparent to (x1,y1) at alpha
            g = QLinearGradient(QPointF(s.x0 * w, s.y0 * h), QPointF(s.x1 * w, s.y1 * h))
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, a))
            area = s.rect() if callable(s.rect) else QRectF(self.rect())
            p.fillRect(area, g)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        self.paint_background(p)


class StaticBackdrop(QWidget):
    """Static cover-fit image with an optional black overlay (used by the workspace)."""

    _cache: dict[str, QPixmap] = {}

    def __init__(self, path: Path | None, overlay: float = 0.0, parent: QWidget | None = None,
                 fallback: QColor = QColor(15, 13, 20)) -> None:
        super().__init__(parent)
        self.overlay = overlay
        self.fallback = fallback
        self.pixmap: QPixmap | None = None
        if path is not None:
            key = str(path)
            if key not in self._cache:
                self._cache[key] = QPixmap(key)
            self.pixmap = self._cache[key]

    def paint_static(self, p: QPainter) -> None:
        p.fillRect(self.rect(), self.fallback)
        if self.pixmap is not None and not self.pixmap.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            pm = self.pixmap
            p.drawPixmap(QRectF(self.rect()), pm, cover_rect(pm.width(), pm.height(), self.width(), self.height()))
        if self.overlay > 0:
            p.fillRect(self.rect(), QColor(0, 0, 0, round(255 * self.overlay)))

    def paintEvent(self, _e) -> None:  # noqa: N802
        self.paint_static(QPainter(self))
