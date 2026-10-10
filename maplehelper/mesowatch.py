"""Free Market sales from MesoWatch (meso.watch): what players' shops really sold, read from public streams.

MesoWatch watches public Twitch / YouTube streams of the live world and records a sale when a shop's row goes grey or
its stock drops (its sales say how: "found sold out", "stock dropped"). Per item it publishes the usual sold price
(the median), the middle half of the prices (p25-p75), how many sold, the daily medians, and the shops seen listing
it now. NiaMeowDB's Free Market (market.py) is what players type in: asks, buy offers and price checks. The two sit
side by side, both community data; neither replaces the other, and neither replaces an official price.

MesoWatch gave Maple Helper written permission (the owner, 2026-10-10) to read its data files, keep them and use them
in the app and its AI answers. Its terms ask for a visible credit and link wherever a price is shown: every line here
carries "MesoWatch" and the app links to the item's page there (item_url).

Polite by design: one small index file (index.json says when the market was last published) decides whether the
big market file (about 13 MB) is fetched at all, that fetch is conditional (ETag), at most once an hour, and only
when a price is asked for. What it needs is kept on disk (DATA_DIR/mesowatch.json, about 1 MB), so a restart never
downloads again. Seller names are not kept: the app shows where a shop stands and its price, not who runs it.

Only the live world's market (MesoWatch's default, "Classic World"); its closed-test markets (COT1, COT2) are past
tests, not today's prices. Items are matched by the game's own item ID when the KB has it (an item's icon URL,
meowdb.com/msclassic/api/assets/icons/4010001; MesoWatch's ids are "basil:04010001"), else by name: MesoWatch takes its
item names from NiaMeowDB, as the KB does. A name only matches when it is one item on both sides (checked against the
old icon-URL KB: 1,632 items matched, none wrongly; 29 shared names are left out)."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import market

SITE = "https://meso.watch/"
INDEX_URL = SITE + "data/markets/index.json"
MARKET_URL = SITE + "data/market.json"       # the default market's whole file
LIVE_MARKET = "Classic World"                # MesoWatch's id of the live world's market
REFRESH_SECONDS = 3600                       # at most one check an hour (the site publishes about every 12 minutes)
RETRY_SECONDS = 300                          # after a failed check
STALE_SECONDS = 3 * 24 * 3600                # older than this, the numbers are not shown at all
TREND_DAYS = 7                               # the trend is read over the last week's daily medians
TREND_MIN_SOLD = 3                           # a day with fewer sales is too thin to measure a trend from
SHOPS_KEPT = 8                               # as many as MesoWatch publishes per item
TIMEOUT = 30
SLIM_VERSION = 1

log = logging.getLogger(__name__)
_lock = threading.Lock()                     # one check at a time; the others wait and read its result
_snap: Snapshot | None = None
_loaded = False                              # the disk copy was read (once per run)
_checked = 0.0                               # monotonic time of the last check (successful or not)
_failed = False


def _file() -> Path:
    from .store import DATA_DIR
    return DATA_DIR / "mesowatch.json"


# ---------------------------------------------------------------- the data

@dataclass
class Shop:
    location: str
    price: int                  # each (or each set, when the item is priced per set)
    qty: int = 1
    stock: int | None = None    # how many the shop had
    seen: float | None = None   # unix time the stream last showed it


@dataclass
class Sales:
    game_id: int
    name: str
    price: int | None = None    # the usual sold price: the median
    p25: int | None = None
    p75: int | None = None
    sold: int = 0               # sales seen
    low_data: bool = True       # under the site's lowDataBelow sales: a guess, not a price
    ask: int | None = None      # the median price shops ask
    last_sold: float | None = None
    per_set: bool = False       # priced per set (MesoWatch's unitBasis "set"), not per item
    scrolled_only: bool = False  # the price comes from scrolled or high-roll copies only
    trend_pct: int | None = None
    trend_days: int = 0
    shops: list[Shop] = field(default_factory=list)      # listing it now, most recently seen first
    listing_shops: int = 0      # every shop seen listing it

    @property
    def empty(self) -> bool:
        return not self.sold and not self.shops


@dataclass
class Snapshot:
    world: str                  # the market's name for the player ("Windia")
    generated: float            # when MesoWatch published it
    items: dict[int, Sales]
    fetched: float = 0.0        # when we last confirmed it is current
    by_name: dict[str, int] = field(default_factory=dict)   # folded name -> game id, names one item has

    @property
    def stale(self) -> bool:
        return time.time() - self.generated > STALE_SECONDS


def _num(v) -> int | float | None:
    """A positive amount; whole mesos as int, and a price under 1 meso kept as it is (arrows sell at 0.5 each)."""
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
        return None
    return round(v) if v >= 1 else round(v, 2)


def _ts(v) -> float | None:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def game_id_of(mw_id) -> int | None:
    """MesoWatch's "basil:04010001" -> 4010001; its Cash Shop ids ("cash:...") have no game item to match."""
    m = re.fullmatch(r"basil:0*(\d+)", str(mw_id or ""))
    return int(m.group(1)) if m else None


