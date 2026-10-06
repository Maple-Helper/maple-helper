"""The news' pictures: the nightly scrape keeps NiaMeowDB's cover and the pictures of Nexon's announcement that show
what the item names (tools/scrape_news.py), the KB validator checks them, and the News window shows them
(maplehelper/news.py, ui/newsview.py). No network: the fetches are fakes, the pictures made here with Pillow."""
import io
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget  # noqa: E402

from maplehelper import news  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402

app = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
PAGE = (ROOT / "tests" / "fixtures" / "meowdb" / "news_page.html").read_text(encoding="utf-8")
NOTES = "founders-access-release-notes"         # the fixture's Nexon item (Nexon's article 45621)


def image(fmt: str = "PNG", size=(40, 30), color=(200, 80, 40)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


KIND = {"webp": "WEBP", "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}
MEDIA = "https://g.nexonstatic.com/media"
ARTICLE = {"id": 45621, "name": "Founder's Access Release Notes", "body": f"""
<p><img src="{MEDIA}/aaa1/in-post-banner-1100x225-founders-access-release-notes.jpg" alt="Founder's Access Release Notes"></p>
<p><img src="{MEDIA}/bbb2/jr-balrog-founders-access-release-notes.png" alt="Jr. Balrog Founder's Access Release Notes Global MapleStory Classic World"></p>
<p><img src="{MEDIA}/ccc3/mano-founders-access-release-notes.png" alt="Mano Founder's Access Release Notes Global MapleStory Classic World"></p>
<p><img src="{MEDIA}/ccc3/mano-founders-access-release-notes.png" alt="Mano Founder's Access Release Notes Global MapleStory Classic World"></p>
<p><img src="{MEDIA}/ddd4/zakum-founders-access-release-notes.png" alt="Mushmom Founder's Access Release Notes Global MapleStory Classic World"></p>
<p><img src="{MEDIA}/eee5/pink-bean-founders-access-release-notes.png" alt="Pink Bean Founder's Access Release Notes Global MapleStory Classic World"></p>
<p><img src="https://example.com/shade-founders-access-release-notes.png" alt="Shade Founder's Access Release Notes"></p>
<p><img src="{MEDIA}/fff6/blue-cap-ripped-jeans.png" alt="Blue Cap Ripped Jeans January 1 cash shop update maplestory"></p>
"""}


@pytest.fixture(autouse=True)
def every_item(monkeypatch):
    """The fixture's items reach back to April (the app's cut is off here, as in test_site_news); no pauses."""
    import scrape_news
    monkeypatch.setattr(news, "SINCE", "2000-01-01")
    monkeypatch.setattr(scrape_news, "SINCE", "2000-01-01")
    monkeypatch.setattr(scrape_news, "PAUSE", 0)


class Site:
    """The fake web: the news page, Nexon's article JSON, and a picture for every picture address."""

    def __init__(self, article=ARTICLE, broken=(), big=()):
        self.article, self.broken, self.big = article, set(broken), set(big)
        self.pages, self.pictures = [], []

    def fetch(self, url):
        self.pages.append(url)
        if url.startswith("https://g.nexonstatic.com/maplestory/cms/v1/news/"):
            return json.dumps(self.article) if self.article else None
        return PAGE

    def fetch_bytes(self, url):
        self.pictures.append(url)
        if any(b in url for b in self.broken):
            return None
        if any(b in url for b in self.big):
            return b"\0" * 1_100_000
        return image(KIND[url.rsplit(".", 1)[-1].lower()])


def read(kb: Path) -> dict:
    return {i["id"]: i for i in json.loads((kb / "news.json").read_text(encoding="utf-8"))["items"]}


# ------------------------------------------------------------------ the nightly scrape

def test_the_scrape_keeps_the_covers_and_downloads_each_once(tmp_path):
    import scrape_news
    site = Site()
    assert scrape_news.update(tmp_path, site.fetch, site.fetch_bytes) == 5
    by = read(tmp_path)
    first = by[NOTES]
    assert first["image"] == f"img/news/{NOTES}.webp" and (tmp_path / first["image"]).is_file()
    assert (first["image_w"], first["image_h"]) == (40, 30)
    assert first["image_src"] == "https://meowdb.com/msclassic/news-img/founders-access-release-notes-2026.webp"
    # MeowDB's covers only, each as the format it is (jpg, png and webp as published)
    assert by["second-cot-signups-open"]["image"].endswith(".jpg")
    assert by["cot2-meowdb-exploration-report"]["image"].endswith(".png")
    assert all(u.startswith(("https://meowdb.com/", MEDIA + "/")) for u in site.pictures)
    # the next night downloads nothing and changes nothing (no fake "updated" every night)
    again = Site()
    assert scrape_news.update(tmp_path, again.fetch, again.fetch_bytes) == 0
    assert again.pictures == [] and again.pages == [scrape_news.NEWS_URL]
    # without a picture fetch (an old caller) the pictures there are stay
    assert scrape_news.update(tmp_path, again.fetch) == 0 and read(tmp_path) == by


def test_nexons_pictures_are_the_ones_the_text_names_with_alt_and_file_agreeing(tmp_path):
    import scrape_news
    site = Site()
    scrape_news.update(tmp_path, site.fetch, site.fetch_bytes)
    nx = read(tmp_path)[NOTES]["nexon"]
    assert nx["id"] == 45621 and nx["key"]
    assert nx["banner"]["file"] == f"img/news/{NOTES}/aaa1.jpg"
    # in the text's order ("Mano, Mushmom, Shade, Zombie Mushmom, and Jr. Balrog"), once each; not the "Mushmom" alt
    # on Zakum's picture, not Pink Bean (the text doesn't name it), not a picture from elsewhere, not an outfit
    assert [(p["name"], p["file"]) for p in nx["pictures"]] == [
        ("Mano", f"img/news/{NOTES}/ccc3.png"), ("Jr. Balrog", f"img/news/{NOTES}/bbb2.png")]
    assert all((tmp_path / p["file"]).is_file() for p in nx["pictures"])
    assert sum(1 for u in site.pages if "cms/v1/news/45621" in u) == 1
    assert not any("ddd4" in u or "eee5" in u or "example.com" in u for u in site.pictures)
    # only Nexon's own items ask Nexon (the China item's source is Shengqu's site)
    assert not any("cms/v1/news/" in u and "45621" not in u and "45385" not in u for u in site.pages)


def test_a_name_inside_a_longer_one_is_not_named():
    import scrape_news
    body = (f'<img src="{MEDIA}/a1/megaphone-cash-shop.png" alt="Megaphone Cash Shop Global MapleStory Classic World">'
            f'<img src="{MEDIA}/a2/super-megaphone-cash-shop.png" alt="Super Megaphone Cash Shop Global MapleStory '
            f'Classic World">')
    assert scrape_news.nexon_pictures(body, "Cash Shop", "It has 10 Super Megaphones.")[1] == [
        ("Super Megaphone", f"{MEDIA}/a2/super-megaphone-cash-shop.png")]
    _, both = scrape_news.nexon_pictures(body, "Cash Shop", "A Megaphone costs 350 NX. 10 Super Megaphones.")
    assert [n for n, _ in both] == ["Megaphone", "Super Megaphone"]
    assert scrape_news.nexon_pictures("<img src='x' alt=", "", "x") == (None, [])


def test_a_refused_picture_is_not_asked_again_but_a_failed_one_is(tmp_path):
    import scrape_news
    site = Site(big={"bbb2"}, broken={"ccc3"})
    scrape_news.update(tmp_path, site.fetch, site.fetch_bytes)
    nx = read(tmp_path)[NOTES]["nexon"]
    assert nx["pictures"] == [] and nx["banner"] and nx["key"] == ""      # Mano didn't come: asked again
    assert not list((tmp_path / "img" / "news" / NOTES).glob("bbb2.*"))    # over a megabyte: never saved
    nxt = Site(big={"bbb2"})
    assert scrape_news.update(tmp_path, nxt.fetch, nxt.fetch_bytes) == 1
    nx = read(tmp_path)[NOTES]["nexon"]
    assert [p["name"] for p in nx["pictures"]] == ["Mano"] and nx["key"]
    assert not any("aaa1" in u for u in nxt.pictures)                      # the banner it had stays
    last = Site(big={"bbb2"})
    assert scrape_news.update(tmp_path, last.fetch, last.fetch_bytes) == 0
    assert last.pictures == [] and not any("cms/v1" in u for u in last.pages)


def test_a_site_answer_that_is_no_picture_is_refused(tmp_path):
    import scrape_news
    site = Site(article=None)
    site.fetch_bytes = lambda url: b"<html>not found</html>"
    scrape_news.update(tmp_path, site.fetch, site.fetch_bytes)
    assert not any("image" in i for i in read(tmp_path).values())
    assert not [f for f in (tmp_path / "img").rglob("*") if f.is_file()]
    assert scrape_news.cover_src({"image_url": "https://g.nexonstatic.com/media/x/y.png"}) is None
    assert scrape_news.cover_src({"image_url": "//evil.example/x.webp"}) is None
    assert scrape_news.cover_src({"image_url": "/msclassic/news-img/a.webp"}) == "https://meowdb.com/msclassic/news-img/a.webp"


def test_old_pictures_are_deleted(tmp_path):
    import scrape_news
    site = Site()
    scrape_news.update(tmp_path, site.fetch, site.fetch_bytes)
    items = list(read(tmp_path).values())
    keep = [i for i in items if i["id"] != NOTES]
    assert scrape_news.prune_pictures(tmp_path, keep) == 4                # the cover, the banner, Mano, Jr. Balrog
    assert not (tmp_path / "img" / "news" / NOTES).exists()
    assert all((tmp_path / i["image"]).is_file() for i in keep if i.get("image"))
    assert scrape_news.prune_pictures(tmp_path, keep) == 0


def test_the_validator_checks_the_news_pictures(tmp_path):
    import kb_release
    import scrape_news
    site = Site()
    scrape_news.update(tmp_path, site.fetch, site.fetch_bytes)
    items = list(read(tmp_path).values())
    assert kb_release._news_picture_problems(tmp_path, items) == []
    gone = dict(items[0], image="img/news/missing.webp")
    out = dict(items[0], image="img/news/../../index.json")
    big = tmp_path / "img" / "news" / "big.webp"
    big.write_bytes(b"\0" * 1_100_000)
    for bad in (gone, out, dict(items[0], image="img/news/big.webp"),
                dict(items[0], nexon={"pictures": [{"name": "X", "file": "pages/x.md"}]})):
        assert kb_release._news_picture_problems(tmp_path, [bad]), bad
    (tmp_path / "index.json").write_text("[]", encoding="utf-8")
    (tmp_path / "news.json").write_text(json.dumps({"items": [gone]}), encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="news.json: 1 pictures missing"):
        kb_release.validate(tmp_path, categories=[])


def test_the_kb_zip_carries_the_news_pictures(tmp_path):
    import zipfile

    import kb_release
    import scrape_news
    kb = tmp_path / "kb"
    kb.mkdir()
    site = Site()
    scrape_news.update(kb, site.fetch, site.fetch_bytes)
    kb_release.pack(kb, tmp_path / "out", "2026.10.06.0000")
    names = zipfile.ZipFile(tmp_path / "out" / "kb.zip").namelist()
    assert f"img/news/{NOTES}.webp" in names and f"img/news/{NOTES}/ccc3.png" in names


# ------------------------------------------------------------------ the app

@pytest.fixture
def pictured(kb_copy):
    import scrape_news
    site = Site()
    scrape_news.update(kb_copy, site.fetch, site.fetch_bytes)
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(kb_copy)


def _shown(w) -> list:
    return [x for x in w.findChildren(QWidget) if isinstance(x, (QLabel, QPushButton))]


def _texts(w) -> str:
    return re.sub("[‎‏‪-‮⁦-⁩]", "", " ".join(x.text() for x in _shown(w)))


@pytest.mark.parametrize("lang", ["he", "en"])
def test_an_article_shows_its_picture_and_what_it_names(pictured, lang):
    from maplehelper.ui.newsview import Picture, article
    t = I18n(lang)
    i = next(x for x in news.items(pictured) if x["id"] == NOTES)
    assert news.header(pictured, i).name == "aaa1.jpg"              # Nexon's banner over MeowDB's cover
    page = article(t, i, pictured)
    pics = page.findChildren(Picture)
    assert len(pics) == 1 and pics[0].objectName() == "NewsPicture"
    lay = page.layout()
    at = [lay.itemAt(n).widget() for n in range(lay.count())]
    assert at.index(pics[0]) == 2                                   # under the title (chips, title, picture)
    tiles = [w for w in page.findChildren(QLabel) if w.parent() and w.parent().objectName() == "NewsTile"
             and w.text()]
    assert [re.sub("[⁦-⁩‎‏]", "", w.text()) for w in tiles] == ["Mano", "Jr. Balrog"]
    texts = _texts(page)
    assert t("news_pictured") in texts and t("news_pictures_credit", who="Nexon") in texts
    if lang == "he":
        assert "לבל" not in texts


def test_without_pictures_the_article_is_as_before(kb_copy):
    """No picture fields, a picture file gone, or one Qt can't decode: the same page as without a KB."""
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.newsview import NewsCard, Picture, article
    t = I18n("en")
    kb = KnowledgeBase(kb_copy)
    (kb_copy / "img" / "news").mkdir(parents=True)
    (kb_copy / "img" / "news" / "x.webp").write_bytes(b"RIFF....WEBPVP8 broken")
    base = {"id": "x", "title": "Patch", "date": "2026-10-03", "summary": "Something.", "highlights": ["One."],
            "url": "https://meowdb.com/msclassic/news/x", "source_url": "https://example.com/x", "publisher": "Nexon"}
    plain = article(t, base)
    shape = lambda w: [(type(c).__name__, c.objectName(), c.text()) for c in _shown(w)]  # noqa: E731
    for i in (base, dict(base, image="img/news/gone.webp"), dict(base, image="img/news/x.webp"),
              dict(base, nexon={"banner": {"file": "img/news/x.webp"}, "pictures": [{"name": "A", "file": "img/news/x.webp"}]})):
        page = article(t, i, kb)
        assert not page.findChildren(Picture) and shape(page) == shape(plain), i
        card = NewsCard(t, i, kb=kb)
        assert not card.findChildren(Picture)


def test_a_news_card_has_the_cover_as_a_thumbnail(pictured):
    from maplehelper.ui.newsview import THUMB, NewsCard, Picture
    i = next(x for x in news.items(pictured) if x["id"] == NOTES)
    for lang in ("he", "en"):
        card = NewsCard(I18n(lang), i, kb=pictured)
        thumbs = card.findChildren(Picture)
        assert len(thumbs) == 1 and thumbs[0].objectName() == "NewsThumb"
        assert (thumbs[0].width(), thumbs[0].height()) == THUMB
    card = NewsCard(I18n("en"), i)
    assert not card.findChildren(Picture)                           # no KB given: as before


def test_the_kbs_pictures_are_of_names_written_alone(kb_copy):
    """KB items and monsters the text names as the game writes them; not inside a longer name, not a price's tier,
    not one Nexon's pictures already show."""
    import shutil

    from maplehelper.kb import KnowledgeBase
    for mob in ("100100", "100101", "1210100"):            # the fixture has Red Snail's picture only
        shutil.copy(kb_copy / "img" / "monster" / "130101.png", kb_copy / "img" / "monster" / f"{mob}.png")
    kb = KnowledgeBase(kb_copy)
    i = {"title": "News", "summary": "", "highlights": [
        "Snail and Blue Snail are back. Pig Points are new. The $99 Red Snail tier sells out."], "commentary": ""}
    names = [kb.get(k)["name"] for k in news.entities(kb, i)]
    assert names == ["Snail", "Blue Snail"]
    assert [kb.get(k)["name"] for k in news.entities(kb, i, skip=["blue snail"])] == ["Snail"]
    assert news.entities(kb, dict(i, highlights=["a snail, the pig"])) == []        # the game's case only


def test_qt_reads_webp_here_and_the_frozen_build_keeps_its_plugin():
    """The covers are WebP: Qt reads it with its imageformats plugin (qwebp), which PyInstaller's PySide6 hook ships
    with the app as long as packaging/maplehelper.spec doesn't drop it."""
    from PySide6.QtGui import QImageReader, QPixmap
    assert b"webp" in [bytes(f) for f in QImageReader.supportedImageFormats()]
    pm = QPixmap()
    assert pm.loadFromData(image("WEBP")) and (pm.width(), pm.height()) == (40, 30)
    spec = (ROOT / "packaging" / "maplehelper.spec").read_text(encoding="utf-8")
    drop = re.search(r"DROP = \((.*?)\)\n", spec, re.S).group(1)
    for name in ("qwebp", "qjpeg", "imageformats", "Qt6Gui"):
        assert not any(d.lower() in name.lower() for d in re.findall(r'"([^"]+)"', drop)), name
    assert "imageformats" not in spec.split("excludes=")[1].split("]")[0]
