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