def trend(history: list[dict]) -> tuple[int | None, int]:
    """(percent from the first to the last solid daily median of the last TREND_DAYS days, days between them)."""
    pts = [p for p in history if isinstance(p, dict) and isinstance(p.get("date"), str) and _num(p.get("median"))
           and (p.get("sold") or 0) >= TREND_MIN_SOLD]
    pts.sort(key=lambda p: p["date"])
    pts = pts[-TREND_DAYS:]
    if len(pts) < 2:
        return None, 0
    try:
        days = (date.fromisoformat(pts[-1]["date"][:10]) - date.fromisoformat(pts[0]["date"][:10])).days
    except ValueError:
        return None, 0
    first, last = pts[0]["median"], pts[-1]["median"]
    return round((last - first) / first * 100), days


def slim(doc: dict, world: str = "") -> dict:
    """MesoWatch's market file cut down to what the app uses: no sales list and no seller names."""
    items = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict) or game_id_of(it.get("id")) is None:
            continue
        if not it.get("sold") and not it.get("listings"):
            continue                    # nothing seen: an item without a price or a shop
        shops = []
        for s in (it.get("listings") or [])[:SHOPS_KEPT]:
            if isinstance(s, dict) and _num(s.get("price")):
                shops.append({"location": str(s.get("location") or ""), "price": s.get("price"),
                              "qty": s.get("qty"), "stock": s.get("stock"), "seen": s.get("lastSeen")})
        tr, days = trend(it.get("history") or [])
        items.append({"id": it["id"], "name": it.get("name") or "", "price": it.get("price"), "p25": it.get("p25"),
                      "p75": it.get("p75"), "sold": it.get("sold") or 0, "lowData": bool(it.get("lowData", True)),
                      "ask": it.get("askMedian"), "lastSold": it.get("lastSold"),
                      "set": it.get("unitBasis") == "set", "scrolledOnly": bool(it.get("scrolledOnly")),
                      "trend": tr, "trendDays": days, "shops": shops, "listingShops": it.get("listingShops") or 0})
    return {"version": SLIM_VERSION, "world": world, "market": doc.get("market"),
            "generated_at": doc.get("generated_at"), "items": items}


def parse(data: dict) -> Snapshot | None:
    """A Snapshot from slim()'s shape; None for anything else (an old or broken file)."""
    if not isinstance(data, dict) or data.get("version") != SLIM_VERSION or data.get("market") != LIVE_MARKET:
        return None
    generated = _ts(data.get("generated_at"))
    if generated is None:
        return None
    out: dict[int, Sales] = {}
    for it in data.get("items") or []:
        gid = game_id_of(it.get("id")) if isinstance(it, dict) else None
        if gid is None:
            continue
        shops = [Shop(s["location"], _num(s.get("price")), _num(s.get("qty")) or 1, _num(s.get("stock")),
                      _ts(s.get("seen"))) for s in it.get("shops") or [] if isinstance(s, dict) and _num(s.get("price"))]
        shops.sort(key=lambda s: s.seen or 0, reverse=True)
        out[gid] = Sales(gid, str(it.get("name") or ""), _num(it.get("price")), _num(it.get("p25")), _num(it.get("p75")),
                         int(it.get("sold") or 0), bool(it.get("lowData", True)), _num(it.get("ask")),
                         _ts(it.get("lastSold")), bool(it.get("set")), bool(it.get("scrolledOnly")),
                         it.get("trend") if isinstance(it.get("trend"), int) else None, int(it.get("trendDays") or 0),
                         shops, int(it.get("listingShops") or 0))
    names: dict[str, list[int]] = {}
    for gid, s in out.items():
        names.setdefault(_fold(s.name), []).append(gid)
    return Snapshot(str(data.get("world") or "") or LIVE_MARKET, generated, out,
                    by_name={n: ids[0] for n, ids in names.items() if n and len(ids) == 1})


