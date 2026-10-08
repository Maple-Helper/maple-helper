"""Prices: what an NPC pays and charges (the KB's item pages) and what players ask on the Free Market
(NiaMeowDB's player-reported listings, the same public endpoint its Free Market page reads).

item_market() reads more of NiaMeowDB's Free Market section for one item, through the endpoints its item pages call
(meowdb.com/msclassic/item-db/<id>): the usual price over the last 14 days and how many price checks and finished
trades it comes from, the cheapest offer for sale and the best buy offer, the price trend (daily medians over 30
days) and the newest listings. Live community data, not the KB: cached CACHE_SECONDS, at most CACHE_ITEMS items."""
from __future__ import annotations

import json
import re
import statistics
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import sources

FM_URL = "https://meowdb.com/msclassic/api/market-listings/browse"
API = "https://meowdb.com/msclassic/api"
FM_PAGE = "https://meowdb.com/msclassic/free-market"
UA = "Maple Helper (https://github.com/Maple-Helper/maple-helper)"
CACHE_SECONDS = 600
CACHE_ITEMS = 40            # items remembered at once (the oldest lookup goes first)
TREND_DAYS = 30             # the price-history window the trend is read over (the site offers 14, 30, 90)
LISTINGS_SHOWN = 3
_cache: dict[str, tuple[float, dict | None]] = {}
_item_cache: dict[int, tuple[float, "ItemMarket | None"]] = {}
# the price card's and the sell check's threads both use the cache: an insert while the other picked the oldest
# raised "dictionary changed size during iteration" (TL2-10). Held for the cache only, never over a request
_cache_lock = threading.Lock()


@dataclass
class NpcPrices:
    sell_back: int | None                       # what an NPC pays you
    shops: list[tuple[str, str, int]] = field(default_factory=list)   # (NPC, where, price), cheapest first
    unpriced: list[tuple[str, str]] = field(default_factory=list)     # (NPC, where): sells it, no price in the page
    ranks: dict[tuple[str, str], str] = field(default_factory=dict)   # (NPC, where) -> citizen grade its price needs
    # (NPC, where) -> the build the KB labels its price with ("COT2 prices": the second closed test's price, not a
    # confirmed launch price; the release guide: "Do not turn COT2 omissions into launch facts"). Read from the
    # page, so "Launch prices" after launch is that label with no app update.
    labels: dict[tuple[str, str], str] = field(default_factory=dict)

    def test_price(self, shop: tuple) -> bool:
        """A shop (NPC, where, ...) whose price the KB labels with a build."""
        return tuple(shop[:2]) in self.labels

    def source(self, shop: tuple) -> str:
        """The source tag of a shop's price: its build label, else MeowDB's own (sources.py)."""
        return self.labels.get(tuple(shop[:2])) or sources.MEOWDB


def _int(text: str) -> int | None:
    m = re.search(r"[\d,]+", text or "")
    return int(m.group(0).replace(",", "")) if m else None


# the price line under a town shop: "COT2 prices Citizen of Honor +" (that citizen grade and up; the KB's item pages,
# e.g. pages/item/274.md, Max City General Store), or a bare "COT2 prices" (sources.price_label reads the build)


def npc_prices(kb, key: str) -> NpcPrices:
    """Once per item and KB (the Farm tab read ~1,700 item pages a redraw, PERF-05); callers only read it."""
    from .kb import memo
    seen = memo(kb, "_npc_prices")
    if key not in seen:
        seen[key] = _npc_prices(kb, key)
    return seen[key]


def _npc_prices(kb, key: str) -> NpcPrices:
    lines = [ln.strip() for ln in kb.page(key).split("\n---", 2)[-1].splitlines()]
    sell = next((_int(ln) for ln in lines if ln.startswith("NPC Sell-back")), None)
    out = NpcPrices(sell)
    if "Where to buy" in lines:
        i = lines.index("Where to buy") + 1
        # blocks of: "<NPC> <role> [cheapest]" / "<map> · <town>" / "<price>" / "mesos" [/ "COT2 prices [grade +]"]
        # the price is "-" for a few NPCs the page lists without one (pages/item/241.md: Jane, Lith Harbor)
        while i + 3 < len(lines) and lines[i + 3] == "mesos":
            npc = re.sub(r"\s+cheapest$", "", lines[i]).strip()
            where, price = lines[i + 1], _int(lines[i + 2])
            if price is not None:
                out.shops.append((npc, where, price))
            elif npc:
                out.unpriced.append((npc, where))
            nxt = lines[i + 4] if i + 4 < len(lines) else ""
            label = sources.price_label(nxt)
            if label:
                out.labels[(npc, where)] = label
                rank = sources.price_rest(nxt).rstrip("+ ").strip()
                if rank and not rank.endswith("→"):
                    out.ranks[(npc, where)] = rank
            i += 5 if label else 4
    out.shops.sort(key=lambda s: s[2])
    return out


