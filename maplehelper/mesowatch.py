"""Free Market sales from MesoWatch (meso.watch): what players' shops really sold, read from public streams.

MesoWatch watches public Twitch / YouTube streams of the live world and records a sale when a shop's row goes grey or
its stock drops (its sales say how: "found sold out", "stock dropped"). Per item it publishes the usual sold price
(the median), the middle half of the prices (p25-p75), how many sold, the daily medians, and the shops seen listing
it now. NiaMeowDB's Free Market (market.py) is what players type in: asks, buy offers and price checks. The two sit
side by side, both community data; neither replaces the other, and neither replaces an official price.

MesoWatch gave Maple Helper written permission (the owner, 2026-10-10) to read its data files, keep them and use them
in the app and its AI answers. Its terms ask for a visible credit and link wherever a price is shown: every line here
carries "MesoWatch" and the app links to the item's page there (item_url).

Close to the site, and light on it: an open app checks every 15 minutes (the owner: no gap between us and
MesoWatch, which publishes about every 12 minutes; app.py's timer, and a price looked up after that long). One small
index file (index.json says when the market was last published) decides whether the market file is fetched at all;
that fetch is conditional (ETag) and gzip-compressed (14 MB of JSON comes down as about 1.5 MB). What the app needs is
kept on disk (DATA_DIR/mesowatch.json, a few MB), so a restart doesn't download again. A shop's owner is kept, as
MesoWatch shows it: the character name is how a player finds that shop in the Free Market (the owner, 2026-10-11).

Only the live world's market (MesoWatch's default, "Classic World"); its closed-test markets (COT1, COT2) are past
tests, not today's prices. Items are matched by the game's own item ID when the KB has it (an item's icon URL,
meowdb.com/msclassic/api/assets/icons/4010001; MesoWatch's ids are "basil:04010001"), else by name: MesoWatch takes its
item names from NiaMeowDB, as the KB does. A name only matches when it is one item on both sides (checked against the
old icon-URL KB: 1,632 items matched, none wrongly; 29 shared names are left out)."""
from __future__ import annotations

import gzip
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import market

SITE = "https://meso.watch/"
INDEX_URL = SITE + "data/markets/index.json"
MARKET_URL = SITE + "data/market.json"       # the default market's whole file
LIVE_MARKET = "Classic World"                # MesoWatch's id of the live world's market
# the shop owners' character pictures: {seller: "afcd2541d8f9.webp"} (45 KB), each picture under sellers/<market>/
FACES_URL = SITE + "data/shopmap/" + urllib.parse.quote(LIVE_MARKET) + ".json"
FACE_BASE = SITE + "data/shopmap/sellers/" + urllib.parse.quote(LIVE_MARKET) + "/"
REFRESH_SECONDS = 15 * 60                    # a check every 15 minutes (the site publishes about every 12)
RETRY_SECONDS = 300                          # after a failed check
SAME_PUBLISH_SECONDS = 60                    # the index's time and the file's own differ by a second or so
STALE_SECONDS = 3 * 24 * 3600                # older than this, the numbers are not shown at all
TREND_DAYS = 7                               # the trend is read over the last week's daily medians
TREND_MIN_SOLD = 3                           # a day with fewer sales is too thin to measure a trend from
SHOPS_KEPT = 8                               # as many as MesoWatch publishes per item
RECENT_KEPT = 10                             # an item's newest sales kept (the details window's list)
HISTORY_KEPT = 30                            # daily medians kept (its over-time chart)
TIMEOUT = 30
SLIM_VERSION = 6                             # 2: recent sales, listings, Cash Shop items; 3: sellers, history; 4: category;
                                             # 6: crate odds
CATEGORIES = ("Equips", "Weapons", "Scrolls", "Use", "Etc", "Stars", "Cash Shop")   # MesoWatch's tabs, in its order

log = logging.getLogger(__name__)
_lock = threading.Lock()                     # one check at a time; the others wait and read its result
_snap: Snapshot | None = None
_loaded = False                              # the disk copy was read (once per run)
_checked = 0.0                               # monotonic time of the last check (successful or not)
_failed = False
_version = 0                                 # goes up each time a newer market comes in (the Free Market page redraws)


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
    seller: str = ""            # the shop owner's character name, as the stream showed it


@dataclass
class Sale:
    price: int                  # each
    qty: int = 1
    location: str = ""
    time: float | None = None
    seller: str = ""


