"""Narrow-window and wording fixes (offscreen Qt): wrapping tag rows, the chat at 470 px, no-character way back,
orange text contrast, bidi of "40+", the "?" badge after a label's colon, session lines."""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from maplehelper import bidi, glossary  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


def _tags(n=6, rtl=False):
    from maplehelper.ui.controls import FlowLayout
    host = QWidget()
    host.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
    flow = FlowLayout(host, spacing=5)
    labels = []
    for i in range(n):
        lb = QLabel(f"tag number {i}")
        lb.setFixedSize(100, 20)
        flow.addWidget(lb)
        labels.append(lb)
    return host, flow, labels


def test_flow_layout_wraps_instead_of_widening(app):
    host, flow, labels = _tags()
    assert flow.minimumSize().width() == 100            # never wider than its widest tag
    assert flow.heightForWidth(1000) == 20               # one line when there is room
    assert flow.heightForWidth(210) == 3 * 20 + 2 * 5    # two per line, three lines
    flow.setGeometry(QRect(0, 0, 210, flow.heightForWidth(210)))
    assert [lb.geometry().x() for lb in labels[:3]] == [0, 105, 0]


def test_flow_layout_starts_on_the_right_in_hebrew(app):
    host, flow, labels = _tags(rtl=True)
    flow.setGeometry(QRect(0, 0, 210, flow.heightForWidth(210)))
    assert labels[0].geometry().right() == 209 and labels[1].geometry().right() == 104


def test_stepper_maximum_updates_plus_and_typing(app):
    from maplehelper.ui.controls import Stepper
    st = Stepper(1, 10, 10)
    assert not st.plus.isEnabled()
    st.setMaximum(20)
    assert st.plus.isEnabled() and st.edit.validator().top() == 20
    st.setMaximum(5)
    assert st.value() == 5 and not st.plus.isEnabled()


def test_adaptive_row_puts_the_button_under_a_narrow_field(app):
    from PySide6.QtWidgets import QLineEdit, QPushButton
    from maplehelper.ui.controls import AdaptiveRow
    row = AdaptiveRow(QLineEdit(), QPushButton("Check key"), main_min=260)
    assert not row.wide_enough(300) and row.wide_enough(600)
    assert row.minimumSizeHint().width() < 300


def test_orange_text_is_darker_on_light_glass():
    from maplehelper.ui import theme
    try:
        theme.set_mode("light")
        css = theme.stylesheet("Rubik", 13)
        assert theme.accent_text() == theme.ORANGE_TEXT_LIGHT
        link = css[css.index("QPushButton#Link, QPushButton#LinkDanger"):]
        assert f"color: {theme.ORANGE_TEXT_LIGHT}" in link.split("}")[0]
        assert "stop:1 #F07A12" in css           # the orange fills stay the brand orange
        theme.set_mode("dark")
        assert theme.accent_text() == theme.ORANGE and theme.accent_text(deep=True) == theme.ORANGE_DEEP
    finally:
        theme.set_mode("dark")


def test_a_trailing_plus_stays_with_its_number():
    assert f"{bidi.LRE}ACC\u00a040+{bidi.PDF}" in bidi.isolate_ltr_runs("כדאי לבוא עם ACC 40+.")
    assert f"{bidi.LRE}Lv.\u00a030+{bidi.PDF}" in bidi.isolate_ltr_runs("מ-Lv. 30+ אפשר")
    assert f"{bidi.LRE}Kerning{bidi.PDF}" in bidi.isolate_ltr_runs("Kerning +שלום")    # not a number's plus


def test_term_badge_goes_after_the_labels_colon():
    out = glossary.annotate("HP: 7420", "en")
    assert out.startswith("HP:<a href='g:HP'") and out.endswith(" 7420")


def test_session_lines_keep_names_whole_and_apart():
    from maplehelper.session import lines
    summary = {"chars": [
        {"name": "Elipaz", "start_level": 26, "end_level": 28, "start_job": "Fighter", "end_job": "Fighter",
         "questions": 0, "quests_done": ["[Construction Site B1] Shumi's Lost Coin", "Pio's Goods"], "quests_started": []},
        {"name": "Kiwi", "start_level": 3, "end_level": 3, "start_job": "Beginner", "end_job": "Beginner",
         "questions": 1, "quests_done": [], "quests_started": []}]}
    he = lines(summary, I18n("he"))
    assert "" in he and he.index("") == 2                         # the second character starts a new group
    assert he[0].startswith(f"{bidi.LRI}Elipaz{bidi.PDI}")
    assert f"{bidi.LRI}[Construction Site B1] Shumi's Lost Coin{bidi.PDI}" in he[1]
    en = lines(summary, I18n("en"))
    assert en[0] == "Elipaz: level 26 → 28" and bidi.LRI not in "".join(en)


def test_wording_fixes():
    he, en = I18n("he"), I18n("en")
    assert he("wishlist") == "פריטים במעקב" and "אני" not in he("exp_title")
    assert he("profile_updated", label="לבל", value="29") == "✓ עודכן · לבל: 29"
    assert he("inv_check") and en("inv_check") == "Inventory check"


