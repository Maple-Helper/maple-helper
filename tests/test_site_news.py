"""Nexon's news, MeowDB's server status and Free Market sections in the app, from recorded answers: no network.

tests/fixtures/nexon: Nexon's news list (news_list.json, the real answer of 2026-10-12 cut to the 16 articles since
September 29) and five Classic World articles (article_<id>.json, recorded the same day: the short ones as
published, the long ones with their styling and pictures stripped, the same text). tests/fixtures/meowdb:
server_status_prelaunch.json and the item_*_298.json / market_listings_298.json files are the real endpoints'
answers of 2026-10-04 (the game wasn't open, so the market ones are empty). The filled market answers below use the
field names the site's own item page reads.
"""
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper import market, news, serverstatus  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import scrape_news  # noqa: E402

app = QApplication.instance() or QApplication([])
FIX = Path(__file__).parent / "fixtures" / "meowdb"
NX = Path(__file__).parent / "fixtures" / "nexon"
LIST = (NX / "news_list.json").read_text(encoding="utf-8")
TODAY = date(2026, 10, 4)
# the Classic World articles since 2026-10-02, newest first: a maintenance, Founder's Access opening, its Cash Shop,
# its release notes, and the Inkwell interview (not marked Classic World by Nexon, but named so)
IDS = ["nexon-46321", "nexon-45490", "nexon-45819", "nexon-45621", "nexon-45681"]


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(scrape_news, "PAUSE", 0)


def nexon(url, broken=()):
    """Nexon's site as recorded: the list, and the articles there is a fixture of (any other: no answer)."""
    if url == scrape_news.NEWS_LIST:
        return LIST
    m = re.fullmatch(r"https://g\.nexonstatic\.com/maplestory/cms/v1/news/(\d+)", url)
    f = NX / f"article_{m.group(1)}.json" if m and m.group(1) not in broken else None
    return f.read_text(encoding="utf-8") if f and f.is_file() else None


def article(nid: int) -> dict:
    return json.loads((NX / f"article_{nid}.json").read_text(encoding="utf-8"))


def scraped(kb: Path) -> dict:
    return {i["id"]: i for i in json.loads((kb / "news.json").read_text(encoding="utf-8"))["items"]}


def test_news_start_at_the_cut(tmp_path):
    """Only news from 2026-10-02 on (the owner's call): the scrape keeps none older (the FAQ and the pre-download
    steps of September 29 are Classic World's too), and the app shows none older from a KB that still has them."""
    assert scrape_news.SINCE == news.SINCE == "2026-10-02"
    kept = scrape_news.selected(scrape_news.entries(LIST))
    assert [e["id"] for e in kept] == [int(i.split("-")[1]) for i in IDS]
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

def test_nexons_classic_world_articles_become_news_items(tmp_path):
    """Nexon's own title and summary, its article's address, official: nothing in the item is anyone else's."""
    assert scrape_news.update(tmp_path, nexon) == 5
    by = scraped(tmp_path)
    assert list(by) == IDS                                                 # newest first
    notes = by["nexon-45621"]
    assert notes["date"] == "2026-10-03" and notes["title"] == "Founder's Access Release Notes"
    assert notes["summary"] == ("Click here to read the release notes for the Global MapleStory Classic World "
                                "Founder's Access.")
    assert notes["url"] == notes["source_url"] == "https://www.nexon.com/maplestory/news/general/45621"
    assert scrape_news.NEXON_ARTICLE.match(notes["url"]).group(1) == "45621"
    assert (notes["publisher"], notes["official"], notes["region"], notes["commentary"]) == ("Nexon", True, "gms", "")
    assert notes["tags"] == ["general"] and by["nexon-45819"]["tags"] == ["sale"]
    assert notes["hash"] == scrape_news._hash(notes["title"], notes["summary"])
    assert "Cash Shop" in by["nexon-45819"]["mentions"]
    assert json.loads((tmp_path / "news.json").read_text(encoding="utf-8"))["source"] == \
        "Nexon (nexon.com/maplestory/news)"


