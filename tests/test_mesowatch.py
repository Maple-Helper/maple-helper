"""MesoWatch's Free Market sales (mesowatch.py): the slim copy, the polite refresh, the KB match and the AI lines."""
import json
import time

import pytest

from maplehelper import mesowatch, sellkeep, sources

NOW = "2026-10-10T07:47:36+00:00"


def market_doc(**over):
    doc = {
        "notice": "Data (c) MesoWatch", "version": 1, "market": "Classic World", "preview": False, "generated_at": NOW,
        "items": [
            {"id": "basil:04010001", "name": "Iron Ore", "price": 375.0, "p25": 299.0, "p75": 499.0, "sold": 2243,
             "lowData": False, "askMedian": 500.0, "lastSold": "2026-10-10T07:29:44Z", "unitBasis": "item",
             "scrolledOnly": False, "listingShops": 1170,
             "history": [{"date": "2026-10-07", "median": 499.0, "sold": 713},
                         {"date": "2026-10-08", "median": 450.0, "sold": 2},          # too thin for the trend
                         {"date": "2026-10-10", "median": 389.0, "sold": 500}],
             "listings": [{"seller": "Thorfein", "location": "The Rain-Forest East of Henesys", "price": 399.0,
                           "qty": 1, "stock": 8, "lastSeen": "2026-10-10T07:29:11Z", "up": True}]},
            {"id": "basil:02060000", "name": "Arrow for Bow", "price": 0.76, "p25": 0.54, "p75": 1.0, "sold": 1332,
             "lowData": False, "unitBasis": "item", "history": [], "listings": []},
            {"id": "basil:01302008", "name": "Gladius", "price": 50000.0, "sold": 2, "lowData": True,
             "unitBasis": "item", "history": [], "listings": []},
            {"id": "basil:01000000", "name": "Never Seen", "price": None, "sold": 0, "history": [], "listings": []},
            {"id": "cash:5000", "name": "Pet Food", "price": 1000.0, "sold": 9, "history": [], "listings": []},
        ],
        "sales": [{"id": "L-1", "itemId": "basil:04010001", "price": 399, "seller": "Thorfein"}],
    }
    doc.update(over)
    return doc


def index_doc(updated=NOW):
    return {"version": 1, "default": "Classic World", "markets": [
        {"id": "Classic World", "label": "Windia", "updated": updated},
        {"id": "COT2", "label": "COT #2", "updated": "2026-10-06T03:04:50+00:00"}]}


class FakeKB:
    def __init__(self, urls, names=None):
        self.urls, self.names = urls, names or {}
        self.entities = {k: {"name": self.names.get(k, k)} for k in {**urls, **self.names}}

    def get(self, key):
        return {"name": self.names.get(key, key), "url": self.urls.get(key, "")} if key in self.entities else None


KB = FakeKB({"item/414": "https://meowdb.com/msclassic/api/assets/icons/4010001",
             "item/209": "https://meowdb.com/msclassic/api/assets/icons/2060000",
             "item/541": "https://meowdb.com/msclassic/api/assets/icons/1302008",
             "item/9": "https://meowdb.com/msclassic/item/9"})


@pytest.fixture
def site(monkeypatch, tmp_path):
    """A fake meso.watch: the module starts empty, keeps its copy in tmp_path, and every request is recorded."""
    monkeypatch.setattr(mesowatch, "_snap", None)
    monkeypatch.setattr(mesowatch, "_loaded", False)
    monkeypatch.setattr(mesowatch, "_checked", 0.0)
    monkeypatch.setattr(mesowatch, "_failed", False)
    monkeypatch.setattr(mesowatch, "_file", lambda: tmp_path / "mesowatch.json")
    state = {"index": index_doc(), "market": market_doc(), "etag": '"v1"', "calls": [], "down": False}

    def request(url, etag="", timeout=30):
        state["calls"].append((url, etag))
        if state["down"]:
            raise OSError("offline")
        if url == mesowatch.INDEX_URL:
            return 200, json.dumps(state["index"]).encode(), ""
        if etag and etag == state["etag"]:
            return 304, b"", etag
        return 200, json.dumps(state["market"]).encode(), state["etag"]
    monkeypatch.setattr(mesowatch, "_request", request)
    return state


