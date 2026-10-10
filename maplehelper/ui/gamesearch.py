"""The in-game toolbar's search window (ui/gametoolbar.py opens it): the KB's monsters, NPCs and items, typed
down to a row. A click opens what the app already has for an NPC or an item (the map window with the way there,
the item's details); a monster first shows where it lives, here in the window, each map opening the map window.
It is the one window over the game that takes the keyboard (the player is typing in it); Esc or the ✕ hides it,
and the minimap reads never see it. The search itself is plain data: Hit / search / monster_maps need no Qt."""
from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from .. import availability, bidi, combat, sitedata, tables
from ..i18n import I18n
from ..kb import KnowledgeBase, memo
from ..store import Settings
from . import mapview, theme
from .glass import SHADOW, EdgeResize
from .npcoverlay import (BOTTOM, FILL1, FILL2, MUTED, OPACITY_DEFAULT, OPACITY_MAX, OPACITY_MIN, SIDE, TEXT, TOP,
                         _Header, _exclude_from_capture, _paint_glass)
from .widgets import ITEM_REQUESTS, MAP_REQUESTS

KINDS = ("monster", "npc", "item")   # the three searches the toolbar offers
MIN_W, MIN_H = 260, 300             # the smallest the player can drag the window to
DEFAULT_W, DEFAULT_H = 320, 420     # what opens under the toolbar before it is ever resized
GAP = 8                             # between the toolbar and the window that opens under it
LIMIT = 50                          # rows shown before the "and N more" line
TYPE_DEBOUNCE_MS = 150              # a pause in the typing before the rows are read again
GEOM_DEBOUNCE_MS = 400              # a move/resize saves the window's place once it settles

# the same dark glass as the NPCs window (its own panel, whatever theme the app's windows are in)
QSS = f"""
#Title {{ color: {TEXT}; font-weight: 600; }}
#Status {{ color: {MUTED}; }}
#Lives {{ color: {MUTED}; font-size: 11px; font-weight: 600; padding: 6px 2px 0 2px; }}
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
    for r in tables.rows(kb, "npcs", build=False):
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
    pages)."""
    cache = memo(kb, "_game_search")
    if kind not in cache:
        cache[kind] = _BUILDERS[kind](kb)
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


