"""MeowDB's news, server status and Free Market sections in the app, from recorded answers (tests/fixtures/meowdb,
fetched 2026-10-04, two days before Founder's Access): no network.

news_page.html is the real news page's payload cut to five items; server_status_prelaunch.json and the
item_*_298.json / market_listings_298.json files are the real endpoints' answers that day (the game wasn't open,
so the market ones are empty). The filled market answers below use the field names the site's own item page reads.
"""
import json
import os
import re
import time
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper import market, news, serverstatus  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402

app = QApplication.instance() or QApplication([])
FIX = Path(__file__).parent / "fixtures" / "meowdb"
PAGE = (FIX / "news_page.html").read_text(encoding="utf-8")
TODAY = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def every_fixture_item(monkeypatch, request):
    """The five recorded items reach back to April: the app's cut (news.SINCE, from 2026-10-02) is off here, so the
    tests see them all; test_news_start_at_the_cut puts it back."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import scrape_news
    if request.node.name != "test_news_start_at_the_cut":
        monkeypatch.setattr(news, "SINCE", "2000-01-01")
        monkeypatch.setattr(scrape_news, "SINCE", "2000-01-01")


def test_news_start_at_the_cut(tmp_path):
    """Only news from 2026-10-02 on (the owner's call): the scrape keeps none older, and the app shows none older
    from a KB that still has them; everything after it comes in."""
    import scrape_news
    assert scrape_news.SINCE == news.SINCE == "2026-10-02"
    kept = scrape_news.build(PAGE, he={})
    assert kept and all(i["date"] >= "2026-10-02" for i in kept)
    (tmp_path / "news.json").write_text(json.dumps({"items": [
        {"id": "old", "title": "Old", "date": "2026-09-29"}, {"id": "new", "title": "New", "date": "2026-10-09"}]}),
        encoding="utf-8")
    from types import SimpleNamespace
    assert [i["id"] for i in news.items(SimpleNamespace(root=tmp_path))] == ["new"]


def pump(ms=60):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def _load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the nightly scrape (tools/scrape_news.py)

def test_the_news_page_payload_becomes_news_items():
    import scrape_news
    items = scrape_news.build(PAGE, he={})
    assert [i["id"] for i in items] == ["founders-access-release-notes", "classic-world-opening-time-confirmed",
                                        "cot2-meowdb-exploration-report", "cms-classic-opening-august-3",
                                        "second-cot-signups-open"]          # newest first
    first = items[0]
    assert first["date"] == "2026-10-03" and first["region"] == "gms" and first["publisher"] == "Nexon"
    assert first["official"] and first["url"] == "https://meowdb.com/msclassic/news/founders-access-release-notes"
    assert first["title"].startswith("Founder's Access release notes") and "level 100" in first["summary"]
    assert first["highlights"] and "summary_he" not in first
    # NiaMeowDB's own report is community news; China's operator is official for its region
    by = {i["id"]: i for i in items}
    assert not by["cot2-meowdb-exploration-report"]["official"]
    assert by["cms-classic-opening-august-3"]["official"] and by["cms-classic-opening-august-3"]["region"] == "cms"
    # what an item names, as a hint for the AI and the owner (never for availability)
    assert {"Orbis", "El Nath", "3rd job"} <= set(by["second-cot-signups-open"]["mentions"])


def test_a_hebrew_summary_is_kept_only_for_the_english_it_was_made_from():
    import scrape_news
    plain = scrape_news.build(PAGE, he={})
    h = {i["id"]: i["hash"] for i in plain}
    he = {"founders-access-release-notes": {"summary": "תקציר", "source_hash": h["founders-access-release-notes"]},
          "classic-world-opening-time-confirmed": {"summary": "ישן", "source_hash": "0000"}}
    by = {i["id"]: i for i in scrape_news.build(PAGE, he=he)}
    assert by["founders-access-release-notes"]["summary_he"] == "תקציר"
    assert "summary_he" not in by["classic-world-opening-time-confirmed"]      # NiaMeowDB rewrote it since


def test_update_writes_news_json_and_a_bad_night_keeps_it(tmp_path):
    import scrape_news
    assert scrape_news.update(tmp_path, lambda url: PAGE) == 5
    first = (tmp_path / "news.json").read_text(encoding="utf-8")
    assert len(json.loads(first)["items"]) == 5
    assert scrape_news.update(tmp_path, lambda url: PAGE) == 0                     # nothing new: not a change
    assert scrape_news.update(tmp_path, lambda url: None) == 0                     # the site was down
    assert scrape_news.update(tmp_path, lambda url: "<html>new layout</html>") == 0
    assert (tmp_path / "news.json").read_text(encoding="utf-8") == first


def test_news_go_into_the_changelog_and_the_validator(tmp_path):
    import kb_release
    import scrape_news
    old, new = tmp_path / "old", tmp_path / "new"
    for d in (old, new):
        d.mkdir()
        (d / "index.json").write_text("[]", encoding="utf-8")
    items = scrape_news.build(PAGE, he={})
    (old / "news.json").write_text(json.dumps({"items": items[1:]}), encoding="utf-8")
    (new / "news.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    d = kb_release.diff_kb(old, new)
    assert d["counts"]["news"] == 1 and d["news"][0]["id"] == "founders-access-release-notes"
    assert kb_release.diff_kb(new, new)["counts"] == {"added": 0, "removed": 0, "changed": 0, "updated": 0}
    (new / "news.json").write_text(json.dumps({"items": [{"id": "x"}]}), encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="news.json"):
        kb_release.validate(new, categories=[])


def test_translate_news_round_trip(tmp_path, monkeypatch):
    import scrape_news
    import translate_news
    monkeypatch.setattr(translate_news, "OUT", tmp_path / "assets")
    kb = tmp_path / "kb"
    kb.mkdir()
    scrape_news.update(kb, lambda url: PAGE)
    out = tmp_path / "export.json"
    assert translate_news.export("he", out, kb) == 5
    done = json.loads(out.read_text(encoding="utf-8"))
    done["strings"] = {k: "בעברית" for k in list(done["strings"])[:2]}
    out.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    assert translate_news.import_("he", out) == 2
    assert translate_news.export("he", out, kb) == 3            # only what still needs a translation


# ------------------------------------------------------------------ the app's news (news.py)

@pytest.fixture
def news_kb(kb_copy):
    import scrape_news
    scrape_news.update(kb_copy, lambda url: PAGE)
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(kb_copy)


def test_unread_is_recent_global_news_not_yet_read(news_kb):
    ids = [i["id"] for i in news.unread(news_kb, [], today=TODAY)]
    # the last NEW_DAYS days only: August's report, China's news and July's stay in the News tab
    assert ids == ["founders-access-release-notes", "classic-world-opening-time-confirmed"]
    assert [i["id"] for i in news.unread(news_kb, [], today=date(2026, 8, 20))] == ["cot2-meowdb-exploration-report"]
    assert [i["id"] for i in news.unread(news_kb, ["founders-access-release-notes"], today=TODAY)][0] == \
        "classic-world-opening-time-confirmed"
    assert news.unread(news_kb, [], today=date(2027, 1, 1)) == []


def test_mark_read_keeps_the_newest(isolated_store):
    s = isolated_store.Settings()
    news.mark_read(s, ["a", "b"])
    news.mark_read(s, ["b", "c"])
    assert s["news_read"] == ["a", "b", "c"]


def test_hebrew_summary_falls_back_to_english():
    i = {"summary": "English.", "summary_he": "עברית."}
    assert news.summary(i, "he") == ("עברית.", True)
    assert news.summary({"summary": "English."}, "he") == ("English.", False)
    assert news.summary(i, "en") == ("English.", True)


def test_the_ai_gets_news_only_when_asked_and_as_announcements(news_kb):
    from maplehelper.brain import build_prompt
    lines = news.ai_lines(news_kb, today=TODAY)
    assert "announcements, not the game's data" in lines[0]
    assert lines[1].startswith("News 2026-10-03 (official, Nexon): Founder's Access release notes")
    assert not any("China" in ln or "CMS" in ln for ln in lines)
    asked = build_prompt("When does Orbis open?", None, None, news_kb, False)
    assert "News 2026-10-03 (official, Nexon)" in asked
    assert "official, Nexon" not in build_prompt("What does Mano drop?", None, None, news_kb, False)


def test_news_never_change_what_is_released(news_kb, kb):
    from maplehelper import availability
    assert availability.of(news_kb).scope_note() == availability.of(kb).scope_note()


# ------------------------------------------------------------------ server status (serverstatus.py)

def test_the_recorded_prelaunch_answer():
    now = 1791089818.891                                   # when it was recorded
    st = serverstatus.parse(_load("server_status_prelaunch.json"), now)
    assert st.state == "prelaunch" and st.verdict == "prelaunch"
    assert st.opens_at == 1791309600.0                     # Founder's Access, October 6 18:00 UTC
    assert st.notice_done and st.notice_url.startswith("https://www.nexon.com/") and not st.scheduled


def test_verdicts_and_transitions():
    now = time.time()
    up, maint = serverstatus.parse({"verdict": "up"}, now), serverstatus.parse({"verdict": "maintenance"}, now)
    assert serverstatus.parse({"verdict": "scheduled", "notice": {"startAt": (now + 3600) * 1000}}, now).scheduled
    assert serverstatus.parse({"verdict": "rising"}, now).state == "issues"
    assert serverstatus.parse({"verdict": "something new"}, now).state == "unknown"
    assert serverstatus.parse("not json", now).state == "unknown"
    assert serverstatus.transition(None, maint) == "started"          # opened during a maintenance
    assert serverstatus.transition(None, up) is None                  # nothing ended that this session saw start
    assert serverstatus.transition(up, maint) == "started"
    assert serverstatus.transition(maint, maint) is None
    assert serverstatus.transition(maint, up) == "ended"


def test_the_poller_backs_off_and_stops():
    from maplehelper.ui import serverdot
    answers = []
    poller = serverdot.StatusPoller(fetch=lambda: None)
    poller.status.connect(answers.append)
    poller.start()
    for _ in range(3):
        poller._timer.stop()
        poller._ask()
        deadline = time.time() + 2
        while poller._busy and time.time() < deadline:
            pump(10)
    assert answers == [None, None, None] and poller._minutes == 40
    poller._fetch = lambda: serverstatus.Status("up")
    poller._timer.stop()
    poller._ask()
    deadline = time.time() + 2
    while poller._busy and time.time() < deadline:
        pump(10)
    assert poller._minutes == serverdot.POLL_MINUTES
    poller.stop()
    assert not poller._timer.isActive()


def test_the_dot_tooltip_says_where_it_comes_from():
    from maplehelper.ui import serverdot
    now = time.time()
    st = serverstatus.parse({"verdict": "maintenance", "notice": {"endAt": (now + 3600) * 1000}}, now)
    for lang in ("he", "en"):
        t = I18n(lang)
        tip = serverdot.tip(t, st, now)
        assert serverdot.when(st.notice_end) in tip and t("server_source", time=serverdot.when(st.checked)) in tip
    assert I18n("en")("server_unknown") in serverdot.tip(I18n("en"), None)


# ------------------------------------------------------------------ the chat: news strip, server dot

@pytest.fixture
def chat(isolated_store, news_kb, monkeypatch):
    from maplehelper import osapi
    from maplehelper.ui.overlay import Overlay
    for name in ("float_over_fullscreen", "activate_self", "focus_window"):
        monkeypatch.setattr(osapi, name, lambda *a: None)
    monkeypatch.setattr(osapi, "find_game_window", lambda: None)
    monkeypatch.setattr(news, "date", type("D", (date,), {"today": staticmethod(lambda: TODAY)}))
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    s["language"], s["tour_done"] = "he", True
    p.add("Kiwi", "Thief", "Assassin", 34)
    ov = Overlay(s, p, news_kb, None)
    ov.resize(480, 640)
    ov.show()
    pump()
    yield ov
    ov.hide()
    ov.deleteLater()
    pump()


def test_the_news_strip_shows_the_newest_unread_and_dismisses_per_item(chat):
    chat.show_news()
    assert chat.news_strip.isVisible()
    # the Hebrew title (assets/news/he.json), its English names kept whole
    assert "תקרת רמה" in chat.news_strip.title.text() and "Founder's" in chat.news_strip.title.text()
    assert "ועוד אחת שלא קראתם" in chat.news_strip.head.text() and "חדשה מ-" in chat.news_strip.head.text()
    chat.news_strip.close_btn.click()
    assert chat.settings["news_read"] == ["founders-access-release-notes"]
    assert "Classic World opens" in chat.news_strip.title.text()
    asked = []
    chat.news_requested.connect(lambda: asked.append(True))
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(chat.news_strip, Qt.LeftButton, pos=QPoint(40, 10))
    assert asked == [True]
    news.mark_read(chat.settings, ["classic-world-opening-time-confirmed"])
    chat.show_news()
    assert not chat.news_strip.isVisible()


def test_the_server_dot_and_one_notice_per_maintenance(chat):
    from maplehelper.ui.widgets import NoticeCard, SystemLine
    now = time.time()

    def feed():
        return chat.feed.findChildren(NoticeCard) + chat.feed.findChildren(SystemLine)
    assert chat.server_poller.running                      # the chat is open: polling
    chat._on_server_status(serverstatus.parse({"verdict": "up"}, now))
    assert chat.server_dot.state == "up" and not feed()
    maint = {"verdict": "maintenance", "notice": {"url": "https://www.nexon.com/maplestory/news/maintenance/1",
                                                  "endAt": (now + 3600) * 1000}}
    chat._on_server_status(serverstatus.parse(maint, now))
    chat._on_server_status(serverstatus.parse(maint, now))
    chat._on_server_status(None)                           # unreachable meanwhile: grey, and no notice
    assert chat.server_dot.state == "unknown"
    chat._on_server_status(serverstatus.parse({"verdict": "up"}, now))
    texts = [w.msg.text() if isinstance(w, NoticeCard) else w.text() for w in feed()]
    assert len(texts) == 2 and "נכנסו לתחזוקה" in texts[0] and "התחזוקה הסתיימה" in texts[1]
    notice = chat.feed.findChildren(NoticeCard)[0]
    assert "Nexon" in notice.btn.text()                     # the notice links Nexon's maintenance post
    chat.hide()
    assert not chat.server_poller.running                  # closed: no polling


# ------------------------------------------------------------------ the patch notes window's News tab

def test_the_news_tab_lists_news_and_reports_what_was_seen(news_kb):
    from maplehelper.ui.newsview import NewsCard
    from maplehelper.ui.patchnotes import PatchNotesDialog
    seen = []
    d = PatchNotesDialog([], "he", "", news_kb, tab="news", unread=["founders-access-release-notes"])
    d.news_seen.connect(seen.append)
    pump(30)
    assert d.tab == "news" and seen == [["founders-access-release-notes"]]
    cards = d.findChildren(NewsCard)
    assert len(cards) == 5
    d.tabs.group.buttons()[0].click()
    assert d.tab == "changes"
    d.deleteLater()


def test_patch_notes_summary_counts_news():
    from maplehelper.ui.patchnotes import summary
    entries = [{"version": "1", "counts": {"added": 0, "changed": 0, "updated": 0, "removed": 0, "news": 2}}]
    assert summary(I18n("en"), entries) == "2 news items"
    assert summary(I18n("he"), [{"version": "1", "counts": {"news": 1}}]) == "ידיעה חדשה אחת"


# ------------------------------------------------------------------ one item's Free Market (market.py)

FILLED = ({"itemId": 298, "condition": "clean", "usual": 1250, "priceChecks": 14, "finishedTrades": 3,
           "cheapestSell": 1100, "bestBuy": 900, "forSale": 6, "windowDays": 14},
          {"itemId": 298, "condition": "clean", "days": 30, "points": [
              {"day": "2026-10-10", "low": 1000, "median": 1100, "high": 1300, "count": 5},
              {"day": "2026-10-19", "low": 1150, "median": 1232, "high": 1400, "count": 7}]},
          {"listings": [
              {"id": 1, "side": "sell", "priceEach": 1180, "quantity": 50, "channel": 7, "fmRoom": 0,
               "createdAt": "2026-10-19 09:00:00"},
              {"id": 2, "side": "sell", "priceEach": 1100, "quantity": 200, "channel": 3, "fmRoom": 4,
               "createdAt": "2026-10-19 12:00:00"},
              {"id": 3, "side": "buy", "priceEach": 900, "quantity": 100, "channel": None, "fmRoom": None,
               "createdAt": "2026-10-18 12:00:00"},
              {"id": 4, "side": "sell", "priceEach": 1300, "quantity": 1, "createdAt": "2026-10-17 12:00:00"}],
           "summary": {"lowestSell": 1100, "highestBuy": 900, "activeCount": 4}})


def test_the_recorded_empty_market_reads_as_empty():
    m = market.parse_item_market(_load("item_market_summary_298.json"), _load("item_price_history_298.json"),
                                 _load("market_listings_298.json"))
    assert m.empty and m.usual is None and m.listings == []


def test_a_filled_market_reads_usual_offers_trend_and_listings():
    m = market.parse_item_market(*FILLED)
    assert (m.usual, m.checks, m.trades, m.cheapest_sell, m.best_buy, m.for_sale) == (1250, 14, 3, 1100, 900, 6)
    assert (m.trend_pct, m.trend_days, m.volume) == (12, 9, 12)
    assert [x.price for x in m.listings] == [1100, 1180, 900]                   # newest first, three
    assert m.listings[0].channel == 3 and m.listings[0].room == 4 and m.listings[2].side == "buy"
    # one failed request: the others still show
    part = market.parse_item_market(None, FILLED[1], FILLED[2])
    assert part.usual is None and part.cheapest_sell == 1100 and part.trend_pct == 12


def test_item_market_is_cached_bounded_and_polite(monkeypatch):
    asked = []

    def get(url, timeout):
        asked.append(url)
        return {} if "summary" in url else None
    monkeypatch.setattr(market, "_get", get)
    monkeypatch.setattr(market, "_item_cache", {})
    monkeypatch.setattr(market, "CACHE_ITEMS", 2)
    market._lookup(298)
    market._lookup(298)
    assert len(asked) == 3                                 # summary, history, listings, then the cache
    market._lookup(1), market._lookup(2)
    assert set(market._item_cache) == {1, 2}               # at most CACHE_ITEMS
    asked.clear()
    monkeypatch.setattr(market, "_get", lambda url, timeout: asked.append(url))
    assert market._lookup(5) is None and len(asked) == 1  # unreachable: one request, not three


def test_the_prices_page_shows_the_item_market(isolated_store, kb, monkeypatch):
    from maplehelper.ui.tools import ToolsDialog
    m = market.parse_item_market(*FILLED)
    monkeypatch.setattr(market, "item_market", lambda i: m)
    p = isolated_store.Profiles()
    p.add("Kiwi", "Thief", "Assassin", 34)
    for lang, words in (("en", ("usually about", "Cheapest for sale: 1,100", "Trend: ▲ 12% over 9 days",
                                "Selling x200 at 1,100 each", "Ch 3", "Room 4")),
                        ("he", ("בדרך כלל בערך", "הכי זול למכירה: 1,100", "מוכרים x200", "ערוץ 3"))):
        d = ToolsDialog(kb, p, isolated_store.Settings(), lang, "", {}, "prices")
        name = next(n for n in kb._item_by_name if kb.get(kb._item_by_name[n])["key"].split("/")[1].isdigit())
        d.price_input.setText(kb.get(kb._item_by_name[name])["name"])
        d._fill_prices()
        d._on_market((d._price_for, m))                     # as the lookup thread delivers it
        text = d.fm_label.text() + " ".join(d.fm_more.itemAt(i).widget().text() for i in range(d.fm_more.count()))
        text = re.sub("[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text).replace("\xa0", " ")     # the bidi marks, NBSPs
        for w in words:
            assert w in text, (lang, w)
        d.deleteLater()
        pump()


def test_the_first_kb_with_news_lists_none_as_new(tmp_path):
    import json
    import kb_release
    old, new = tmp_path / "old", tmp_path / "new"
    for d in (old, new):
        d.mkdir()
        (d / "index.json").write_text("[]", encoding="utf-8")
    (new / "news.json").write_text(json.dumps({"items": [{"id": "a", "title": "T", "date": "2026-10-01"}]}), encoding="utf-8")
    assert "news" not in kb_release.diff_kb(old, new)["counts"]          # no news.json before: the list starts
    (old / "news.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    assert kb_release.diff_kb(old, new)["counts"]["news"] == 1


def test_the_megaphone_opens_the_news_window_and_marks_it_read(chat, news_kb):
    from PySide6.QtWidgets import QApplication
    qt = QApplication.instance()
    """News has its own place: a header button (orange while there is unread news) and its own window."""
    from maplehelper import news
    from maplehelper.ui.newsview import NewsCard, news_dialog
    assert chat.news_btn.property("unread") == "true"
    asked = []
    chat.news_requested.connect(lambda: asked.append(True))
    chat.news_btn.click()
    assert asked == [True]
    kb = chat.kb
    unread = [i["id"] for i in news.unread(kb, chat.settings[news.SETTING])]
    dlg = news_dialog("he", "", kb, unread)
    seen = []
    dlg.news_seen.connect(seen.append)
    qt.processEvents()
    assert seen == [unread] and dlg.findChildren(NewsCard)
    news.mark_read(chat.settings, unread)
    chat.show_news()
    assert chat.news_btn.property("unread") == "false"
    dlg.close()


def test_an_article_reads_in_full_in_the_app_and_back_returns_to_the_list(news_kb):
    """The whole item in the News window, like a guide: summary, every highlight and NiaMeowDB's note, in Hebrew
    when translated; Back and Esc return to the list."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QPushButton
    from maplehelper.ui.newsview import news_dialog
    i = news.items(news_kb)[0]
    i = dict(i, summary_he="תקציר", highlights_he=[f"עיקר {n}" for n in range(len(i["highlights"]))],
             commentary_he="הערה" if i.get("commentary") else "")
    dlg = news_dialog("he", "", news_kb)
    reads = [b for b in dlg.findChildren(QPushButton) if "לקריאת הכתבה" in b.text()]
    assert reads
    dlg.open_article(i)
    texts = re.sub("[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "",
                   " ".join(lb.text() for lb in dlg.stack.currentWidget().findChildren(QLabel)))
    assert "עיקר 0" in texts and f"עיקר {len(i['highlights']) - 1}" in texts and "העיקר" in texts
    QTest.keyClick(dlg, Qt.Key_Escape)
    assert dlg.stack.count() == 1 and dlg.stack.currentIndex() == 0
    dlg.close()


def test_the_server_tip_says_when_it_was_checked_with_a_comma_before_another_days_time():
    """The tooltip stays until the next answer, so it names the check's time, never a frozen "just now"; a date
    and a time read "6.10, 21:00"."""
    from datetime import datetime

    from maplehelper.ui import serverdot
    other_day = datetime(2025, 10, 6, 21, 0).timestamp()    # never today (it was, on 6.10.2026)
    assert serverdot.when(other_day) == "6.10, 21:00"
    st = serverstatus.Status(state="prelaunch", opens_at=other_day, checked=time.time() - 600)
    tip = serverdot.tip(I18n("he"), st)
    assert "נבדק ב-" + serverdot.when(st.checked) in tip and "עכשיו" not in tip and "6.10, 21:00" in tip