@dataclass
class Market:
    count: int
    median: int | None = None
    low: int | None = None
    high: int | None = None
    latest: float | None = None                 # unix time of the newest report


def summarize(rows: list[dict], name: str) -> Market:
    prices, times = [], []
    for r in rows:
        if not isinstance(r, dict) or str(r.get("itemName") or "").strip().lower() != name.strip().lower():
            continue                            # the search is "contains": keep this exact item
        each = r.get("priceEach") or r.get("price")
        if isinstance(each, (int, float)) and each > 0:
            prices.append(int(each))
        t = r.get("createdAt")
        if isinstance(t, (int, float)):
            times.append(t / 1000 if t > 1e11 else t)
        elif isinstance(t, str):
            try:
                from datetime import datetime
                times.append(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
    if not prices:
        return Market(0)
    return Market(len(prices), int(statistics.median(prices)), min(prices), max(prices), max(times) if times else None)


def free_market(name: str, timeout: float = 10) -> Market | None:
    """Player listings for an item (sell side, last 14 days). None when the site can't be reached."""
    hit = _cache.get(name.lower())
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    url = FM_URL + "?" + urllib.parse.urlencode({"q": name, "sort": "price_low"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    rows = data.get("rows") if isinstance(data, dict) else None
    out = summarize(rows if isinstance(rows, list) else [], name)
    _cache[name.lower()] = (time.time(), out)
    return out


def page_url(name: str) -> str:
    return FM_PAGE + "?" + urllib.parse.urlencode({"q": name})


# ---------------------------------------------------------------- one item's market (the item page's endpoints)

@dataclass
class Listing:
    price: int                  # each
    quantity: int = 1
    side: str = "sell"          # sell | buy
    channel: int | None = None
    room: int | None = None     # Free Market room (0 = the entrance)
    created: float | None = None    # unix seconds


@dataclass
class ItemMarket:
    usual: int | None = None            # the usual price, last `window` days (the site's median)
    checks: int = 0                     # price checks behind it
    trades: int = 0                     # finished trades behind it
    window: int = 14
    cheapest_sell: int | None = None
    best_buy: int | None = None
    for_sale: int = 0
    trend_pct: int | None = None        # first to last daily median of the history, rounded percent
    trend_days: int = 0                 # the days between them
    volume: int = 0                     # prices reported over TREND_DAYS (the history's daily counts)
    listings: list[Listing] = field(default_factory=list)      # newest first, at most LISTINGS_SHOWN

    @property
    def empty(self) -> bool:
        return (self.usual is None and not self.for_sale and not self.volume and not self.listings
                and self.cheapest_sell is None and self.best_buy is None)


def _num(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def _count(v, default: int = 0) -> int:
    """A count from the site's API, or default for anything else ("n/a", a list, null): int() on what the site
    sent raised, and the market card waited forever."""
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else default


def _when(v) -> float | None:
    """A listing's time: epoch milliseconds or seconds, or an ISO / "2026-10-04 12:00:00" UTC string."""
    if isinstance(v, (int, float)) and v > 0:
        return v / 1000 if v > 1e11 else float(v)
    if isinstance(v, str) and v.strip():
        from datetime import datetime, timezone
        try:
            d = datetime.fromisoformat(v.strip().replace(" ", "T").replace("Z", "+00:00"))
        except ValueError:
            return None
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    return None


def trend(points: list[dict]) -> tuple[int | None, int, int]:
    """(percent from the first to the last daily median, days between them, prices reported) from the history's
    points [{day: "2026-10-04", low, median, high, count}], read as the site reads it: under two days, no trend."""
    from datetime import date
    pts = [p for p in points if isinstance(p, dict) and _num(p.get("median")) and isinstance(p.get("day"), str)]
    pts.sort(key=lambda p: p["day"])
    volume = sum(_count(p.get("count")) for p in pts)
    if len(pts) < 2:
        return None, 0, volume
    first, last = pts[0], pts[-1]
    try:
        days = (date.fromisoformat(last["day"][:10]) - date.fromisoformat(first["day"][:10])).days
    except ValueError:
        days = 0
    return round((last["median"] - first["median"]) / first["median"] * 100), days, volume


def listings(rows) -> list[Listing]:
    """The newest LISTINGS_SHOWN listings (both sides), newest first."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        price = _num(r.get("priceEach") or r.get("price"))
        if price is None:
            continue
        out.append(Listing(price, _count(r.get("quantity"), 1) or 1, "buy" if r.get("side") == "buy" else "sell",
                           r.get("channel") if isinstance(r.get("channel"), int) else None,
                           r.get("fmRoom") if isinstance(r.get("fmRoom"), int) else None, _when(r.get("createdAt"))))
    out.sort(key=lambda x: x.created or 0, reverse=True)
    return out[:LISTINGS_SHOWN]


def parse_item_market(summary, history, listed) -> ItemMarket:
    """An ItemMarket from the three answers (item-market-summary, item-price-history, market-listings); any of
    them may be None (that request failed): what the others say still shows."""
    m = ItemMarket()
    if isinstance(summary, dict):
        m.usual, m.cheapest_sell, m.best_buy = (_num(summary.get("usual")), _num(summary.get("cheapestSell")),
                                                _num(summary.get("bestBuy")))
        m.checks, m.trades = _count(summary.get("priceChecks")), _count(summary.get("finishedTrades"))
        m.for_sale, m.window = _count(summary.get("forSale")), _count(summary.get("windowDays"), 14) or 14
    if isinstance(history, dict):
        m.trend_pct, m.trend_days, m.volume = trend(history.get("points") or [])
    if isinstance(listed, dict):
        m.listings = listings(listed.get("listings"))
        brief = listed.get("summary") if isinstance(listed.get("summary"), dict) else {}
        m.cheapest_sell = m.cheapest_sell or _num(brief.get("lowestSell"))
        m.best_buy = m.best_buy or _num(brief.get("highestBuy"))
        m.for_sale = m.for_sale or _count(brief.get("activeCount"))
    return m


def _get(url: str, timeout: float):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:          # noqa: BLE001 - offline, a timeout, an error page: that part is unknown
        return None


def item_market(item_id: int, timeout: float = 8) -> ItemMarket | None:
    """One item's Free Market (clean condition, as its page opens). None when the site can't be reached; cached
    CACHE_SECONDS. Its three requests go one after another, never in parallel (up to 3 x timeout); a failed
    summary stops it, the history and listings are each asked for anyway.
    (The tests stub this entry point out; _lookup is the work.)"""
    return _lookup(item_id, timeout)


def _lookup(item_id: int, timeout: float = 8) -> ItemMarket | None:
    with _cache_lock:
        hit = _item_cache.get(item_id)
    if hit and time.time() - hit[0] < (CACHE_SECONDS if hit[1] is not None else 60):     # offline: retry sooner
        return hit[1]
    q = urllib.parse.urlencode
    summary = _get(f"{API}/item-market-summary?{q({'itemId': item_id, 'condition': 'clean'})}", timeout)
    out = None
    if summary is not None:
        history = _get(f"{API}/item-price-history?{q({'itemId': item_id, 'condition': 'clean', 'days': TREND_DAYS})}",
                       timeout)
        listed = _get(f"{API}/market-listings?{q({'itemId': item_id})}", timeout)
        out = parse_item_market(summary, history, listed)
    with _cache_lock:
        if len(_item_cache) >= CACHE_ITEMS and item_id not in _item_cache:
            _item_cache.pop(min(_item_cache, key=lambda k: _item_cache[k][0]))
        _item_cache[item_id] = (time.time(), out)
    return out


def item_page(item_id: int) -> str:
    """The item's NiaMeowDB page, at its Free Market section."""
    return f"https://meowdb.com/msclassic/item-db/{item_id}#free-market"