def monster_maps(kb, key: str) -> list[tuple[str, str, str, int]]:
    """Every map the monster lives on, as (map key, map name, street, how many spawn there), the most spawns
    first and each map once: every row of the monster's name in the KB's spawns table, all its versions (a
    field one and a boss one share a name)."""
    m = combat.monster(kb, key)
    name = m.name if m is not None else str((kb.get(key) or {}).get("name") or "")
    if not name:
        return []
    out: dict[str, tuple[str, str, str, int]] = {}
    for r in tables.rows(kb, "spawns", build=False):
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
    """One clickable line of the list: a bold-ish name over a muted grey one (its type, where it stands, how
    many spawn), in the colours of the NPCs window's rows."""

    clicked = Signal()

    def __init__(self, name: str, sub: str, tip: str = ""):
        super().__init__(objectName="Row")
        self.setAttribute(Qt.WA_Hover, True)          # its QSS :hover
        self.setCursor(Qt.PointingHandCursor)
        if tip:
            self.setToolTip(tip)
            self.setAccessibleName(tip)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(1)
        self.name = QLabel(name, objectName="Name")
        lay.addWidget(self.name)
        self.sub = QLabel(sub, objectName="Sub") if sub else None
        if self.sub is not None:
            lay.addWidget(self.sub)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class GameSearch(EdgeResize, QWidget):
    """The search window itself: over the game, in the NPCs window's dark glass, but taking the keyboard (the
    player types in it; Esc or the ✕ gives it back). The toolbar opens it in a mode — the same mode again is a
    toggle off — and each mode keeps its last query. A click opens the app's own window for an NPC or an item;
    a monster's maps show here first. Its place is kept in game_search_geom."""

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
        self._detail: Hit | None = None                  # the monster whose maps are shown
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
        self._detail = None
        self._box.setText(self._queries[kind])     # the mode's own last query, back in the box
        self._retext_chrome()
        self._run()

    def _typed(self, _text: str) -> None:
        self._typing.start()

    def _run(self) -> None:
        """The rows for what is typed (the typing debounce's work too)."""
        q = self._box.text()
        self._queries[self._kind] = q
        self._detail = None
        self._hits, self._total = search(self._kb, self._kind, q, LIMIT)
        self._rebuild_rows()
        self._retext_status()

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._detail = None
        self._run()      # every list is read again (each one is built once per KB)

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._retext_chrome()
        if self._detail is not None:
            self._show_monster(self._detail)      # its words again, its maps' names re-embedded
        else:
            self._rebuild_rows()
            self._retext_status()

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
        tip = {"npc": t("search_npc_open"), "item": t("search_item_open")}.get(self._kind, "")
        self._clear_rows()
        for hit in self._hits:
            row = _Row(bidi.ltr_name(hit.name, rtl), bidi.plain(hit.sub, rtl) if hit.sub else "", tip)
            row.clicked.connect(lambda _=False, h=hit: self._picked(h))
            self._add(row)

    def _picked(self, hit: Hit) -> None:
        """A result clicked: an NPC or an item opens in the app's own window for it; a monster shows where it
        lives, here."""
        if self._kind == "npc":
            MAP_REQUESTS.requested.emit(hit.key)
        elif self._kind == "item":
            ITEM_REQUESTS.requested.emit(hit.key)
        else:
            self._show_monster(hit)

    def _show_monster(self, hit: Hit) -> None:
        """The monster's own page in the window: its stats, every map it lives on (a click opens the map
        window, with the way there from the player's map) and the way back to the results."""
        t, rtl = self.t, self.t.rtl
        self._detail = hit
        self._clear_rows()
        back = QPushButton(objectName="Back")
        back.setCursor(Qt.PointingHandCursor)
        back.setText(bidi.plain(t("search_back"), rtl))
        back.setToolTip(t("search_back"))
        back.clicked.connect(self._back)
        self._add(back)
        self._add(QLabel(bidi.ltr_name(hit.name, rtl), objectName="DetailName"))
        m = combat.monster(self._kb, hit.key)
        if m is not None:
            self._add(QLabel(bidi.plain(t("mob_stats", lv=m.level, hp=f"{m.hp:,}", exp=f"{m.exp:,}"), rtl),
                             objectName="Status"))
        self._add(QLabel(bidi.plain(t("mob_lives", name=hit.name), rtl), objectName="Lives"))
        maps = monster_maps(self._kb, hit.key)
        if not maps:
            self._add(QLabel(bidi.plain(t("mob_none", name=hit.name), rtl), objectName="Status"))
        for map_key, name, street, count in maps:
            row = _Row(bidi.ltr_name(f"{name}  ·  {street}" if street else name, rtl),
                       bidi.plain(t("mob_spawns", n=count), rtl), t("mob_map_open"))
            row.clicked.connect(lambda _=False, k=map_key: MAP_REQUESTS.requested.emit(k))
            self._add(row)

    def _back(self) -> None:
        """Back to the results, the query still in the box."""
        self._detail = None
        self._rebuild_rows()
        self._retext_status()

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
        super().moveEvent(e)

    def resizeEvent(self, e) -> None:
        self._geom.start()
        super().resizeEvent(e)

    def hideEvent(self, e) -> None:
        self._typing.stop()       # nothing fires once it is hidden: the rows are not read again behind the player's back
        if self._placed:             # hidden (the toolbar, Esc, the ✕): keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        self.mode_changed.emit("")
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self._settings["game_search_geom"] = {"x": self.x(), "y": self.y(), "w": self.width(), "h": self.height()}
