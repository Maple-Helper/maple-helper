"""The featured article (featured.py, ui/featuredview.py): Nexon's Grand Launch release notes copied into the app,
shown once to every player while it runs, readable from the News window and saved as a PDF."""
from __future__ import annotations

from datetime import date

import pytest

from maplehelper import featured


def article() -> dict:
    m = next((m for m in featured.articles() if m["id"] == "nexon-46506"), None)
    assert m, "the Grand Launch article ships complete (every tile on disk)"
    return m


def test_the_grand_launch_article_ships_whole_in_hebrew_and_english():
    m = article()
    assert set(m["langs"]) == {"he", "en"}
    for page in m["langs"].values():
        assert page["width"] > 0 and page["tiles"]
        # the tiles cover the article from the top down, without a gap or an overlap
        y = 0.0
        for t in page["tiles"]:
            assert t["y"] == pytest.approx(y, abs=0.5)
            assert featured.tile_path(m, t).is_file()
            y += t["h"]
        assert y == pytest.approx(page["height"], abs=1)
        for a in page["anims"]:
            assert featured.file_path(m, a["file"]).is_file()
            x, top, w, h = a["rect"]
            assert 0 <= x and x + w <= page["width"] + 1 and 0 <= top and top + h <= page["height"] + 1
        assert all(ln["href"].startswith(("https://", "http://")) for ln in page["links"])
    assert m["source_url"].startswith("https://www.nexon.com/")


def test_it_shows_once_and_only_while_it_runs():
    m = article()
    assert featured.due([], date(2026, 10, 21))["id"] == m["id"]
    assert featured.due([m["id"]], date(2026, 10, 21)) is None          # seen: never again
    assert featured.due([], date(2026, 10, 20)) is None                  # before Grand Launch
    assert featured.due([], date(2026, 11, 19)) is None                  # after its events ended

    class S(dict):
        pass
    s = S(featured_seen=[])
    featured.mark_seen(s, m["id"])
    featured.mark_seen(s, m["id"])
    assert s["featured_seen"] == [m["id"]]


def test_the_news_item_opens_the_copy_and_the_language_falls_back_to_english():
    m = article()
    assert featured.for_news("nexon-46506") is m and featured.for_news("nexon-1") is None
    assert featured.language(m, "he") == "he" and featured.language(m, "fr") == "en"


def test_the_pdf_is_the_pictures_the_app_shows(tmp_path):
    m = article()
    out = featured.to_pdf(m, "he", tmp_path / "notes.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") \
        + data.count(b"/Type /Page/") >= 1
    assert not (tmp_path / "notes.pdf.part").exists()


def test_the_window_draws_the_article_as_wide_as_it_and_plays_only_what_is_on_screen():
    from PySide6.QtGui import QMovie
    from PySide6.QtWidgets import QApplication, QPushButton

    from maplehelper.ui.featuredview import FeaturedDialog
    qt = QApplication.instance() or QApplication([])
    m = article()
    dlg = FeaturedDialog(m, "he", "")
    dlg.resize(800, 900)
    dlg.show()
    qt.processEvents()
    dlg.scroll.fit()
    page = m["langs"]["he"]
    c = dlg.canvas
    assert c.width() == dlg.scroll.viewport().width()
    assert c.height() == pytest.approx(page["height"] * c.width() / page["width"], abs=1)
    assert not any(lb.movie().state() == QMovie.Running for lb in c._anims)      # the top has no animation
    first = c._anims[0]
    dlg.scroll.verticalScrollBar().setValue(first.y() - 50)
    qt.processEvents()
    assert first.movie().state() == QMovie.Running
    assert len(c._cache) <= 8
    # the English original, a click away; the link spots follow the language shown
    texts = [b.text() for b in dlg.findChildren(QPushButton)]
    assert any("PDF" in t for t in texts) and any("באנגלית" in t for t in texts)
    dlg.switch_language()
    assert dlg.lang == "en" and c.page is m["langs"]["en"]
    ln = m["langs"]["en"]["links"][0]
    k = c.width() / m["langs"]["en"]["width"]
    from PySide6.QtCore import QPoint
    assert c.link_at(QPoint(round((ln["rect"][0] + 2) * k), round((ln["rect"][1] + 2) * k))) == ln["href"]
    dlg.close()


def test_the_app_shows_it_once_and_marks_it_seen(monkeypatch):
    from types import SimpleNamespace

    from maplehelper.app import MapleHelperApp
    shown = []
    f = SimpleNamespace(settings={"featured_seen": [], "tour_done": True}, _featured_waiting=True,
                        show_featured=lambda m: shown.append(m["id"]))
    monkeypatch.setattr(featured, "due", lambda seen, today=None: None if seen else article())
    MapleHelperApp._featured_after_wait(f)
    MapleHelperApp._featured_after_wait(f)
    assert shown == ["nexon-46506"] and f.settings["featured_seen"] == ["nexon-46506"]
