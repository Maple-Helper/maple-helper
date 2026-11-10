"""The nightly scrape must never publish a broken or emptied KB, and must not report changes that didn't happen
(launch audit, SCP-*). Everything here runs on simulated pages and answers: no network."""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import scrape_meowdb  # noqa: E402


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buf, "PNG")
    return buf.getvalue()


def test_a_missing_pillow_is_an_error_not_a_silently_lost_picture(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(ImportError):
        scrape_meowdb.save_image(b"x", tmp_path / "a.png")


def test_a_bad_picture_is_just_skipped(tmp_path):
    assert scrape_meowdb.save_image(b"not a picture", tmp_path / "a.png") is False
    assert scrape_meowdb.save_image(_png(), tmp_path / "b.png") is True


GUIDE = """<html><head><title>Assassin Guide | NiaMeowDB</title></head><body>
<div>Explore the database</div><div>Items Monsters Pets Maps</div>
<section class="HomeNoticeStrip_strip__x" aria-label="Notice"><span>[ Notice ]</span> Beginner's guide refreshed.</section>
<div><nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">Home</a> / <a href="/m">MS Classic</a> / Guides / Assassin 30-70</nav>
</div><h1>MapleStory Classic Assassin Build and Leveling Guide: Lv 30-70</h1>
<p>Assassins use Lucky Seven.</p>
<p>Ad blocked? Fair. NiaMeowDB pays for catnip with ads. No hard feelings.</p><a href="/s">Buy us a coffee →</a></div>
<p>Pros</p></body></html>"""


def test_a_guide_keeps_neither_the_site_menu_nor_the_notice_banner_nor_the_ad_note():
    text = scrape_meowdb.main_text(GUIDE, "MapleStory Classic Assassin Guide: Lv 30-70")
    assert text.startswith("MapleStory Classic Assassin Build") and "Lucky Seven" in text and "Pros" in text
    for chrome in ("Explore the database", "Notice", "Home /", "Ad blocked", "Buy us a coffee"):
        assert chrome not in text


MONSTER = """<html><body><div>Explore the database</div><div>Monsters / Snail</div><div>🐾</div>
<div>Mesos per kill Loading...</div><div>HP</div><div>8</div><div>Base 1 Calculate</div>
<div>Items players have personally seen drop in-game. Upvote what you've seen, downvote what you haven't.</div>
<div>Loading…</div><div>no rolls yet Log in to submit</div><div>Log in to sell or buy</div><div>Next →</div>
<div>Snail Shell</div></body></html>"""


def test_the_sites_controls_and_placeholders_are_not_page_text():
    lines = scrape_meowdb.main_text(MONSTER, "Snail").split("\n")
    assert lines == ["Mesos per kill", "HP", "8", "Base 1", "no rolls yet", "Snail Shell"]


def test_an_items_watchlist_button_and_its_watch_count_are_not_page_text():
    """The site added a watch count next to "Add to watchlist": the item window showed it as an untranslated stat,
    and 220 map pages carried it before "Find path here"."""
    page = """<html><body><div>Explore the database</div><div>Items / Lie Detector Test</div>
<div>Use · Misc · No. 0268</div><div><button>Add to watchlist</button> <span>Loading watch count...</span></div>
<div>Loading watch count…</div><div>Required Level 10</div></body></html>"""
    assert scrape_meowdb.main_text(page, "Lie Detector Test").split("\n") == ["Use · Misc · No. 0268", "Required Level 10"]
    page = """<html><body><div>Explore the database</div><div>Maps / Henesys Hunting Ground I</div>
<div><button>Add to watchlist</button> <span>Loading watch count...</span> <a>Find path here</a></div></body></html>"""
    assert scrape_meowdb.main_text(page, "Henesys Hunting Ground I").split("\n") == ["Add to watchlist Find path here"]


# ---------------------------------------------------------------- a night's scrape, simulated

def _night(tmp_path, monkeypatch, site: dict[str, str | None], refresh: bool):
    """Run scrape() against a fake site: {key: page hash, or None for a fetch that failed tonight}."""
    import json

    import meowdb_sections
    import scrape_news
    monkeypatch.setattr(scrape_meowdb, "KB", tmp_path)
    urls = {p: [] for p in scrape_meowdb.CATEGORIES}
    prefix = {v[0]: k for k, v in scrape_meowdb.CATEGORIES.items()}
    for key in site:
        cat, _, slug = key.partition("/")
        url = f"{scrape_meowdb.BASE}/msclassic/{prefix[cat]}/{slug}"
        urls[prefix[cat]].append(url)
        scrape_meowdb.LASTMOD[url] = "2026-10-06"
    monkeypatch.setattr(scrape_meowdb, "entity_urls", lambda: urls)

    def one(category, slug, url, refresh, prev_hash=None):
        h = site[f"{category}/{slug}"]
        if h is None:
            return None
        (tmp_path / "pages" / category / f"{slug}.md").write_text(h, encoding="utf-8")
        return {"key": f"{category}/{slug}", "name": slug, "category": category, "url": url, "hash": h,
                "lastmod": "2026-10-06"}
    monkeypatch.setattr(scrape_meowdb, "scrape_one", one)
    monkeypatch.setattr(meowdb_sections, "scrape", lambda kb, fetch, delay: 0)
    monkeypatch.setattr(scrape_news, "update", lambda kb, fetch, fetch_bytes=None: 0)
    monkeypatch.setattr(scrape_meowdb, "scrape_routes", lambda: 0)
    scrape_meowdb.scrape(None, refresh=refresh, changed_only=not refresh)
    index = {e["key"]: e for e in json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))}
    return index, json.loads((tmp_path / "last_run.json").read_text(encoding="utf-8"))