def test_the_slim_copy_keeps_prices_and_shops_but_no_names_or_sales():
    data = mesowatch.slim(market_doc(), "Windia")
    assert [i["id"] for i in data["items"]] == ["basil:04010001", "basil:02060000", "basil:01302008"]
    assert "Thorfein" not in json.dumps(data) and "sales" not in data       # no seller names, no sales list
    assert data["items"][0]["shops"][0]["location"] == "The Rain-Forest East of Henesys"


def test_parse_reads_the_market_by_game_id():
    snap = mesowatch.parse(mesowatch.slim(market_doc(), "Windia"))
    ore = snap.items[4010001]
    assert (snap.world, ore.price, ore.p25, ore.p75, ore.sold, ore.low_data, ore.ask) == \
        ("Windia", 375, 299, 499, 2243, False, 500)
    assert (ore.trend_pct, ore.trend_days) == (-22, 3)              # 499 -> 389, the thin day skipped
    assert snap.items[2060000].price == 0.76                        # under one meso each: kept as it is
    assert snap.items[1302008].low_data and mesowatch.solid_price(snap.items[1302008]) is None
    assert mesowatch.solid_price(ore) == 375
    assert mesowatch.parse({"version": 1, "market": "COT2", "generated_at": NOW, "items": []}) is None


def test_ids_match_the_kb_by_its_icon_url():
    assert mesowatch.game_id_of("basil:04010001") == 4010001 and mesowatch.game_id_of("cash:5000") is None
    assert mesowatch.game_id(KB, "item/414") == 4010001
    assert mesowatch.game_id(KB, "item/9") is None and mesowatch.game_id(KB, "item/0") is None
    assert mesowatch.item_url(4010001) == "https://meso.watch/?item=04010001"
    assert mesowatch.item_url(None) == "https://meso.watch/"


def test_first_lookup_downloads_and_keeps_a_copy(site, tmp_path):
    snap, ore = mesowatch.for_item(KB, "item/414")
    assert snap.world == "Windia" and ore.price == 375
    assert [u for u, _ in site["calls"]] == [mesowatch.INDEX_URL, mesowatch.MARKET_URL]
    kept = json.loads((tmp_path / "mesowatch.json").read_text(encoding="utf-8"))
    assert kept["etag"] == '"v1"' and "Thorfein" not in json.dumps(kept)


def test_within_the_hour_nothing_is_asked_again(site):
    mesowatch.snapshot()
    mesowatch.snapshot()
    mesowatch.for_item(KB, "item/414")
    assert len(site["calls"]) == 2


def test_a_restart_reads_the_disk_and_skips_the_big_file_when_nothing_is_new(site, monkeypatch):
    mesowatch.snapshot()
    monkeypatch.setattr(mesowatch, "_snap", None)
    monkeypatch.setattr(mesowatch, "_loaded", False)
    monkeypatch.setattr(mesowatch, "_checked", 0.0)
    site["calls"].clear()
    data = json.loads(mesowatch._file().read_text(encoding="utf-8"))
    data["fetched"] = time.time() - 2 * mesowatch.REFRESH_SECONDS        # an old check: it asks the index
    mesowatch._file().write_text(json.dumps(data), encoding="utf-8")
    assert mesowatch.snapshot().items[4010001].price == 375
    assert [u for u, _ in site["calls"]] == [mesowatch.INDEX_URL]          # not newer: the 13 MB file is not asked


def test_a_newer_market_is_fetched_with_its_etag(site, monkeypatch):
    mesowatch.snapshot()
    monkeypatch.setattr(mesowatch, "_checked", 0.0)
    mesowatch._snap.fetched = 0
    site["index"] = index_doc("2026-10-10T08:00:00+00:00")
    site["calls"].clear()
    assert mesowatch.snapshot() is not None
    assert site["calls"] == [(mesowatch.INDEX_URL, ""), (mesowatch.MARKET_URL, '"v1"')]    # 304: kept as it was


