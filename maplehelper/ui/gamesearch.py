"""The in-game toolbar's search window (ui/gametoolbar.py opens it): the KB's monsters, NPCs, maps and items, typed
down to a row. Each row is the thing's picture, its name in bold with its tag chips (a monster's level, NPC, Boss,
an equip's or a map's levels) and a muted line under it (HP and EXP, the NPC's role and map, the item's type, the
street), an item's NPC price on the end. A click opens the thing's own page in the detail window docked beside the
search, the row lit in the accent while it shows: a header card with a larger picture, the name and its chips, then
titled blocks — an NPC: where it stands and the way there; a monster: where it lives; an item: who drops and who
sells it; a map: the way there and its monsters — with a way back through what was opened. Safe to sell? lists the
items before anything is typed, the ones a current quest or recipe still needs first (NiaMeowDB's list,
sitedata.sell_list, sorted as its page sorts: the most needed first), each with its why-keep chips (quests,
recipes, Repeatable) or a green Safe to sell and what an NPC pays; an All / Safe to sell / Keep switch narrows it,
and a row opens the verdict with what needs it. The search is the one window over the game that takes the keyboard
(the player is typing in it); the detail never does; Esc or the ✕ hides it, and the minimap reads never see
either. The search itself is plain data: Hit / search / sell_need / sell_chips / monster_maps need no Qt."""
from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from .. import availability, bidi, combat, gamelookup, itemterms, market, routes, sitedata, tables
from ..i18n import I18n
from ..kb import KnowledgeBase, memo
from ..store import Settings
from . import mapview, theme
from .controls import FlowLayout, Segmented
from .glass import SHADOW, EdgeResize
from .location import LOCATION
from .npcoverlay import (BOTTOM, FILL1, FILL2, MUTED, OPACITY_DEFAULT, OPACITY_MAX, OPACITY_MIN, SIDE, TEXT, TOP,
                         _Header, _flags, _paint_glass, accent_of, direction, dock_beside, set_capturable)
from .widgets import fit_picture, zoom_on_hover

# the searches the toolbar offers: monsters and NPCs together (each row opens its own page), the maps (listed before
# anything is typed), the items, and "sell" (the items again, safe to sell?, listed before anything is typed too)
KINDS = ("monster_npc", "map", "item", "sell")
SELL_FILTERS = ("all", "safe", "keep")   # Safe to sell?'s switch: every item, those nothing needs, those something needs
MIN_W, MIN_H = 260, 300             # the smallest the player can drag the window to
DEFAULT_W, DEFAULT_H = 360, 500     # what opens under the toolbar before it is ever resized
GAP = 8                             # between the toolbar and the window that opens under it
LIMIT = 50                          # rows shown before the "and N more" line
TYPE_DEBOUNCE_MS = 150              # a pause in the typing before the rows are read again
GEOM_DEBOUNCE_MS = 400              # a move/resize saves the window's place once it settles
DETAIL_W = 360                     # the detail window's width until the player resizes it
DETAIL_H = 560                     # the height it grows to, then the body scrolls (until the player resizes it)
DETAIL_MIN_W, DETAIL_MIN_H = 240, 100   # the smallest the player can drag it to (a short page folds to its own height)
ROW_PIC = 30                       # a row's picture (a monster's, an NPC's, an item's)
MAP_PIC = (44, 30)                 # a map row's picture: its minimap, wider than tall
HEAD_PIC = 52                      # the page's header picture
HEAD_MAP = (80, 52)                # ...a map's
SRC_CAP = 12                       # droppers, sellers, quests and recipes listed, then "and N more"
SCROLLBAR_W = 8                    # the body's scrollbar (QSS below): pictures stay clear of it
BLOCK_PAD = 6                      # a page block's inside margin, left and right: its pictures stay clear of it
NO_NUM = "\u2014"                  # an end number the KB doesn't have (no NPC buys it): a muted dash, why on hover
# the chips' colours, each readable on every glass tint (the accent only lights what is picked): a monster amber, a
# boss red, an NPC blue; why to keep an item — its quests violet, its recipes blue, a quest that comes back grey —
# or safe to sell green
TONES = {"monster": "#FFB066", "boss": "#FF8A80", "npc": "#64B5FF", "quest": "#C9A0FF", "craft": "#64B5FF",
         "repeat": "#B8BCC6", "safe": "#4CD07D", "keep": "#FFB066"}

# the same dark glass as the NPCs window (its own panel, whatever theme the app's windows are in); the other windows
# over the game build on it (qss below adds this window's own rows, chips and blocks)
QSS = f"""
#Title {{ color: {TEXT}; font-weight: 600; }}
#Status {{ color: {MUTED}; }}
#Lives {{ color: {MUTED}; font-size: 11px; font-weight: 600; padding: 6px 2px 0 2px; }}
#GuideLine {{ color: {TEXT}; }}
#StepMap {{ color: {TEXT}; font-weight: 700; padding: 6px 2px 0 2px; }}
#DetailName {{ color: {TEXT}; font-weight: 700; font-size: 15px; }}
QLineEdit {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px; padding: 6px 10px; }}
QLineEdit:focus {{ background: {FILL2}; }}
QFrame#Row {{ background: {FILL1}; border: none; border-radius: 8px; }}
QFrame#Row:hover {{ background: {FILL2}; }}
#Name {{ background: transparent; color: {TEXT}; font-weight: 600; }}
#Sub {{ background: transparent; color: {MUTED}; }}
QPushButton {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px;
              padding: 6px 10px; text-align: left; }}
QPushButton:hover {{ background: {FILL2}; }}
QPushButton#Back {{ background: transparent; color: {MUTED}; padding: 2px 4px; }}
QPushButton#Back:hover {{ background: {FILL2}; color: {TEXT}; }}
QToolButton {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
              padding: 2px 7px; font-size: 13px; }}
QToolButton:hover {{ background: {FILL2}; color: {TEXT}; }}
QScrollArea {{ background: transparent; border: none; }}
#Rows {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: {SCROLLBAR_W}px; margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.25); border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


def _rgba(color: str, alpha: float) -> str:
    """A "#RRGGBB" colour at this opacity, for a tinted fill."""
    c = color.lstrip("#")
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{alpha})"


def qss(settings) -> str:
    """Both windows' own style: the glass's (QSS), then the rows, chips, header card and blocks, the picked row and
    the switch's lit segment in the accent the settings pick (npcoverlay.ACCENTS; the glass itself is painted in the
    tint by _paint_glass). Built again whenever the accent is picked, so both windows follow."""
    fill, on_fill = accent_of(settings)
    tones = "\n".join(f'QLabel#Chip[tone="{k}"], QLabel#Verdict[tone="{k}"] {{ color: {c}; '
                      f'background: {_rgba(c, 0.16)}; }}' for k, c in TONES.items())
    return QSS + f"""
QLabel {{ background: transparent; }}
QFrame#Row {{ border-radius: 10px; }}
QFrame#Row[flat="true"] {{ background: transparent; }}
QFrame#Row[flat="true"]:hover {{ background: {FILL1}; }}
QFrame#Row[sel="true"] {{ background: {_rgba(fill, 0.26)}; }}
QFrame#Row[sel="true"]:hover {{ background: {_rgba(fill, 0.34)}; }}
#Name {{ font-size: 13px; }}
#Sub {{ font-size: 12px; }}
QLabel#Chip {{ color: {TEXT}; background: {FILL2}; border-radius: 6px; padding: 1px 6px; font-size: 11px;
              font-weight: 700; }}
{tones}
#Num {{ color: {TEXT}; font-size: 13px; font-weight: 700; }}
#NoNum {{ color: {MUTED}; font-size: 13px; }}
#Unit {{ color: {MUTED}; font-size: 10px; }}
QFrame#HeadCard {{ background: {FILL1}; border: none; border-radius: 12px; }}
QFrame#Block {{ background: rgba(255,255,255,0.05); border: none; border-radius: 12px; }}
#BlockTitle {{ color: {TEXT}; font-size: 12px; font-weight: 700; padding: 0 4px 2px 4px; }}
#BlockNote {{ color: {MUTED}; padding: 0 4px; }}
QLabel#Verdict {{ border-radius: 10px; padding: 5px 14px; font-size: 15px; font-weight: 700; }}
#Segmented {{ background: {FILL1}; border: none; border-radius: 10px; }}
QPushButton#Segment {{ background: transparent; color: {MUTED}; border: none; border-radius: 8px; min-height: 26px;
                       max-height: 26px; padding: 0 10px; font-size: 12px; font-weight: 600; text-align: center; }}