def _seed(tmp_path, keys):
    import json
    for key in keys:
        cat, _, slug = key.partition("/")
        (tmp_path / "pages" / cat).mkdir(parents=True, exist_ok=True)
        (tmp_path / "img" / cat).mkdir(parents=True, exist_ok=True)
        (tmp_path / "pages" / cat / f"{slug}.md").write_text("old", encoding="utf-8")
        (tmp_path / "img" / cat / f"{slug}.png").write_bytes(b"png")
    rows = [{"key": k, "name": k, "category": k.partition("/")[0], "hash": "h", "lastmod": "2026-10-06"} for k in keys]
    scrape_meowdb.write_index(tmp_path / "index.json", rows)
    (tmp_path / "meta.json").write_text(json.dumps({"version": "1"}), encoding="utf-8")


def test_a_refresh_that_finds_nothing_new_reports_no_change(tmp_path, monkeypatch):
    _seed(tmp_path, ["monster/1", "item/2"])
    index, run = _night(tmp_path, monkeypatch, {"monster/1": "h", "item/2": "h"}, refresh=True)
    assert run == {"checked": 2, "changed": 0}            # SCP-3: it was "changed": 4161 every Sunday
    _, run = _night(tmp_path, monkeypatch, {"monster/1": "h", "item/2": "new"}, refresh=True)
    assert run["changed"] == 1


def test_a_failed_fetch_on_a_refresh_keeps_the_entity(tmp_path, monkeypatch):
    _seed(tmp_path, ["quest/1", "npc/2"])
    index, _ = _night(tmp_path, monkeypatch, {"quest/1": None, "npc/2": "h"}, refresh=True)
    assert set(index) == {"quest/1", "npc/2"}               # SCP-4: not "removed" tonight, "added" tomorrow
    assert (tmp_path / "pages" / "quest" / "1.md").exists()


def test_what_the_site_removed_leaves_the_kb_on_a_refresh(tmp_path, monkeypatch):
    _seed(tmp_path, ["item/1", "item/2"])
    index, run = _night(tmp_path, monkeypatch, {"item/1": "h"}, refresh=True)
    assert set(index) == {"item/1"} and run["changed"] == 1
    assert not (tmp_path / "pages" / "item" / "2.md").exists() and not (tmp_path / "img" / "item" / "2.png").exists()
    assert (tmp_path / "pages" / "item" / "1.md").exists()


