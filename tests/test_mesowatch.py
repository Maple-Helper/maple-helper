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
            {"id": "cash:01007145", "name": "Black Cat Beanie", "category": "Cash Shop", "price": 229000.0,
             "p25": 99999.0, "p75": 265000.0, "sold": 19, "lowData": False, "askMedian": 299999.0, "asks": 111,
             "listingShops": 62, "cash": {"nx": None, "onSale": False, "crateOdds": None},
             "history": [{"date": "2026-10-08", "median": 74999.0, "sold": 5},
                         {"date": "2026-10-09", "median": 199999.5, "sold": 6},
                         {"date": "2026-10-10", "median": 299999.0, "sold": 7},
                         {"date": "2026-10-11", "median": 250000.0, "sold": 1}],
             "listings": [{"seller": "Tagged", "location": None, "price": 399999.0, "qty": 1,
                           "lastSeen": "2026-10-11T06:33:26Z"}]},
        ],
        "sales": [   # newest first, as MesoWatch publishes them
            {"id": "L-4", "itemId": "cash:01007145", "price": 250000.0, "qty": 1, "seller": "Space Ghost",
             "location": None, "time": "2026-10-11T00:13:38Z"},
            {"id": "L-3", "itemId": "cash:01007145", "price": 349999.0, "qty": 1, "seller": "ShadowBlade",
             "location": "Free Market<2> <2>", "time": "2026-10-10T23:07:42Z"},
            {"id": "L-2", "itemId": "cash:01007145", "price": 299999.0, "qty": 1, "seller": "SimmeBowjobs",
             "location": None, "time": "2026-10-10T19:23:34Z"},
            {"id": "L-1", "itemId": "basil:04010001", "price": 399, "qty": 2, "seller": "Thorfein",
             "location": "The Rain-Forest East of Henesys", "time": "2026-10-10T07:29:00Z"}],
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
        if url == mesowatch.FACES_URL and not state["down"]:
            return 200, b'{"faces": {}}', ""          # the sellers' pictures: not what these tests count
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


def test_the_slim_copy_keeps_prices_shops_and_their_owners_but_not_the_whole_sales_list():
    data = mesowatch.slim(market_doc(), "Windia")
    assert [i["id"] for i in data["items"]] == ["basil:04010001", "basil:02060000", "basil:01302008",
                                                "cash:01007145"]
    text = json.dumps(data)
    assert "sales" not in data
    # the shop's owner, as MesoWatch shows it: how a player finds the shop (the owner, 2026-10-11)
    assert data["items"][0]["shops"][0]["seller"] == "Thorfein" and data["items"][3]["recent"][0]["seller"] == "Space Ghost"
    assert data["items"][3]["category"] == "Cash Shop" and len(data["items"][3]["history"]) == 4
    assert "Thorfein" in text
    assert data["items"][3]["recent"][1]["location"] == "Free Market 2"      # the room's sign, read twice
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
    assert mesowatch.game_id_of("basil:04010001") == 4010001 and mesowatch.game_id_of("cash:01007145") == 1007145
    assert mesowatch.game_id_of("other:1") is None
    assert mesowatch.game_id(KB, "item/414") == 4010001
    assert mesowatch.game_id(KB, "item/9") is None and mesowatch.game_id(KB, "item/0") is None
    assert mesowatch.item_url(4010001) == "https://meso.watch/?item=04010001"
    assert mesowatch.item_url(None) == "https://meso.watch/"
    assert mesowatch.item_url("basil:04010001") == "https://meso.watch/?item=04010001"
    assert mesowatch.item_url("cash:01007145") == "https://meso.watch/?item=cash:01007145"


def test_first_lookup_downloads_and_keeps_a_copy(site, tmp_path):
    snap, ore = mesowatch.for_item(KB, "item/414")
    assert snap.world == "Windia" and ore.price == 375
    assert [u for u, _ in site["calls"]] == [mesowatch.INDEX_URL, mesowatch.MARKET_URL]
    kept = json.loads((tmp_path / "mesowatch.json").read_text(encoding="utf-8"))
    assert kept["etag"] == '"v1"' and "Thorfein" in json.dumps(kept)


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