@dataclass
class Day:
    date: str                   # "2026-10-10"
    median: int | float
    low: int | float | None = None
    high: int | float | None = None
    sold: int = 0


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
    mw_id: str = ""             # MesoWatch's own id ("basil:04010001", "cash:01007145"), for its item link
    asks: int = 0               # listings seen (MesoWatch's "111 listings")
    days: int = 0               # days with sales seen (its "19 sales · 4 days")
    recent: list[Sale] = field(default_factory=list)     # the newest sales, newest first
    last3: int | None = None    # the average of the last 3 sales (its "last 3 avg")
    cash: bool = False          # a Cash Shop item
    cash_on_sale: bool | None = None     # in the Cash Shop now (None: MesoWatch doesn't know)
    nx: int | None = None       # its Cash Shop price
    history: list[Day] = field(default_factory=list)      # daily medians, oldest first
    category: str = ""          # MesoWatch's tab: "Equips", "Weapons", "Scrolls", "Use", "Etc", "Stars", "Cash Shop"
    crate_odds: float | None = None     # its chance in the Founder's Mystery Fashion Crate, percent (Nexon's rates)

    @property
    def first_day(self) -> str:
        """The first day MesoWatch saw it sell (within the history it publishes): "2026-10-10", or ""."""
        return self.history[0].date if self.history else ""

    @property
    def empty(self) -> bool:
        return not self.sold and not self.shops

    @property
    def last(self) -> Sale | None:
        return self.recent[0] if self.recent else None


@dataclass
class Snapshot:
    world: str                  # the market's name for the player ("Windia")
    generated: float            # when MesoWatch published it
    items: dict[int, Sales]
    fetched: float = 0.0        # when we last confirmed it is current
    by_name: dict[str, int] = field(default_factory=dict)   # folded name -> game id, names one item has
    faces: dict[str, str] = field(default_factory=dict)     # seller -> their character picture's file name

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
    """MesoWatch's "basil:04010001" -> 4010001; a Cash Shop item's "cash:01007145" is its game id too (1007145)."""
    m = re.fullmatch(r"(?:basil|cash):0*(\d+)", str(mw_id or ""))
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


def _place(v) -> str:
    """A shop's map as MesoWatch read it: "Free Market<2> <2>" (the room's sign, read twice) is "Free Market 2",
    as its own page says."""
    text = " ".join(str(v or "").split())
    m = re.fullmatch(r"Free Market\s*<(\d+)>(?:\s*<\1>)?", text)
    return f"Free Market {m.group(1)}" if m else text


