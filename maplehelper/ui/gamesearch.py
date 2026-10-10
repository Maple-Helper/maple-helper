"""The in-game toolbar's search window (ui/gametoolbar.py opens it): the KB's monsters, NPCs and items, typed
down to a row. A click opens the thing's own page in the detail window docked beside the search — an NPC: where
it stands and the way there; a monster: where it lives; an item: who drops and who sells it; a map: its
monsters — with a way back through what was opened. The Safe to sell? search lists the same items, each with
whether it's safe to sell (NiaMeowDB's list of what current quests and recipes need, sitedata.sell_list), and opens
what needs it. The search is the one window over the game that takes the keyboard (the player is typing in it);
the detail never does; Esc or the ✕ hides it, and the minimap reads never see either. The search itself is plain
data: Hit / search / monster_maps need no Qt."""
from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from .. import availability, bidi, combat, gamelookup, market, routes, sitedata, tables
from ..i18n import I18n
from ..kb import KnowledgeBase, memo
from ..store import Settings
from . import mapview, theme
from .glass import SHADOW, EdgeResize
from .location import LOCATION
from .npcoverlay import (BOTTOM, FILL1, FILL2, MUTED, OPACITY_DEFAULT, OPACITY_MAX, OPACITY_MIN, SIDE, TEXT, TOP,
                         _Header, _exclude_from_capture, _flags, _paint_glass, direction, dock_beside)

KINDS = ("monster", "npc", "item", "sell")   # the searches the toolbar offers ("sell": the items, safe to sell?)
MIN_W, MIN_H = 260, 300             # the smallest the player can drag the window to
DEFAULT_W, DEFAULT_H = 320, 420     # what opens under the toolbar before it is ever resized
GAP = 8                             # between the toolbar and the window that opens under it
LIMIT = 50                          # rows shown before the "and N more" line
TYPE_DEBOUNCE_MS = 150              # a pause in the typing before the rows are read again
GEOM_DEBOUNCE_MS = 400              # a move/resize saves the window's place once it settles
DETAIL_W = 300                     # the detail window's width; its height follows what it shows
DETAIL_H = 520                     # the height it grows to, then the body scrolls
PIC = 36                           # the header's small picture of the thing whose page it is
SRC_CAP = 12                       # droppers and sellers listed, then "and N more"

# the same dark glass as the NPCs window (its own panel, whatever theme the app's windows are in)
QSS = f"""
#Title {{ color: {TEXT}; font-weight: 600; }}
#Status {{ color: {MUTED}; }}
#Lives {{ color: {MUTED}; font-size: 11px; font-weight: 600; padding: 6px 2px 0 2px; }}
#GuideLine {{ color: {TEXT}; }}
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
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.25); border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


# ---------------------------------------------------------------- the search itself (no Qt in it)

@dataclass(frozen=True)
class Hit:
    """One row of a search: the thing's KB key (what a click opens), its name, and the grey line under it."""
    key: str
    name: str
    sub: str


def _fold(name: str) -> str:
    """A name or a typed query as they are compared: lower case, one space between the words."""
    return re.sub(r"\s+", " ", str(name).strip().lower())


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
    return [Hit(m.key, m.name, f"Lv. {m.level}")
            for m in sorted(best.values(), key=lambda m: (m.level, m.name))]


def _npc_hits(kb) -> list[Hit]:
    """Every NPC in the game (the KB's own npc_open rule), the grey line the map it stands on and the street,
    as the KB's npcs table has them; an NPC the table has no place for keeps an empty one."""
    where: dict[str, str] = {}
    for r in tables.rows(kb, "npcs"):
        if r.get("key"):
            where[str(r["key"])] = " · ".join(x for x in (str(r.get("map") or ""), str(r.get("street") or "")) if x)
    open_ = availability.of(kb)
    return sorted((Hit(k, e["name"], where.get(k, ""))
                   for k, e in kb.entities.items()
                   if e.get("category") == "npc" and str(e.get("name") or "").strip() and open_.npc_open(k)),
                  key=lambda h: _fold(h.name))