QPushButton#Segment:hover:!checked {{ background: {FILL2}; color: {TEXT}; }}
QPushButton#Segment:checked {{ background: {fill}; color: {on_fill}; font-weight: 700; }}
"""


# ---------------------------------------------------------------- the search itself (no Qt in it)

@dataclass(frozen=True)
class Hit:
    """One row of a search: the thing's KB key (what a click opens), its name, the muted line under it (a monster's
    HP and EXP, an NPC's role and map, an item's KB type, a map's street: names and numbers, the same in every
    language), which page it opens ("monster" / "npc" / "map"; "" for the search's own: an item's, or Safe to
    sell?'s), the levels its level chip shows (a monster's, an equip's required level, the levels a map is for), and
    whether it is a boss."""
    key: str
    name: str
    sub: str = ""
    kind: str = ""
    lv: tuple[int, int] | None = None
    boss: bool = False


def _fold(name: str) -> str:
    """A name or a typed query as they are compared: lower case, one space between the words."""
    return re.sub(r"\s+", " ", str(name).strip().lower())


def monster_line(m: combat.Monster) -> str:
    """A monster's muted line: its HP and EXP, each only when the KB has it (never a made-up 0)."""
    return " · ".join(x for x in (f"{m.hp:,} HP" if m.hp else "", f"{m.exp:,} EXP" if m.exp else "") if x)


def _monster_hits(kb) -> list[Hit]:
    """Every monster in the game once per name — the training picker's own rule (tools.monster_rows): only what
    the KB confirms is in the game, none of the tutorial, event and job-test versions — the version that spawns
    on the most maps standing for all of them."""
    best: dict[str, combat.Monster] = {}
    open_ = availability.of(kb)
    for m in combat.monsters(kb):
        if combat.special_monster(m.name) or not open_.monster_key_open(m.key):
            continue
        if m.name not in best or sum(n for _, n in m.maps) > sum(n for _, n in best[m.name].maps):
            best[m.name] = m
    return [Hit(m.key, m.name, monster_line(m), "monster", (m.level, m.level), m.boss)
            for m in sorted(best.values(), key=lambda m: (m.level, m.name))]


def _npc_lines(kb) -> dict[str, str]:
    """NPC key -> its muted line: its role (the first of the KB's words for it: "Armor Seller", not "Armor Seller ·
    Shopkeeper") and the map it stands on, as the KB's npcs table has them (the first place it stands). Kept once
    the tables are ready (a read while they still built at start-up would leave every NPC without its map)."""
    cache = memo(kb, "_game_search")
    if "npc_lines" in cache:
        return cache["npc_lines"]
    out: dict[str, str] = {}
    for r in tables.rows(kb, "npcs"):
        if r.get("key"):
            role = str(r.get("role") or "").split(" · ")[0].strip()
            out.setdefault(str(r["key"]), " · ".join(x for x in (role, str(r.get("map") or "").strip()) if x))
    if tables.ready(kb):
        cache["npc_lines"] = out
    return out


def npc_line(kb, key: str) -> str:
    """The NPC's role and map ("Quest Giver · Mushroom Town"), "" when the KB's npcs table has no row for it."""
    return _npc_lines(kb).get(key, "")


def _npc_hits(kb) -> list[Hit]:
    """Every NPC in the game (the KB's own npc_open rule), the muted line its role and the map it stands on; an NPC
    the table has no row for keeps an empty one."""
    lines = _npc_lines(kb)
    open_ = availability.of(kb)
    return sorted((Hit(k, e["name"], lines.get(k, ""), "npc")
                   for k, e in kb.entities.items()
                   if e.get("category") == "npc" and str(e.get("name") or "").strip() and open_.npc_open(k)),
                  key=lambda h: _fold(h.name))


def equip_level(e: dict) -> int | None:
    """An equip's required level as the KB's item gives it; None for an item that isn't an equip, or has none."""
    if not str(e.get("type") or "").startswith("Equip"):
        return None
    lv = (e.get("props") or {}).get("Level Requirement")
    return int(lv) if isinstance(lv, (int, float)) and not isinstance(lv, bool) and lv > 0 else None


def _item_hits(kb) -> list[Hit]:
    """Every item the item picker lists (tools.item_rows' rule, the Cash Shop's own pets aside), the muted line its
    type, an equip with its required level."""
    out: dict[str, Hit] = {}
    for k, e in kb.entities.items():
        name = str(e.get("name") or "").strip()
        if e.get("category") != "item" or not name or name in out:
            continue
        if str(e.get("type") or "").startswith("Cash") and sitedata.untradeable(kb, k):
            continue          # a pet: the Cash Shop's NX only, no price to look up (the owner)
        lv = equip_level(e)
        out[name] = Hit(k, name, str(e.get("type") or ""), "", (lv, lv) if lv else None)
    return sorted(out.values(), key=lambda h: _fold(h.name))


def _map_hits(kb) -> list[Hit]:
    """Every map the KB's maps table lists (only what it confirms is in the game), the muted line its street and
    the levels it is for on its chip; each map once, by name."""
    out: dict[str, Hit] = {}
    for r in tables.rows(kb, "maps"):
        key, name = str(r.get("key") or ""), str(r.get("map") or "").strip()
        if not key.startswith("map/") or not name or key in out:
            continue
        lo, hi = r.get("lv_min"), r.get("lv_max")
        lv = None if lo is None or hi is None else (int(lo), int(hi))
        out[key] = Hit(key, name, str(r.get("street") or ""), "map", lv)
    return sorted(out.values(), key=lambda h: (_fold(h.name), h.key))


_BUILDERS = {"monster": _monster_hits, "npc": _npc_hits, "item": _item_hits, "map": _map_hits}
# the kinds listed before anything is typed (the first LIMIT): the maps by name, Safe to sell? the most needed first;
# the rest wait for 2 letters
BROWSE = {"map", "sell"}


def _source(kb, kind: str) -> list[Hit]:
    """One kind's every row, built once per KB (a keystroke only ranks them; the item list alone reads ~650
    pages). A list made while the KB's tables weren't ready (still building at start-up) isn't kept: it would have
    left every NPC without its map, and the maps list empty, for the whole session. Monsters & NPCs lists both,
    each row still opening its own page."""
    if kind == "monster_npc":
        return _source(kb, "monster") + _source(kb, "npc")
    cache = memo(kb, "_game_search")
    if kind not in cache:
        hits = _BUILDERS[kind](kb)
        if kind not in ("npc", "map") or tables.ready(kb):
            cache[kind] = hits
        return hits
    return cache[kind]


def map_levels(kb, key: str) -> tuple[int, int] | None:
    """The levels a map is for, as the maps search lists it; None when the KB's maps table doesn't say."""
    return next((h.lv for h in _source(kb, "map") if h.key == key), None)


def item_key(kb, name: str) -> str | None:
    """The item the KB lists under exactly this name (case and spacing aside), as the items search does; None."""
    cache = memo(kb, "_game_search")
    items = _source(kb, "item")
    seen = cache.get("item_names")
    if seen is None or seen[0] is not items:
        seen = cache["item_names"] = (items, {_fold(h.name): h.key for h in items})
    return seen[1].get(_fold(name))


def sell_need(kb, key: str) -> sitedata.SellNeeds | None:
    """What still needs the item on NiaMeowDB's Safe to Sell? list: its entry by the item's key, else by its name —
    the search lists each name once, under the KB's first item of that name, while the list may have picked another
    copy of it (Hero's Gladius: item/465 listed, item/556 on the list). None: nothing needs it, or no list."""
    sl = sitedata.sell_list(kb)
    if sl is None:
        return None
    need = sl.needs.get(key)
    if need is not None:
        return need
    cache = memo(kb, "_game_search")
    seen = cache.get("sell_names")
    if seen is None or seen[0] is not sl:
        seen = cache["sell_names"] = (sl, {_fold(n.name): n for n in sl.needs.values()})
    return seen[1].get(_fold((kb.get(key) or {}).get("name") or ""))


def sell_price(kb, key: str) -> int:
    """What an NPC pays for one: the list's price for the very item it names, else the item's own page's; 0 when
    neither knows."""
    need = sell_need(kb, key)
    own = need.price if need is not None and need.key == key else 0
    return own or market.npc_prices(kb, key).sell_back or 0


def sell_state(kb, key: str) -> str:
    """Safe to sell?'s answer for an item, by NiaMeowDB's list: "keep" (a current quest or recipe needs it), "safe"
    (nothing on the list needs it, and an NPC buys it), "unknown" (no NPC price: the list leaves out what no NPC
    buys — Glowing Mushroom, which a quest needs, among them — so its silence says nothing), "" while the KB has no
    list (never "safe" without one)."""
    if sitedata.sell_list(kb) is None:
        return ""
    if sell_need(kb, key) is not None:
        return "keep"
    return "safe" if sell_price(kb, key) else "unknown"


def sell_chips(t, kb, key: str) -> list[tuple[str, str]]:
    """A Safe to sell? row's chips as (text, tone): why to keep the item — how many current quests and recipes need
    it, Repeatable when one of those quests comes back — or Safe to sell, or No NPC price (sell_state); none while
    the KB has no list."""
    state = sell_state(kb, key)
    if state != "keep":
        return {"safe": [(t("sell_safe_word"), "safe")], "unknown": [(t("sell_chip_no_price"), "")]}.get(state, [])
    need = sell_need(kb, key)
    out = []
    if need.quests:
        out.append((t("sell_chip_quests", n=len(need.quests)), "quest"))
    if need.recipes:
        out.append((t("sell_chip_recipes", n=len(need.recipes)), "craft"))
    if any(repeat for *_, repeat in need.quests):
        out.append((t("sell_repeat"), "repeat"))
    return out


def _sell_source(kb, which: str) -> list[Hit]:
    """Safe to sell?'s rows for its switch (SELL_FILTERS): "keep", the items a current quest or recipe needs, the
    most needs (quests and recipes) first as NiaMeowDB's page sorts, ties by name; "all", those and then every other
    item by name; "safe", only the items nothing needs that an NPC buys, by name (each item's NPC price read once,
    the first time it's asked). Without a list nothing is known either way: every item for "all", none else."""
    items = _source(kb, "item")
    sl = sitedata.sell_list(kb)
    if sl is None:
        return items if which == "all" else []
    cache = memo(kb, "_game_search")
    seen = cache.get("sell")
    if seen is None or seen[0] is not sl or seen[1] is not items:
        needs = {h.key: sell_need(kb, h.key) for h in items}
        keep = sorted((h for h in items if needs[h.key] is not None),
                      key=lambda h: -(len(needs[h.key].quests) + len(needs[h.key].recipes)))
        rest = [h for h in items if needs[h.key] is None]
        seen = cache["sell"] = (sl, items, {"all": keep + rest, "keep": keep, "rest": rest})
    lists = seen[2]
    if which == "safe" and "safe" not in lists:
        lists["safe"] = [h for h in lists["rest"] if sell_price(kb, h.key)]
    return lists.get(which, lists["all"])


def search(kb, kind: str, query: str, limit: int = 50, sell: str = "all") -> tuple[list[Hit], int]:
    """Every row of this kind whose name has all the query's words in it — case aside, the words in any order —
    up to `limit` of them, ranked: the exact name, then the names the query starts, then the rest; ties by
    name. The pair's other half is how many matched in all (the "and N more" line). A query under two letters
    (spaces aside) is still being typed: nothing yet — but a BROWSE kind lists all of its rows (the maps by name,
    Safe to sell? the most needed first). Safe to sell? searches only the rows its switch keeps (`sell`, one of
    SELL_FILTERS)."""
    q = _fold(query or "")
    if len(q.replace(" ", "")) < 2:
        if kind in BROWSE:
            rows = _sell_source(kb, sell) if kind == "sell" else _source(kb, kind)
            return rows[:max(0, int(limit))], len(rows)
        return [], 0
    words = q.split(" ")
    ranked: list[list[Hit]] = [[], [], []]      # the exact name, a name the query starts, the rest
    for hit in _sell_source(kb, sell) if kind == "sell" else _source(kb, kind):
        low = _fold(hit.name)
        if not all(w in low for w in words):
            continue
        ranked[0 if low == q else 1 if low.startswith(q) else 2].append(hit)
    hits = [h for bucket in ranked for h in sorted(bucket, key=lambda h: (_fold(h.name), h.name))]
    return hits[:max(0, int(limit))], len(hits)


def monster_maps(kb, key: str) -> list[tuple[str, str, str, int]]:
    """Every map the monster lives on, as (map key, map name, street, how many spawn there), the most spawns
    first and each map once: every row of the monster's name in the KB's spawns table, all its versions (a
    field one and a boss one share a name)."""
    m = combat.monster(kb, key)
    name = m.name if m is not None else str((kb.get(key) or {}).get("name") or "")
    if not name:
        return []
    out: dict[str, tuple[str, str, str, int]] = {}
    # the tables built when missing (cheap when current): with build=False a fresh KB, or a search while the app
    # still built them at start-up, found no maps at all (CI's fixture KB, 2026-10-10)
    for r in tables.rows(kb, "spawns"):
        mk = str(r.get("map_key") or "")
        if r.get("monster") != name or not mk:
            continue
        n = int(r.get("count") or 0)
        if mk not in out or n > out[mk][3]:
            out[mk] = (mk, str(r.get("map") or mk), str(r.get("street") or ""), n)
    return sorted(out.values(), key=lambda t: (-t[3], _fold(t[1])))


# ---------------------------------------------------------------- the pieces both windows are built of

def _lv(t, lv: tuple[int, int]) -> str:
    """A level chip's words: "Lv. 15", a range "Lv. 2–14" (in Hebrew the range one left-to-right block, or it read
    "14–2")."""
    lo, hi = lv
    return t("lv_short", n=str(lo) if lo == hi else bidi.ltr_block(f"{lo}–{hi}", t.rtl))


def _mob_chips(t, level: int, boss: bool) -> list[tuple[str, str]]:
    """A monster's chips where monsters and NPCs list together: "Monster · Lv. 15" (a level the KB lacks: "Monster"),
    and Boss for a boss or an hourly spawn (combat.Monster.boss)."""
    lv = _lv_chip(t, level)
    out = [(t("search_chip_monster", lv=lv[0]) if lv else t("search_is_monster"), "monster")]
    return out + ([(t("search_chip_boss"), "boss")] if boss else [])


def _lv_chip(t, level: int) -> tuple[str, str] | None:
    """A monster's level chip in a list of monsters (who drops it, what lives on a map); None for a level the KB
    doesn't have."""
    return (_lv(t, (level, level)), "monster") if 0 < level < gamelookup.UNKNOWN_LEVEL else None


def _boss_chip(t, m: combat.Monster | None) -> list[tuple[str, str]]:
    """Boss beside a monster's name, when it is one (combat.Monster.boss: a boss or an hourly spawn)."""
    return [(t("search_chip_boss"), "boss")] if m is not None and m.boss else []


_TYPE_SPLIT = re.compile(r"\s*[/·]\s*")


def _type_parts(typ: str, lang: str) -> list[str]:
    """An item's KB type ("Equip / One-Handed Sword") as its words in the UI's language (itemterms.kind), each
    part once: ["Equip", "One-Handed Sword"]."""
    return [itemterms.kind(p, lang) for p in _TYPE_SPLIT.split(str(typ or "").strip()) if p]


def _type_word(typ: str, lang: str) -> str:
    """The item's type word, its KB type's last part ("One-Handed Sword", "Potion", "Quest Item")."""
    parts = _type_parts(typ, lang)
    return parts[-1] if parts else ""


def _chip(text: str, tone: str, rtl: bool) -> QLabel:
    """A small coloured tag (TONES; no tone: a plain light one, a level or a type)."""
    c = QLabel(bidi.plain(text, rtl), objectName="Chip")
    c.setProperty("tone", tone)
    return c


def _price_end(t, price: int | None) -> tuple[str, str]:
    """An NPC price on a row's end: the number over "mesos"; none known: the muted dash (_Row explains it)."""
    if not price:
        return NO_NUM, ""
    return bidi.ltr_block(f"{price:,}", t.rtl), bidi.plain(t("search_mesos"), t.rtl)


def _text(text: str, muted: bool = False, rich: bool = False, t: I18n | None = None) -> QLabel:
    """A line of a page: a sentence, a muted fact, or a route step (`rich`: its **bold** names drawn bold, as the
    map window draws them — mapview.says_html — never shown as asterisks)."""
    w = QLabel(objectName="Status" if muted else "GuideLine")
    if rich and t is not None:
        w.setTextFormat(Qt.RichText)
        w.setText(mapview.says_html(t, text))
    else:
        w.setText(text)
    w.setWordWrap(True)
    return w


class _Pictures:
    """Each thing's picture (kb.picture: its own sprite, else its kind's icon), fitted once per box: the rows are
    built again on every keystroke and every page, and each would read its file from disk and scale it again."""

    def __init__(self, kb):
        self._kb = kb
        self._paths: dict[str, object] = {}
        self._fitted: dict[tuple, QPixmap] = {}

    def pixmap(self, key: str, w: int, h: int, widget: QWidget) -> tuple[QPixmap, object]:
        """The picture fitted into w x h at the widget's screen, and its file (for the big one on hover)."""
        if key not in self._paths:
            self._paths[key] = self._kb.picture(key) if key else None
        path = self._paths[key]
        if not path:
            return QPixmap(), None
        dpr = widget.devicePixelRatioF()
        pm = self._fitted.get((key, w, h, dpr))
        if pm is None:
            raw = QPixmap(str(path))
            pm = fit_picture(raw, w, h, widget, trim=True) if not raw.isNull() else QPixmap()
            self._fitted[(key, w, h, dpr)] = pm
        return pm, path

    def label(self, key: str, w: int, h: int | None = None) -> QLabel:
        """The picture in a fixed box (none: the box empty, the rows still line up), shown large on hover."""
        h = w if h is None else h
        pic = QLabel(objectName="Pic")
        pic.setFixedSize(w, h)
        pic.setAlignment(Qt.AlignCenter)
        pm, path = self.pixmap(key, w, h, pic)
        if not pm.isNull():
            pic.setPixmap(pm)
            zoom_on_hover(pic, path)
        return pic


class _SearchBox(QLineEdit):
    """The typing box: Esc hides the window (the keyboard goes back to the game at once)."""

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.window().hide()
            return
        super().keyPressEvent(e)


class _TitleLine(QWidget):
    """A row's name in bold with its chips: beside it while both fit the width, else the chips on a line of their
    own under it (below=True: always), wrapping there when they don't fit one line either. The name wraps only
    when it alone is wider than the row — beside its chips in a box layout "Zombie Mushroom" broke in two with room
    to spare under it. Mirrored in Hebrew: the name at the right, its chips to its left."""

    GAP = 6           # between the name and a chip, and between chips
    LINE_GAP = 4      # between the name's line and the chips' line under it, and between wrapped chip lines

    def __init__(self, name: QLabel, chips: list[QLabel], below: bool = False):
        super().__init__()
        self.name, self.chips, self._below = name, chips, below
        name.setParent(self)
        for c in chips:
            c.setParent(self)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _name_w(self) -> int:
        """The name's width on one line."""
        m = self.name.contentsMargins()
        return self.name.fontMetrics().horizontalAdvance(self.name.text()) + m.left() + m.right() + 2

    def _arrange(self, width: int, apply: bool) -> int:
        """The name and chips laid out across this width (placed when `apply`); the height they take."""
        self.ensurePolished()
        width = max(1, width)
        name_w = self._name_w()
        sizes = [c.sizeHint() for c in self.chips]
        chips_w = sum(s.width() for s in sizes) + self.GAP * max(0, len(sizes) - 1)
        if sizes and not self._below and name_w + self.GAP + chips_w <= width:
            name_h = self.name.heightForWidth(name_w)
            line = max([name_h] + [s.height() for s in sizes])
            if apply:
                self._place(self.name, 0, (line - name_h) // 2, name_w, name_h, width)
                x = name_w + self.GAP
                for c, s in zip(self.chips, sizes):
                    self._place(c, x, (line - s.height()) // 2, s.width(), s.height(), width)
                    x += s.width() + self.GAP
            return line
        name_w = min(name_w, width)
        name_h = self.name.heightForWidth(name_w)
        if apply:
            self._place(self.name, 0, 0, name_w, name_h, width)
        if not sizes:
            return name_h
        y, x, line = name_h + self.LINE_GAP, 0, 0
        for c, s in zip(self.chips, sizes):
            w = min(s.width(), width)
            if x and x + w > width:
                y, x, line = y + line + self.LINE_GAP, 0, 0
            if apply:
                self._place(c, x, y, w, s.height(), width)
            x, line = x + w + self.GAP, max(line, s.height())
        return y + line

    def _place(self, w: QWidget, x: int, y: int, ww: int, hh: int, width: int) -> None:
        """A piece at x from the reading side's edge: the left, the right in Hebrew."""
        if self.layoutDirection() == Qt.RightToLeft:
            x = width - x - ww
        w.setGeometry(x, y, ww, hh)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(width, False)

    def sizeHint(self) -> QSize:
        self.ensurePolished()
        sizes = [c.sizeHint() for c in self.chips]
        chips_w = sum(s.width() for s in sizes) + self.GAP * max(0, len(sizes) - 1)
        w = self._name_w() + (self.GAP + chips_w if sizes and not self._below else 0)
        return QSize(max(w, chips_w), self._arrange(max(w, chips_w), False))

    def minimumSizeHint(self) -> QSize:
        self.ensurePolished()
        w = max([self.name.minimumSizeHint().width()] + [c.sizeHint().width() for c in self.chips])
        return QSize(w, self._arrange(w, False))

    def resizeEvent(self, e) -> None:
        self._arrange(self.width(), True)
        super().resizeEvent(e)

    def changeEvent(self, e) -> None:
        if e.type() == QEvent.LayoutDirectionChange:
            self._arrange(self.width(), True)
        super().changeEvent(e)


class _Row(QFrame):
    """One row of a list: the thing's picture, its name in bold with its tag chips (_TitleLine; chips_below: on a
    line of their own under it, Safe to sell?'s why-keep chips), a muted line under them, and on the end a chip (a
    dropper's level) and a number over its unit (a price, how many spawn) — in the NPCs window's row colours, lit in
    the accent while its page is open (set_selected). clickable=False: a fact in the same look (a quest that needs
    an item); flat: a row inside a page's titled block, no card of its own."""

    clicked = Signal()

    def __init__(self, name: str, sub: str = "", tip: str = "", clickable: bool = True, pic: QLabel | None = None,
                 chips=(), end: tuple[str, str] | None = None, end_tip: str = "", chips_below: bool = False,
                 end_chip: tuple[str, str] | None = None, flat: bool = False, rtl: bool = False):
        super().__init__(objectName="Row")
        self.page: tuple[str, str] | None = None     # the page a click opens, (kind, key)
        self.setProperty("flat", flat)
        self.setProperty("sel", False)
        if clickable:
            self.setAttribute(Qt.WA_Hover, True)          # its QSS :hover
            self.setCursor(Qt.PointingHandCursor)
        if tip:
            self.setToolTip(tip)
            self.setAccessibleName(tip)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8 if pic is not None else 10, 6, 10, 6)
        lay.setSpacing(10)
        self.pic = pic
        if pic is not None:
            lay.addWidget(pic, 0, Qt.AlignVCenter)
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(3)
        # the name wraps: "Construction Site North of Kerning City  ·  Victoria Road" ran under the scrollbar
        self.name = QLabel(name, objectName="Name", wordWrap=True)
        self.chips = [_chip(text, tone, rtl) for text, tone in chips]
        mid.addWidget(_TitleLine(self.name, self.chips, chips_below))
        self.sub = QLabel(sub, objectName="Sub", wordWrap=True) if sub else None
        if self.sub is not None:
            mid.addWidget(self.sub)
        lay.addLayout(mid, 1)
        self.end_chip = _chip(*end_chip, rtl) if end_chip is not None else None
        if self.end_chip is not None:
            lay.addWidget(self.end_chip, 0, Qt.AlignVCenter)
        self.num = self.unit = None
        if end is not None:
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addStretch(1)
            right = Qt.AlignRight | Qt.AlignVCenter
            self.num = QLabel(end[0], objectName="NoNum" if end[0] == NO_NUM else "Num", alignment=right)
            if end_tip:
                self.num.setToolTip(end_tip)
            col.addWidget(self.num)
            if end[1]:
                self.unit = QLabel(end[1], objectName="Unit", alignment=right)
                col.addWidget(self.unit)
            col.addStretch(1)
            lay.addLayout(col)

    def set_selected(self, on: bool) -> None:
        """Lit in the accent (its page is the one open), or not."""
        if bool(self.property("sel")) != on:
            self.setProperty("sel", on)
            self.style().unpolish(self)
            self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class _Block(QFrame):
    """A titled section of a page (who drops it, where it lives, the way there): its title over its rows, one card."""

    def __init__(self, title: str):
        super().__init__(objectName="Block")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(BLOCK_PAD, 8, BLOCK_PAD, 6)
        self._lay.setSpacing(2)
        self.title = QLabel(title, objectName="BlockTitle", wordWrap=True)
        self._lay.addWidget(self.title)
        self.items: list[QWidget] = []          # what is under the title, top to bottom

    def add(self, w: QWidget) -> None:
        self.items.append(w)
        self._lay.addWidget(w)


class DetailWindow(EdgeResize, QWidget):
    """A clicked result's own page, in the NPCs window's guide's own look: a see-through always-on-top panel
    docked beside the search (its left, or its right where the screen has no room) that moves and hides with it
    and never takes the keyboard from the game. Each page opens with a header card — the thing's picture, its name
    and chips, a muted line — over its titled blocks. Its views are a history — a row's click pushes one, ‹ Back
    pops, a new search click starts it over. While it shows an NPC on the player's map it guides them: the sentence
    and the picture's blue dot follow the player, and the game's own minimap rings the spot (LOCATION.guide).
    Its edges resize it (EdgeResize): the size the player drags it to is kept (game_detail_size) and its pictures
    follow the width; until then it is DETAIL_W wide and as tall as its page, up to DETAIL_H."""

    def __init__(self, owner: GameSearch):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)          # hovering the rim shows the resize cursor
        self.setMinimumSize(DETAIL_MIN_W, DETAIL_MIN_H)
        self._fit_size = QSize()             # the size the window last gave itself (a resize to it isn't the player's)
        self._sized = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._resized)
        self._owner = owner
        # a window of its own (no parent to take it from): mirrored in Hebrew from the start, as the search is
        self.setLayoutDirection(Qt.RightToLeft if owner.t.rtl else Qt.LeftToRight)
        self._stack: list[tuple[str, str]] = []     # each view pushed, the last one shown
        self._guided: tuple | None = None           # the guide this window set, None while it set none
        self._rows: list[QWidget] = []
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(8)
        self._head = QWidget()
        self._head_lay = QVBoxLayout(self._head)
        self._head_lay.setContentsMargins(0, 0, 0, 0)
        self._head_lay.setSpacing(4)
        self.back = QPushButton(objectName="Back")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.setFocusPolicy(Qt.NoFocus)
        self.back.clicked.connect(lambda _=False: self.go_back())
        self._head_lay.addWidget(self.back, 0, Qt.AlignLeft)
        # the page's header card, built again for every page (_set_head): its picture, name, chips, muted line, ✕
        self._card: QFrame | None = None
        self.pic: QLabel | None = None
        self.title: QLabel | None = None
        self.head_sub: QLabel | None = None
        self.close_btn: QToolButton | None = None
        self.chips: list[QLabel] = []
        col.addWidget(self._head)
        self._scroll = QScrollArea(objectName="Body")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rows = QWidget(objectName="Rows")
        self._rows_lay = QVBoxLayout(rows)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(8)
        self._rows_lay.addStretch(1)
        self._scroll.setWidget(rows)
        col.addWidget(self._scroll, 1)
        self._retext_chrome()
        LOCATION.changed.connect(self._moved)

    # ------------------------------------------------------------ the views

    def open_view(self, kind: str, key: str) -> None:
        """A search result clicked: its page, the history started over."""
        self._stack = [(kind, key)]
        self._draw()
        self.show_beside()

    def push(self, kind: str, key: str) -> None:
        """A row's click inside a page: one more view on the history."""
        self._stack.append((kind, key))
        self._draw()
        self.show_beside()

    def go_back(self) -> None:
        """‹ Back: the view under this one on the history."""
        if len(self._stack) > 1:
            self._stack.pop()
            self._draw()
            self.show_beside()

    def redraw(self) -> None:
        """The view drawn again — the language, the KB or the Cabs & Teleports switch changed under it."""
        if self._stack:
            self._draw()

    def _draw(self) -> None:
        """The last view on the history, built again from the KB and where the player is now: also the redraw
        when they move (the guide let go first — the view may no longer be on their map)."""
        self._clear_guide()
        self._clear_body()
        kind, key = self._stack[-1]
        body = {"npc": self._npc_body, "monster": self._monster_body,
                "item": self._item_body, "map": self._map_body, "sell": self._sell_body}.get(kind)
        if body is not None:
            body(key)
        self._fit()
        if self.isVisible():
            self.dock()
            self.update()

    def _npc_body(self, key: str) -> None:
        """The NPC's page: on the player's map, where it stands from them and the picture with its dot and
        theirs, the game's own minimap ringed; elsewhere, the map it stands on and the way there."""
        o = self._owner
        t, kb = o.t, o._kb
        name = self._name(key)
        self._set_head(key, name, [(t("search_is_npc"), "npc")], bidi.ltr_name(npc_line(kb, key), t.rtl))
        here = LOCATION.here
        place = gamelookup.npc_place(kb, key, here.map if here is not None else None)
        if place is None:
            return                                   # nowhere the KB can place it: the header says who it is
        g = routes.of(kb)
        if here is not None and place.map == here.map:
            who = t("npc_guide_door", place=g.name(place.inside)) if place.inside else name
            line = t(direction(g, place.map, here.spot, place.spot), name=who)
            if place.inside:
                line = t("npc_guide_inside", name=name, place=g.name(place.inside)) + " " + line
            self._line(line)
            self._picture(g.minimap(place.map), place.spot, not place.inside, here.spot)
            if place.spot is not None:
                self._guided = (place.map, place.spot, key)
                LOCATION.set_guide(self._guided)
            return
        self._line(t("lk_on_map", name=name, map=place.map_name or g.name(place.map)))
        self._picture(g.minimap(place.map), place.spot, not place.inside, None)
        self._way(place.map)

    def _monster_body(self, key: str) -> None:
        """The monster's page: its level and HP and EXP in the header, then every map it lives on (a click: that
        map's page), each with how many spawn there."""
        o = self._owner
        t, kb, rtl = o.t, o._kb, o.t.rtl
        name = self._name(key)
        m = combat.monster(kb, key)
        chips = _mob_chips(t, m.level, m.boss) if m is not None else [(t("search_is_monster"), "monster")]
        self._set_head(key, name, chips, bidi.ltr_name(monster_line(m), rtl) if m is not None else "")
        block = _Block(bidi.plain(t("mob_lives", name=name), rtl))
        maps = monster_maps(kb, key)
        if not maps:
            block.add(self._note(t("mob_none", name=name)))
        for map_key, mname, street, count in maps:
            row = _Row(bidi.ltr_name(mname, rtl), bidi.ltr_name(street, rtl) if street else "", t("lk_open_map"),
                       pic=o._pics.label(map_key, *MAP_PIC),
                       end=(bidi.ltr_block(str(count), rtl), bidi.plain(t("lk_spawns_unit"), rtl)), flat=True, rtl=rtl)
            row.clicked.connect(lambda _=False, k=map_key: self.push("map", k))
            block.add(row)
        self._add(block)

    def _item_body(self, key: str) -> None:
        """The item's page: its type and level chips and what an NPC pays in the header, then the monsters that
        drop it and the NPCs that sell it, each a click away (a dozen of each, then "and N more")."""
        o = self._owner
        t, kb, rtl = o.t, o._kb, o.t.rtl
        price = market.npc_prices(kb, key).sell_back
        self._set_head(key, self._name(key), self._item_chips(key),
                       bidi.plain(t("sell_npc_pays", n=f"{price:,}") if price else t("sell_no_npc_price"), rtl))
        drop, sell = gamelookup.item_sources(kb, key)
        if not drop and not sell:
            self._line(t("lk_no_sources"), muted=True)
            return
        if drop:
            block = _Block(bidi.plain(t("lk_dropped_by"), rtl))
            for d in drop[:SRC_CAP]:
                m = combat.monster(kb, d.key)
                row = _Row(bidi.ltr_name(d.name, rtl), "", t("lk_open_monster"), pic=o._pics.label(d.key, ROW_PIC),
                           chips=_boss_chip(t, m), end_chip=_lv_chip(t, d.level), flat=True, rtl=rtl)
                row.clicked.connect(lambda _=False, k=d.key: self.push("monster", k))
                block.add(row)
            self._more(block, len(drop))
            self._add(block)
        if sell:
            block = _Block(bidi.plain(t("lk_sold_by"), rtl))
            for s in sell[:SRC_CAP]:
                row = _Row(bidi.ltr_name(s.name, rtl), bidi.ltr_name(s.place, rtl) if s.place else "",
                           t("lk_open_npc") if s.key else "", clickable=bool(s.key),
                           pic=o._pics.label(s.key or "npc/", ROW_PIC), end=_price_end(t, s.price),
                           end_tip=t("sell_no_npc_price") if s.price is None else "", flat=True, rtl=rtl)
                if s.key:
                    row.clicked.connect(lambda _=False, k=s.key: self.push("npc", k))
                block.add(row)
            self._more(block, len(sell))
            self._add(block)

    def _sell_body(self, key: str) -> None:
        """Safe to sell?: the verdict by NiaMeowDB's list as a big chip and why (sell_state), then each quest that
        needs it (how many, and whether it comes back) and each recipe that uses it, what an NPC pays for one, the
        NPC shops that sell it back, and a way to its item page. A KB without the list says so, never "safe"."""
        o = self._owner
        t, kb, rtl = o.t, o._kb, o.t.rtl
        self._set_head(key, self._name(key), self._item_chips(key))
        sl = sitedata.sell_list(kb)
        if sl is None:
            self._line(t("sell_no_list"), muted=True)
            return
        state = sell_state(kb, key)
        word, tone, why = {"keep": ("sell_keep_word", "keep", "sell_keep_why"),
                           "safe": ("sell_safe_word", "safe", "sell_safe_why")}.get(
            state, ("sell_chip_no_price", "", "sell_unknown_why"))
        big = QLabel(bidi.plain(t(word), rtl), objectName="Verdict")
        big.setProperty("tone", tone)
        self._add(big, Qt.AlignLeft)
        self._line(t(why))
        need = sell_need(kb, key)
        if need and need.quests:
            block = _Block(bidi.plain(t("sell_quests_heading"), rtl))
            for qkey, name, qty, repeat in need.quests[:SRC_CAP]:
                chips = [(t("sell_qty", n=qty), "")] if qty else []
                if repeat:
                    chips.append((t("sell_repeat"), "repeat"))
                block.add(_Row(bidi.ltr_name(name, rtl), "", clickable=False,
                               pic=o._pics.label(qkey or "quest/", ROW_PIC), chips=chips, flat=True, rtl=rtl))
            self._more(block, len(need.quests))
            self._add(block)
        if need and need.recipes:
            block = _Block(bidi.plain(t("sell_recipes_heading"), rtl))
            for discipline, recipe, count in need.recipes[:SRC_CAP]:
                block.add(_Row(bidi.ltr_name(recipe, rtl), bidi.ltr_name(discipline, rtl) if discipline else "",
                               clickable=False, pic=o._pics.label(item_key(kb, recipe) or "item/", ROW_PIC),
                               chips=[(t("sell_qty", n=count), "")] if count else [], flat=True, rtl=rtl))
            self._more(block, len(need.recipes))
            self._add(block)
        if state != "unknown":            # (unknown: the verdict already says no NPC price is known)
            price = sell_price(kb, key)
            self._add(_Row(bidi.plain(t("sell_npc_price_label"), rtl), "", clickable=False,
                           end=_price_end(t, price), end_tip="" if price else t("sell_no_npc_price"), rtl=rtl))
        if need and need.shops:
            block = _Block(bidi.plain(t("sell_shops_heading"), rtl))
            block.add(_Row(bidi.ltr_name(", ".join(need.shops), rtl), bidi.plain(t("sell_rebuy"), rtl),
                           clickable=False, flat=True, rtl=rtl))
            self._add(block)
        row = _Row(bidi.plain(t("sell_open_item"), rtl), "", t("search_item_open"),
                   end=(bidi.plain("‹" if rtl else "›", rtl), ""), rtl=rtl)
        row.clicked.connect(lambda _=False: self.push("item", key))
        self._add(row)
        if sl.generated:
            self._line(t("sell_credit", date=sl.generated), muted=True)

    def _map_body(self, key: str) -> None:
        """The map's page: its street and levels in the header, its picture (the player's blue dot on it, while
        they're there), whether they're on it or the way there, and the monsters that spawn on it (a click: that
        monster's page)."""
        o = self._owner
        t, kb, rtl = o.t, o._kb, o.t.rtl
        g = routes.of(kb)
        mid = key.partition("/")[2]
        m = g.known.get(mid)
        lv = map_levels(kb, key)
        self._set_head(key, self._map_name(g, mid), [(_lv(t, lv), "")] if lv else [],
                       bidi.ltr_name(m.street, rtl) if m is not None and m.street else "")
        here = LOCATION.here
        on = here is not None and here.map == mid
        self._picture(g.minimap(mid), None, False, here.spot if on else None)
        if on:
            self._line(t("lk_here"))
        else:
            self._way(mid)
        mobs = gamelookup.map_monsters(kb, key)
        if mobs:
            block = _Block(bidi.plain(t("lk_map_monsters"), rtl))
            for mkey, mname, level, count in mobs:
                row = _Row(bidi.ltr_name(mname, rtl), "", t("lk_open_monster"), pic=o._pics.label(mkey, ROW_PIC),
                           chips=_boss_chip(t, combat.monster(kb, mkey)), end_chip=_lv_chip(t, level),
                           end=(bidi.ltr_block(str(count), rtl), bidi.plain(t("lk_spawns_unit"), rtl)),
                           flat=True, rtl=rtl)
                row.clicked.connect(lambda _=False, k=mkey: self.push("monster", k))
                block.add(row)
            self._add(block)

    def _way(self, to_map: str) -> None:
        """The way from the player's map to this one, step by step as the ◎ window draws it, in a block of its own:
        the step's number and map, what to do there, and the map's picture with the portal (orange) or the NPC
        (green) marked, the player's blue dot on the first; already there, no way known, or their map not read
        yet. On foot, or with cabs and NPC teleports when the toolbar's switch says so (settings["game_rides"])."""
        o = self._owner
        t = o.t
        here = LOCATION.here
        rides = bool(o._settings["game_rides"])
        way = gamelookup.way_to(o._kb, here.map if here is not None else None, to_map, rides=rides)
        block = _Block(bidi.plain(t("lk_way_rides" if rides else "lk_way_walk"), t.rtl))
        if not way.known:
            key = "lk_way_unknown_here" if way.here is None else "lk_way_none" if rides else "lk_way_none_walk"
            block.add(self._note(t(key)))
        elif not way.legs:
            block.add(self._note(t("lk_here")))
        else:
            g = routes.of(o._kb)
            for i, leg in enumerate(way.legs, 1):
                w = QLabel(objectName="StepMap")
                w.setText(bidi.plain(f"{i} · {bidi.ltr_block(self._map_name(g, leg.frm), t.rtl)}", t.rtl))
                w.setWordWrap(True)
                block.add(w)
                block.add(_text(mapview.route_says(t, g, leg), rich=True, t=t))
                you = here.spot if i == 1 and here is not None and here.map == leg.frm else None
                pic = self._picture_label(g.minimap(leg.frm), leg.spot, leg.kind != "portal", you, 2 * BLOCK_PAD)
                if pic is not None:
                    block.add(pic)
        self._add(block)

    def _item_chips(self, key: str) -> list[tuple[str, str]]:
        """An item's header chips: its type's words in the UI's language ("Equip", "One-Handed Sword";
        itemterms.kind) and an equip's required level."""
        t = self._owner.t
        e = self._owner._kb.get(key) or {}
        out = [(part, "") for part in _type_parts(str(e.get("type") or ""), t.lang)]
        lv = equip_level(e)
        if lv:
            out.append((_lv(t, (lv, lv)), ""))
        return out

    def _line(self, text: str, muted: bool = False, rich: bool = False) -> None:
        """A line of the page (_text); plain text wrapped for the UI's direction."""
        t = self._owner.t
        self._add(_text(text if rich else bidi.plain(text, t.rtl), muted, rich, t))

    def _note(self, text: str, muted: bool = True) -> QLabel:
        """A line inside a block, in from its edges like the rows' text."""
        w = _text(bidi.plain(text, self._owner.t.rtl), muted)
        w.setObjectName("BlockNote" if muted else "GuideLine")
        w.setContentsMargins(4, 0, 4, 0)
        return w

    def _more(self, block: _Block, n: int) -> None:
        if n > SRC_CAP:
            block.add(self._note(self._owner.t("lk_more", n=f"{n - SRC_CAP:,}")))

    def _picture(self, path, spot, npc: bool, you) -> None:
        """The map's picture with the thing's dot (an NPC's green, its building's door orange) and the
        player's blue one while they're on that map; no picture: nothing."""
        w = self._picture_label(path, spot, npc, you)
        if w is not None:
            self._add(w)

    def _picture_label(self, path, spot, npc: bool, you, inset: int = 0) -> QLabel | None:
        """_picture's label, `inset` narrower (a block's own margins); None when there is no picture."""
        pm = mapview.route_picture(path, spot, npc, you, cap_w=self.pic_cap() - inset)
        if pm.isNull():
            return None
        w = QLabel(alignment=Qt.AlignHCenter)
        w.setPixmap(pm)
        return w

    def _name(self, key: str) -> str:
        return str((self._owner._kb.get(key) or {}).get("name") or key.partition("/")[2])

    def _map_name(self, g, mid: str) -> str:
        """A map's name: its own page's, else the route graph's (routes.json names maps the KB has no page for)."""
        return str((self._owner._kb.get(f"map/{mid}") or {}).get("name") or g.name(mid))

    def _set_head(self, key: str, name: str, chips=(), sub: str = "") -> None:
        """The page's header card: the thing's picture (a map's wider), its name, its chips, a muted line under
        them (already wrapped for the UI's direction) and the ✕; ‹ Back over it when there is a view under this one.
        Built anew for every page: a kept card whose texts changed measured at the last page's height until the next
        event pass, and _fit cut the page's end off (offscreen render, 2026-10-11)."""
        o = self._owner
        t, rtl = o.t, o.t.rtl
        self.back.setVisible(len(self._stack) > 1)
        card = QFrame(objectName="HeadCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 10, 6, 10)
        row.setSpacing(12)
        self.pic = o._pics.label(key, *(HEAD_MAP if key.startswith("map/") else (HEAD_PIC, HEAD_PIC)))
        row.addWidget(self.pic, 0, Qt.AlignTop)
        if self.pic.pixmap().isNull():
            self.pic.hide()
        mid = QVBoxLayout()
        mid.setSpacing(4)
        self.title = QLabel(bidi.ltr_name(name, rtl), objectName="DetailName", wordWrap=True)
        mid.addWidget(self.title)
        self.chips = [_chip(text, tone, rtl) for text, tone in chips]
        if self.chips:
            box = QWidget()
            flow = FlowLayout(box, spacing=4)
            for c in self.chips:
                flow.addWidget(c)
            mid.addWidget(box)
        self.head_sub = QLabel(sub, objectName="Sub", wordWrap=True)
        mid.addWidget(self.head_sub)
        if not sub:
            self.head_sub.hide()
        mid.addStretch(1)
        row.addLayout(mid, 1)
        self.close_btn = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.setAccessibleName(t("lk_close"))
        self.close_btn.setToolTip(t("lk_close"))
        self.close_btn.clicked.connect(lambda _=False: self.hide())
        row.addWidget(self.close_btn, 0, Qt.AlignTop)
        if self._card is not None:
            self._head_lay.removeWidget(self._card)
            self._card.hide()
            self._card.deleteLater()
        self._card = card
        self._head_lay.addWidget(card)
        card.show()             # now, not on the next event pass: _fit measures the header at once

    def _add(self, w: QWidget, align: Qt.AlignmentFlag | None = None) -> None:
        """A piece of the page under the last one, across the page (align: at its own width, on that side)."""
        self._rows.append(w)
        if align is None:
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, w)
        else:
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, w, 0, align)
        w.show()            # now, not on the next event pass: _fit measures the new view at once (it measured 0
                            # and the window folded to its header after the first view, offscreen render)

    def _clear_body(self) -> None:
        while self._rows:
            w = self._rows.pop()
            self._rows_lay.removeWidget(w)
            w.hide()        # gone from the panel now: deleteLater left the old view drawn under the new one
            w.deleteLater()

    def _clear_guide(self) -> None:
        """Let the game-minimap ring go — only when it is still this window's (another window may have taken
        the guide over since it was set)."""
        if self._guided is not None:
            guide, self._guided = self._guided, None
            if LOCATION.guide == guide:
                LOCATION.set_guide(None)

    def _moved(self, _here: object = None) -> None:
        """The player moved (a Qt slot too: LOCATION.changed): the view of an NPC or a map is drawn again —
        on the map they're on the sentence and the blue dot follow them, another map and the guide is let go."""
        if self.isVisible() and self._stack and self._stack[-1][0] in ("npc", "map"):
            self._draw()

    # ------------------------------------------------------------ language

    def apply_language(self, t: I18n) -> None:
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._retext_chrome()
        self.redraw()

    def _retext_chrome(self) -> None:
        """‹ Back's words (the header card, its ✕ with it, is built in the language with each page)."""
        t = self._owner.t
        self.back.setText(bidi.plain(f"‹ {t('lk_back')}", t.rtl))
        self.back.setToolTip(t("lk_back"))

    # ------------------------------------------------------------ the panel

    def pic_cap(self) -> int:
        """The pictures' width: the panel's inside at the width the page is drawn for (the player's, else
        DETAIL_W), bar the scrollbar's groove."""
        return (self._saved_size() or QSize(DETAIL_W, 0)).width() - 2 * SIDE - SCROLLBAR_W - 2

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._owner._opacity)

    def dock(self) -> None:
        """Beside the search's panel, top edges lined up: on its left, or on its right when the search's screen
        has no room left of it; kept on that screen top to bottom."""
        dock_beside(self, self._owner)

    def show_beside(self) -> None:
        """Shown (or kept) docked beside the search, at the height its contents need."""
        self.dock()
        if not self.isVisible():
            self.show()
        set_capturable(self, self._owner._settings)
        self.update()

    def _saved_size(self) -> QSize | None:
        """The size the player dragged the window to (game_detail_size), kept inside the screens; None: never."""
        saved = self._owner._settings["game_detail_size"]
        if not (isinstance(saved, dict) and isinstance(saved.get("w"), int) and isinstance(saved.get("h"), int)):
            return None
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        return mapview.clamp_size(saved["w"], saved["h"], screens, QSize(DETAIL_MIN_W, DETAIL_MIN_H))

    def _fit(self) -> None:
        """The player's size, when they resized it (the body scrolls inside); else DETAIL_W wide and as tall as
        the page up to the cap, past it the body scrolls."""
        size = self._saved_size()
        if size is None:
            inner = DETAIL_W - 2 * SIDE
            head = self._need(self._head, inner)
            body = self._need(self._rows_lay, inner - 2)     # the scrollbar's groove eats a little of it
            size = QSize(DETAIL_W, max(DETAIL_MIN_H, min(DETAIL_H, TOP + BOTTOM + 8 + head + body)))
        self._fit_size = size
        if self.size() != size:
            self.resize(size)

    def resizeEvent(self, e) -> None:
        """The player dragging an edge: kept once it settles (_resized). The window's own sizing (_fit), and the
        resize Qt sends when it first shows, are not theirs."""
        if self.isVisible() and e.size() != self._fit_size:
            self._sized.start()
        super().resizeEvent(e)

    def _resized(self) -> None:
        """A drag settled: its size kept, the page drawn again at the new width (its pictures fill it, the scroll
        kept), and docked beside the search again (a left-edge drag pulled its right side off the search)."""
        self._owner._settings["game_detail_size"] = {"w": self.width(), "h": self.height()}
        self._fit_size = self.size()
        if self._stack:
            bar = self._scroll.verticalScrollBar()
            at = bar.value()
            self._draw()
            bar.setValue(at)
        if self.isVisible():
            self.dock()

    @staticmethod
    def _need(item, width: int) -> int:
        """The height a widget or layout needs at this width: the wrapped way when it has one, else its hint."""
        h = item.heightForWidth(width)
        return h if h >= 0 else item.sizeHint().height()

    def hideEvent(self, e) -> None:
        self._clear_guide()
        self._owner._page_closed()       # its row in the search no longer lit
        super().hideEvent(e)