def test_a_cash_shop_item_has_its_sales_listings_and_shop_status():
    """Black Cat Beanie as MesoWatch's card shows it: 229K typical, 111 listings, 19 sales over 4 days, last sale
    250K, last 3 average 300K, not on sale in the Cash Shop."""
    hat = mesowatch.parse(mesowatch.slim(market_doc(), "Windia")).items[1007145]
    assert (hat.price, hat.sold, hat.days, hat.asks, hat.listing_shops) == (229000, 19, 4, 111, 62)
    assert (hat.last.price, hat.last3, hat.cash, hat.cash_on_sale, hat.nx) == (250000, 299999, True, False, None)
    assert [r.price for r in hat.recent] == [250000, 349999, 299999] and hat.mw_id == "cash:01007145"


def test_the_ai_line_carries_the_last_sales_and_the_cash_shop(site):
    kb = FakeKB({"item/3198": "https://meowdb.com/msclassic/item-db/3198"}, {"item/3198": "Black Cat Beanie"})
    mesowatch.snapshot()
    (line,) = mesowatch.ai_lines(kb, ["item/3198"])[1:]
    assert "last sale 250,000" in line and "last 3 sales average 299,999" in line and "19 sales over 4 days" in line
    assert "111 listings in 62 shops" in line and "not on sale in the Cash Shop now" in line


def test_the_market_file_comes_compressed(monkeypatch):
    """14 MB of JSON comes down as about 1.5 MB: asked for gzip, unpacked here."""
    import gzip

    class Reply:
        status = 200
        headers = {"Content-Encoding": "gzip", "ETag": 'W/"v2"'}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return gzip.compress(b'{"ok": 1}')
    asked = []
    monkeypatch.setattr(mesowatch.urllib.request, "urlopen",
                        lambda req, timeout=0: asked.append(req.get_header("Accept-encoding")) or Reply())
    assert mesowatch._http(mesowatch.MARKET_URL) == (200, b'{"ok": 1}', 'W/"v2"') and asked == ["gzip"]


def test_a_check_is_due_after_15_minutes_even_when_the_timer_is_early(site):
    mesowatch.snapshot()
    mesowatch._snap.fetched = time.time() - (mesowatch.REFRESH_SECONDS - 30)
    mesowatch._checked = time.monotonic() - (mesowatch.REFRESH_SECONDS - 30)
    site["calls"].clear()
    mesowatch.snapshot()
    assert [u for u, _ in site["calls"]] == [mesowatch.INDEX_URL]


def test_a_newer_market_moves_the_version_and_brings_the_sellers_pictures(site, tmp_path, monkeypatch):
    real = site and mesowatch._request
    pictures = []

    def request(url, etag="", timeout=30):
        if url == mesowatch.FACES_URL:
            return 200, json.dumps({"faces": {"Thorfein": "abc123.webp", "bad": "../x.exe"}}).encode(), ""
        if url.startswith(mesowatch.FACE_BASE):
            pictures.append(url)
            return 200, b"RIFF-webp", ""
        return real(url, etag, timeout)
    monkeypatch.setattr(mesowatch, "_request", request)
    before = mesowatch.version()
    snap = mesowatch.snapshot()
    assert mesowatch.version() == before + 1
    assert snap.faces == {"Thorfein": "abc123.webp"}                   # a file name only: nothing else is fetched
    path = mesowatch.face_file(snap, "Thorfein")
    assert path.read_bytes() == b"RIFF-webp" and mesowatch.face_file(snap, "Thorfein") == path
    assert len(pictures) == 1                                           # kept: asked once
    assert mesowatch.face_file(snap, "Nobody") is None and mesowatch.face_file(snap, "bad") is None
    hat = snap.items[1007145]
    assert hat.category == "Cash Shop" and hat.first_day == "2026-10-08"


def test_the_index_a_second_after_the_file_is_the_same_publish(site, monkeypatch):
    """MesoWatch's index stamps a publish a second or so after the file's own generated_at (09:28:23 / 09:28:22,
    seen live): that is no newer market, so the market file isn't asked for again."""
    mesowatch.snapshot()
    monkeypatch.setattr(mesowatch, "_checked", 0.0)
    mesowatch._snap.fetched = 0
    site["index"] = index_doc("2026-10-10T07:47:37+00:00")          # NOW + 1 second
    site["calls"].clear()
    mesowatch.snapshot()
    assert [u for u, _ in site["calls"]] == [mesowatch.INDEX_URL]
