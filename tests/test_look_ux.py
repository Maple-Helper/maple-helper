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


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_disabled_buttons_and_controls_look_disabled(app, mode):
    """VIS-4: only Primary / Send / StepBtn / Link had a :disabled rule; an answered chat choice row, "Check key"
    while it checks and the busy character menu were pixel-identical to live buttons."""
    from PySide6.QtWidgets import QPushButton, QToolButton

    from maplehelper.ui.controls import Select, Switch
    theme.set_mode(mode)
    app.setStyleSheet(theme.stylesheet("Rubik", 14))
    makers = {name: (lambda name=name: QPushButton("Grind", objectName=name))
              for name in ("Chip", "Secondary", "Danger", "NowChip", "Quick", "TagChip", "ProfilePill")}
    makers.update({name: (lambda name=name: QToolButton(objectName=name, text=theme.ICON["settings"]))
                   for name in ("Icon", "IconClose")})
    makers["Select"] = lambda: Select(["F9"])
    makers["Switch"] = lambda: Switch(True)
    try:
        for name, make in makers.items():
            shots = []
            for enabled in (True, False):
                w = make()
                w.setEnabled(enabled)
                w.resize(90, 32)
                shots.append(w.grab().toImage())
                w.deleteLater()
            assert shots[0] != shots[1], f"{name} looks the same disabled ({mode})"
    finally:
        app.setStyleSheet("")


class _Screen2x:
    def devicePixelRatioF(self):
        return 2.0


def test_pictures_are_made_at_the_screens_pixels_and_sprites_grow_pixel_for_pixel(app):
    """VIS-8: card pictures were scaled to 56 logical px and stretched by Qt to 112 at 200% (blurred), and small
    sprites were smoothed up. Now: device pixels with the screen's ratio, and a sprite grows in whole steps."""
    from PySide6.QtGui import QColor, QImage, QPixmap

    from maplehelper.ui.widgets import fit_picture
    img = QImage(8, 8, QImage.Format_ARGB32)
    for x in range(8):
        for y in range(8):
            img.setPixelColor(x, y, QColor("#000000") if (x + y) % 2 else QColor("#FFFFFF"))
    pm = fit_picture(QPixmap.fromImage(img), 56, 56, _Screen2x())
    assert pm.devicePixelRatio() == 2.0
    assert (pm.width(), pm.height()) == (112, 112)
    assert pm.deviceIndependentSize().toSize().width() == 56
    out = pm.toImage()
    # 8 -> 112 is exactly 14x: nearest neighbour keeps only the sprite's own two colors (smoothing made greys)
    colors = {out.pixelColor(x, y).name() for x in range(0, 112, 3) for y in range(0, 112, 3)}
    assert colors <= {"#000000", "#ffffff"}, colors


def test_a_wide_minimap_spans_the_card_column(app):
    """VIS-9: Henesys' 431x74 minimap in the 56 px square was a 56x9 sliver. A wide picture now takes the column's
    width (up to 2x, at most 96 px high) and keeps its shape."""
    from PySide6.QtGui import QPixmap

    from maplehelper.ui.widgets import WidePicture
    pm = QPixmap(431, 74)
    assert WidePicture.wide(pm) and not WidePicture.wide(QPixmap(60, 50))
    w = WidePicture(pm)
    assert w.hasHeightForWidth()
    assert 60 <= w.heightForWidth(380) <= 70                 # 380 x 65: the map's own size, nearly
    assert w.heightForWidth(2000) == 96                       # never taller than 96 px
    assert w.heightForWidth(200) == round(74 * 200 / 431)


def test_a_map_card_shows_its_minimap_under_the_name(app, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QPixmap

    from maplehelper.ui import widgets
    img = tmp_path / "map.png"
    pm = QPixmap(431, 74)
    pm.fill(QColor("#3A7"))
    pm.save(str(img))

    class KB:
        def get(self, key):
            return {"key": key, "name": "Henesys", "category": "map", "props": {}}

        def picture(self, key):
            return img

        def community_mesos(self, key):
            return None

    from maplehelper import recent, routes, sources
    monkeypatch.setattr(sources, "stat_source", lambda kb, key: None)
    monkeypatch.setattr(recent, "of", lambda kb, key: None)
    monkeypatch.setattr(routes, "of", lambda kb: type("G", (), {"of_key": lambda self, k: None})())
    card = widgets.EntityCard(KB(), "map/100000000", "he")
    card.resize(440, 200)
    card.show()
    strips = card.findChildren(widgets.WidePicture)
    assert len(strips) == 1 and strips[0].heightForWidth(strips[0].width()) >= 40
    card.close()


def test_a_plain_text_row_in_a_section_gets_the_rows_padding(app):
    """VIS-13: add_row rows have 8 px above and below, a bare label added with add_widget had none (the pets' intro
    sat 4 px from the card's top edge). A label with its own margins keeps them."""
    from PySide6.QtCore import QMargins
    from PySide6.QtWidgets import QLabel

    from maplehelper.ui.controls import Section
    sec = Section("Pets", True)
    intro, own = QLabel("intro"), QLabel("own")
    own.setContentsMargins(0, 2, 0, 2)
    sec.add_widget(intro)
    sec.add_widget(own)
    assert intro.contentsMargins() == QMargins(0, 8, 0, 8)
    assert own.contentsMargins() == QMargins(0, 2, 0, 2)


def _line_right_edges(img, ink_below: int = 160) -> list[int]:
    """The rightmost dark pixel of each text line (rows of ink separated by blank rows)."""
    lines, cur = [], None
    for y in range(img.height()):
        xs = [x for x in range(img.width()) if img.pixelColor(x, y).lightness() < ink_below]
        if xs:
            cur = max(cur or 0, max(xs))
        elif cur is not None:
            lines.append(cur)
            cur = None
    if cur is not None:
        lines.append(cur)
    return lines


def test_a_wrapped_hebrew_news_title_starts_at_the_right_edge(app):
    """VIS-14: as plain text, a wrapped Hebrew title with an English name kept the space before the name at the end
    of line 1, and line 1 started ~6 px in from line 2. Now right-to-left rich text: both lines start at one edge."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPixmap

    from maplehelper.ui import newsview
    theme.load_fonts()
    item = {"id": "x", "title": "Founder's Access details: level cap 100", "official": True,
            "title_he": "פרטי Founder's Access: תקרת רמה 100, ו-Forgotten Hollow ייפתח בהמשך"}
    lb = newsview.title_label(item, True)
    assert lb.textFormat() == Qt.RichText and 'dir="rtl"' in lb.text() and "Forgotten Hollow" in lb.text().replace(chr(0xA0), " ")
    f = QFont("Rubik")
    f.setPixelSize(15)
    f.setBold(True)
    lb.setFont(f)
    lb.setStyleSheet("color: black; background: white;")
    lb.setLayoutDirection(Qt.RightToLeft)
    lb.setFixedWidth(420)
    lb.setFixedHeight(lb.heightForWidth(420))
    pm = QPixmap(lb.size())
    pm.fill(QColor("white"))
    lb.render(pm)
    edges = _line_right_edges(pm.toImage())
    assert len(edges) >= 2, edges
    assert abs(edges[0] - edges[1]) <= 2, edges
    # an English title (no translation) stays one plain left-to-right block
    en = newsview.title_label({"id": "y", "title": "Patch notes", "official": True}, True)
    assert en.textFormat() == Qt.PlainText
