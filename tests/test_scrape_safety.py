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