@pytest.mark.parametrize("entry,shown", [
    ({"isMSCW": True, "name": "Patch"}, True),
    ({"isMSCW": "True", "name": "Patch"}, True),                 # an article's flags are strings
    ({"isMSCW": False, "name": "Inkwell on the Future of Global MapleStory and Classic World"}, True),
    ({"name": "Patch", "summary": "Classic World servers are back."}, True),     # no flag at all
    ({"isMSCW": "False", "name": "Cash Shop Update for October 7"}, False),
    ({"name": "v.271 Known Issues"}, False),
])
def test_classic_world_is_nexons_flag_or_its_name(entry, shown):
    assert scrape_news.classic(entry) is shown


def test_the_global_games_news_and_older_news_stay_out():
    found = scrape_news.entries(LIST)
    by = {e["id"]: e for e in found}
    assert not scrape_news.classic(by[46249])            # Global MapleStory's own maintenance that day
    assert scrape_news.classic(by[45385]) and 45385 not in {e["id"] for e in scrape_news.selected(found)}
    assert by[45681].get("isMSCW") is False and 45681 in {e["id"] for e in scrape_news.selected(found)}


# ------------------------------------------------------------------ the highlights: Nexon's sentences, chosen alike

def _points(nid: int) -> list[str]:
    a = article(nid)
    return scrape_news.highlights(a["body"], a["summary"], a["name"])


def test_a_maintenance_says_its_times_and_what_is_down():
    """Every line of a short notice, "Times:" read with the times it introduces."""
    p = _points(46321)
    assert p[0].startswith("[Updated 10/8 at 8:25 AM PDT] The maintenance has been completed")
    assert p[2].startswith("Times: Thursday, October 8, 2026; PDT (UTC -7): 7:00 AM - 9:00 AM; EDT (UTC -4): 10:00")
    assert "What will be unavailable: All MapleStory Classic World game servers." in p
    assert p[-1] == "Resolved an issue where some players were unable to connect to Channels 1, 2, 3, and 4."
    assert len(p) == 8 and not any(x.endswith(":") for x in p)


def test_a_short_announcement_is_its_paragraphs_without_the_pictures():
    p = _points(45490)
    assert len(p) == 5 and p[0].startswith("Founder's Access for Global MapleStory Classic World is now live!")
    assert p[3].startswith("All Founder's Package items can be fully equipped")       # a picture's caption
    # the last paragraph is over MAX_HIGHLIGHT: it ends at its last whole sentence that fits
    assert len(p[4]) <= scrape_news.MAX_HIGHLIGHT and p[4].endswith("November 30 at 11:59 PM (UTC).")


def test_the_cash_shop_is_its_sales_each_with_its_first_line():
    """421 lines: each section and the first thing under it (its dates), from the top heading level only, since the
    sub-headings ("Male Hairs" under each coupon) made more than MAX_HIGHLIGHTS."""
    p = _points(45819)
    assert p[:4] == [
        "Limited-Time and Rotational Sales: The following sales will only be available for a limited time, or will "
        "have their sales details periodically update over time.",
        "Founder's Mystery Fashion Crate: UTC: Tuesday, October 6 (End of Maintenance) - Wednesday, October 28 7:59 AM",
        "Aurora Stamp Shop Season 1: UTC: Tuesday, October 6, 2026 (End of Maintenance) - Wednesday, January 13, 2027 "
        "7:59 AM",
        "Aurora Mystery Fashion Crate: UTC: Tuesday, October 6, 2026 (End of Maintenance) - Wednesday, January 13, "
        "2027 7:59 AM"]
    assert len(p) == scrape_news.MAX_HIGHLIGHTS and not any(x.startswith(("Male Hairs", "A Note on Gifting"))
                                                             for x in p)


