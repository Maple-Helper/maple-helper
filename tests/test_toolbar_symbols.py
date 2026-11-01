"""Without the Segoe icon fonts (macOS), every bar and Play tools glyph has a symbol to draw instead of a blank."""
from maplehelper.ui import gametoolbar


def test_every_toolbar_glyph_has_a_symbol():
    glyphs = set(gametoolbar.GLYPHS.values()) | set(gametoolbar.PAGE_GLYPHS.values())
    assert glyphs <= set(gametoolbar.SYMBOL_GLYPHS)
    assert not any("" <= ch <= "" for s in gametoolbar.SYMBOL_GLYPHS.values() for ch in s)
