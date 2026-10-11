"""The Cash Shop from OSMS Data Explorer (osms.py): only what is on sale, the game's tabs, at most once an hour."""
import json
import time

import pytest

from maplehelper import osms

DOC = {"categories": [
    {"category": "Coupon", "items": [
        {"id": 5150000, "name": "Mystery Hair Coupon", "category": "Coupon", "sub_category": None, "price": 5500,
         "count": 1, "period": 14, "on_sale": True, "description": "Random hair.", "thumbnail": "images/items/05150000.png"},
        {"id": 5150001, "name": "Old Coupon", "category": "Coupon", "price": 100, "on_sale": False}]},
    {"category": "Equipment", "items": [
        {"id": 1007098, "name": "Kuniragi Hat", "category": "Equipment", "sub_category": "Hat", "price": 1200,
         "period": 10, "on_sale": True, "thumbnail": "../../etc/passwd"},
        {"id": 1802000, "name": "Red Ribbon", "category": "Equipment", "sub_category": "PetEquip", "price": 3200,
         "on_sale": True}]}], "total": 4}


@pytest.fixture
def site(monkeypatch, tmp_path):
    monkeypatch.setattr(osms, "_items", None)
    monkeypatch.setattr(osms, "_loaded", False)
    monkeypatch.setattr(osms, "_checked", 0.0)
    monkeypatch.setattr(osms, "_failed", False)
    monkeypatch.setattr(osms, "_file", lambda: tmp_path / "osms.json")
    state = {"doc": DOC, "etag": '"a"', "calls": [], "down": False}

    def request(url, etag="", timeout=30):
        state["calls"].append((url, etag))
        if state["down"]:
            raise OSError("offline")
        if url.endswith(".png"):
            return 200, b"PNG", ""
        if etag and etag == state["etag"]:
            return 304, b"", etag
        return 200, json.dumps(state["doc"]).encode(), state["etag"]
    monkeypatch.setattr(osms, "_request", request)
    return state


def test_only_whats_on_sale_is_kept(site, tmp_path):
    names = [i.name for i in osms.items()]
    assert names == ["Mystery Hair Coupon", "Kuniragi Hat", "Red Ribbon"]
    kept = json.loads((tmp_path / "osms.json").read_text(encoding="utf-8"))
    assert "Old Coupon" not in json.dumps(kept) and kept["etag"] == '"a"'
    hat = osms.items()[1]
    assert (hat.nx, hat.days, hat.thumbnail) == (1200, 10, "")          # an odd thumbnail path is dropped


def test_at_most_once_an_hour_even_after_a_restart(site, monkeypatch):
    osms.items()
    osms.items()
    assert len(site["calls"]) == 1
    monkeypatch.setattr(osms, "_items", None)
    monkeypatch.setattr(osms, "_loaded", False)
    monkeypatch.setattr(osms, "_checked", 0.0)
    assert len(osms.items()) == 3 and len(site["calls"]) == 1          # the copy on disk, checked minutes ago


def test_an_hour_later_it_asks_with_its_etag(site, monkeypatch):
    osms.items()
    before = osms.version()
    monkeypatch.setattr(osms, "_checked", time.monotonic() - osms.REFRESH_SECONDS)
    osms.items()
    assert site["calls"][-1] == (osms.CASH_URL, '"a"') and osms.version() == before      # 304: unchanged
    site["doc"] = {"categories": [{"category": "Coupon", "items": [DOC["categories"][0]["items"][0]]}]}
    site["etag"] = '"b"'
    monkeypatch.setattr(osms, "_checked", time.monotonic() - osms.REFRESH_SECONDS)
    assert [i.name for i in osms.items()] == ["Mystery Hair Coupon"] and osms.version() == before + 1


def test_offline_keeps_the_copy(site, monkeypatch):
    osms.items()
    site["down"] = True
    monkeypatch.setattr(osms, "_checked", time.monotonic() - osms.REFRESH_SECONDS)
    assert len(osms.items()) == 3