def test_the_release_notes_are_their_sections_with_what_each_says_first():
    """Nexon's words as written (its "by 30, 2026" too); a section whose first lines are the greeting has its heading
    alone."""
    assert _points(45621) == [
        "October 6, 2026 6:00 PM UTC",
        "Accessing Classic World: Download and install the official Nexon Launcher from the Nexon website.",
        "General Key Details: Your Founder’s Package will be available to claim from your in-game mailbox. Items must "
        "be claimed by 30, 2026 at 11:59 PM UTC.",
        "Founder’s First Step: How to Participate",
        "Join Founder’s Access: There’s still time to join us! Founder’s Packages are available on the Nexon website "
        "and Steam through October 14, 2026 at 11:59 PM PDT. Choose the Orange Mushroom or Zakum Package to get "
        "Founder’s Access and start your MapleStory Classic World adventure before Grand Launch!"]


def test_the_interview_is_its_parts_then_its_questions():
    """Three sections only (the title's heading isn't one): the bold paragraphs, the interviewer's questions, fill
    it up to MAX_BOLD."""
    p = _points(45681)
    assert len(p) == scrape_news.MAX_BOLD
    assert p[:4] == [
        "A Conversation with Inkwell: There is one name that has been continually brought up amongst MMORPG players "
        "lately: Global MapleStory Classic World.",
        "Global MapleStory: A deeper, evolving experience.",
        "Classic World: A fresh beginning with a modern approach.",
        "To start, could you introduce yourself for readers who may not know much about Inkwell yet? You have a very "
        "long history with MapleStory."]
    assert p[5] == "Wasn’t one of those companies Embark Studios?"


def test_a_heading_with_nothing_or_only_itself_under_it_stands_alone():
    body = ("<h2>Times</h2><p><strong>Times</strong></p><p>Noon.</p><h2>Rewards</h2><h2>Events:</h2><p>Hi Maplers!</p>"
            "<p>Coconut Harvest.</p>" + "".join(f"<li>Line {n}.</li>" for n in range(12)))
    assert scrape_news.highlights(body)[:3] == ["Times: Noon.", "Rewards", "Events: Coconut Harvest."]
    # under FEW_HEADINGS sections, the bold paragraphs come after them, never the same text twice
    assert scrape_news.highlights(body + "<p><b>Why?</b></p><p><b>Noon.</b></p>")[3:] == ["Why?"]


def test_greetings_the_summary_and_empty_lines_are_no_highlight():
    body = ("<p>Hi Maplers! Welcome back.</p><p>The summary.</p><p><img src='x.png'></p><p>&nbsp;</p>"
            "<ul><li><strong>Patch</strong><ul><li>Fixed A.</li></ul></li></ul><p>Thank you for playing!</p>"
            "<p>Join our official Discord.</p><p>Ends &amp; starts<br>at noon<br></p>")
    assert scrape_news.highlights(body, "The summary.") == ["Patch", "Fixed A.", "Ends & starts; at noon"]
    long = "<h4>x</h4>" + "".join(f"<p>Line {n}.</p>" for n in range(20))
    assert scrape_news.highlights(long) == [f"Line {n}." for n in range(scrape_news.SHORT_HEADINGS)]
    assert scrape_news.clip("word " * 100).endswith("…") and len(scrape_news.clip("word " * 100)) <= 401


# ------------------------------------------------------------------ ids, failures, Hebrew

