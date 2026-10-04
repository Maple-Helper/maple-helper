"""Liquid-glass look in two neutral appearances: light (white glass, dark text) and
dark (black glass, white text). Maple orange is the only accent.

Tokens follow Apple's system colors (label / secondaryLabel / fills) for each appearance.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QProxyStyle, QSlider, QWidget

from ..store import ASSETS

ORANGE = "#FF9533"
ORANGE_DEEP = "#F07A12"
CREAM = "#FFF4E6"
RADIUS = 22

PALETTES = {
    "dark": {
        "glass": (28, 28, 30), "glass_alpha": 1.0, "solid_alpha": 1.0,
        "sheen": 14, "rim_top": 60, "rim": 22,
        "text": "#F5F5F7", "muted": "rgba(235,235,245,0.64)", "faint": "rgba(235,235,245,0.40)",
        "fill1": "rgba(255,255,255,0.08)", "fill2": "rgba(255,255,255,0.12)", "fill3": "rgba(255,255,255,0.20)",
        "pressed": "rgba(255,255,255,0.28)", "stroke": "rgba(255,255,255,0.14)", "hair": "rgba(255,255,255,0.07)",
        "scroll": "rgba(255,255,255,0.25)",
    },
    "light": {
        "glass": (242, 242, 247), "glass_alpha": 1.0, "solid_alpha": 1.0,
        "sheen": 0, "rim_top": 40, "rim": 30,
        "text": "#1D1D1F", "muted": "rgba(60,60,67,0.66)", "faint": "rgba(60,60,67,0.42)",
        "fill1": "#FFFFFF", "fill2": "#FFFFFF", "fill3": "#E5E5EA",
        "pressed": "rgba(230,230,235,0.95)", "stroke": "rgba(0,0,0,0.08)", "hair": "rgba(0,0,0,0.05)",
        "scroll": "rgba(0,0,0,0.25)",
    },
}
MODE = "dark"
HIGH_CONTRAST = False      # Windows high contrast is on (set_mode): no see-through grey text, firmer borders


def _contrast(c: dict) -> dict:
    """A palette for high contrast: secondary text in the full text color, the faint (disabled) grey as the
    normal muted one, and borders a reader can see."""
    rgb = "235,235,245" if c is PALETTES["dark"] else "0,0,0"
    return {**c, "muted": c["text"], "faint": c["muted"], "stroke": f"rgba({rgb},0.60)", "hair": f"rgba({rgb},0.35)"}


def qcolor(css: str):
    """A palette color as a QColor: "#RRGGBB" or "rgba(r,g,b,a)" with a 0-1 alpha (QColor can't read the latter)."""
    import re

    from PySide6.QtGui import QColor
    m = re.fullmatch(r"\s*rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)\s*", css or "")
    if not m:
        return QColor(css)
    r, g, b, a = m.groups()
    return QColor(int(r), int(g), int(b), round(float(a if a is not None else 1) * 255))


def P() -> dict:
    c = PALETTES.get(MODE, PALETTES["dark"])
    return _contrast(c) if HIGH_CONTRAST else c


ORANGE_TEXT_LIGHT = "#C9620A"     # orange as text on white: #FF9533 / #F07A12 are too faint to read there
# text and icons on an orange fill (the player's bubble, Primary / Send buttons, a checked chip, a hovered menu
# row): white, the brand look the owner chose (a darker text read better but changed the look)
ON_ORANGE = "#FFFFFF"
GOOD_TEXT_LIGHT = "#2E9E5B"
CHANGED = {"light": "#0A6CD6", "dark": "#64B5FF"}    # the "Changed in COT2" chip's text, readable on either glass


def accent_text(deep: bool = False) -> str:
    """Orange for text (links, chips, small marks): the brand orange on dark glass, a darker one on light.
    Orange fills (primary buttons, the user's bubble) keep the brand colors."""
    if MODE == "light":
        return ORANGE_TEXT_LIGHT
    return ORANGE_DEEP if deep else ORANGE


# legacy names still used by toasts/dialogs (resolved at call time through P())
TEXT = "#F5F5F7"
MUTED = "rgba(235,235,245,0.64)"
BORDER = "rgba(255,149,51,0.55)"

FONT_FAMILY = "Rubik"
ICON_FONT = "Segoe Fluent Icons"
ICON = {"open": "\ue8a7", "refresh": "\ue72c", "info": "\ue946", "edit": "\ue70f", "delete": "\ue74d", "add": "\ue710", "minimize": "\ue921", "close": "\ue8bb", "settings": "\ue713", "camera": "\ue722", "mic": "\ue720", "send": "\ue74a", "stop": "\ue71a", "copy": "\ue8c8", "star": "\ue734", "star_on": "\ue735", "plan": "\ue8fd", "book": "\ue736", "search": "\ue721", "tools": "\ue90f", "timer": "\ue916", "play": "\ue768", "check": "\ue73e", "route": "\ue707"}
# the same keys without an icon font (a trailing U+FE0E asks for the plain glyph, not the color emoji)
SYMBOL_ICONS = {"open": "\u2197", "refresh": "\u21bb", "info": "\u24d8", "edit": "\u270e", "delete": "\u232b", "add": "+", "minimize": "\u2013",
                "close": "\u2715", "settings": "\u2699\ufe0e", "camera": "\ud83d\udcf7\ufe0e", "mic": "\ud83c\udf99\ufe0e", "send": "\u27a4", "stop": "\u25a0",
                "copy": "\u29c9", "star": "\u2606", "star_on": "\u2605", "plan": "\u2261", "book": "\u2630", "search": "\u2315",
                "tools": "\u2692\ufe0e", "timer": "\u23f1\ufe0e", "play": "\u25b6\ufe0e", "check": "\u2713",
                "route": "\u2316"}


def high_contrast() -> str | None:
    """'dark' or 'light' when Windows high contrast (a Contrast theme) is on, by the system's window background;
    None when it's off or unknown. The app draws its own colors, so without this a contrast theme changed
    nothing (and the see-through grey text stayed)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class HIGHCONTRASTW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwFlags", wintypes.DWORD), ("lpszDefaultScheme", wintypes.LPWSTR)]
        hc = HIGHCONTRASTW()
        hc.cbSize = ctypes.sizeof(hc)
        user32 = ctypes.windll.user32
        if not user32.SystemParametersInfoW(0x0042, hc.cbSize, ctypes.byref(hc), 0) or not hc.dwFlags & 0x1:
            return None                                       # SPI_GETHIGHCONTRAST, HCF_HIGHCONTRASTON
        bg = user32.GetSysColor(5)                            # COLOR_WINDOW, 0x00BBGGRR
        r, g, b = bg & 0xFF, (bg >> 8) & 0xFF, (bg >> 16) & 0xFF
        return "light" if 0.299 * r + 0.587 * g + 0.114 * b > 128 else "dark"
    except Exception:
        return None


def set_mode(mode: str) -> None:
    """The appearance from Settings, unless Windows high contrast is on: then the matching light or dark look in
    its high-contrast palette (checked on every restyle, so turning it on applies when a window next opens)."""
    global MODE, TEXT, MUTED, HIGH_CONTRAST
    hc = high_contrast()
    HIGH_CONTRAST = hc is not None
    MODE = hc or (mode if mode in PALETTES else "dark")
    TEXT, MUTED = P()["text"], P()["muted"]


def load_fonts() -> str:
    fam = None
    for f in sorted((ASSETS / "fonts").glob("*.ttf")):
        fid = QFontDatabase.addApplicationFont(str(f))
        if fid >= 0 and not fam:
            fams = QFontDatabase.applicationFontFamilies(fid)
            fam = fams[0] if fams else None
    global ICON_FONT
    families = QFontDatabase.families()
    if ICON_FONT not in families:
        ICON_FONT = "Segoe MDL2 Assets"
    if ICON_FONT not in families and sys.platform != "win32":
        # macOS has no Segoe icon fonts: plain Unicode symbols, which the system fonts cover
        ICON_FONT = "Helvetica Neue"
        ICON.update(SYMBOL_ICONS)
    return fam or ("Helvetica Neue" if sys.platform == "darwin" else "Segoe UI")


def app_font(size: int = 14) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPixelSize(size)
    return f


def stylesheet(font_family: str, size: int, opacity: float = 1.0) -> str:
    s, c = size, P()
    ot, otd = accent_text(), accent_text(deep=True)      # orange text: darker on white for contrast
    good = "#2E9E5B" if MODE == "dark" else GOOD_TEXT_LIGHT
    install_focus_ring()
    return f"""
    QPushButton, QToolButton {{ outline: none; }}
    * {{ font-family: "{font_family}"; font-size: {s}px; color: {c['text']}; }}
    QWidget#Overlay, QWidget#Feed {{ background: transparent; }}
    #Title {{ font-size: {s + 1}px; font-weight: 600; letter-spacing: -0.2px; color: {c['text']}; }}
    #SaverBadge {{ font-size: {s - 4}px; font-weight: 600; color: {good}; background: rgba(52,199,89,0.14);
                   border-radius: 8px; padding: 1px 7px; }}
    QProgressBar#ExpBar {{ background: {c['fill3']}; border: none; border-radius: 3px; }}
    QProgressBar#ExpBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FFA24A, stop:1 {ORANGE_DEEP});
                                  border-radius: 3px; }}
    #ExpText {{ color: {c['muted']}; font-size: {s - 3}px; }}
    #PlanHead {{ color: {c['muted']}; font-size: {s - 2}px; font-weight: 600; padding-top: 6px; }}
    QPushButton#PlanLink {{ background: transparent; border: none; padding: 2px 0; text-align: left; color: {c['text']}; }}
    QPushButton#PlanLink:hover {{ color: {ot}; }}
    QTextBrowser#GuideText {{ background: {c['fill1']}; border: 1px solid {c['hair']}; border-radius: 14px;
                               padding: 10px 12px; color: {c['text']}; selection-background-color: {ORANGE};
                               selection-color: {ON_ORANGE}; }}
    #PinAnswer {{ color: {c['text']}; font-size: {s - 1}px; }}
    QFrame#ShareCard {{ background: {c['fill1']}; border: 1px solid {c['stroke']}; border-radius: 18px; }}
    #ShareName {{ font-size: {s + 8}px; font-weight: 700; color: {c['text']}; }}
    #ShareMeta {{ font-size: {s + 1}px; font-weight: 500; color: {c['muted']}; }}
    #ShareBrand {{ font-size: {s - 3}px; font-weight: 600; color: {ot}; }}
    #BetaBadge {{ font-size: {s - 5}px; font-weight: 700; color: {accent_text()}; background: transparent;
                  border: 1px solid rgba(255,149,51,0.6); border-radius: 5px; padding: 0 4px; min-height: 0; }}
    #ScopeNote {{ color: {c['faint']}; font-size: {s - 4}px; }}
    #Version {{ font-size: {s - 3}px; font-weight: 300; color: {c['muted']}; background: transparent; }}
    #ProfilePill {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 12px;
                    min-height: 24px; max-height: 24px; padding: 0 11px; font-size: {s - 2}px; font-weight: 500; color: {c['text']}; }}
    #ProfilePill:hover {{ background: {c['fill3']}; }}
    #ProfilePill:pressed {{ background: {c['pressed']}; }}
    QToolButton#Icon {{ font-family: "{ICON_FONT}"; font-size: 14px; color: {c['muted']}; background: transparent;
                        border: none; border-radius: 14px; min-width: 28px; min-height: 28px; }}
    QToolButton#Icon:hover {{ background: {c['fill2']}; color: {c['text']}; }}
    QToolButton#Icon:pressed {{ background: {c['fill3']}; }}
    QToolButton#Icon[active="true"] {{ color: #FF453A; }}
    QToolButton#Icon[unread="true"] {{ color: {ORANGE}; }}
    QToolButton#Icon[wished="true"] {{ color: {ot}; }}
    #HeaderSep {{ background: {c['stroke']}; border: none; }}
    QToolButton#IconClose {{ font-family: "{ICON_FONT}"; font-size: 11px; color: {ot}; background: transparent;
                             border: none; border-radius: 14px; min-width: 28px; min-height: 28px; }}
    QToolButton#IconClose:hover {{ background: {ORANGE}; color: #FFFFFF; }}
    QToolButton#IconClose:pressed {{ background: {ORANGE_DEEP}; color: #FFFFFF; }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 6px; margin: 4px 1px; }}
    QScrollBar::handle:vertical {{ background: {c['scroll']}; border-radius: 3px; min-height: 28px; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}

    #CharacterRow {{ background: transparent; border: none; }}
    #Check {{ color: {ot}; font-size: {s + 2}px; font-weight: 700; }}
    QPushButton#IconDanger {{ font-family: "{ICON_FONT}"; font-size: 13px; color: {c['muted']}; background: transparent;
                              border: none; border-radius: 13px; min-width: 26px; max-width: 26px;
                              min-height: 26px; max-height: 26px; }}
    QPushButton#IconDanger:hover {{ color: #FF3B30; background: {c['fill3']}; }}
    QPushButton#IconPlain {{ font-family: "{ICON_FONT}"; font-size: 13px; color: {c['muted']}; background: transparent;
                             border: none; border-radius: 13px; min-width: 26px; max-width: 26px;
                             min-height: 26px; max-height: 26px; }}
    QPushButton#IconPlain:hover {{ color: {ot}; background: {c['fill3']}; }}
    #Stepper {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 10px; }}
    QToolButton#StepBtn {{ background: transparent; border: none; border-radius: 8px; color: {ot};
                           font-size: {s + 4}px; font-weight: 600; min-width: 30px; min-height: 28px; }}
    QToolButton#StepBtn:hover {{ background: {c['fill3']}; }}
    QToolButton#StepBtn:pressed {{ background: {c['pressed']}; }}
    QToolButton#StepBtn:disabled {{ color: {c['faint']}; }}
    QLineEdit#StepValue {{ background: transparent; border: none; font-weight: 600; padding: 0; color: {c['text']}; }}
    #JobFixed {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 8px; min-height: 28px;
                 max-height: 28px; padding: 0 12px; font-weight: 500; color: {c['text']}; }}
    #InfoNote {{ background: {"rgba(255,149,51,0.12)" if MODE == "dark" else "rgba(255,149,51,0.10)"};
                 border: 1px solid rgba(255,149,51,0.35); border-radius: 12px; }}
    #InfoIcon {{ font-family: "{ICON_FONT}"; font-size: 15px; color: {ot}; }}
    #InfoText {{ color: {c['text']}; font-size: {s - 1}px; }}
    #JobHint {{ color: {c['muted']}; font-size: {s - 3}px; }}
    #ProfileCard {{ background: {c['fill1']}; border: 1px solid {c['hair']}; border-radius: 16px; }}
    #ProfileCard:hover, #ProfileCard[active="true"] {{ border: 1px solid rgba(255,149,51,0.7); }}
    QToolButton#Refresh {{ font-family: "{ICON_FONT}"; font-size: 15px; color: {c['muted']}; background: transparent;
                           border: none; border-radius: 15px; min-width: 30px; max-width: 30px; min-height: 30px;
                           max-height: 30px; }}
    QToolButton#Refresh:hover {{ background: {c['fill3']}; color: {ot}; }}
    QToolButton#Refresh:disabled {{ color: {ot}; }}
    QToolButton#Refresh:checked {{ color: {ot}; background: {c['fill3']}; }}
    #ProfileName {{ font-size: {s + 1}px; font-weight: 600; color: {c['text']}; }}
    #ProfileMeta {{ font-size: {s - 1}px; font-weight: 500; color: {c['muted']}; }}
    #BubbleUser {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFA24A, stop:1 {ORANGE_DEEP});
                   border-radius: 15px; min-height: 32px; }}
    #BubbleUser QLabel {{ color: {ON_ORANGE}; }}
    #BubbleBot {{ background: {c['fill1']}; border: 1px solid {c['hair']}; border-radius: 15px; min-height: 32px; }}
    #SystemLine {{ color: {c['muted']}; font-size: {s - 2}px; }}

    #Card {{ background: {c['fill1']}; border: 1px solid {c['hair']}; border-radius: 14px; }}
    #Card:hover {{ background: {c['fill2']}; border: 1px solid rgba(255,149,51,0.7); }}
    #GroupHeader {{ background: transparent; border: 1px solid transparent; border-radius: 10px; }}
    #GroupHeader:hover {{ border: 1px solid rgba(255,149,51,0.7); }}
    #TileGridTitle {{ color: {c['muted']}; font-size: {s - 2}px; font-weight: 600; }}
    #BubbleTag {{ color: rgba(255,255,255,0.85); font-size: {s - 3}px; font-weight: 600; }}
    #FocusBar {{ background: rgba(255,149,51,0.12); border: 1px solid rgba(255,149,51,0.55); border-radius: 12px; }}
    QPushButton#TagChip {{ background: {"rgba(255,255,255,0.10)" if MODE == "dark" else "#FFFFFF"};
                           border: 1px solid rgba(255,149,51,0.55); border-radius: 12px; min-height: 24px;
                           max-height: 24px; padding: 0 8px; font-size: {s - 2}px; font-weight: 600; color: {c['text']}; }}
    QPushButton#TagChip:hover {{ background: rgba(255,149,51,0.20); }}
    #FocusText {{ color: {c['text']}; font-size: {s - 1}px; }}
    #TileGrid {{ background: {c['fill1']}; border: 1px solid {c['hair']}; border-radius: 14px; }}
    #Tile {{ background: {"rgba(255,255,255,0.05)" if MODE == "dark" else "#F7F7F9"}; border: 1px solid {c['hair']};
             border-radius: 10px; }}
    #Tile:hover {{ border: 1px solid rgba(255,149,51,0.7); }}
    #Card[selected="true"], #Tile[selected="true"], #GroupHeader[selected="true"] {{
        border: 2px solid {ORANGE}; background: rgba(255,149,51,0.12); }}
    #TileName {{ font-size: {s - 1}px; font-weight: 500; color: {c['text']}; }}
    #StatPill {{ font-size: {s - 2}px; font-weight: 500; color: {c['text']}; background: {c['fill3']};
                 border-radius: 7px; padding: 1px 7px; }}
    #TileStats {{ font-size: {s - 3}px; color: {c['muted']}; }}
    #CardName {{ font-weight: 600; color: {c['text']}; }}
    #CardSub {{ color: {c['muted']}; font-size: {s - 2}px; }}
    #CardStat {{ color: {c['text']}; font-size: {s - 2}px; }}
    #CardCredit {{ color: {c['faint']}; font-size: {s - 4}px; }}

    QPushButton#Chip {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 14px;
                        min-height: 28px; max-height: 28px; padding: 0 13px; font-size: {s - 2}px; font-weight: 500; color: {c['text']}; }}
    QPushButton#Chip:hover {{ background: {c['fill3']}; }}
    QPushButton#Chip:pressed {{ background: {c['pressed']}; }}
    QPushButton#Chip:checked {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFA24A, stop:1 {ORANGE_DEEP});
                                border: 1px solid {ORANGE_DEEP}; color: {ON_ORANGE}; font-weight: 700; }}
    QPushButton#SubChip {{ background: transparent; border: 1px solid {c['stroke']}; border-radius: 10px;
                           min-height: 26px; max-height: 26px; padding: 0 10px; font-size: {s - 3}px; font-weight: 500;
                           color: {c['muted']}; }}
    QPushButton#SubChip:hover {{ background: {c['fill3']}; color: {c['text']}; }}
    QPushButton#SubChip:checked {{ background: rgba(255,149,51,0.14); border: 1.5px solid {ORANGE}; color: {otd};
                                   font-weight: 700; }}
    #Tag, #TagGood, #TagWarn, #TagAccent {{ font-size: {s - 3}px; font-weight: 600; border-radius: 8px; padding: 2px 8px; }}
    #Tag {{ color: {c['muted']}; background: {c['fill3']}; }}
    #TagGood {{ color: {good}; background: rgba(52,199,89,0.16); }}
    #TagWarn {{ color: #C9620A; background: rgba(255,149,51,0.18); }}
    #TagAccent {{ color: {otd}; background: rgba(255,149,51,0.12); }}
    /* where a datum comes from (sources.py): quieter than the tags above, an outline beside the data */
    #SourceTag {{ font-size: {s - 4}px; font-weight: 600; color: {c['muted']}; background: transparent;
                  border: 1px solid {c['stroke']}; border-radius: 7px; padding: 1px 5px; }}
    /* a community drop's votes: "16 ✓" in green, "single report" (one player alone) in the warning colour */
    #VoteTag {{ font-size: {s - 4}px; font-weight: 600; color: {good}; background: transparent; padding: 0 1px; }}
    #VoteTag[single="true"] {{ color: #C9620A; }}
    #UpdatedTag {{ font-size: {s - 4}px; font-weight: 700; color: {otd}; background: rgba(255,149,51,0.14);
                   border: 1px solid rgba(255,149,51,0.45); border-radius: 7px; padding: 1px 5px; }}
    /* a skill whose values changed between two builds (sitedata.py): blue, apart from the orange "Updated" */
    #ChangedTag {{ font-size: {s - 4}px; font-weight: 700; color: {CHANGED[MODE]}; background: rgba(10,132,255,0.12);
                   border: 1px solid rgba(10,132,255,0.40); border-radius: 7px; padding: 1px 5px; }}
    #BigStat {{ font-size: {s + 10}px; font-weight: 700; letter-spacing: -0.4px; color: {c['text']}; }}
    #BigStatLabel {{ font-size: {s - 3}px; color: {c['muted']}; }}
    QPushButton#NowChip {{ background: rgba(255,149,51,0.12); border: 1px solid rgba(255,149,51,0.55); border-radius: 12px;
                           min-height: 24px; max-height: 24px; padding: 0 11px; font-size: {s - 2}px; font-weight: 600;
                           color: {otd}; }}
    QPushButton#NowChip:hover {{ background: rgba(255,149,51,0.22); }}
    QPushButton#NowChip:pressed {{ background: rgba(255,149,51,0.32); }}
    #ShotHint {{ color: {c['muted']}; font-size: {s - 3}px; padding: 0 6px 2px 6px; }}
    #ToolHeader {{ font-size: {s - 1}px; color: {c['muted']}; }}

    #Capsule {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 21px; }}
    #Capsule[focus="true"] {{ border: 1px solid rgba(255,149,51,0.85); }}
    QLineEdit#Input {{ background: transparent; border: none; padding: 0 4px; selection-background-color: {ORANGE};
                       selection-color: {ON_ORANGE};
                       color: {c['text']}; }}
    QToolButton#Send {{ font-family: "{ICON_FONT}"; font-size: 13px; color: {ON_ORANGE}; border: none; border-radius: 15px;
                        min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFA24A, stop:1 {ORANGE_DEEP}); }}
    QToolButton#Send:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFB066, stop:1 #F58A2A); }}
    QToolButton#Send:pressed {{ background: {ORANGE_DEEP}; }}
    QToolButton#Send:disabled {{ background: {c['fill2']}; color: {c['faint']}; }}

    QPushButton#Primary {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFA24A, stop:1 {ORANGE_DEEP});
                           color: {ON_ORANGE}; border: none; border-radius: 12px; min-height: 26px; padding: 4px 18px; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFB066, stop:1 #F58A2A); }}
    QPushButton#Primary:pressed {{ background: {ORANGE_DEEP}; }}
    QPushButton#Primary:disabled {{ background: {c['fill2']}; color: {c['faint']}; }}
    QPushButton#Danger {{ background: #FF3B30; color: #FFFFFF; border: none; border-radius: 12px; min-height: 26px;
                          padding: 4px 18px; font-weight: 600; }}
    QPushButton#Danger:hover {{ background: #FF5147; }}
    QPushButton#Danger:pressed {{ background: #D70015; }}
    QPushButton#Secondary {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 12px; min-height: 26px; padding: 4px 18px; }}
    QPushButton#Secondary:hover {{ background: {c['fill3']}; }}
    QPushButton#Quick {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 14px; min-height: 30px; padding: 4px 12px; }}
    QPushButton#Quick:hover, QToolButton#ClassTile:hover {{ background: {c['fill3']}; }}
    QPushButton#Quick:checked, QToolButton#ClassTile:checked {{ background: rgba(255,149,51,0.12); border: 2px solid {ORANGE}; font-weight: 600; }}
    QToolButton#ClassTile {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 14px; padding: 4px 4px;
                             color: {c['text']}; }}
    QLineEdit {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 10px; padding: 7px 10px; }}
    QLineEdit:focus {{ border: 1px solid rgba(255,149,51,0.85); }}

    /* grouped settings (iOS inset-grouped) */
    #SectionHeader {{ color: {c['muted']}; font-size: {s - 2}px; font-weight: 500; padding: 0 14px; }}
    #Group {{ background: {c['fill1']}; border: 1px solid {c['hair']}; border-radius: 14px; }}
    #Separator {{ background: {c['hair']}; border: none; }}
    #RowLabel {{ color: {c['text']}; }}
    #DialogBody {{ font-size: {s + 1}px; color: {c['text']}; line-height: 140%; }}
    #RowHint {{ color: {c['muted']}; font-size: {s - 3}px; }}
    #WarnHint {{ color: {ORANGE if MODE == "dark" else ORANGE_TEXT_LIGHT}; font-size: {s - 3}px; font-weight: 500; }}
    #PageTitle {{ font-size: {s + 8}px; font-weight: 700; letter-spacing: -0.3px; color: {c['text']}; }}
    #PageBody {{ color: {c['muted']}; }}
    #FieldLabel {{ color: {c['muted']}; font-size: {s - 2}px; font-weight: 500; }}
    QPushButton#Link, QPushButton#LinkDanger {{ background: transparent; border: none; min-height: 34px;
                        font-weight: 500; text-align: left; padding: 0; color: {ot}; }}
    QPushButton#LinkDanger {{ color: #FF453A; }}
    QPushButton#Link:hover {{ color: {otd}; text-decoration: underline; }}
    QPushButton#LinkDanger:hover {{ color: #FF3B30; text-decoration: underline; }}
    QPushButton#Link:pressed, QPushButton#LinkDanger:pressed {{ color: {c['muted']}; }}
    QPushButton#Link:disabled, QPushButton#LinkDanger:disabled {{ color: {c['faint']}; text-decoration: none; }}

    #Segmented {{ background: {"rgba(118,118,128,0.24)" if MODE == "dark" else "#E3E3E8"}; border: none;
                  border-radius: 10px; }}
    QPushButton#Segment {{ background: transparent; border: none; border-radius: 8px; min-height: 26px; max-height: 26px;
                           padding: 0 12px; font-size: {s - 2}px; font-weight: 500; color: {c['text']}; }}
    QPushButton#Segment:checked {{ background: {"#636366" if MODE == "dark" else "#FFFFFF"};
                                   border: 1px solid {"rgba(255,255,255,0.10)" if MODE == "dark" else "rgba(0,0,0,0.10)"};
                                   font-weight: 700; color: {c['text']}; }}
    QPushButton#Segment:!checked {{ color: {c['muted']}; }}
    QPushButton#Segment:hover:!checked {{ background: {c['fill1']}; }}

    QPushButton#Select {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 8px;
                          min-height: 28px; max-height: 28px; padding: 0 28px; color: {c['text']};
                          text-align: left; font-weight: 500; }}
    QPushButton#Select:hover {{ background: {c['fill3']}; }}
    QPushButton#Select:pressed {{ background: {c['pressed']}; }}
    QComboBox {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 10px; min-height: 26px;
                 padding: 0 10px; color: {c['text']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
    QComboBox QAbstractItemView {{ background: {"#2C2C2E" if MODE == "dark" else "#FFFFFF"}; color: {c['text']};
                                   border: 1px solid {c['stroke']}; border-radius: 10px; padding: 4px; outline: none;
                                   selection-background-color: {ORANGE}; selection-color: {ON_ORANGE}; }}
    QSpinBox {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 10px; min-height: 26px;
                padding: 0 8px; color: {c['text']}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}

    QSlider::groove:horizontal {{ height: 6px; background: {"rgba(255,255,255,0.22)" if MODE == "dark" else "rgba(0,0,0,0.13)"};
                                  border-radius: 3px; }}
    QSlider::add-page:horizontal {{ background: {"rgba(255,255,255,0.22)" if MODE == "dark" else "rgba(0,0,0,0.13)"};
                                    border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {ORANGE}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: #FFFFFF; width: 24px; height: 24px; margin: -9px 0; border-radius: 12px;
                                  border: 1px solid rgba(0,0,0,0.14); }}

    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item {{ padding: 8px 4px; border-radius: 8px; color: {c['text']}; }}
    QListWidget::item:hover {{ background: {c['fill1']}; }}
    QListWidget::item:selected {{ background: rgba(255,149,51,0.22); color: {c['text']}; }}

    QMenu {{ background: {"rgba(44,44,46,0.98)" if MODE == "dark" else "rgba(255,255,255,0.98)"};
             border: 1px solid {c['stroke']}; border-radius: 12px; padding: 5px; }}
    QMenu::item {{ padding: 6px 16px 6px 28px; border-radius: 7px; color: {c['text']}; min-width: 64px; }}
    QMenu::item:selected {{ background: {ORANGE}; color: {ON_ORANGE}; }}
    QMenu::item:disabled {{ color: {c['muted']}; font-weight: 600; font-size: {s - 2}px; }}
    QMenu::indicator {{ width: 14px; height: 14px; left: 8px; }}
    QMenu::separator {{ height: 1px; background: {c['hair']}; margin: 4px 8px; }}
    QMenu#SplitMenu {{ background: transparent; border: none; }}
    QMenu#SplitMenu::item {{ margin: 0 5px; }}
    #MenuRow {{ border-radius: 7px; background: transparent; }}
    #MenuRow:hover {{ background: {ORANGE}; }}
    #MenuRow:disabled {{ background: transparent; }}
    /* Qt ignores a pseudo-state on an ancestor ("#MenuRow:hover #MenuRowText" matched every row, and the
       ":disabled" one painted every label grey), so the label is styled by its own state: a child of a disabled
       row is disabled itself, and the hover comes as a property set by MenuRowHover (installed per row) */
    #MenuRowText {{ color: {c['text']}; }}
    #MenuRowText:disabled {{ color: {c['faint']}; }}
    #MenuRow[active="true"] {{ background: {ORANGE}; }}
    #MenuRowText[hover="true"], #MenuRow[active="true"] #MenuRowText {{ color: {ON_ORANGE}; }}
    QMenu#SplitMenu::separator {{ margin: 4px 13px; }}
    QToolTip {{ background: {"#2C2C2E" if MODE == "dark" else "#FFFFFF"}; color: {c['text']};
                border: 1px solid {c['stroke']}; border-radius: 6px; padding: 4px 8px; }}
    """


_FOCUS_RING = None


def wants_focus_ring(w) -> bool:
    """Buttons, sliders, and the custom widgets that take keyboard focus (cards, the character card) and asked
    for the ring with the "focus_ring" property."""
    return isinstance(w, (QAbstractButton, QSlider)) or bool(w.property("focus_ring"))


class AppStyle(QProxyStyle):
    """The app's style (Fusion underneath). Its polish(), which Qt runs once for every widget it styles (a
    stylesheet's own style passes it on), gives buttons and drop-downs the pointing hand and hooks the focus
    ring to the widgets that can show one.

    Both used to be event filters on the whole application: every event of every object went through Python
    twice, and a widget-heavy window (the history's "Show more", the play tools) took about twice as long."""

    def polish(self, arg):
        if isinstance(arg, QWidget):
            super().polish(arg)
            if isinstance(arg, (QAbstractButton, QComboBox)) and not arg.testAttribute(Qt.WA_SetCursor):
                arg.setCursor(Qt.PointingHandCursor)      # like a link, unless the widget set its own cursor
            if _FOCUS_RING is not None and wants_focus_ring(arg):
                arg.removeEventFilter(_FOCUS_RING)         # polished again (a restyle): still one filter
                arg.installEventFilter(_FOCUS_RING)
            return None
        return super().polish(arg)


def install_focus_ring() -> None:
    """A subtle orange ring on the button that has keyboard focus (Tab / Shift+Tab), like the web's
    :focus-visible: a click gives no ring. Drawn by a see-through child over the button's own edge, so no
    layout moves and no tight parent clips it (a QFocusFrame outside the button was cut off in a chat row).
    The app's style (AppStyle) hooks it to each button as Qt styles it: only their events reach Python."""
    global _FOCUS_RING
    app = QApplication.instance()
    if app is None or _FOCUS_RING is not None:
        return
    _FOCUS_RING = FocusRing(app)
    if not isinstance(app.style(), AppStyle):
        app.setStyle(AppStyle(app.style().name()))      # re-polishes the widgets that exist already
    else:
        for w in app.allWidgets():                      # made before the ring existed
            if wants_focus_ring(w):
                w.installEventFilter(_FOCUS_RING)


def menu_row_hover() -> "MenuRowHover":
    """The one hover watcher menu rows install on themselves (an app-wide filter slowed every screen)."""
    global _MENU_ROW_HOVER
    if _MENU_ROW_HOVER is None:
        _MENU_ROW_HOVER = MenuRowHover()
    return _MENU_ROW_HOVER


_MENU_ROW_HOVER = None


class MenuRowHover(QObject):
    """A menu row's label turns dark on the row's orange hover. The stylesheet can't say it ("#MenuRow:hover
    #MenuRowText" is matched by every row: Qt ignores an ancestor's pseudo-state), so the label gets a hover
    property here and is re-polished."""

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QEvent.Enter, QEvent.Leave) and isinstance(obj, QWidget) and obj.objectName() == "MenuRow":
            label = obj.findChild(QWidget, "MenuRowText")
            hover = t == QEvent.Enter and obj.isEnabled()
            if label is not None and bool(label.property("hover")) != hover:
                label.setProperty("hover", hover)
                label.style().unpolish(label)
                label.style().polish(label)
        return False


class _Ring(QWidget):
    def __init__(self, target):
        super().__init__(target)
        self.setObjectName("FocusRing")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.setGeometry(target.rect())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(ORANGE if MODE == "dark" else ORANGE_DEEP)
        color.setAlphaF(0.9)
        p.setPen(QPen(color, 2))
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = min(r.height() / 2, 13)
        p.drawRoundedRect(r, radius, radius)
        p.end()


class FocusRing(QObject):
    KEYBOARD = (Qt.TabFocusReason, Qt.BacktabFocusReason)

    def __init__(self, parent):
        super().__init__(parent)
        self.ring: _Ring | None = None

    def _hide(self) -> None:
        try:
            if self.ring is not None:
                self.ring.deleteLater()
        except RuntimeError:          # its button is gone (a closed window), and the ring with it
            pass
        self.ring = self.target = None

    target = None

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QEvent.FocusIn, QEvent.FocusOut) and wants_focus_ring(obj):
            self._hide()
            if t == QEvent.FocusIn and e.reason() in self.KEYBOARD:
                self.ring, self.target = _Ring(obj), obj
                self.ring.show()
                self.ring.raise_()
        elif t == QEvent.Resize and obj is self.target and self.target is not None:
            try:
                self.ring.setGeometry(obj.rect())
            except RuntimeError:
                self.ring = self.target = None
        return False


def dialog_background() -> str:
    return "#1C1C1E" if MODE == "dark" else "#F2F2F7"


def glyph_icon(name: str, color: str | None = None, px: int = 16):
    """A menu icon drawn from the app's icon font (the same pencil / trash as the Settings screen)."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
    scale = 3
    pm = QPixmap(px * scale, px * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    f = QFont(ICON_FONT)
    f.setPixelSize(int(px * scale * 0.8))
    p.setFont(f)
    p.setPen(QColor(color or P()["muted"]))
    p.drawText(QRect(0, 0, px * scale, px * scale), Qt.AlignCenter, ICON[name])
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)