def test_a_dropped_connection_is_retried_not_fatal(monkeypatch):
    import http.client
    import ssl
    calls = []

    def boom(req, timeout=30):
        calls.append(1)
        raise [http.client.RemoteDisconnected("gone"), ConnectionResetError(), ssl.SSLError(),
               http.client.IncompleteRead(b"")][len(calls) - 1]
    monkeypatch.setattr(scrape_meowdb.urllib.request, "urlopen", boom)
    monkeypatch.setattr(scrape_meowdb.time, "sleep", lambda s: None)
    assert scrape_meowdb.fetch("https://meowdb.com/x", retries=4) is None and len(calls) == 4


@pytest.fixture
def gate(monkeypatch):
    """scrape_meowdb.fetch with a fresh gate and a fake clock: `waits` collects every pause it takes."""
    clock, waits = [1000.0], []
    monkeypatch.setattr(scrape_meowdb, "_last", [0.0])
    monkeypatch.setattr(scrape_meowdb, "_blocked", [])
    monkeypatch.setattr(scrape_meowdb.time, "monotonic", lambda: clock[0])

    def sleep(s):
        waits.append(s)
        clock[0] += s
    monkeypatch.setattr(scrape_meowdb.time, "sleep", sleep)
    return clock, waits


class _Answer:
    def __init__(self, body=b"ok"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def test_meowdb_requests_are_ten_seconds_apart_other_hosts_are_not(gate, monkeypatch):
    clock, waits = gate
    sent = []

    def answer(req, timeout=30):
        sent.append((req.full_url, clock[0], req.get_header("User-agent")))
        clock[0] += 2                       # the answer takes two seconds
        return _Answer()
    monkeypatch.setattr(scrape_meowdb.urllib.request, "urlopen", answer)
    for url in ("https://meowdb.com/a", "https://meowdb.com/b", "https://www.nexon.com/n", "https://meowdb.com/c"):
        assert scrape_meowdb.fetch(url) == "ok"
    meow = [t for u, t, _ in sent if "meowdb" in u]
    # the site's tool rules: one request every 10 seconds, counted from the end of the last answer
    assert all(b - a >= scrape_meowdb.DELAY_SECONDS + 2 for a, b in zip(meow, meow[1:]))
    assert scrape_meowdb.DELAY_SECONDS >= 10
    assert sent[2][1] == sent[1][1] + 2     # Nexon's news never waits for meowdb's turn
    # ...and our name and a way to reach us in the User-Agent
    assert all("MapleHelper" in ua and "github.com/Maple-Helper" in ua for _, _, ua in sent)


@pytest.mark.parametrize("code", [403, 429])
def test_a_403_or_429_stops_every_later_meowdb_request(gate, monkeypatch, code):
    import urllib.error
    sent = []

    def answer(req, timeout=30):
        sent.append(req.full_url)
        if "meowdb" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)
        return _Answer()
    monkeypatch.setattr(scrape_meowdb.urllib.request, "urlopen", answer)
    with pytest.raises(scrape_meowdb.Blocked):
        scrape_meowdb.fetch("https://meowdb.com/a")
    with pytest.raises(scrape_meowdb.Blocked):
        scrape_meowdb.fetch("https://meowdb.com/b")
    assert sent == ["https://meowdb.com/a"]          # no retry, and nothing more is sent
    assert scrape_meowdb.fetch("https://www.nexon.com/n") == "ok"
    assert str(code) in scrape_meowdb.blocked()