def test_an_item_keeps_the_id_it_had_so_read_marks_and_hebrew_stay(tmp_path):
    """The KB's news before Nexon's: the release notes linked Nexon's article, the interview had its pictures;
    their ids stay. Founder's Access opening had no Nexon link: it comes back under Nexon's id."""
    old = [{"id": "founders-access-release-notes", "title": "x", "date": "2026-10-03",
            "source_url": "https://www.nexon.com/maplestory/news/general/45621/founder-s-access-release-notes",
            "url": "https://meowdb.com/msclassic/news/founders-access-release-notes", "highlights": ["MeowDB's"]},
           {"id": "inkwell-interview", "title": "y", "date": "2026-10-02", "source_url": "https://example.com/i",
            "nexon": {"id": 45681, "pictures": []}},
           {"id": "classic-world-founders-access-live", "title": "z", "date": "2026-10-06"}]
    (tmp_path / "news.json").write_text(json.dumps({"items": old}), encoding="utf-8")
    assert scrape_news.update(tmp_path, lambda url: nexon(url, broken={"45621"})) == 6     # 5 new or changed, 1 gone
    by = scraped(tmp_path)
    assert list(by) == ["nexon-46321", "nexon-45490", "nexon-45819", "founders-access-release-notes",
                        "inkwell-interview"]
    # its article unreadable tonight: no highlights rather than the community site's
    assert by["founders-access-release-notes"]["highlights"] == []
    assert by["founders-access-release-notes"]["url"] == "https://www.nexon.com/maplestory/news/general/45621"
    assert scrape_news.update(tmp_path, nexon) == 1
    kept = scraped(tmp_path)["founders-access-release-notes"]["highlights"]
    assert kept == _points(45621)
    assert scrape_news.update(tmp_path, lambda url: nexon(url, broken={"45621"})) == 0     # Nexon's highlights stay


def test_a_bad_night_keeps_the_news_there_is(tmp_path):
    assert scrape_news.update(tmp_path, nexon) == 5
    first = (tmp_path / "news.json").read_text(encoding="utf-8")
    assert scrape_news.update(tmp_path, nexon) == 0                                 # nothing new: not a change

    def down(url):
        raise OSError("refused")
    no_classic = json.dumps([e for e in json.loads(LIST) if not scrape_news.classic(e)])
    for night in (lambda url: None, down, lambda url: "<html>new layout</html>", lambda url: "[]",
                  lambda url: json.dumps({"items": []}),
                  lambda url: no_classic if url == scrape_news.NEWS_LIST else nexon(url)):
        assert scrape_news.update(tmp_path, night) == 0
        assert (tmp_path / "news.json").read_text(encoding="utf-8") == first


def test_a_hebrew_summary_is_kept_only_for_the_english_it_was_made_from(tmp_path):
    scrape_news.update(tmp_path, nexon)
    h = {k: i["hash"] for k, i in scraped(tmp_path).items()}
    he = {"nexon-45621": {"summary": "תקציר", "source_hash": h["nexon-45621"]},
          "nexon-45681": {"summary": "ישן", "source_hash": "0000"}}
    by = scraped(tmp_path)
    for i in by.values():
        scrape_news._with_he(i, he)
    assert by["nexon-45621"]["summary_he"] == "תקציר"
    assert "summary_he" not in by["nexon-45681"]                         # Nexon edited it since


def test_news_go_into_the_changelog_and_the_validator(tmp_path):
    import kb_release
    old, new = tmp_path / "old", tmp_path / "new"
    for d in (old, new):
        d.mkdir()
        (d / "index.json").write_text("[]", encoding="utf-8")
    scrape_news.update(new, nexon)
    items = list(scraped(new).values())
    (old / "news.json").write_text(json.dumps({"items": items[1:]}), encoding="utf-8")
    d = kb_release.diff_kb(old, new)
    assert d["counts"]["news"] == 1 and d["news"][0]["id"] == "nexon-46321"
    assert kb_release.diff_kb(new, new)["counts"] == {"added": 0, "removed": 0, "changed": 0, "updated": 0}
    (new / "news.json").write_text(json.dumps({"items": [{"id": "x"}]}), encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="news.json"):
        kb_release.validate(new, categories=[])


def test_translate_news_round_trip(tmp_path, monkeypatch):
    import translate_news
    monkeypatch.setattr(translate_news, "OUT", tmp_path / "assets")
    kb = tmp_path / "kb"
    kb.mkdir()
    scrape_news.update(kb, nexon)
    out = tmp_path / "export.json"
    assert translate_news.export("he", out, kb) == 5
    done = json.loads(out.read_text(encoding="utf-8"))
    done["strings"] = {k: "בעברית" for k in list(done["strings"])[:2]}
    out.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    assert translate_news.import_("he", out) == 2
    assert translate_news.export("he", out, kb) == 3            # only what still needs a translation