def test_esc_in_an_open_guide_goes_back_to_the_list(app):
    from PySide6.QtTest import QTest
    from maplehelper.ui.guides import GuidesDialog
    d = GuidesDialog(SimpleNamespace(entities={}, page=lambda k: "", get=lambda k: None), None, "en", "")
    d.show()
    d.open_guide("guide/warrior-class-guide")
    assert d.stack.currentIndex() == 1
    QTest.keyClick(d, Qt.Key_Escape)
    assert d.stack.currentIndex() == 0 and d.isVisible()
    QTest.keyClick(d, Qt.Key_Escape)
    assert not d.isVisible()


@pytest.fixture
def overlay(app, isolated_store, kb, monkeypatch):
    from maplehelper import osapi
    from maplehelper.ui import terms
    from maplehelper.ui.overlay import Overlay
    for name in ("float_over_fullscreen", "activate_self", "focus_window"):
        monkeypatch.setattr(osapi, name, lambda *a: None)
    monkeypatch.setattr(osapi, "find_game_window", lambda: None)
    monkeypatch.setattr(terms, "LANG", terms.LANG)
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    s["font_size"] = 16
    c = p.add("Elipaz", "Thief", "Assassin", 32)
    p.set_active(c.id)
    from PySide6.QtGui import QFontDatabase
    from maplehelper.ui import theme
    for f in ("SegoeIcons.ttf", "segmdl2.ttf", "seguisym.ttf", "seguiemj.ttf", "segoeui.ttf"):   # as on a real PC
        if os.path.exists(os.path.join(r"C:\Windows\Fonts", f)):
            QFontDatabase.addApplicationFont(os.path.join(r"C:\Windows\Fonts", f))
    family = theme.load_fonts()
    ov = Overlay(s, p, kb, None)
    ov.setStyleSheet(theme.stylesheet(family, 16))
    ov.setGeometry(QRect(-3000, -3000, 700, 640))
    ov.show()
    yield ov
    ov.hide()


@pytest.mark.skipif(sys.platform != "win32", reason="measured with the Windows icon font")
def test_the_chat_fits_470_with_a_large_font_and_the_saver_badge(overlay, app):
    overlay.show_saver_badge(True)
    # the header tightens on each resize (_fit_header in resizeEvent), as a dragged edge brings one after another.
    # One jump from 700 px alone left 1-4 px to chance: at 700 the relaxed header holds the window's minimum at
    # ~466 px, a few px of font width more and Qt clamped the jump to 471/474 (this test's "flaky on this PC")
    for _ in range(2):
        overlay.resize(470, 640)
        app.processEvents()
    assert overlay.minimumSizeHint().width() <= 470
    assert overlay.width() == 470 and overlay.saver_badge.isVisible()
    assert overlay.title_bar.layout().sizeHint().width() <= overlay.title_bar.width()
    assert overlay.version_label.isVisible()               # in the footer, at any width


def test_no_character_shows_the_way_to_add_one(overlay, app):
    seen = []
    overlay.add_character_requested.connect(lambda: seen.append(1))
    overlay.profiles.characters = []
    overlay.profiles.active_id = None
    overlay.refresh_profile_chip()
    assert overlay.no_char_card.isVisibleTo(overlay) and not overlay.profile_card.isVisibleTo(overlay)
    overlay.no_char_card.btn.click()
    assert seen == [1]


def test_a_long_app_question_shows_a_short_label(overlay, monkeypatch):
    from maplehelper.ui import overlay as om
    started = []
    monkeypatch.setattr(om.QThread, "start", lambda self: started.append(1))
    t = I18n("he")
    assert overlay.ask(t("sell_q"), shown=t("inv_check"))
    bubbles = [b for b in overlay.feed.findChildren(om.Bubble) if b.role == "user"]
    assert bubbles[-1]._text == t("inv_check")
    assert overlay._worker.question == t("sell_q")             # the AI still gets the whole question
    from maplehelper.store import History
    assert History(overlay.profiles.active_id).recent()[-1]["text"] == t("inv_check")
    overlay.busy = False


def test_update_progress_survives_a_language_switch(overlay):
    overlay.show_update("0.7.5", "downloading", 42.4)
    overlay.settings["language"] = "en"
    overlay.apply_language()
    assert "42%" in overlay.update_label.text() and overlay.update_progress.value() == 424


def test_the_version_sits_opposite_the_scope_line(overlay, app):
    """Hebrew: the line on the right, the version at the bottom left; English mirrors it."""
    from PySide6.QtCore import QPoint
    app.processEvents()
    def x(w):
        return w.mapTo(overlay, QPoint(0, 0)).x()
    assert x(overlay.version_label) < x(overlay.scope_note)
    overlay.settings["language"] = "en"
    overlay.apply_language()
    app.processEvents()
    assert x(overlay.version_label) > x(overlay.scope_note)