class GameSearch(EdgeResize, QWidget):
    """The search window itself: over the game, in the NPCs window's dark glass, but taking the keyboard (the
    player types in it; Esc or the ✕ gives it back). The toolbar opens it in a mode — the same mode again is a
    toggle off — and each mode keeps its last query (Safe to sell? its switch too). A click opens the thing's own
    page in the detail window beside this one, the results still listed and the clicked row lit in the accent
    while its page shows. Its place is kept in game_search_geom."""

    mode_changed = Signal(str)          # the mode it shows, "" once hidden

    def __init__(self, kb: KnowledgeBase, settings: Settings, t: I18n):
        # not the NPCs window's _flags(): this one takes the keyboard, so no WindowDoesNotAcceptFocus
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)          # hovering the rim shows the resize cursor
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(DEFAULT_W, DEFAULT_H)
        self._kb = kb
        self._pics = _Pictures(kb)
        self._settings = settings
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._kind = KINDS[0]
        self._queries = {kind: "" for kind in KINDS}     # each mode's last query, kept while it switches
        self._sell_filter = SELL_FILTERS[0]              # Safe to sell?'s switch, kept while the modes switch
        self._hits: list[Hit] = []
        self._total = 0
        self._rows: list[_Row] = []
        self._open: tuple[str, str] | None = None        # the page the detail window shows, from a row here
        self._opacity = OPACITY_DEFAULT
        self._placed = False                 # geometry applied at least once: there is a place to remember
        self._under: QRect | None = None     # the toolbar's rect, where the window first opens
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._run)
        self._build()
        self._restyle()

    # ------------------------------------------------------------ the panel

    def _build(self) -> None:
        t, rtl = self.t, self.t.rtl
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(8)
        head = _Header()
        hrow = QHBoxLayout(head)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(8)
        self._title = QLabel(objectName="Title")
        self._close = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.clicked.connect(self._close_clicked)
        hrow.addWidget(self._title, 1)
        hrow.addWidget(self._close, 0, Qt.AlignTop)
        col.addWidget(head)
        self._box = _SearchBox(objectName="Search")
        self._box.setClearButtonEnabled(True)
        self._box.textChanged.connect(self._typed)
        col.addWidget(self._box)
        # Safe to sell?'s switch: every item, only those nothing needs, only those to keep (the mode's alone)
        self._filter = Segmented([(t(key), value) for key, value in zip(self._filter_keys(), SELL_FILTERS)],
                                 self._sell_filter, rtl)
        for b in self._filter.group.buttons():
            b.setFocusPolicy(Qt.NoFocus)          # the keyboard stays in the box
        self._filter.set_label(t("sell_filter_a11y"))
        self._filter.changed.connect(self._filter_set)
        self._filter.hide()
        col.addWidget(self._filter)
        self._status = QLabel(objectName="Status")       # type more / nothing found / and N more
        self._status.setWordWrap(True)
        col.addWidget(self._status)
        self._scroll = QScrollArea(objectName="Body")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rows = QWidget(objectName="Rows")
        self._rows_lay = QVBoxLayout(rows)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(4)
        self._rows_lay.addStretch(1)
        self._scroll.setWidget(rows)
        col.addWidget(self._scroll, 1)
        self._retext_chrome()
        self._retext_status()
        self._detail = DetailWindow(self)             # the clicked result's page, a window of its own

    @staticmethod
    def _filter_keys() -> tuple[str, str, str]:
        """The switch's words, in SELL_FILTERS' order."""
        return "sell_filter_all", "sell_safe_word", "sell_keep_word"

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    def _restyle(self) -> None:
        """The accent the settings pick, in both windows' style sheets (their glass is painted in the tint by
        _paint_glass); set again only when it changed — a restyle re-polishes every row."""
        sheet = qss(self._settings)
        for w in (self, self._detail):
            if sheet != w.styleSheet():
                w.setStyleSheet(sheet)

    # ------------------------------------------------------------ opening, modes

    def open(self, kind: str) -> None:
        """Show the window in this mode (its box focused), or hide it: the mode it already shows, asked for
        again, is a toggle. Another mode while it shows only switches, each one's last query kept."""
        if kind not in KINDS:
            return
        if self.isVisible() and self._kind == kind:
            self.hide()          # hideEvent says mode_changed("")
            return
        self._read_opacity()
        self._set_kind(kind)
        if not self.isVisible():
            self._place()
            self.show()
            set_capturable(self, self._settings)
        self.raise_()
        self.activateWindow()
        self._box.setFocus()
        self.update()
        self.mode_changed.emit(self._kind)

    def _read_opacity(self) -> None:
        """The look the ⚙ panel and Settings pick, taken again on every open and by the app whenever it changes:
        the NPCs window's opacity for the glass (the same dark glass, as see-through as the player asked), and the
        accent in both windows' style sheets."""
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT
        self._restyle()

    def _set_kind(self, kind: str) -> None:
        self._kind = kind
        self._box.setText(self._queries[kind])     # the mode's own last query, back in the box
        self._retext_chrome()
        self._run()

    def _typed(self, _text: str) -> None:
        self._typing.start()

    def _filter_set(self, which: str) -> None:
        """Safe to sell?'s switch: the rows narrowed to it at once (kept while the modes switch), its segment lit."""
        self._sell_filter = which if which in SELL_FILTERS else SELL_FILTERS[0]
        self._filter.set_value(self._sell_filter)
        self._run()

    def _run(self) -> None:
        """The rows for what is typed (the typing debounce's work too), from the top. Safe to sell?'s switch shows
        while the KB has the list to narrow by (without one every item is listed, nothing known either way)."""
        q = self._box.text()
        self._queries[self._kind] = q
        listed = self._kind == "sell" and sitedata.sell_list(self._kb) is not None
        self._filter.setVisible(listed)
        self._hits, self._total = search(self._kb, self._kind, q, LIMIT, self._sell_filter if listed else "all")
        self._rebuild_rows()
        self._retext_status()
        self._scroll.verticalScrollBar().setValue(0)

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._pics = _Pictures(kb)
        self._detail.redraw()     # the page shown is drawn again: its names and places may all have moved
        self._run()      # every list is read again (each one is built once per KB)

    def rides_changed(self) -> None:
        """The toolbar's Cabs & Teleports switch flipped: the way on the page shown is found again."""
        self._detail.redraw()

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._retext_chrome()
        self._rebuild_rows()
        self._retext_status()
        self._detail.apply_language(t)     # its page drawn again, its words again

    # ------------------------------------------------------------ text (all of it, for apply_language)

    def _retext_chrome(self) -> None:
        t, rtl = self.t, self.t.rtl
        self._title.setText(bidi.plain(t(f"search_{self._kind}_title"), rtl))
        self._close.setAccessibleName(t("search_close"))
        self._close.setToolTip(t("search_close"))
        self._box.setPlaceholderText(bidi.plain(t(f"search_{self._kind}_ph"), rtl))
        for i, key in enumerate(self._filter_keys()):
            self._filter.set_text(i, bidi.plain(t(key), rtl))
        self._filter.set_label(t("sell_filter_a11y"))

    def _retext_status(self) -> None:
        t, rtl = self.t, self.t.rtl
        q = self._box.text()
        if self._kind == "sell" and sitedata.sell_list(self._kb) is None:
            text = t("sell_no_list")          # no chips, no switch to trust: say why
        elif len(q.replace(" ", "")) < 2 and self._kind not in BROWSE:
            text = t("search_type_more")
        elif not self._hits:
            text = t("search_none", q=q) if q.strip() else ""     # (the maps, before the KB's tables are built)
        elif self._total > len(self._hits):
            text = t("search_more", n=f"{self._total - len(self._hits):,}")    # "and 5,160 more"
        else:
            text = ""
        self._status.setText(bidi.plain(text, rtl) if text else "")

    # ------------------------------------------------------------ the rows

    def _add(self, w: _Row) -> None:
        self._rows.append(w)
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, w)

    def _clear_rows(self) -> None:
        while self._rows:
            w = self._rows.pop()
            self._rows_lay.removeWidget(w)
            w.hide()        # gone now: deleteLater left the 50 maps drawn under the narrowed ones (seen offscreen)
            w.deleteLater()

    def _rebuild_rows(self) -> None:
        t = self.t
        tips = {"monster": t("search_monster_open"), "npc": t("search_npc_open"), "item": t("search_item_open"),
                "sell": t("search_sell_open"), "map": t("search_map_open")}
        self._clear_rows()
        for hit in self._hits:
            row = self._hit_row(hit, tips.get(hit.kind or self._kind, ""))
            row.clicked.connect(lambda _=False, h=hit: self._picked(h))
            self._add(row)
        self._light()

    def _hit_row(self, hit: Hit, tip: str) -> _Row:
        """A result's row in its mode's look: a monster's level chip (and Boss) over its HP and EXP; an NPC's chip
        over its role and map; a map's minimap, levels and street; an item's type, an equip's level and what an
        NPC pays; Safe to sell?'s why-keep chips (or Safe to sell) and what an NPC pays."""
        t, rtl, kb = self.t, self.t.rtl, self._kb
        page = hit.kind or self._kind
        chips: list[tuple[str, str]] = []
        sub, end, end_tip, below = "", None, "", False
        if page == "monster":
            chips = _mob_chips(t, hit.lv[0] if hit.lv else 0, hit.boss)
            sub = bidi.ltr_name(hit.sub, rtl) if hit.sub else ""
        elif page == "npc":
            chips = [(t("search_is_npc"), "npc")]
            sub = bidi.ltr_name(hit.sub, rtl) if hit.sub else ""
        elif page == "map":
            chips = [(_lv(t, hit.lv), "")] if hit.lv else []
            sub = bidi.ltr_name(hit.sub, rtl) if hit.sub else ""
        elif page == "sell":
            chips, below = sell_chips(t, kb, hit.key), True
            end = _price_end(t, sell_price(kb, hit.key))
        else:
            chips = [(_lv(t, hit.lv), "")] if hit.lv else []
            word = _type_word(hit.sub, t.lang)
            sub = bidi.plain(word, rtl) if word else ""
            end = _price_end(t, market.npc_prices(kb, hit.key).sell_back)
        if end is not None and end[0] == NO_NUM:
            end_tip = t("sell_no_npc_price")
        pic = self._pics.label(hit.key, *MAP_PIC) if page == "map" else self._pics.label(hit.key, ROW_PIC)
        row = _Row(bidi.ltr_name(hit.name, rtl), sub, tip, pic=pic, chips=chips, end=end, end_tip=end_tip,
                   chips_below=below, rtl=rtl)
        row.page = (page, hit.key)
        return row

    def _picked(self, hit: Hit) -> None:
        """A result clicked: its own page in the detail window beside this one, the history started over (the
        results stay listed here, the clicked one lit)."""
        self._open = (hit.kind or self._kind, hit.key)
        self._detail.open_view(*self._open)
        self._light()

    def _page_closed(self) -> None:
        """The detail window hid (its ✕, or with this one): no row is lit any more."""
        self._open = None
        self._light()

    def _light(self) -> None:
        """The row whose page the detail window shows lit in the accent, every other one not."""
        for row in self._rows:
            row.set_selected(row.page is not None and row.page == self._open)

    def _close_clicked(self, _=False) -> None:
        """The ✕: hidden (the toolbar's button lets go of its highlight; nothing is turned off)."""
        self.hide()

    # ------------------------------------------------------------ place, move, resize

    def place_under(self, rect: QRect) -> None:
        """Where the window opens the first time (no place kept yet): just under the toolbar's rect."""
        self._under = QRect(rect) if isinstance(rect, QRect) else None

    def _place(self) -> None:
        """Where the window opens: where the player left it, when that is still on a screen (a monitor since
        unplugged: under the toolbar again)."""
        screens = [s.geometry() for s in QGuiApplication.screens()]
        saved = self._settings["game_search_geom"]
        size = QSize(self.width(), self.height())
        if isinstance(saved, dict) and isinstance(saved.get("w"), int) and isinstance(saved.get("h"), int):
            size = mapview.clamp_size(saved["w"], saved["h"], screens, QSize(MIN_W, MIN_H))
        spot = mapview.saved_spot(saved, size, screens)
        self.setGeometry(QRect(spot if spot is not None else self._default_spot(size), size))
        self._placed = True

    def _default_spot(self, size: QSize) -> QPoint:
        """Just under the toolbar (the searches are its buttons), kept on that screen; without one — the bar
        never placed, or gone — the primary screen's top-left corner."""
        primary = QGuiApplication.primaryScreen().availableGeometry()
        screens = [s.geometry() for s in QGuiApplication.screens()]
        under = self._under
        if isinstance(under, QRect) and not under.isNull():
            screen = next((g for g in screens if g.intersects(under)), primary)
            x = min(max(under.left(), screen.left()), max(screen.left(), screen.right() - size.width() + 1))
            y = min(max(under.bottom() + 1 + GAP, screen.top()), max(screen.top(), screen.bottom() - size.height() + 1))
            return QPoint(x, y)
        return primary.topLeft() + QPoint(SHADOW + 4, SHADOW + 4)

    def moveEvent(self, e) -> None:
        self._geom.start()
        if self._detail.isVisible():
            self._detail.dock()      # the detail moves with the search
        super().moveEvent(e)

    def resizeEvent(self, e) -> None:
        self._geom.start()
        if self._detail.isVisible():
            self._detail.dock()      # a left-edge drag moves the search's left side
        super().resizeEvent(e)

    def hideEvent(self, e) -> None:
        self._typing.stop()       # nothing fires once it is hidden: the rows are not read again behind the player's back
        self._detail.hide()       # the page goes with it, its guide let go
        if self._placed:             # hidden (the toolbar, Esc, the ✕): keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        self.mode_changed.emit("")
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self._settings["game_search_geom"] = {"x": self.x(), "y": self.y(), "w": self.width(), "h": self.height()}