def _item_hits(kb) -> list[Hit]:
    """Every item the item picker lists (tools.item_rows' rule, the Cash Shop's own pets aside), the grey line
    its type."""
    out: dict[str, Hit] = {}
    for k, e in kb.entities.items():
        name = str(e.get("name") or "").strip()
        if e.get("category") != "item" or not name or name in out:
            continue
        if str(e.get("type") or "").startswith("Cash") and sitedata.untradeable(kb, k):
            continue          # a pet: the Cash Shop's NX only, no price to look up (the owner)
        out[name] = Hit(k, name, str(e.get("type") or ""))
    return sorted(out.values(), key=lambda h: _fold(h.name))


_BUILDERS = {"monster": _monster_hits, "npc": _npc_hits, "item": _item_hits}


def _source(kb, kind: str) -> list[Hit]:
    """One kind's every row, built once per KB (a keystroke only ranks them; the item list alone reads ~650
    pages). A list made while the KB's tables weren't ready (still building at start-up) isn't kept: it would have
    left every NPC without its map for the whole session. Safe to sell? searches the items' own list."""
    kind = "item" if kind == "sell" else kind
    cache = memo(kb, "_game_search")
    if kind not in cache:
        hits = _BUILDERS[kind](kb)
        if kind != "npc" or tables.ready(kb):
            cache[kind] = hits
        return hits
    return cache[kind]


def search(kb, kind: str, query: str, limit: int = 50) -> tuple[list[Hit], int]:
    """Every row of this kind whose name has all the query's words in it — case aside, the words in any order —
    up to `limit` of them, ranked: the exact name, then the names the query starts, then the rest; ties by
    name. The pair's other half is how many matched in all (the "and N more" line). A query under two letters
    (spaces aside) is still being typed: nothing yet."""
    q = _fold(query or "")
    if len(q.replace(" ", "")) < 2:
        return [], 0
    words = q.split(" ")
    ranked: list[list[Hit]] = [[], [], []]      # the exact name, a name the query starts, the rest
    for hit in _source(kb, kind):
        low = _fold(hit.name)
        if not all(w in low for w in words):
            continue
        ranked[0 if low == q else 1 if low.startswith(q) else 2].append(hit)
    hits = [h for bucket in ranked for h in sorted(bucket, key=lambda h: (_fold(h.name), h.name))]
    return hits[:max(0, int(limit))], len(hits)


def sell_verdict(t, kb, key: str) -> str:
    """A Safe to sell? row's grey line: "Keep · quests 2 · recipes 1" for an item on NiaMeowDB's list, "Safe to
    sell · 12 mesos" (an NPC's price, when one is known) off it, "" while the KB has no list."""
    sl = sitedata.sell_list(kb)
    if sl is None:
        return ""
    need = sl.needs.get(key)
    if need is not None:
        parts = [t("sell_keep_word")]
        if need.quests:
            parts.append(t("sell_quests_n", n=len(need.quests)))
        if need.recipes:
            parts.append(t("sell_recipes_n", n=len(need.recipes)))
        return " · ".join(parts)
    price = market.npc_prices(kb, key).sell_back
    return " · ".join([t("sell_safe_word")] + ([t("lk_price", n=f"{price:,}")] if price else []))


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


# ---------------------------------------------------------------- the window

class _SearchBox(QLineEdit):
    """The typing box: Esc hides the window (the keyboard goes back to the game at once)."""

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.window().hide()
            return
        super().keyPressEvent(e)