def test_a_blocked_night_keeps_the_kb_and_still_brings_the_news(tmp_path, monkeypatch):
    import json

    import meowdb_sections
    import scrape_news
    _seed(tmp_path, ["monster/1"])
    monkeypatch.setattr(scrape_meowdb, "KB", tmp_path)
    monkeypatch.setattr(scrape_meowdb, "_blocked", [])

    def refused():
        scrape_meowdb._blocked.append("403 from https://meowdb.com/msclassic/sitemap.xml")
        raise scrape_meowdb.Blocked(scrape_meowdb._blocked[0])
    monkeypatch.setattr(scrape_meowdb, "entity_urls", refused)
    monkeypatch.setattr(meowdb_sections, "scrape", lambda kb, fetch, delay: pytest.fail("asked meowdb again"))
    monkeypatch.setattr(scrape_meowdb, "scrape_routes", lambda: pytest.fail("asked meowdb again"))
    monkeypatch.setattr(scrape_news, "update", lambda kb, fetch, fetch_bytes=None: 1)
    scrape_meowdb.scrape(None, refresh=False, changed_only=True, rolling=400)
    run = json.loads((tmp_path / "last_run.json").read_text(encoding="utf-8"))
    assert run["changed"] == 1 and "403" in run["blocked"]
    assert [e["key"] for e in json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))] == ["monster/1"]


def test_a_rolling_night_rereads_the_pages_fetched_longest_ago(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape_meowdb, "KB", tmp_path)
    keys = ["item/1", "item/2", "item/3", "item/4"]
    _seed(tmp_path, keys)
    url = {k: f"{scrape_meowdb.BASE}/msclassic/item-db/{k[5:]}" for k in keys}
    monkeypatch.setattr(scrape_meowdb, "LASTMOD", {u: "2026-10-06" for u in url.values()})
    index = {k: {"key": k, "lastmod": "2026-10-06", "fetched": d}
             for k, d in zip(keys, ["2026-11-09", "", "2026-11-01", "2026-11-05"])}
    jobs = scrape_meowdb.plan_jobs({"item-db": list(url.values())}, index, None, False, True, rolling=2)
    assert [j[2] for j in jobs] == ["item/2", "item/3"]
    assert scrape_meowdb.plan_jobs({"item-db": list(url.values())}, index, None, False, True) == []


def test_a_page_the_sitemap_dropped_leaves_the_kb_without_a_full_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape_meowdb, "KB", tmp_path)
    keys = [f"item/{n}" for n in range(1, 41)]
    _seed(tmp_path, keys)
    index = {k: {"key": k} for k in keys}
    listed = [f"{scrape_meowdb.BASE}/msclassic/item-db/{n}" for n in range(1, 40)]
    assert scrape_meowdb.drop_unlisted({"item-db": listed}, index) == 1
    assert "item/40" not in index and not (tmp_path / "pages" / "item" / "40.md").exists()
    # a half-served sitemap removes nothing
    assert scrape_meowdb.drop_unlisted({"item-db": listed[:10]}, index) == 0 and len(index) == 39


def test_make_aliases_cleans_shared_aliases_even_with_nothing_to_ask(tmp_path, monkeypatch):
    import json

    import make_aliases
    index = [{"key": "npc/112", "name": "Pison", "category": "npc"}, {"key": "npc/801", "name": "Pason", "category": "npc"}]
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    out = tmp_path / "aliases.json"
    out.write_text(json.dumps({"npc/112": ["פייסון", "פיסון"], "npc/801": ["פייסון", "פאסון"]}, ensure_ascii=False),
                   encoding="utf-8")
    monkeypatch.setattr(make_aliases, "KB", tmp_path)
    monkeypatch.setattr(make_aliases, "OUT", out)
    monkeypatch.setattr(make_aliases, "claude_exe", lambda: "claude")
    monkeypatch.setattr(make_aliases, "ask", lambda exe, rows: pytest.fail("nothing to ask"))
    make_aliases.main(["npc"])
    assert json.loads(out.read_text(encoding="utf-8")) == {"npc/112": ["פיסון"], "npc/801": ["פאסון"]}
