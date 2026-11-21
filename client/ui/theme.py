# client/ui/theme.py
"""Midnight Violet design tokens, fonts and the application style sheet."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

ASSETS = Path(__file__).resolve().parent / "assets"

# ---------------------------------------------------------------- color tokens
BG = "#0F0D14"          # main canvas
SIDEBAR = "#17131F"     # conversation navigation
SURFACE = "#1C1726"     # cards, composer, popovers
SURFACE_2 = "#221C2D"   # inputs on surfaces
HOVER = "#282033"       # hover + selected sub-panels
BORDER = "#2A2336"      # 1px hairlines
BORDER_STRONG = "#3A3148"
ACCENT = "#9A78E8"
ACCENT_HOVER = "#B39AE9"
ACCENT_PRESSED = "#8562D9"
ACCENT_SOFT = "#2E2546"  # tinted selection background
SUCCESS = "#63B995"
WARNING = "#D6A45E"
ERROR = "#EF4444"
ERROR_SOFT = "#F07B7B"
TEXT = "#EDE8F3"
TEXT_MUTED = "#A39AAA"
TEXT_SUBTLE = "#736A7E"

# Avatar fills: muted purple variations, picked deterministically per username
AVATAR_COLORS = [
    "#6E55B8", "#7D5FC9", "#5C4C99", "#8A5CAE", "#5B5BA6",
    "#76579E", "#6A4FA8", "#8467BE", "#53478C", "#7C5491",
]

# ---------------------------------------------------------------- spacing scale
S1, S2, S3, S4, S6 = 4, 8, 12, 16, 24
RADIUS_SM, RADIUS, RADIUS_LG = 6, 8, 12
SIDEBAR_WIDTH = 264

UI_FAMILIES = ["Inter", "Geist", "Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", "Arial"]
MONO_FAMILIES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Menlo", "Courier New"]

_ui_family = "Segoe UI"
_mono_family = "Consolas"


def load_fonts() -> None:
    """Loads bundled fonts (drop Inter / JetBrains Mono .ttf files into assets/fonts)
    and picks the best available families."""
    global _ui_family, _mono_family
    fonts_dir = ASSETS / "fonts"
    if fonts_dir.is_dir():
        for f in sorted(fonts_dir.glob("*.[ot]tf")):
            QFontDatabase.addApplicationFont(str(f))
    available = set(QFontDatabase.families())
    _ui_family = next((f for f in UI_FAMILIES if f in available), "Segoe UI")
    _mono_family = next((f for f in MONO_FAMILIES if f in available), "Consolas")


def ui_family() -> str:
    return _ui_family


def mono_family() -> str:
    return _mono_family


def font(size: float = 10.5, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False) -> QFont:
    f = QFont(_mono_family if mono else _ui_family)
    f.setPointSizeF(size)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def avatar_color(name: str) -> str:
    h = 0
    for ch in name.lower():
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return AVATAR_COLORS[h % len(AVATAR_COLORS)]


def qcolor(hex_color: str, alpha: int | None = None) -> QColor:
    c = QColor(hex_color)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def apply(app: QApplication) -> None:
    load_fonts()
    app.setFont(font(10))
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.Base, QColor(SURFACE_2))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_SUBTLE))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Link, QColor(ACCENT_HOVER))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())


def stylesheet() -> str:
    return f"""
* {{
    color: {TEXT};
    font-family: "{_ui_family}";
    outline: none;
}}
QWidget#AppRoot, QStackedWidget#Screens {{ background: {BG}; }}
QToolTip {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
    border-radius: 6px; padding: 5px 8px; font-size: 9pt;
}}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {TEXT_MUTED}; }}
QLabel[role="subtle"] {{ color: {TEXT_SUBTLE}; }}
QLabel[role="section"] {{
    color: {TEXT_SUBTLE}; font-size: 8pt; font-weight: 600; letter-spacing: 0.6px;
}}
QLabel[role="error"] {{ color: {ERROR_SOFT}; font-size: 9pt; }}
QLabel[role="mono"] {{ font-family: "{_mono_family}"; color: {TEXT}; }}

