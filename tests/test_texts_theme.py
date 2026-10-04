"""Text contrast in both appearances (WCAG AA), the menu rows' own states, Windows high contrast, short English
names kept whole in Hebrew, Mac wording (⌘V, System Settings, fn+F9) and the wording fixes of the audit."""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF  # noqa: E402
from PySide6.QtGui import QEnterEvent, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel  # noqa: E402

from maplehelper.i18n import NBSP, STRINGS, I18n  # noqa: E402
from maplehelper.ui import theme  # noqa: E402

REAL_HIGH_CONTRAST = theme.high_contrast


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def no_system_contrast(monkeypatch):
    monkeypatch.setattr(theme, "high_contrast", lambda: None)     # the test machine's own setting stays out
    yield
    theme.set_mode("dark")


def _rgb(color: str, over=(255, 255, 255)):
    """'#RRGGBB' or 'rgba(r,g,b,a)' as an opaque color over a background."""
    if color.startswith("#"):
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b, a = (float(x) for x in re.findall(r"[\d.]+", color))
    return tuple(round(c * a + o * (1 - a)) for c, o in zip((r, g, b), over))


def _contrast(fg, bg) -> float:
    def lum(c):
        ch = [v / 255 for v in c]
        ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


ORANGE_RGB = _rgb(theme.ORANGE)


def _tint(alpha, base):        # the orange-tinted notes and chips: orange at 10-18% over a surface
    return tuple(round(o * alpha + b * (1 - alpha)) for o, b in zip(ORANGE_RGB, base))


def _menu(app):
    from maplehelper.ui.widgets import SplitMenu
    theme.set_mode("light")
    app.setStyleSheet(theme.stylesheet("Rubik", 14))
    menu = SplitMenu()
    menu.add_row("edit", "Edit character", lambda: None)
    menu.add_row("copy", "Copy the character card", lambda: None, enabled=False)
    rows = [w for w in menu.findChildren(QFrame) if w.objectName() == "MenuRow"]
    for r in rows:
        r.ensurePolished()
        r.findChild(QLabel, "MenuRowText").ensurePolished()
    return menu, rows


def _fg(label) -> str:
    group = QPalette.Active if label.isEnabled() else QPalette.Disabled
    return label.palette().color(group, label.foregroundRole()).name()