def test_the_games_tabs():
    def at(cat, sub="", name="x", gid=1):
        return osms.place(osms.Item(gid, name, cat, sub, 1))
    assert at("Equipment", "Hat") == ("fashion", "hats") and at("Equipment", "Overall") == ("fashion", "outfits")
    assert at("Equipment", "PetEquip") == ("pets", "pet_equip")
    assert at("Coupon", name="Mystery Hair Coupon") == ("beauty", "hair")
    assert at("Coupon", name="Signature Face Coupon") == ("beauty", "faces")
    assert at("Coupon", name="Signature Hair Color Coupon") == ("beauty", "misc")
    assert at("Pet", gid=5000000) == ("pets", "pets") and at("Pet", gid=5180000) == ("pets", "pet_care")
    assert at("Pet Skill") == ("pets", "pet_care") and at("Megaphone") == ("utility", "social")
    assert at("Weather Effect") == ("utility", "effects") and at("Store Permit") == ("utility", "convenience")
    assert at("Other", name="Founder's Mystery Fashion Crate") == ("special", "crate")


def test_a_picture_is_fetched_once(site):
    item = osms.items()[0]
    path = osms.thumbnail_file(item)
    assert path.read_bytes() == b"PNG" and osms.thumbnail_file(item) == path
    assert sum(1 for u, _ in site["calls"] if u.endswith(".png")) == 1


def test_the_games_words_win_over_the_kbs_in_hebrew(monkeypatch, tmp_path):
    """The KB's closed-test text said "up to 24 items" where the game says 18: its Hebrew is used only when it says
    the same; else the Hebrew of the game's own words; an item OSMS has no words for keeps the KB's Hebrew."""
    import json as _json

    from maplehelper import itemdetails, translations
    from maplehelper.i18n import I18n
    from maplehelper.ui import shoppage

    class KB:
        root = tmp_path
        entities = {"item/1": {"name": "Fall Store Permit", "type": "Cash / Store Permit"},
                    "item/2": {"name": "Kuniragi Hat", "type": "Cash Equip / Cash Hat"},
                    "item/3": {"name": "Megaphone", "type": "Cash / Megaphone"}}

        def get(self, k):
            return self.entities.get(k)

    kb_en = {"item/1": "Open a store for up to 24 items.", "item/2": "A cash hat.", "item/3": "Shout to the channel."}
    monkeypatch.setattr(itemdetails, "details", lambda kb, k: type("D", (), {"description": kb_en[k]})())
    (tmp_path / "he.json").write_text(_json.dumps({"item_desc": {
        "item/1": {"en": kb_en["item/1"], "he": "חנות עד 24 פריטים"},
        "item/2": {"en": kb_en["item/2"], "he": "כובע Cash"},
        "item/3": {"en": kb_en["item/3"], "he": "צעקה לערוץ"}},
        "cash_desc": {"cash/5140003": {"en": "Open a store for up to 18 items.", "he": "חנות עד 18 פריטים"}}},
        ensure_ascii=False), encoding="utf-8")
    translations._cache.clear()
    page = shoppage.ShopPage.__new__(shoppage.ShopPage)
    page.kb, page.t, page._desc = KB(), I18n("he"), {}
    page._by_name = shoppage.kb_names(page.kb)
    import re

    def text(*a):
        return re.sub("[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", page.describe(*a))
    assert "18" in text("Fall Store Permit", "Open a store for up to 18 items.", 5140003)
    assert "כובע Cash" in text("Kuniragi Hat", "", 1007098)                          # no game words: the KB's
    assert "צעקה" in text("Megaphone", "Shout to the channel!", 5070000)             # the same words: the KB's
    # an item the Cash Shop just added: no Hebrew yet, so tonight's translation picks its words up
    monkeypatch.setattr(osms, "items", lambda refresh=True, timeout=30: [
        osms.Item(5140003, "Fall Store Permit", "Store Permit", "", 3000, description="Open a store for up to 18 items."),
        osms.Item(5999999, "New Thing", "Other", "", 100, description="Brand new.")])
    import translate_kb
    assert translate_kb.cash_jobs(KB(), tmp_path) == [{"kind": "cash_desc", "key": "cash/5999999", "en": "Brand new."}]
    translations._cache.clear()
