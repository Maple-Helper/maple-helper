"""What the Cash Shop sells now, from OSMS Data Explorer (osmsdataexplorer.com, by ohmi): a datamine of the live game
client, published as data/current/cash_shop.json in its GitHub repository and redeployed by itself after every game
patch. Each item carries its NX price, whether it is on sale, its category (and an equip's slot), its description,
how long it lasts and a thumbnail.

ohmi gave Maple Helper permission (the owner, 2026-10-11), on the terms the owner asked for: a credit and link to "OSMS
Data Explorer by ohmi" wherever the data shows, the file fetched at most once an hour, and the raw file never
redistributed (the app keeps only the items on sale, on the player's own disk).

The Cash Shop's promotions (the packages, the multi-packs with a discount, the crate of 11) are set on the game's
servers, not in the client: they are not in the file, so the app doesn't show them."""
from __future__ import annotations

import gzip
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import market

SITE = "https://osmsdataexplorer.com/"
CREDIT = "OSMS Data Explorer by ohmi"
BASE = "https://raw.githubusercontent.com/ohmi69/osms_datamine_dashboard/main/data/current/"
CASH_URL = BASE + "cash_shop.json"
REFRESH_SECONDS = 3600            # at most once an hour (the terms ohmi agreed to)
RETRY_SECONDS = 300
TIMEOUT = 30
SLIM_VERSION = 1

log = logging.getLogger(__name__)
_lock = threading.Lock()
_items: list[Item] | None = None
_loaded = False
_checked = 0.0
_failed = False
_version = 0


@dataclass(frozen=True)
class Item:
    game_id: int
    name: str
    category: str               # OSMS's: "Equipment", "Coupon", "Pet", "Pet Skill", "Megaphone", "Store Permit", ...
    sub: str                    # an equip's slot ("Hat", "Top", "Overall", "Bottom", "Shoes", "PetEquip"), or ""
    nx: int
    count: int = 1
    days: int = 0               # how long it lasts; 0: permanent
    description: str = ""
    thumbnail: str = ""         # under data/current/ ("images/items/05150000.png")


def _file() -> Path:
    from .store import DATA_DIR
    return DATA_DIR / "osms_cash_shop.json"


def slim(doc: dict) -> dict:
    """The items on sale, as the app uses them (the raw file is never kept)."""
    out = []
    for cat in doc.get("categories") or [] if isinstance(doc, dict) else []:
        for it in (cat.get("items") or []) if isinstance(cat, dict) else []:
            if not isinstance(it, dict) or not it.get("on_sale") or not isinstance(it.get("id"), int):
                continue
            price = it.get("price")
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            thumb = str(it.get("thumbnail") or "")
            out.append({"id": it["id"], "name": str(it.get("name") or ""), "category": str(it.get("category") or ""),
                        "sub": str(it.get("sub_category") or ""), "nx": int(price), "count": int(it.get("count") or 1),
                        "days": int(it.get("period") or 0), "description": str(it.get("description") or ""),
                        "thumbnail": thumb if re.fullmatch(r"images/items/\d+\.png", thumb) else ""})
    return {"version": SLIM_VERSION, "items": out}


def parse(data: dict) -> list[Item] | None:
    if not isinstance(data, dict) or data.get("version") != SLIM_VERSION:
        return None
    return [Item(i["id"], i["name"], i["category"], i["sub"], i["nx"], i.get("count", 1), i.get("days", 0),
                 i.get("description", ""), i.get("thumbnail", "")) for i in data.get("items") or []
            if isinstance(i, dict) and isinstance(i.get("id"), int)]


def _http(url: str, etag: str = "", timeout: float = TIMEOUT) -> tuple[int, bytes, str]:
    headers = {"User-Agent": market.UA, "Accept-Encoding": "gzip"}
    if etag:
        headers["If-None-Match"] = etag
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            body = r.read()
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
            return r.status, body, r.headers.get("ETag") or ""
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, b"", etag
        raise


_request = _http        # every request goes through here (the tests replace it)


def _read_disk() -> tuple[list[Item] | None, dict]:
    try:
        raw = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    return parse(raw), raw