def test_offline_keeps_the_copy_and_retries_later(site, monkeypatch):
    mesowatch.snapshot()
    monkeypatch.setattr(mesowatch, "_checked", 0.0)
    mesowatch._snap.fetched = 0
    site["down"] = True
    assert mesowatch.snapshot().items[4010001].price == 375                 # what we had
    n = len(site["calls"])
    mesowatch.snapshot()
    assert len(site["calls"]) == n                                          # no retry before RETRY_SECONDS


def test_nothing_on_hand_and_offline_is_none(site):
    site["down"] = True
    assert mesowatch.snapshot() is None and mesowatch.for_item(KB, "item/414") == (None, None)


def test_an_old_market_is_not_shown(site):
    site["market"] = market_doc(generated_at="2026-09-01T00:00:00+00:00")
    site["index"] = index_doc("2026-09-01T00:00:00+00:00")
    assert mesowatch.snapshot() is None


def test_the_chat_reads_only_the_copy_on_hand(site, monkeypatch):
    started = []
    monkeypatch.setattr(mesowatch, "warm", lambda: started.append(1))
    assert mesowatch.ai_lines(KB, ["item/414"]) == [] and started == [1]     # nothing yet: fetched for next time
    assert site["calls"] == []
    mesowatch.snapshot()
    lines = mesowatch.ai_lines(KB, ["monster/1", "item/414", "item/541", "item/414"])
    assert lines[0].startswith("Free Market sales (MesoWatch") and len(lines) == 3
    assert "usually sells for 375 mesos each (middle half 299-499), 2,243 sales seen" in lines[1]
    assert "[item/414]" in lines[1] and "MesoWatch, Windia, community" in lines[1]
    assert "few sales: a rough guess" in lines[2]


def test_sell_or_keep_uses_sold_prices_only_without_a_report():
    v = [sellkeep.Verdict("sell", "item/1", "A", price=10), sellkeep.Verdict("sell", "item/2", "B", price=10),
         sellkeep.Verdict("sell", "item/3", "C", price=10)]
    out = {x.key: x for x in sellkeep.with_market(v, {"item/1": 900}, {"item/1": 5000, "item/2": 700})}
    assert (out["item/1"].fm, out["item/1"].fm_src) == (900, "")             # NiaMeowDB's report stays first
    assert (out["item/2"].kind, out["item/2"].fm, out["item/2"].fm_src) == ("fm", 700, sources.MESOWATCH)
    assert out["item/3"].kind == "sell"


def test_the_prompt_carries_the_sales_of_a_named_item(kb, site, monkeypatch):
    from maplehelper import brain
    monkeypatch.setattr(mesowatch, "game_id", lambda kb_, key: 4010001 if key == "item/2000000" else None)
    mesowatch.snapshot()
    p = brain.build_prompt("how much is a Red Potion?", None, None, kb, has_screenshot=False)
    assert "Free Market sales (MesoWatch" in p and "usually sells for 375 mesos" in p


def test_a_kb_without_game_ids_matches_by_a_name_both_sides_give_one_item(site):
    """The published KB's item URLs are the item's page (meowdb.com/msclassic/item-db/414), with no game id."""
    page = "https://meowdb.com/msclassic/item-db/"
    kb = FakeKB({"item/414": page + "414", "item/541": page + "541", "item/1": page + "1", "item/2": page + "2"},
                {"item/414": "Iron Ore", "item/541": "gladius", "item/1": "Arrow for Bow", "item/2": "Arrow for Bow"})
    snap, ore = mesowatch.for_item(kb, "item/414")
    assert ore.game_id == 4010001 and mesowatch.match(snap, kb, "item/541") == 1302008      # case and spaces folded
    assert mesowatch.match(snap, kb, "item/1") is None                    # two KB items by that name: no guess
    assert [x.split(" [")[0] for x in mesowatch.ai_lines(kb, ["item/414"])[1:]] == ["- Iron Ore"]
