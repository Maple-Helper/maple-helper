"""The owner-approved look fixes (ux/look): dark-mode warning / good chips, a disabled look, sharp pictures on HiDPI,
readable minimaps, padded text rows, unindented Hebrew titles, even picture sizes, and the tools' wording fixes."""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper.ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def restore_mode():
    yield
    theme.set_mode("dark")


def _rgba(css: str) -> tuple[float, float, float, float]:
    c = theme.qcolor(css)
    return c.red(), c.green(), c.blue(), c.alpha() / 255


def _flatten(*layers: str) -> tuple[float, float, float]:
    """Colors top first, the last one opaque: what the eye sees."""
    r, g, b, _ = _rgba(layers[-1])
    for css in reversed(layers[:-1]):
        fr, fg, fb, a = _rgba(css)
        r, g, b = fr * a + r * (1 - a), fg * a + g * (1 - a), fb * a + b * (1 - a)
    return r, g, b


def _contrast(fg, bg) -> float:
    def lum(c):
        ch = [v / 255 for v in c]
        ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def _prop(rule: str, name: str) -> str:
    m = re.search(rf"(?:^|;|\s){name}\s*:\s*([^;]+);", rule)
    assert m, (name, rule)
    return m.group(1).strip()


@pytest.mark.parametrize("selector", ["#TagWarn", "#TagGood", "#VoteTag", '#VoteTag[single="true"]', "#SaverBadge"])
def test_warning_and_good_chips_read_on_dark_glass(selector):
    """VIS-2: the light-glass orange / green were drawn on dark glass too (2.4-3.4:1: the class grade letters were
    dim). Each chip's text is >= 4.5:1 on its own tint over a card (or the plain card, for the vote marks)."""
    theme.set_mode("dark")
    c = theme.P()
    glass = "#%02X%02X%02X" % c["glass"]
    css = theme.stylesheet("Rubik", 14)
    rule = _rule(css, selector)
    fg = _prop(rule, "color")
    bg = re.search(r"background:\s*(rgba\([^)]*\))", rule)
    under = [bg.group(1)] if bg else []
    for surface in (c["fill1"], c["fill2"]):              # a card, and a hovered card
        back = _flatten(*under, surface, glass)
        assert _contrast(_flatten(fg, *under, surface, glass), back) >= 4.5, (selector, fg, surface)


def test_light_chips_keep_their_colors():
    theme.set_mode("light")
    css = theme.stylesheet("Rubik", 14)
    assert _prop(_rule(css, "#TagWarn"), "color") == "#C9620A"
    assert _prop(_rule(css, "#TagGood"), "color") == theme.GOOD_TEXT_LIGHT