/* ---------- inputs ---------- */
QLineEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    padding: 0 12px; min-height: 40px; font-size: 10.5pt; selection-background-color: {ACCENT};
}}
QLineEdit:hover {{ border-color: {BORDER_STRONG}; }}
QLineEdit:focus {{ border-color: {ACCENT}; background: #241D30; }}
QLineEdit:disabled {{ color: {TEXT_SUBTLE}; background: {SURFACE}; }}
QLineEdit[invalid="true"] {{ border-color: {ERROR}; }}

QFrame#Field {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
}}
QFrame#Field:hover {{ border-color: {BORDER_STRONG}; }}
QFrame#Field[focused="true"] {{ border-color: {ACCENT}; background: #241D30; }}
QFrame#Field[invalid="true"] {{ border-color: {ERROR}; }}
QFrame#Field[disabled="true"] {{ background: {SURFACE}; }}
QFrame#Field QLineEdit {{
    background: transparent; border: none; padding: 0; min-height: 40px;
}}

/* ---------- buttons ---------- */
QPushButton {{
    border: none; border-radius: {RADIUS}px; padding: 0 16px; min-height: 36px;
    font-size: 10pt; font-weight: 600;
}}
QPushButton[kind="primary"] {{ background: {ACCENT}; color: #FFFFFF; min-height: 42px; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[kind="primary"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[kind="primary"]:disabled {{ background: #3D3357; color: #9C92B3; }}
QPushButton[kind="secondary"] {{
    background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
}}
QPushButton[kind="secondary"]:hover {{ background: {HOVER}; }}
QPushButton[kind="secondary"]:disabled {{ color: {TEXT_SUBTLE}; }}
QPushButton[kind="danger"] {{
    background: rgba(239, 68, 68, 0.08); color: {ERROR_SOFT}; border: 1px solid rgba(239, 68, 68, 0.45);
}}
QPushButton[kind="danger"]:hover {{ background: rgba(239, 68, 68, 0.16); }}
QPushButton[kind="ghost"] {{
    background: transparent; color: {TEXT_MUTED}; padding: 0 10px; font-weight: 500;
}}
QPushButton[kind="ghost"]:hover {{ background: {HOVER}; color: {TEXT}; }}
QPushButton[kind="link"] {{
    background: transparent; color: {ACCENT_HOVER}; padding: 0; min-height: 0; font-weight: 600;
}}
QPushButton[kind="link"]:hover {{ color: #CDB9F5; text-decoration: underline; }}
QPushButton:focus {{ outline: none; }}
QPushButton[kind="primary"]:focus {{ border: 2px solid {ACCENT_HOVER}; }}
QPushButton[kind="secondary"]:focus, QPushButton[kind="danger"]:focus {{ border-color: {ACCENT}; }}

QToolButton {{
    background: transparent; border: none; border-radius: {RADIUS_SM}px; padding: 4px;
}}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton:pressed {{ background: {BORDER_STRONG}; }}
QToolButton:focus {{ background: {HOVER}; }}
QToolButton:disabled {{ background: transparent; }}

QCheckBox {{ spacing: 8px; color: {TEXT_MUTED}; font-size: 9.5pt; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER_STRONG}; background: {SURFACE_2};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({(ASSETS / "check.svg").as_posix()}); }}

/* ---------- scroll bars ---------- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px 2px 0; }}
QScrollBar::handle:vertical {{ background: #2F2740; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: #43385A; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}

/* ---------- composer ---------- */
QFrame#Composer {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_LG}px;
}}
QFrame#Composer[focused="true"] {{ border-color: {ACCENT}; }}
QFrame#Composer[disabled="true"] {{ background: #17131F; border-color: {BORDER}; }}
QPlainTextEdit#ComposerInput {{
    background: transparent; border: none; font-size: 10.5pt; padding: 0;
    selection-background-color: {ACCENT};
}}

QMenu {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {HOVER}; }}
"""