def slim(doc: dict, world: str = "", faces: dict | None = None) -> dict:
    """MesoWatch's market file cut down to what the app uses: no seller names, and of the sales list only each
    item's newest RECENT_KEPT (price, amount, where, when)."""
    recent: dict[str, list[dict]] = {}
    for sale in doc.get("sales") or []:       # newest first
        if not isinstance(sale, dict) or not _num(sale.get("price")):
            continue
        got = recent.setdefault(str(sale.get("itemId")), [])
        if len(got) < RECENT_KEPT:
            got.append({"price": sale.get("price"), "qty": sale.get("qty"), "location": _place(sale.get("location")),
                        "time": sale.get("time"), "seller": str(sale.get("seller") or "")})
    items = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict) or game_id_of(it.get("id")) is None:
            continue
        cash = it.get("cash") if isinstance(it.get("cash"), dict) else None
        odds = cash.get("crateOdds") if cash else None
        if not it.get("sold") and not it.get("listings") and not isinstance(odds, (int, float)):
            continue                    # nothing seen and not in the crate: no price, no shop
        shops = []
        for s in (it.get("listings") or [])[:SHOPS_KEPT]:
            if isinstance(s, dict) and _num(s.get("price")):
                shops.append({"location": _place(s.get("location")), "price": s.get("price"),
                              "qty": s.get("qty"), "stock": s.get("stock"), "seen": s.get("lastSeen"),
                              "seller": str(s.get("seller") or "")})
        tr, days = trend(it.get("history") or [])
        items.append({"id": it["id"], "name": it.get("name") or "", "category": str(it.get("category") or ""),
                      "crate": odds if isinstance(odds, (int, float)) else None,
                      "price": it.get("price"), "p25": it.get("p25"),
                      "p75": it.get("p75"), "sold": it.get("sold") or 0, "lowData": bool(it.get("lowData", True)),
                      "ask": it.get("askMedian"), "asks": it.get("asks") or 0, "lastSold": it.get("lastSold"),
                      "set": it.get("unitBasis") == "set", "scrolledOnly": bool(it.get("scrolledOnly")),
                      "trend": tr, "trendDays": days, "shops": shops, "listingShops": it.get("listingShops") or 0,
                      "days": sum(1 for p in it.get("history") or [] if isinstance(p, dict) and p.get("sold")),
                      "recent": recent.get(str(it["id"]), []),
                      "history": [{"date": p["date"], "median": p.get("median"), "low": p.get("low"),
                                   "high": p.get("high"), "sold": p.get("sold") or 0}
                                  for p in (it.get("history") or [])[-HISTORY_KEPT:]
                                  if isinstance(p, dict) and isinstance(p.get("date"), str) and _num(p.get("median"))],
                      "cash": {"onSale": cash.get("onSale"), "nx": cash.get("nx")} if cash else None})
    faces = {str(k): str(v) for k, v in (faces or {}).items()
             if isinstance(v, str) and re.fullmatch(r"[\w-]+\.(?:webp|png|jpg)", v)}
    return {"version": SLIM_VERSION, "world": world, "market": doc.get("market"),
            "generated_at": doc.get("generated_at"), "items": items, "faces": faces}


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
        if gid is None or (gid in out and str(it["id"]).startswith("cash:")):
            continue                    # the same number on both sides: the game item's row wins
        shops = [Shop(s["location"], _num(s.get("price")), _num(s.get("qty")) or 1, _num(s.get("stock")),
                      _ts(s.get("seen")), str(s.get("seller") or ""))
                 for s in it.get("shops") or [] if isinstance(s, dict) and _num(s.get("price"))]
        shops.sort(key=lambda s: s.seen or 0, reverse=True)
        recent = [Sale(_num(r.get("price")), _num(r.get("qty")) or 1, str(r.get("location") or ""), _ts(r.get("time")),
                       str(r.get("seller") or ""))
                  for r in it.get("recent") or [] if isinstance(r, dict) and _num(r.get("price"))]
        history = [Day(p["date"], _num(p.get("median")), _num(p.get("low")), _num(p.get("high")), int(p.get("sold") or 0))
                   for p in it.get("history") or [] if isinstance(p, dict) and _num(p.get("median"))]
        recent.sort(key=lambda r: r.time or 0, reverse=True)
        last3 = [r.price for r in recent[:3]]
        cash = it.get("cash") if isinstance(it.get("cash"), dict) else None
        out[gid] = Sales(gid, str(it.get("name") or ""), _num(it.get("price")), _num(it.get("p25")), _num(it.get("p75")),
                         int(it.get("sold") or 0), bool(it.get("lowData", True)), _num(it.get("ask")),
                         _ts(it.get("lastSold")), bool(it.get("set")), bool(it.get("scrolledOnly")),
                         it.get("trend") if isinstance(it.get("trend"), int) else None, int(it.get("trendDays") or 0),
                         shops, int(it.get("listingShops") or 0), str(it["id"]), int(it.get("asks") or 0),
                         int(it.get("days") or 0), recent,
                         _num(sum(last3) / len(last3)) if len(last3) == 3 else None,
                         cash is not None, cash.get("onSale") if cash and isinstance(cash.get("onSale"), bool) else None,
                         _num(cash.get("nx")) if cash else None, history, str(it.get("category") or ""),
                         it.get("crate") if isinstance(it.get("crate"), (int, float)) else None)
    names: dict[str, list[int]] = {}
    for gid, s in out.items():
        names.setdefault(_fold(s.name), []).append(gid)
    faces = data.get("faces") if isinstance(data.get("faces"), dict) else {}
    return Snapshot(str(data.get("world") or "") or LIVE_MARKET, generated, out,
                    by_name={n: ids[0] for n, ids in names.items() if n and len(ids) == 1}, faces=faces)