def _check(timeout: float) -> None:
    global _items, _failed, _version
    items, raw = _read_disk()
    if _items is None:
        _items = items
    status, body, etag = _request(CASH_URL, raw.get("etag", "") if _items is not None else "", timeout)
    now = time.time()
    if status == 304 and _items is not None:
        raw["fetched"] = now
    else:
        data = slim(json.loads(body.decode("utf-8")))
        parsed = parse(data)
        if not parsed:
            raise ValueError("OSMS's cash shop file has no items on sale")
        changed = [i.game_id for i in parsed] != [i.game_id for i in (_items or [])] or parsed != _items
        _items = parsed
        raw = {**data, "etag": etag, "fetched": now}
        if changed:
            _version += 1
    try:
        path = _file()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        log.warning("could not keep the Cash Shop list on disk", exc_info=True)
    _failed = False


def items(refresh: bool = True, timeout: float = TIMEOUT) -> list[Item] | None:
    """What the Cash Shop sells now: the copy on hand, checked against OSMS once an hour. None: nothing on hand."""
    global _items, _loaded, _checked, _failed
    with _lock:
        if not _loaded:
            _loaded = True
            if _items is None:
                _items, raw = _read_disk()
                fetched = float(raw.get("fetched") or 0)
            else:
                fetched = 0.0
            if _items is not None and time.time() - fetched < REFRESH_SECONDS:
                _checked = time.monotonic() - (time.time() - fetched)     # a restart doesn't ask again sooner
        wait = RETRY_SECONDS if _failed else REFRESH_SECONDS - 60
        if refresh and (not _checked or time.monotonic() - _checked > wait):
            _checked = time.monotonic()
            try:
                _check(timeout)
            except Exception:      # noqa: BLE001 - offline, a changed file: keep what we have
                _failed = True
                log.warning("OSMS check failed", exc_info=True)
        return _items


def version() -> int:
    """Changes when a different Cash Shop list came in (the Shop page redraws)."""
    return _version


def warm() -> None:
    threading.Thread(target=items, daemon=True, name="osms").start()


def thumbnail_file(item: Item, timeout: float = 10) -> Path | None:
    """The item's picture from OSMS, on disk (fetched once, kept in DATA_DIR/osms_images): for an item the KB has no
    picture of. Blocking: call it off the UI thread."""
    if not item.thumbnail:
        return None
    folder = _file().parent / "osms_images"
    path = folder / item.thumbnail.rsplit("/", 1)[1]
    if path.exists():
        return path
    try:
        status, body, _ = _request(BASE + item.thumbnail, timeout=timeout)
        if status != 200 or not body:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return path
    except Exception:          # noqa: BLE001 - offline: no picture
        return None


# ---------------------------------------------------------------- the game's own tabs

# the Cash Shop's tabs as the game has them (the owner's screenshots, 2026-10-11), and where OSMS's categories go;
# Main and Seasonal are the promotions, which the client file doesn't have
TABS = ("special", "fashion", "beauty", "pets", "utility")
SECTIONS = {"special": ("crate",), "fashion": ("hats", "outfits", "tops", "bottoms", "shoes"),
            "beauty": ("hair", "faces", "misc"), "pets": ("pets", "pet_equip", "pet_care", "guide"),
            "utility": ("convenience", "social", "effects")}
_SLOTS = {"Hat": "hats", "Overall": "outfits", "Top": "tops", "Bottom": "bottoms", "Shoes": "shoes",
          "PetEquip": "pet_equip"}


def place(item: Item) -> tuple[str, str]:
    """(tab, section) the game shows the item under; ("utility", "convenience") for anything else."""
    if item.category == "Equipment":
        sec = _SLOTS.get(item.sub)
        if sec == "pet_equip":
            return "pets", sec
        return ("fashion", sec) if sec else ("fashion", "hats")
    if item.category == "Coupon":
        name = item.name.lower()
        if "color" in name:
            return "beauty", "misc"
        return ("beauty", "faces") if "face" in name else ("beauty", "hair")
    if item.category == "Pet":
        return ("pets", "pets") if 5000000 <= item.game_id < 5010000 else ("pets", "pet_care")
    if item.category == "Pet Skill":
        return "pets", "pet_care"
    if item.category == "Megaphone":
        return "utility", "social"
    if item.category == "Weather Effect":
        return "utility", "effects"
    if "crate" in item.name.lower():
        return "special", "crate"
    return "utility", "convenience"