def _fold(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


# ---------------------------------------------------------------- fetching

def _request(url: str, etag: str = "", timeout: float = TIMEOUT) -> tuple[int, bytes, str]:
    """(status, body, etag); status 304 (with If-None-Match) means unchanged."""
    headers = {"User-Agent": market.UA, "Accept": "application/json", "Accept-Encoding": "identity"}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("ETag") or ""
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, b"", etag
        raise


def _live_entry(index: dict) -> dict | None:
    for m in index.get("markets") or [] if isinstance(index, dict) else []:
        if isinstance(m, dict) and m.get("id") == LIVE_MARKET:
            return m
    return None


def _read_disk() -> tuple[Snapshot | None, dict]:
    try:
        raw = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    snap = parse(raw)
    if snap:
        snap.fetched = float(raw.get("fetched") or 0)
    return snap, raw


def _write_disk(data: dict) -> None:
    path = _file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _check(timeout: float) -> None:
    """Brings the snapshot up to date (caller holds _lock)."""
    global _snap, _failed
    snap, raw = _read_disk() if _snap is None else (_snap, {})
    if snap is not None:
        _snap = snap
    _, body, _ = _request(INDEX_URL, timeout=timeout)
    entry = _live_entry(json.loads(body.decode("utf-8")))
    if entry is None:
        _failed = False
        return                          # no live market published (yet): nothing to show
    world = str(entry.get("label") or LIVE_MARKET)
    updated = _ts(entry.get("updated"))
    if _snap is not None and updated is not None and updated <= _snap.generated:
        _snap.world = world
        _snap.fetched = time.time()
        _failed = False
        return                          # nothing new since our copy: the big file isn't asked for
    if not raw:
        try:
            raw = json.loads(_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
    etag = raw.get("etag", "") if _snap is not None else ""
    status, body, new_etag = _request(MARKET_URL, etag, timeout)
    if status == 304 and _snap is not None:
        _snap.world, _snap.fetched = world, time.time()
        _failed = False
        return
    doc = json.loads(body.decode("utf-8"))
    if not isinstance(doc, dict) or doc.get("market") != LIVE_MARKET or doc.get("preview"):
        _failed = False
        return
    data = slim(doc, world)
    snap = parse(data)
    if snap is None:
        raise ValueError("MesoWatch's market file has an unexpected shape")
    snap.fetched = time.time()
    data.update(etag=new_etag, fetched=snap.fetched)
    try:
        _write_disk(data)
    except OSError:
        log.warning("could not keep MesoWatch's prices on disk", exc_info=True)
    _snap = snap
    _failed = False


def snapshot(refresh: bool = True, timeout: float = TIMEOUT) -> Snapshot | None:
    """The live market's prices: the copy on hand, checked against the site at most once an hour (refresh=False:
    never asks the site, for the chat, which must not wait on a 13 MB download). None: nothing on hand."""
    global _snap, _loaded, _checked, _failed
    with _lock:
        if not _loaded:
            _loaded = True
            if _snap is None:
                _snap, _ = _read_disk()
        wait = RETRY_SECONDS if _failed else REFRESH_SECONDS
        fresh = _snap is not None and time.time() - _snap.fetched < REFRESH_SECONDS
        if refresh and not fresh and (not _checked or time.monotonic() - _checked > wait):
            _checked = time.monotonic()
            try:
                _check(timeout)
            except Exception:          # noqa: BLE001 - offline, an error page, a changed file: keep what we have
                _failed = True
                log.warning("MesoWatch check failed", exc_info=True)
        snap = _snap
    return None if snap is None or snap.stale else snap


def warm() -> None:
    """Checks the site in the background (the chat's next answer has the prices)."""
    threading.Thread(target=snapshot, daemon=True, name="mesowatch").start()


def game_id(kb, key: str) -> int | None:
    """The game's item ID of a KB item, from its icon URL (".../api/assets/icons/4010001") when the KB has one."""
    m = re.search(r"/icons/(\d+)(?:\D|$)", str((kb.get(key) or {}).get("url") or ""))
    return int(m.group(1)) if m else None


_kb_names: dict[int, tuple[object, dict[str, int]]] = {}


def _kb_name_counts(kb) -> dict[str, int]:
    """How many KB items have each folded name (one KB per run; rebuilt when it is swapped)."""
    hit = _kb_names.get(id(kb))
    if hit and hit[0] is kb:
        return hit[1]
    counts: dict[str, int] = {}
    for k, e in (getattr(kb, "entities", None) or {}).items():
        if k.startswith("item/"):
            n = _fold(e.get("name"))
            counts[n] = counts.get(n, 0) + 1
    _kb_names.clear()
    _kb_names[id(kb)] = (kb, counts)
    return counts


def match(snap: Snapshot | None, kb, key: str) -> int | None:
    """The game id MesoWatch knows a KB item by: the KB's own when it has one, else a name both sides give one item."""
    gid = game_id(kb, key)
    if gid or snap is None:
        return gid
    name = _fold((kb.get(key) or {}).get("name"))
    if not name or _kb_name_counts(kb).get(name, 0) > 1:
        return None
    return snap.by_name.get(name)


def for_item(kb, key: str, refresh: bool = True, timeout: float = TIMEOUT) -> tuple[Snapshot | None, Sales | None]:
    """(the market, the item's sales there): the snapshot is None when MesoWatch can't be read, the sales None when
    the item was never seen on it."""
    snap = snapshot(refresh, timeout)
    gid = match(snap, kb, key)
    return snap, (snap.items.get(gid) if snap and gid else None)


def item_url(gid: int | None = None) -> str:
    """The item's page on MesoWatch (the site's own share link: ?item=04010001), or its front page."""
    return f"{SITE}?item={gid:08d}" if gid else SITE


def solid_price(s: Sales | None) -> int | None:
    """The usual sold price when enough sales stand behind it (not low_data, not from scrolled copies only)."""
    return s.price if s and s.price and not s.low_data and not s.scrolled_only else None


# ---------------------------------------------------------------- for the AI (English)

def ai_line(snap: Snapshot, s: Sales, key: str) -> str:
    when = datetime.fromtimestamp(snap.generated, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    unit = " per set" if s.per_set else " each"
    bits = []
    if s.price:
        bits.append(f"usually sells for {s.price:,} mesos{unit}"
                    + (f" (middle half {s.p25:,}-{s.p75:,})" if s.p25 and s.p75 and s.p25 != s.p75 else "")
                    + f", {s.sold:,} sale{'s' if s.sold != 1 else ''} seen")
        if s.low_data:
            bits.append("few sales: a rough guess")
        if s.scrolled_only:
            bits.append("price from scrolled copies only")
    else:
        bits.append("no sales seen yet")
    if s.trend_pct is not None and s.trend_days:
        bits.append(f"trend {s.trend_pct:+d}% over {s.trend_days} days")
    if s.ask:
        bits.append(f"shops ask about {s.ask:,}")
    if s.shops:
        cheapest = min(s.shops, key=lambda x: x.price)
        bits.append(f"cheapest shop seen now {cheapest.price:,} at {cheapest.location or 'the Free Market'}")
    return (f"- {s.name or key} [{key}]: " + "; ".join(bits)
            + f". (MesoWatch, {snap.world}, community: shop sales read from public streams; as of {when})")


def ai_lines(kb, keys: list[str], limit: int = 8) -> list[str]:
    """Free Market sales of the items in the context, from the copy on hand (no network: the chat never waits)."""
    items = [k for k in dict.fromkeys(keys) if k.startswith("item/")][:limit]
    if not items:
        return []
    snap = snapshot(refresh=False)
    if snap is None or snap.fetched and time.time() - snap.fetched > REFRESH_SECONDS:
        warm()                          # the next answer has fresh prices
    if snap is None:
        return []
    lines = []
    for k in items:
        gid = match(snap, kb, k)
        s = snap.items.get(gid) if gid else None
        if s and not s.empty:
            lines.append(ai_line(snap, s, k))
    if not lines:
        return []
    return ["Free Market sales (MesoWatch, meso.watch: completed player-shop sales read from public streams of the "
            f"{snap.world} world; community data, not official; name it \"(MesoWatch)\" when you use it):"] + lines