def _fold(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


# ---------------------------------------------------------------- fetching

def _http(url: str, etag: str = "", timeout: float = TIMEOUT) -> tuple[int, bytes, str]:
    """(status, body, etag); status 304 (with If-None-Match) means unchanged."""
    headers = {"User-Agent": market.UA, "Accept": "application/json", "Accept-Encoding": "gzip"}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
            return r.status, body, r.headers.get("ETag") or ""
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, b"", etag
        raise


_request = _http        # every request goes through here (the tests replace it)


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
    global _snap, _failed, _version
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
    # (the index's time is the publish's, a second or so after the file's own "generated_at": a live watch saw
    # 09:28:23 against 09:28:22, and every check asked for the market file again; ETag answered 304, but still)
    if _snap is not None and updated is not None and updated <= _snap.generated + SAME_PUBLISH_SECONDS:
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
    data = slim(doc, world, _faces(timeout))
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
    _version += 1


def _faces(timeout: float) -> dict:
    """The sellers' pictures map, fetched with the market file; without it the rows show an initial."""
    try:
        _, body, _ = _request(FACES_URL, timeout=timeout)
        data = json.loads(body.decode("utf-8"))
        return data.get("faces") if isinstance(data, dict) and isinstance(data.get("faces"), dict) else {}
    except Exception:          # noqa: BLE001 - pictures are a nicety
        log.info("MesoWatch faces unavailable", exc_info=True)
        return {}


def face_file(snap: Snapshot | None, seller: str, timeout: float = 10) -> Path | None:
    """The seller's character picture on disk (downloaded once, kept in DATA_DIR/mesowatch_faces); None when
    MesoWatch has none for them or it can't be fetched. Blocking: call it off the UI thread."""
    name = snap.faces.get(seller) if snap and seller else None
    if not name or not re.fullmatch(r"[\w-]+\.(?:webp|png|jpg)", name):
        return None
    folder = _file().parent / "mesowatch_faces"
    path = folder / name
    if path.exists():
        return path
    try:
        status, body, _ = _request(FACE_BASE + name, timeout=timeout)
        if status != 200 or not body:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return path
    except Exception:          # noqa: BLE001 - offline: the initial stays
        return None


def version() -> int:
    """A number that changes when a newer market came in: a page showing the market redraws when it moves."""
    return _version


def crate(snap: Snapshot | None) -> list[Sales]:
    """What the Founder's Mystery Fashion Crate can give, each with its chance (MesoWatch's crate odds, Nexon's published
    rates), likeliest first."""
    if snap is None:
        return []
    return sorted((s for s in snap.items.values() if s.crate_odds), key=lambda s: (-s.crate_odds, s.name))


def snapshot(refresh: bool = True, timeout: float = TIMEOUT) -> Snapshot | None:
    """The live market's prices: the copy on hand, checked against the site once REFRESH_SECONDS have gone by
    (refresh=False: never asks the site, for the chat, which must not wait on a download). None: nothing on hand."""
    global _snap, _loaded, _checked, _failed
    with _lock:
        if not _loaded:
            _loaded = True
            if _snap is None:
                _snap, _ = _read_disk()
        # a minute's slack: the app's 15-minute timer firing a moment early still checks (else every other tick)
        due = REFRESH_SECONDS - 60
        wait = RETRY_SECONDS if _failed else due
        fresh = _snap is not None and time.time() - _snap.fetched < due
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


def item_url(item=None) -> str:
    """The item's page on MesoWatch, as its own share link writes it (?item=04010001 for "basil:04010001", the
    Cash Shop's ?item=cash:01007145), or its front page."""
    if isinstance(item, int):
        return f"{SITE}?item={item:08d}"
    if isinstance(item, str) and item:
        return SITE + "?" + urllib.parse.urlencode({"item": re.sub(r"^basil:", "", item)}, safe=":")
    return SITE


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
    if s.last:
        ago = f", {round((snap.generated - s.last.time) / 3600)} h before this snapshot" if s.last.time else ""
        bits.append(f"last sale {s.last.price:,}{ago}")
    if s.last3:
        bits.append(f"last 3 sales average {s.last3:,}")
    if s.days:
        bits.append(f"{s.sold:,} sales over {s.days} day{'s' if s.days != 1 else ''}")
    if s.trend_pct is not None and s.trend_days:
        bits.append(f"trend {s.trend_pct:+d}% over {s.trend_days} days")
    if s.ask:
        bits.append(f"shops ask about {s.ask:,}")
    if s.asks:
        bits.append(f"{s.asks:,} listings in {max(s.listing_shops, 1):,} shops")
    if s.cash and s.cash_on_sale is not None:
        bits.append("in the Cash Shop now" + (f" for {s.nx:,} NX" if s.nx else "") if s.cash_on_sale
                    else "not on sale in the Cash Shop now")
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