class _Row(QFrame):
    """One line of the list: a bold-ish name over a muted grey one (its type, where it stands, how many spawn), in
    the colours of the NPCs window's rows; clickable=False: a fact in the same look (a quest that needs an item)."""

    clicked = Signal()

    def __init__(self, name: str, sub: str, tip: str = "", clickable: bool = True):
        super().__init__(objectName="Row")
        if clickable:
            self.setAttribute(Qt.WA_Hover, True)          # its QSS :hover
            self.setCursor(Qt.PointingHandCursor)
        if tip:
            self.setToolTip(tip)
            self.setAccessibleName(tip)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(1)
        # both lines wrap: "Construction Site North of Kerning City  ·  Victoria Road" ran under the scrollbar
        self.name = QLabel(name, objectName="Name", wordWrap=True)
        lay.addWidget(self.name)
        self.sub = QLabel(sub, objectName="Sub", wordWrap=True) if sub else None
        if self.sub is not None:
            lay.addWidget(self.sub)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class DetailWindow(QWidget):
    """A clicked result's own page, in the NPCs window's guide's own look: a see-through always-on-top panel
    docked beside the search (its left, or its right where the screen has no room) that moves and hides with it
    and never takes the keyboard from the game. Its views are a history — a row's click pushes one, ‹ Back pops,
    a new search click starts it over. While it shows an NPC on the player's map it guides them: the sentence
    and the picture's blue dot follow the player, and the game's own minimap rings the spot (LOCATION.guide)."""

    def __init__(self, owner: GameSearch):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedWidth(DETAIL_W)
        self.setStyleSheet(QSS)
        self._owner = owner
        self._excluded = False
        self._stack: list[tuple[str, str]] = []     # each view pushed, the last one shown
        self._guided: tuple | None = None           # the guide this window set, None while it set none
        self._rows: list[QWidget] = []
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(8)
        self._head = QWidget()
        hrow = QHBoxLayout(self._head)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(8)
        self.back = QPushButton(objectName="Back")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.setFocusPolicy(Qt.NoFocus)
        self.back.clicked.connect(lambda _=False: self.go_back())
        self.pic = QLabel()
        self.title = QLabel(objectName="Title")
        self.title.setWordWrap(True)
        self.close_btn = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.clicked.connect(lambda _=False: self.hide())
        hrow.addWidget(self.back, 0, Qt.AlignTop)
        hrow.addWidget(self.pic, 0, Qt.AlignTop)
        hrow.addWidget(self.title, 1)
        hrow.addWidget(self.close_btn, 0, Qt.AlignTop)
        col.addWidget(self._head)
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
        self._set_head(key, name)
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
        """The monster's page: its stats, every map it lives on (a click: that map's page)."""
        o = self._owner
        t, kb = o.t, o._kb
        name = self._name(key)
        self._set_head(key, name)
        m = combat.monster(kb, key)
        if m is not None:
            self._line(t("mob_stats", lv=m.level, hp=f"{m.hp:,}", exp=f"{m.exp:,}"), muted=True)
        self._heading(t("mob_lives", name=name))
        maps = monster_maps(kb, key)
        if not maps:
            self._line(t("mob_none", name=name), muted=True)
        for map_key, mname, street, count in maps:
            row = _Row(bidi.ltr_name(f"{mname}  ·  {street}" if street else mname, t.rtl),
                       bidi.plain(t("mob_spawns", n=count), t.rtl), t("lk_open_map"))
            row.clicked.connect(lambda _=False, k=map_key: self.push("map", k))
            self._add(row)

    def _item_body(self, key: str) -> None:
        """The item's page: its type, the monsters that drop it and the NPCs that sell it, each a click away
        (a dozen of each, then "and N more")."""
        o = self._owner
        t, kb = o.t, o._kb
        self._set_head(key, self._name(key))
        what = str((kb.get(key) or {}).get("type") or "")
        if what:
            self._line(what, muted=True)
        drop, sell = gamelookup.item_sources(kb, key)
        if not drop and not sell:
            self._line(t("lk_no_sources"), muted=True)
            return
        if drop:
            self._heading(t("lk_dropped_by"))
            for d in drop[:SRC_CAP]:
                row = _Row(bidi.ltr_name(d.name, t.rtl), f"Lv. {d.level}", t("lk_open_monster"))
                row.clicked.connect(lambda _=False, k=d.key: self.push("monster", k))
                self._add(row)
            self._more(len(drop))
        if sell:
            self._heading(t("lk_sold_by"))
            for s in sell[:SRC_CAP]:
                price = t("lk_price", n=f"{s.price:,}") if s.price is not None else ""
                row = _Row(bidi.ltr_name(s.name, t.rtl),
                           bidi.plain(" · ".join(x for x in (price, s.place) if x), t.rtl), t("lk_open_npc"))
                row.clicked.connect(lambda _=False, k=s.key: self.push("npc", k))
                self._add(row)
            self._more(len(sell))

    def _sell_body(self, key: str) -> None:
        """Safe to sell?: the verdict by NiaMeowDB's list (on it: a current quest or recipe needs it), what an NPC
        pays, then each quest and recipe that needs it, the NPC shops that sell it back, and a way to its item
        page. A KB without the list says so, never "safe"."""
        o = self._owner
        t, kb, rtl = o.t, o._kb, o.t.rtl
        self._set_head(key, self._name(key))
        sl = sitedata.sell_list(kb)
        if sl is None:
            self._line(t("sell_no_list"), muted=True)
            return
        need = sl.needs.get(key)
        self._line(t("sell_keep_line" if need else "sell_safe_line"))
        price = (need.price if need else 0) or market.npc_prices(kb, key).sell_back
        self._line(t("sell_npc_pays", n=f"{price:,}") if price else t("sell_no_npc_price"), muted=True)
        if need and need.quests:
            self._heading(t("sell_quests_heading"))
            for _qkey, name, qty, repeat in need.quests[:SRC_CAP]:
                many = t("sell_qty", n=qty) if qty else ""
                sub = " · ".join(x for x in (many, t("sell_repeat") if repeat else "") if x)
                self._add(_Row(bidi.ltr_name(name, rtl), bidi.plain(sub, rtl) if sub else "", clickable=False))
            self._more(len(need.quests))
        if need and need.recipes:
            self._heading(t("sell_recipes_heading"))
            for discipline, recipe, count in need.recipes[:SRC_CAP]:
                many = t("sell_qty", n=count) if count else ""
                sub = " · ".join(x for x in (discipline, many) if x)
                self._add(_Row(bidi.ltr_name(recipe, rtl), bidi.plain(sub, rtl) if sub else "", clickable=False))
            self._more(len(need.recipes))
        if need and need.shops:
            self._heading(t("sell_shops_heading"))
            self._add(_Row(bidi.ltr_name(", ".join(need.shops), rtl), bidi.plain(t("sell_rebuy"), rtl),
                           clickable=False))
        row = _Row(bidi.plain(t("sell_open_item"), rtl), "", t("search_item_open"))
        row.clicked.connect(lambda _=False: self.push("item", key))
        self._add(row)
        if sl.generated:
            self._line(t("sell_credit", date=sl.generated), muted=True)

    def _map_body(self, key: str) -> None:
        """The map's page: its street and its picture (the player's blue dot on it, while they're there),
        whether they're on it or the way there, and the monsters that spawn on it (a click: that monster's
        page)."""
        o = self._owner
        t, kb = o.t, o._kb
        g = routes.of(kb)
        mid = key.partition("/")[2]
        self._set_head(key, self._map_name(g, mid))
        m = g.known.get(mid)
        if m is not None and m.street:
            self._line(m.street, muted=True)
        here = LOCATION.here
        self._picture(g.minimap(mid), None, False, here.spot if here is not None and here.map == mid else None)
        if here is not None and here.map == mid:
            self._line(t("lk_here"))
        else:
            self._way(mid)
        for mkey, mname, level, count in gamelookup.map_monsters(kb, key):
            row = _Row(bidi.ltr_name(mname, t.rtl),
                       bidi.plain(f"Lv. {level} · {t('mob_spawns', n=count)}", t.rtl), t("lk_open_monster"))
            row.clicked.connect(lambda _=False, k=mkey: self.push("monster", k))
            self._add(row)

    def _way(self, to_map: str) -> None:
        """The way from the player's map to this one, a line per step; already there, no way known, or their
        map not read yet. On foot, or with cabs and NPC teleports when the toolbar's switch says so
        (settings["game_rides"])."""
        o = self._owner
        t = o.t
        here = LOCATION.here
        rides = bool(o._settings["game_rides"])
        way = gamelookup.way_to(o._kb, here.map if here is not None else None, to_map, rides=rides)
        self._heading(t("lk_way_rides" if rides else "lk_way_walk"))
        if not way.known:
            key = "lk_way_unknown_here" if way.here is None else "lk_way_none" if rides else "lk_way_none_walk"
            self._line(t(key), muted=True)
        elif not way.legs:
            self._line(t("lk_here"), muted=True)
        else:
            g = routes.of(o._kb)
            for leg in way.legs:
                self._line(mapview.route_says(t, g, leg), rich=True)

    def _line(self, text: str, muted: bool = False, rich: bool = False) -> None:
        """A line of the page; `rich`: a route step, its **bold** names drawn bold as the map window draws them
        (mapview.says_html), not shown as asterisks."""
        t = self._owner.t
        w = QLabel(objectName="Status" if muted else "GuideLine")
        if rich:
            w.setTextFormat(Qt.RichText)
            w.setText(mapview.says_html(t, text))
        else:
            w.setText(bidi.plain(text, t.rtl))
        w.setWordWrap(True)
        self._add(w)

    def _heading(self, text: str) -> None:
        w = QLabel(bidi.plain(text, self._owner.t.rtl), objectName="Lives")
        w.setWordWrap(True)
        self._add(w)

    def _more(self, n: int) -> None:
        if n > SRC_CAP:
            self._line(self._owner.t("lk_more", n=n - SRC_CAP), muted=True)

    def _picture(self, path, spot, npc: bool, you) -> None:
        """The map's picture with the thing's dot (an NPC's green, its building's door orange) and the
        player's blue one while they're on that map; no picture: nothing."""
        pm = mapview.route_picture(path, spot, npc, you, cap_w=self.pic_cap())
        if not pm.isNull():
            w = QLabel(alignment=Qt.AlignHCenter)
            w.setPixmap(pm)
            self._add(w)

    def _name(self, key: str) -> str:
        return str((self._owner._kb.get(key) or {}).get("name") or key.partition("/")[2])

    def _map_name(self, g, mid: str) -> str:
        """A map's name: its own page's, else the route graph's (routes.json names maps the KB has no page for)."""
        return str((self._owner._kb.get(f"map/{mid}") or {}).get("name") or g.name(mid))

    def _set_head(self, key: str, name: str) -> None:
        """The page's header: the thing's name and a small picture of it, ‹ Back when there is a view under
        this one."""
        pm = QPixmap()
        p = self._owner._kb.picture(key)
        if p is not None:
            pm = QPixmap(str(p))
        self.back.setVisible(len(self._stack) > 1)
        self.title.setText(bidi.ltr_name(name, self._owner.t.rtl))
        if pm.isNull():
            self.pic.clear()
            self.pic.hide()
        else:
            self.pic.setPixmap(pm.scaled(PIC, PIC, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.pic.show()

    def _add(self, w: QWidget) -> None:
        self._rows.append(w)
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, w)
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
        t = self._owner.t
        self.back.setText(bidi.plain(t("lk_back"), t.rtl))
        self.back.setToolTip(t("lk_back"))
        self.close_btn.setAccessibleName(t("lk_close"))
        self.close_btn.setToolTip(t("lk_close"))

    # ------------------------------------------------------------ the panel

    def pic_cap(self) -> int:
        """The pictures' width: the panel's inside, bar the scrollbar's groove."""
        return DETAIL_W - 2 * SIDE - 4

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
        if not self._excluded:
            self._excluded = True
            _exclude_from_capture(self)
        self.update()

    def _fit(self) -> None:
        """The window's height follows its body up to the cap; past it, the body scrolls."""
        inner = DETAIL_W - 2 * SIDE
        head = self._need(self._head, inner)
        body = self._need(self._rows_lay, inner - 2)     # the scrollbar's groove eats a little of it
        self.setFixedHeight(min(DETAIL_H, TOP + BOTTOM + 8 + head + body))

    @staticmethod
    def _need(item, width: int) -> int:
        """The height a widget or layout needs at this width: the wrapped way when it has one, else its hint."""
        h = item.heightForWidth(width)
        return h if h >= 0 else item.sizeHint().height()

    def hideEvent(self, e) -> None:
        self._clear_guide()
        super().hideEvent(e)


class GameSearch(EdgeResize, QWidget):
    """The search window itself: over the game, in the NPCs window's dark glass, but taking the keyboard (the
    player types in it; Esc or the ✕ gives it back). The toolbar opens it in a mode — the same mode again is a
    toggle off — and each mode keeps its last query. A click opens the thing's own page in the detail window
    beside this one, the results still listed. Its place is kept in game_search_geom."""

    mode_changed = Signal(str)          # the mode it shows, "" once hidden

    def __init__(self, kb: KnowledgeBase, settings: Settings, t: I18n):
        # not the NPCs window's _flags(): this one takes the keyboard, so no WindowDoesNotAcceptFocus
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)          # hovering the rim shows the resize cursor
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(DEFAULT_W, DEFAULT_H)
        self._kb = kb
        self._settings = settings
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._kind = KINDS[0]
        self._queries = {kind: "" for kind in KINDS}     # each mode's last query, kept while it switches
        self._hits: list[Hit] = []
        self._total = 0
        self._rows: list[QWidget] = []
        self._opacity = OPACITY_DEFAULT
        self._placed = False                 # geometry applied at least once: there is a place to remember
        self._excluded = False
        self._under: QRect | None = None     # the toolbar's rect, where the window first opens
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._run)
        self.setStyleSheet(QSS)
        self._build()

    # ------------------------------------------------------------ the panel

    def _build(self) -> None:
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

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

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
            if not self._excluded:
                self._excluded = True
                _exclude_from_capture(self)
        self.raise_()
        self.activateWindow()
        self._box.setFocus()
        self.update()
        self.mode_changed.emit(self._kind)

    def _read_opacity(self) -> None:
        """The NPCs window's own slider sets it: the same dark glass, as see-through as the player asked."""
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT

    def _set_kind(self, kind: str) -> None:
        self._kind = kind
        self._box.setText(self._queries[kind])     # the mode's own last query, back in the box
        self._retext_chrome()
        self._run()

    def _typed(self, _text: str) -> None:
        self._typing.start()

    def _run(self) -> None:
        """The rows for what is typed (the typing debounce's work too)."""
        q = self._box.text()
        self._queries[self._kind] = q
        self._hits, self._total = search(self._kb, self._kind, q, LIMIT)
        self._rebuild_rows()
        self._retext_status()

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
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

    def _retext_status(self) -> None:
        t, rtl = self.t, self.t.rtl
        q = self._box.text()
        if len(q.replace(" ", "")) < 2:
            text = t("search_type_more")
        elif not self._hits:
            text = t("search_none", q=q)
        elif self._total > len(self._hits):
            text = t("search_more", n=self._total - len(self._hits))
        else:
            text = ""
        self._status.setText(bidi.plain(text, rtl) if text else "")

    # ------------------------------------------------------------ the rows

    def _add(self, w: QWidget) -> None:
        self._rows.append(w)
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, w)

    def _clear_rows(self) -> None:
        while self._rows:
            w = self._rows.pop()
            self._rows_lay.removeWidget(w)
            w.deleteLater()

    def _rebuild_rows(self) -> None:
        t, rtl = self.t, self.t.rtl
        tip = {"npc": t("search_npc_open"), "item": t("search_item_open"),
               "sell": t("search_sell_open")}.get(self._kind, "")
        self._clear_rows()
        for hit in self._hits:
            sub = sell_verdict(t, self._kb, hit.key) if self._kind == "sell" else hit.sub
            row = _Row(bidi.ltr_name(hit.name, rtl), bidi.plain(sub, rtl) if sub else "", tip)
            row.clicked.connect(lambda _=False, h=hit: self._picked(h))
            self._add(row)

    def _picked(self, hit: Hit) -> None:
        """A result clicked: its own page in the detail window beside this one, the history started over (the
        results stay listed here)."""
        self._detail.open_view(self._kind, hit.key)

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