def test_nothing_fetches_meowdbs_news_page_any_more():
    """The owner's rule: the news come from Nexon, never from a community site's summary of them."""
    root = Path(__file__).resolve().parents[1]
    for f in [*(root / "tools").glob("*.py"), *(root / "maplehelper").rglob("*.py"),
              *(root / ".github" / "workflows").glob("*.yml")]:
        assert "meowdb.com/msclassic/news" not in f.read_text(encoding="utf-8"), f


# ------------------------------------------------------------------ the app's news (news.py)

@pytest.fixture
def news_kb(kb_copy, tmp_path, monkeypatch):
    """The recorded news, the release notes with a Hebrew title made from their current English."""
    notes = next(e for e in json.loads(LIST) if e["id"] == 45621)
    tr = tmp_path / "news_he"
    tr.mkdir()
    (tr / "he.json").write_text(json.dumps({"nexon-45621": {
        "title": "הערות השחרור של Founder's Access", "summary": "הערות השחרור.",
        "source_hash": scrape_news._hash(notes["name"], notes["summary"])}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(scrape_news, "TRANSLATIONS", tr)
    scrape_news.update(kb_copy, nexon)
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(kb_copy)


def test_unread_is_recent_global_news_not_yet_read(news_kb):
    ids = [i["id"] for i in news.unread(news_kb, [], today=TODAY)]
    # the last NEW_DAYS days, up to today: what came out after October 4 isn't news yet on the 4th
    assert ids == ["nexon-45621", "nexon-45681"]
    assert [i["id"] for i in news.unread(news_kb, [], today=date(2026, 10, 9))] == IDS
    assert [i["id"] for i in news.unread(news_kb, ["nexon-45621"], today=TODAY)] == ["nexon-45681"]
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
    assert "Nexon's official announcements" in lines[0] and "announcements, not the game's data" in lines[0]
    assert "NiaMeowDB" not in lines[0]
    assert lines[1].startswith("News 2026-10-03 (official, Nexon): Founder's Access Release Notes")
    asked = build_prompt("When does Orbis open?", None, None, news_kb, False)
    assert "(official, Nexon): [Completed] Unscheduled Maintenance" in asked
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
        # in the tooltip's language: past midnight the end is "Oct 11, 00:07" in English, "11.10, 00:07" in Hebrew
        assert serverdot.when(st.notice_end, t.rtl) in tip
        assert t("server_source", time=serverdot.when(st.checked, t.rtl)) in tip
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
    # the Hebrew title (news_kb's he.json), its English names kept whole
    assert "הערות השחרור" in chat.news_strip.title.text() and "Founder's" in chat.news_strip.title.text()
    assert "ועוד כתבה אחת שלא קראתם" in chat.news_strip.head.text() and "כתבה מ-" in chat.news_strip.head.text()
    chat.news_strip.close_btn.click()
    assert chat.settings["news_read"] == ["nexon-45621"]
    assert "Inkwell" in chat.news_strip.title.text()
    asked = []
    chat.news_requested.connect(lambda: asked.append(True))
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(chat.news_strip, Qt.LeftButton, pos=QPoint(40, 10))
    assert asked == [True]
    news.mark_read(chat.settings, ["nexon-45681"])
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
    d = PatchNotesDialog([], "he", "", news_kb, tab="news", unread=["nexon-45621"])
    d.news_seen.connect(seen.append)
    pump(30)
    assert d.tab == "news" and seen == [["nexon-45621"]]
    cards = d.findChildren(NewsCard)
    assert len(cards) == 5
    d.tabs.group.buttons()[0].click()
    assert d.tab == "changes"
    d.deleteLater()


def test_patch_notes_summary_counts_news():
    from maplehelper.ui.patchnotes import summary
    entries = [{"version": "1", "counts": {"added": 0, "changed": 0, "updated": 0, "removed": 0, "news": 2}}]
    assert summary(I18n("en"), entries) == "2 new articles"
    assert summary(I18n("he"), [{"version": "1", "counts": {"news": 1}}]) == "כתבה חדשה אחת"


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
    """The whole item in the News window, like a guide: summary and every highlight, in Hebrew when translated;
    Back and Esc return to the list."""
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
    assert serverdot.when(other_day, rtl=False) == "Oct 6, 21:00"     # never day.month in English (HEB-16)
    assert "Oct 6, 21:00" in serverdot.tip(I18n("en"), st)


@pytest.mark.parametrize("q,asks", [
    ("מתי אני מקבל ג'וב שני?", False), ("where do I open my inventory", False), ("is the event shop good?", False),
    ("מה החדשות?", True), ("מתי השרת נפתח?", True), ("is the server open?", True), ("when is the next GM event", True),
    ("any patch notes today?", True), ("מתי ההשקה?", True), ("מתי Orbis יוצא?", True),
])
def test_only_a_news_question_asks_for_the_news(q, asks):
    """"מתי" or "open" alone added the news block to every such prompt."""
    assert news.asks_news(q) is asks


def test_one_more_article_on_nexons_site_is_singular():
    """"עוד 1 כתבות" for a section of 31 (review2 UI2-5); the rest are on Nexon's site, where the news come from."""
    from maplehelper.i18n import I18n
    assert I18n("he")("news_more_site", n=1) == "עוד כתבה אחת באתר של Nexon"
    assert I18n("he")("news_more_site", n=3) == "עוד 3 כתבות באתר של Nexon"
    assert I18n("en")("news_more_site", n=1) == "1 more on Nexon's site"
    assert news.NEWS_PAGE == "https://www.nexon.com/maplestory/news"


def test_a_nexon_item_links_its_article_once_and_an_older_kbs_item_both():
    """A Nexon item's page is its source: one link. An older KB's item still links MeowDB's summary and Nexon's."""
    from PySide6.QtWidgets import QPushButton

    from maplehelper.ui.newsview import NewsCard
    from maplehelper.ui.newsview import article as page
    t = I18n("en")
    url = "https://www.nexon.com/maplestory/news/general/45621"
    mine = {"id": "nexon-45621", "title": "T", "date": "2026-10-03", "summary": "S", "highlights": [],
            "url": url, "source_url": url, "publisher": "Nexon", "official": True}
    older = dict(mine, url="https://meowdb.com/x")
    for build in (lambda i: NewsCard(t, i), lambda i: page(t, i)):
        w, w_old = build(mine), build(older)
        links = [b.text() for b in w.findChildren(QPushButton)]
        assert not any("MeowDB" in x for x in links) and any("Nexon" in x for x in links), links
        assert any("MeowDB" in b.text() for b in w_old.findChildren(QPushButton))
    tip = I18n("en")("news_official_tip", who="Nexon")
    assert "NiaMeowDB" not in tip and "NiaMeowDB" not in I18n("he")("news_official_tip", who="Nexon")


def test_the_maintenance_notice_redraws_its_date_in_the_new_language(chat):
    """review3 HEB-16-a: the end date kept the format of the language it was first drawn in."""
    from datetime import date

    from maplehelper import dates
    from maplehelper.ui.widgets import NoticeCard
    now = time.time()
    end = now + 3 * 86400                                   # another day: the date shows, not only the hour
    chat._on_server_status(serverstatus.parse({"verdict": "up"}, now))
    chat._on_server_status(serverstatus.parse(
        {"verdict": "maintenance", "notice": {"url": "https://www.nexon.com/maplestory/news/maintenance/1",
                                              "endAt": end * 1000}}, now))
    day = date.fromtimestamp(end)
    notice = chat.feed.findChildren(NoticeCard)[0]
    assert dates.day(day, True, False) in notice.msg.text()
    chat.settings["language"] = "en"
    chat.apply_language()
    assert dates.day(day, False, False) in notice.msg.text() and "until about" in notice.msg.text()
