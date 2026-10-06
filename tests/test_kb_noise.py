"""The nightly patch notes said "37 changes affect you, and 3,768 pages updated" for one real change: the scraper put a
picture's address in the page's "url" whenever it fetched a picture (and back the next night), it kept the site's
"Check your build" panel as page text, and a page whose text alone changed counted as affecting the player."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import scrape_meowdb  # noqa: E402

from maplehelper import recent  # noqa: E402

PAGE = """<html><head><title>Fire Boar | NiaMeowDB</title>
<script type="application/ld+json">{"@type": "Thing", "name": "Fire Boar", "image": "/msclassic/monsters/sprites/mob_1.png"}</script>
</head><body><div>Explore the database</div><div>Monsters / Fire Boar</div>
<div>Level 32</div><div>HP 842</div>
<div>Check your build against Fire Boar</div>
<div>Your damage on it How hard it hits you Accuracy to hit it Best maps for your build</div>
<div>Knockback 1 damage</div></body></html>"""


def test_the_page_keeps_its_own_url_when_a_picture_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape_meowdb, "KB", tmp_path)
    monkeypatch.setattr(scrape_meowdb, "DELAY_SECONDS", 0)
    (tmp_path / "pages" / "monster").mkdir(parents=True)
    (tmp_path / "img" / "monster").mkdir(parents=True)
    page_url = "https://meowdb.com/msclassic/monsters/1"
    monkeypatch.setattr(scrape_meowdb, "fetch", lambda url, binary=False, retries=3: b"png" if binary else PAGE)
    monkeypatch.setattr(scrape_meowdb, "save_image", lambda data, path, max_w=None: path.write_bytes(data) or True)
    row = scrape_meowdb.scrape_one("monster", "1", page_url, refresh=True)
    assert row["url"] == page_url
    md = (tmp_path / "pages" / "monster" / "1.md").read_text(encoding="utf-8")
    front = json.loads(md.split("---")[1])
    assert front["url"] == page_url and front["image"] == "img/monster/1.png"


def test_the_sites_build_panel_is_not_page_text():
    text = scrape_meowdb.main_text(PAGE, "Fire Boar")
    assert "HP 842" in text and "Knockback 1 damage" in text
    assert "Check your build" not in text and "Your damage on it" not in text


def test_a_page_whose_text_alone_changed_never_affects_the_player(monkeypatch):
    monkeypatch.setattr(recent, "why", lambda kb, row, char, wished: "train")
    entries = [{"version": "2026.10.05.0720", "counts": {"changed": 1, "updated": 2},
                "changed": [{"key": "monster/30", "name": "Fire Boar", "community_added": ["Kumbi Throwing Stars"]}],
                "updated": [{"key": "monster/1", "name": "Blue Snail"}, {"key": "monster/2", "name": "Brown Snail"}]}]
    mine, rest = recent.split(entries, SimpleNamespace(), SimpleNamespace(level=31), ())
    assert [r["name"] for _, _, r in mine] == ["Fire Boar"]
    assert [r["name"] for r in rest[0]["updated"]] == ["Blue Snail", "Brown Snail"] and rest[0]["counts"]["updated"] == 2


def test_guide_cards_are_stored_at_cover_width(tmp_path):
    # the site's 1200x630 social card was kept whole for a 44 px icon and a 480 px hover (6.1 MB for 32 guides)
    import io

    from PIL import Image

    from maplehelper.ui.guides import COVER_W
    buf = io.BytesIO()
    Image.new("RGB", (1200, 630), (200, 120, 40)).save(buf, "PNG")
    assert scrape_meowdb.GUIDE_IMG_W == COVER_W
    assert scrape_meowdb.save_image(buf.getvalue(), tmp_path / "g.png", scrape_meowdb.GUIDE_IMG_W)
    assert Image.open(tmp_path / "g.png").size == (480, 252)
    assert scrape_meowdb.save_image(buf.getvalue(), tmp_path / "m.png")         # other pictures keep their size
    assert Image.open(tmp_path / "m.png").size == (1200, 630)
