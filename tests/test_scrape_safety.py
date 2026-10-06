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
    monkeypatch.setattr(scrape_news, "update", lambda kb, fetch: 0)
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


def test_a_429_waits_as_long_as_the_site_asks():
    import urllib.error
    e = urllib.error.HTTPError("u", 429, "slow down", {"Retry-After": "45"}, None)
    assert scrape_meowdb.retry_after(e) == 45
    assert scrape_meowdb.retry_after(urllib.error.HTTPError("u", 429, "", {"Retry-After": "9999"}, None)) == 120
    assert scrape_meowdb.retry_after(urllib.error.HTTPError("u", 503, "", {}, None)) is None