def test_menu_rows_show_enabled_disabled_and_hover(app):
    menu, (on, off) = _menu(app)
    on_label, off_label = on.findChild(QLabel, "MenuRowText"), off.findChild(QLabel, "MenuRowText")
    # one rule used to paint every label grey: an enabled and a disabled row looked the same
    assert _fg(on_label) == theme.P()["text"].lower()
    assert _fg(off_label) != _fg(on_label)
    theme.install_focus_ring()
    QApplication.sendEvent(on, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert on_label.property("hover") is True and _fg(on_label) == theme.ON_ORANGE.lower()
    QApplication.sendEvent(on, QEvent(QEvent.Leave))
    assert not on_label.property("hover") and _fg(on_label) == theme.P()["text"].lower()
    QApplication.sendEvent(off, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert not off_label.property("hover")                  # a disabled row doesn't light up
    menu.deleteLater()
    app.setStyleSheet("")


def test_windows_high_contrast_wins_over_the_appearance(monkeypatch):
    monkeypatch.setattr(theme, "high_contrast", lambda: "dark")
    theme.set_mode("light")
    assert theme.MODE == "dark" and theme.HIGH_CONTRAST
    assert theme.P()["muted"] == theme.P()["text"]          # no see-through grey text
    assert "rgba(255,255,255,0.07)" not in theme.stylesheet("Rubik", 14)   # firmer hairlines
    monkeypatch.setattr(theme, "high_contrast", lambda: None)
    theme.set_mode("light")
    assert theme.MODE == "light" and not theme.HIGH_CONTRAST and theme.P() is theme.PALETTES["light"]


def test_the_real_high_contrast_check_answers_without_failing():
    assert REAL_HIGH_CONTRAST() in (None, "dark", "light")


def test_short_english_names_stay_whole_in_hebrew():
    he, en = I18n("he"), I18n("en")
    plan = he("ob_need_plan")
    assert f"Claude{NBSP}Pro" in plan
    assert f"Free{NBSP}Market" in he("sell_body")
    assert "Claude Pro" in en("ob_need_plan")                    # English lines wrap normally
    # a name through a placeholder; a long KB name stays as spelled (bidi keeps it whole by that spelling)
    assert f"Red{NBSP}Snail" in he("other_char_switch", name="Red Snail")
    assert "Tree Dungeon, Monkey Forest I" in he("other_char_switch", name="Tree Dungeon, Monkey Forest I")
    from maplehelper.i18n import keep_names_whole
    assert keep_names_whole("שלום Copy to Clipboard עכשיו") == f"שלום Copy{NBSP}to{NBSP}Clipboard עכשיו"
    long_run = "ראו One Two Three Four כאן"
    assert keep_names_whole(long_run) == long_run              # a longer English run may still wrap
    assert keep_names_whole("בין 10 20 לבין") == "בין 10 20 לבין"   # numbers alone are not a name


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(I18n, "mac", True)


def test_mac_gets_mac_paths_and_shortcuts(mac):
    for lang in ("he", "en"):
        t = I18n(lang)
        assert "⌘V" in t("copied") and "Ctrl" not in t("copied")
        assert "Windows" not in t("voice_mic_failed")
        assert "fn+F9" in t("close_chat") and "fn+F10" in t("input_placeholder") and "fn+F10" in t("voice_nothing")
        assert "fn+F10" in t("mic_tip", key="F10") and "fn+F9" in t("shot_hint_ready")
        assert "fn+F9" in t("ob_done_hint")
        assert "fn+" not in t("hotkey_taken", key="F9")          # naming the key, not teaching a press
        assert t("update_available", version="1") != t("update_available_mac")   # its own Mac line, not swapped
    # the callers swap in the chosen key and keep the "fn+"
    assert "fn+F7" in I18n("en")("close_chat").replace("F9", "F7")


def test_windows_wording_is_unchanged(monkeypatch):
    monkeypatch.setattr(I18n, "mac", False)
    en = I18n("en")
    assert "Ctrl+V" in en("copied") and "Windows Settings" in en("voice_mic_failed") and "fn" not in en("close_chat")


def test_hebrew_addresses_the_player_in_plural():
    assert "שלכם" in I18n("he")("q_no_match") and "שלך." not in I18n("he")("q_no_match")


@pytest.mark.parametrize("key", ["appearance_dark", "appearance_light", "q_needs", "q_gets"])
def test_unused_audit_strings_are_gone(key):
    assert key not in STRINGS


def test_no_default_key_letters_the_kb_does_not_give():
    # the KB says key layouts can be rebound (pages/guide/controls-and-settings.md) and names no Stat/Inventory key
    from maplehelper import glossary
    texts = [v[lang] for k in ("inv_not_found", "my_stats_hint", "sell_body") for v in [STRINGS[k]] for lang in v]
    texts += list(glossary.APP["ACC"])
    assert not any(re.search(r"\(\**[SI]\** key\)|מקש \**[SI]\b", s) for s in texts)


def test_every_text_key_the_code_asks_for_exists():
    """A string lost while editing i18n.py showed its key on screen ("calc_default", the owner's report)."""
    import re
    from pathlib import Path

    from maplehelper.i18n import STRINGS
    root = Path(__file__).resolve().parent.parent / "maplehelper"
    asked = set()
    for f in root.rglob("*.py"):
        asked |= set(re.findall(r'(?<![\w.])(?:self\.)?t\(\s*"([a-z][a-z0-9_]*)"', f.read_text(encoding="utf-8")))
    missing = sorted(k for k in asked if k not in STRINGS)
    assert missing == []
