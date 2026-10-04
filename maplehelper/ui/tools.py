"""Play tools: where to train, hit/damage calculator, build plan, quests, grind tracker, two quick checks (what to
sell, what to buy) and how to get from one map to another. Everything reads the KB and the character; nothing touches
the game. The window is non-modal, so it can stay open beside the chat."""
from __future__ import annotations

import html
import math
import re
import time

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QStandardItem, QStandardItemModel, QTextOption
from PySide6.QtWidgets import (QButtonGroup, QCompleter, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from .. import availability, bidi, buildplan, combat, crafting, glossary, grind, guides, market, plan, quests, routes, sitedata, sources
from ..i18n import I18n
from . import terms, theme
from .controls import FlowLayout, Section, Segmented, Stepper, Switch, WrapLink, follow_typing, rtl_buttons
from .glass import GlassDialog, no_default_buttons
from .widgets import chip_row, info_tag, mesos_text, mesos_tip, pet_parts, source_tag, source_tags, tip_html, updated_tag
from .patchnotes import gutter

PAGES = ("train", "calc", "build", "quests", "crafting", "town", "prices", "exp", "more", "route", "pets")
MAX_QUESTS = 40
CURRENT_ROW = {"light": "#FFD3A3", "dark": "#7A4615"}     # the build table row for the player's level
CHANGED_CHIP = {"light": ("#0A6CD6", "#E3F0FD"), "dark": ("#64B5FF", "#1B3350")}   # its "Changed in COT2" chips


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.hide()
            w.deleteLater()
        elif item.layout():
            clear(item.layout())


def _alone(w: QWidget) -> QHBoxLayout:
    """One widget as a layout (beside chip_row's rows)."""
    box = QHBoxLayout()
    box.setContentsMargins(0, 0, 0, 0)
    box.addWidget(w, 1)
    return box


def tag(text: str, kind: str = "Tag") -> QLabel:
    lb = QLabel(text, objectName=kind)
    lb.setAlignment(Qt.AlignCenter)
    return lb


def scroll_page(rtl: bool) -> tuple[QScrollArea, QVBoxLayout]:
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    body = QWidget(objectName="Feed")
    lay = QVBoxLayout(body)
    lay.setContentsMargins(*gutter(rtl))      # the room before the scrollbar, on its side (left in Hebrew)
    lay.setSpacing(14)
    sc.setWidget(body)
    return sc, lay


NAME_ROLE = Qt.UserRole + 1
PATH_ROLE = Qt.UserRole + 2


class _LazyIcons(QStandardItemModel):
    """Pictures loaded when the list first shows their row: scaling ~3,000 item pictures up front took half a
    second of the Tools window's opening."""

    def __init__(self, parent, size: int):
        super().__init__(parent)
        self._size = size
        self._icons: dict[str, QIcon] = {}

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DecorationRole:
            path = super().data(index, PATH_ROLE)
            if not path:
                return None
            if path not in self._icons:
                pm = QPixmap(path)
                self._icons[path] = QIcon(pm.scaled(self._size, self._size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            return self._icons[path]
        return super().data(index, role)


class EntityPicker(QLineEdit):
    """A search box that opens a list of names with pictures; typing narrows it down.
    rows: (shown text, name to put in the box, picture path or None)."""
    picked = Signal()

    def __init__(self, rows: list[tuple[str, str, object]], placeholder: str, icon: int = 36, rtl: bool = False):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setAccessibleName(placeholder)         # a placeholder isn't read as the field's name
        self.setClearButtonEnabled(True)
        model = _LazyIcons(self, icon)
        for shown, name, path in rows:
            item = QStandardItem(shown)
            item.setData(name, NAME_ROLE)
            if path:
                item.setData(str(path), PATH_ROLE)      # the picture itself: when its row is first shown
            item.setEditable(False)
            model.appendRow(item)
        comp = QCompleter(self)
        comp.setCompletionRole(NAME_ROLE)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.setMaxVisibleItems(9)
        comp.popup().setIconSize(QSize(icon, icon))
        comp.popup().setTextElideMode(Qt.ElideNone)       # long map names stay whole (two lines, see map_rows)
        comp.popup().setWordWrap(True)
        # every row's height from the first one: otherwise the list measures (and loads the picture of) all
        # ~2,500 rows before it shows nine
        comp.popup().setUniformItemSizes(True)
        c = theme.P()
        bg = "#2C2C2E" if theme.MODE == "dark" else "#FFFFFF"
        comp.popup().setStyleSheet(
            f"QListView {{ background: {bg}; color: {c['text']}; border: 1px solid {c['stroke']}; border-radius: 10px;"
            f" padding: 4px; outline: none; }}"
            f"QListView::item {{ padding: 4px 6px; border-radius: 8px; color: {c['text']}; }}"
            f"QListView::item:selected, QListView::item:hover {{ background: rgba(255,149,51,0.22); color: {c['text']}; }}")
        comp.setModel(model)          # after the style: polishing a full list measured every row
        comp.activated.connect(lambda *_: QTimer.singleShot(0, self._chosen))
        self.setCompleter(comp)
        self.returnPressed.connect(self.picked.emit)
        # a chevron says "this opens a list" before anyone clicks
        arrow = self.addAction(self._chevron(), QLineEdit.TrailingPosition)
        arrow.triggered.connect(self.open_list)
        self.setMinimumHeight(34)
        follow_typing(self, rtl)

    @staticmethod
    def _chevron() -> QIcon:
        from PySide6.QtGui import QColor, QPainter, QPen
        pm = QPixmap(20, 20)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(theme.ORANGE_DEEP), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPoint(5, 8), QPoint(10, 13), QPoint(15, 8)])
        p.end()
        return QIcon(pm)

    def _chosen(self):
        self.setCursorPosition(0)              # a long name shows from its start
        self.picked.emit()

    def open_list(self):
        comp = self.completer()
        comp.setCompletionPrefix(self.text())
        comp.popup().setMinimumWidth(max(self.width(), 440))
        comp.complete()

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.open_list()                      # a click shows the whole list, not only after typing

    def event(self, e):
        if e.type() == QEvent.KeyPress and e.key() == Qt.Key_Down and not self.completer().popup().isVisible():
            self.open_list()
            return True
        return super().event(e)


CITIZEN_GRADES = ("Helpful Stranger", "Distinguished Citizen", "Guardian of the Village")


def _bold_names(text: str) -> str:
    """Citizen grades and the towns stand out in a long piece of advice."""
    for name in CITIZEN_GRADES + quests.TOWNS:
        text = re.sub(rf"(?<!\*)\b{re.escape(name)}\b(?!\*)", f"**{name}**", text)
    return text


def monster_rows(kb) -> list[tuple[str, str, object]]:
    """Every monster once (the version that spawns on the most maps), lowest level first."""
    best: dict[str, combat.Monster] = {}
    open_ = availability.of(kb)
    for m in combat.monsters(kb):
        # only monsters the KB confirms are in the game: none from Ossyria, none with no map at all
        if combat.special_monster(m.name) or not open_.monster_key_open(m.key):
            continue
        if m.name not in best or sum(n for _, n in m.maps) > sum(n for _, n in best[m.name].maps):
            best[m.name] = m
    return [(f"{m.name}  ·  Lv. {m.level}", m.name, kb.picture(m.key))
            for m in sorted(best.values(), key=lambda m: (m.level, m.name))]


def item_rows(kb) -> list[tuple[str, str, object]]:
    """Every item once, by name."""
    seen = {}
    for k, e in kb.entities.items():
        if e.get("category") == "item" and e["name"].strip() and e["name"] not in seen:
            seen[e["name"]] = k
    return [(name, name, kb.picture(k)) for name, k in sorted(seen.items(), key=lambda x: x[0].lower())]


def map_rows(kb) -> list[tuple[str, str, object]]:
    """Every reachable map with its minimap: hunting grounds by monster level, then towns and the rest."""
    rows = []
    for k, e in kb.entities.items():
        if e.get("category") != "map":
            continue
        page = kb.page(k)
        where = re.search(r"\nLocation (.+)", page)
        street, _, place = where.group(1).partition(" / ") if where else ("", "", "")
        place = place.strip()
        # confirmed in the game by the KB (its continent is out), and a map people hunt on
        if not combat.grind_map(kb, f"{e['name']} {street.strip()}"):
            continue
        lv = re.search(r"\nMonster levels Lv (\d+)\s*[-–]\s*(\d+)", page)
        lo = int(lv.group(1)) if lv else 999
        info = "  ·  ".join(([f"Lv. {lv.group(1)}-{lv.group(2)}"] if lv else []) + ([place] if place else []))
        shown = f"{e['name']}\n{info}" if info else e["name"]          # the name on its own line, never cut
        rows.append((lo, e["name"], (shown, e["name"], kb.picture(k))))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in rows]


def _whole(name: str) -> str:
    """A short name with no-break spaces, so a line wraps before it rather than inside it."""
    return name.replace(" ", " ") if len(name) <= bidi.KEEP_TOGETHER else name


def route_rows(kb, graph) -> list[tuple[str, str, object]]:
    """Every map a route can start or end on (in the game, on the route graph), towns first, each name once."""
    rows = {}
    for m in graph.maps.values():
        if m.name in rows:
            continue
        info = "  ·  ".join(x for x in ("Town" if m.town else "", m.street or m.continent) if x)
        mid = graph.exact(m.name) or m.id
        shown = f"{m.name}\n{info}" if info else m.name          # the name on its own line, never cut
        rows[m.name] = (not m.town, m.name.lower(), (shown, m.name, kb.picture(f"map/{mid}")))
    return [r[2] for r in sorted(rows.values(), key=lambda r: r[:2])]


class ToolsDialog(GlassDialog):
    sync_requested = Signal()                 # read level/EXP/stats from a screenshot (the chat does it)
    grind_sync_requested = Signal()           # the same read for the grind tracker (also mesos, potions, monster)
    market_ready = Signal(object)             # (item name, Market or None) from the background lookup
    ask_requested = Signal(str, bool)          # question for the chat, with a fresh screenshot?
    detail_ask_requested = Signal(str, str)    # ...with a full-resolution screenshot (inventory icons); bubble label
    tag_requested = Signal(str)                # tag an entity (monster, quest) in the chat
    guide_requested = Signal(str)              # open a guide in the guides window

    def __init__(self, kb, profiles, settings, lang: str, stylesheet: str, runner=None, page: str = "train"):
        self.t = t = I18n(lang or "he")
        super().__init__(t("tools"), t.rtl)
        self.kb, self.profiles, self.settings = kb, profiles, settings
        # the grind tracker's reads and its session: the app's (they go on with this window closed), or its own
        from .grindrunner import GrindRunner
        self._own_runner = not isinstance(runner, GrindRunner)
        self.runner = GrindRunner(kb, profiles, settings) if self._own_runner else runner
        if self._own_runner:
            self.runner.setParent(self)         # its timer goes with this window
        self.grind = self.runner.store
        self.runner.changed.connect(self.grind_changed)
        self.setStyleSheet(stylesheet)
        self.fit_screen(580, 800)
        rtl = t.rtl
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        # the pages as chips, three a row so every label stays readable; a shorter last row fills the width
        # (on a grid of six columns: a chip of a full row spans two, the one chip of a last row all six)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self.nav = QButtonGroup(self)
        for i, name in enumerate(PAGES):
            b = QPushButton(bidi.plain(t(f"tool_{name}"), rtl).replace("&", "&&"), objectName="Chip")   # "&" isn't a shortcut
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("page", name)
            self.nav.addButton(b, i)
            row, col = divmod(i, 3)
            span = 6 // min(3, len(PAGES) - row * 3)
            grid.addWidget(b, row, col * span, 1, span)
        for col in range(6):
            grid.setColumnStretch(col, 1)
        self.nav.idClicked.connect(self.show_page)
        outer.addLayout(grid)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        # only the page asked for is built before the window shows; the others follow right after it is up
        # (all nine at once held the window back for a second or more). A page another one reaches into
        # before then is built on the spot (__getattr__).
        self.pages = {}
        self._pending = list(PAGES)
        for _ in PAGES:
            self.stack.addWidget(QWidget())
        start = PAGES.index(page) if page in PAGES else 0
        self._build_page(PAGES[start])
        no_default_buttons(self)
        self.show_page(start)
        QTimer.singleShot(0, self._build_next)

    def _build_page(self, name: str) -> None:
        if name not in self._pending:
            return
        self._pending.remove(name)
        w = getattr(self, f"_page_{name}")()
        self.pages[name] = w
        i = PAGES.index(name)
        placeholder = self.stack.widget(i)
        self.stack.insertWidget(i, w)
        self.stack.removeWidget(placeholder)
        placeholder.deleteLater()
        rtl_buttons(w, self.t.rtl)
        # Enter in a search box runs that tab's search, never "click the first tab" (Where to train)
        no_default_buttons(w)

    def _build_next(self) -> None:
        """One more page per turn of the event loop, so the window stays responsive while they are made."""
        try:
            if self._pending:
                self._build_page(self._pending[0])
                QTimer.singleShot(0, self._build_next)
        except RuntimeError:      # the window closed meanwhile
            pass

    def _build_rest(self) -> None:
        while self._pending:
            self._build_page(self._pending[0])

    def __getattr__(self, name):
        # a widget of a page not built yet (a test, a page reaching into another): build them all and look again.
        # Only public names: the widgets are public, while a private name is the window's own state, probed with
        # hasattr() before it is first set (_skill_icons' "_skills"). Building every page for that held the window
        # back ~0.2 s whenever it opened on the Build page, the lazy build undone.
        pending = self.__dict__.get("_pending")
        if pending and not name.startswith("_"):
            self._build_rest()
            return getattr(self, name)
        raise AttributeError(name)

    # common -------------------------------------------------------------

    @property
    def c(self):
        return self.profiles.active

    def show_page(self, i: int):
        self._build_page(PAGES[i])
        self.nav.button(i).setChecked(True)
        self.stack.setCurrentIndex(i)
        # the quest pages rebuild up to 40 cards (~0.3 s): a tab switch back to one that would show the same thing
        # keeps it as it is. Every other redraw (a quest marked done, a new reading, the search) still fills.
        state = self._page_state(PAGES[i])
        if state is not None and state == self.__dict__.get("_filled", {}).get(PAGES[i]):
            return
        self.refresh(PAGES[i])

    def _page_state(self, name: str):
        """Everything a quest page's cards are made from, or None for a page that always redraws."""
        c = self.c
        if name not in ("quests", "town") or c is None:
            return None
        common = (c.id, c.level, c.base_class, c.job, tuple(c.quests_done), theme.MODE)
        if name == "quests":
            return common + (tuple(sorted((c.crafts or {}).items())), self.q_mode.value(),
                             self.q_search.text().strip(), self._q_limit, self.q_done_toggle.isChecked())
        return common + (c.town, self._town_limit, self.town_done_toggle.isChecked())

    def refresh(self, name: str | None = None):
        """Redraw a page (or the current one) from the character and the KB."""
        name = name or PAGES[self.stack.currentIndex()]
        getattr(self, f"_fill_{name}", lambda: None)()
        if name in ("quests", "town"):
            self.__dict__.setdefault("_filled", {})[name] = self._page_state(name)

    def profile_changed(self):
        """The chat read the profile again (level, EXP, stats): follow it."""
        self._load_stats()
        self.refresh()

    def sync_done(self, ok: bool):
        """A screenshot read ended: a grind tracker read waiting for it takes it (a failed one says so)."""
        self.setWindowOpacity(1.0)
        if self._own_runner:
            self.runner.sync_done(ok)
        self.refresh()

    def _step_aside(self, then) -> None:
        """Out of the screenshot, then `then()` (the chat captures ~120 ms later), back after 1.5 s. Hidden, not
        see-through: a window at opacity 0 still left traces over the inventory slots (a fake item, seen live)."""
        self.hide()
        QTimer.singleShot(450, then)      # after Windows' own fade-out of the hidden window (~250 ms)
        QTimer.singleShot(1500, self._come_back)

    def _come_back(self):
        try:
            self.show()
            self.raise_()
        except RuntimeError:      # closed meanwhile
            pass

    def _read_screen(self):
        """The chat reads the game from a screenshot; this window steps aside so it isn't in the picture."""
        self._step_aside(self.sync_requested.emit)

    def _p(self, text: str) -> str:
        return bidi.plain(text, self.t.rtl)

    def _html(self, text: str, seen: set | None = None) -> str:
        """seen: the terms already explained in this view (one "?" per term on a page, not one per label)."""
        d = "rtl" if self.t.rtl else "ltr"
        out = []
        for line in (text or "").split("\n"):
            if not line.strip():
                out.append("<p style='margin:0; font-size:5px;'>&nbsp;</p>")
                continue
            out.append(bidi.paragraph_html(line, d).replace("margin:0 0 4px 0;", "margin:0 0 3px 0; line-height:135%;"))
        return glossary.annotate("".join(out), self.t.lang, seen=seen)

    def _set(self, label: QLabel, text: str, seen: set | None = None):
        label.setText(self._html(text, seen))

    def _label(self, text: str, obj: str = "RowLabel", wrap: bool = True, seen: set | None = None) -> QLabel:
        lb = QLabel(objectName=obj)
        lb.setTextFormat(Qt.RichText)
        lb.setWordWrap(wrap)
        self._set(lb, text, seen)
        return terms.watch(lb, self.t.lang)

    def _source_line(self, box: QHBoxLayout, source: str | None) -> None:
        """ "Data source: [MeowDB]" under a page's header, for a page whose whole list has one source (None: empty)."""
        clear(box)
        if not source:
            return
        box.setSpacing(6)
        box.addWidget(self._label(self.t("src_data"), "RowHint", wrap=False), 0, Qt.AlignVCenter)
        box.addWidget(source_tag(self.t, source), 0, Qt.AlignVCenter)
        box.addStretch(1)

    def _row(self, sec: Section, label: str, control: QWidget | None = None, hint: str = "") -> QWidget:
        """A settings-style row whose label explains its game terms ("?")."""
        row = sec.add_row(label, control, hint=hint)
        lb = row.findChild(QLabel, "RowLabel")
        if lb is not None:
            lb.setText(self._html(label))
            terms.watch(lb, self.t.lang)
        return row

    def _ask_link(self, on_click) -> QWidget:
        """"Ask in chat" at the reading start, under what it asks about."""
        ask = QPushButton(self._p(self.t("ask_short")), objectName="Link")
        ask.setAutoDefault(False)
        ask.clicked.connect(lambda *_: on_click())
        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 4)
        bl.addWidget(ask, 0, Qt.AlignLeft)        # AlignLeft is the leading edge (mirrored in Hebrew)
        bl.addStretch(1)
        return box

    def _big(self, value: str, label: str, explain: bool = True) -> QVBoxLayout:
        """A big number with its (explained) name under it."""
        box = QVBoxLayout()
        box.setSpacing(0)
        v = QLabel(value, objectName="BigStat")
        v.setAlignment(Qt.AlignCenter)
        text = html.escape(label)
        lb = QLabel(glossary.annotate(text, self.t.lang) if explain else text, objectName="BigStatLabel")
        lb.setAlignment(Qt.AlignCenter)
        terms.watch(lb, self.t.lang)
        box.addWidget(v)
        box.addWidget(lb)
        return box

    def _no_character(self, lay):
        lay.addWidget(self._label(self.t("tool_no_char"), "RowHint"))

    # my stats (shared by "where to train" and the calculator) ------------

    def _stats_section(self) -> Section:
        t = self.t
        sec = Section(t("my_stats"), t.rtl)
        steppers = {}
        for key, hi in (("acc", 999), ("dmg_min", 99999), ("dmg_max", 99999)):
            st = Stepper(0, hi, 0)
            st.edit.setFixedWidth(64)
            st.valueChanged.connect(lambda v, k=key: self._set_stat(k, v))
            self._row(sec, t(f"stat_{key}"), st)
            steppers[key] = st
        self.__dict__.setdefault("_steppers", []).append(steppers)
        sec.add_widget(self._label(t("my_stats_hint"), "RowHint"))
        read = WrapLink(t("my_stats_read"), t.rtl)          # a long link wraps instead of widening the window
        read.clicked.connect(self._read_screen)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 8, 0, 8)
        bl.addWidget(read)
        sec.add_widget(box)
        return sec

    def _load_stats(self):
        s = (self.c.stats if self.c else {}) or {}
        for steppers in self.__dict__.get("_steppers", []):
            for key, st in steppers.items():
                st.blockSignals(True)
                st.setValue(int(s.get(key) or 0))
                st.blockSignals(False)

    def _set_stat(self, key: str, value: int):
        c = self.c
        if not c:
            return
        c.stats = {**(c.stats or {}), key: value} if value else {k: v for k, v in (c.stats or {}).items() if k != key}
        self.profiles.save()
        self._load_stats()                      # the other page's copy follows
        self.refresh()

    def _stats(self):
        s = (self.c.stats if self.c else {}) or {}
        acc = s.get("acc") or None
        # typed by hand: min above max is swapped, a max of 0 taken as the min (never a range like 200-10)
        return acc, combat.damage_range(s.get("dmg_min"), s.get("dmg_max"))

    # where to train --------------------------------------------------------

    def _page_train(self):
        sc, lay = scroll_page(self.t.rtl)
        self.train_head = self._label("", "ToolHeader")
        lay.addWidget(self.train_head)
        # the stats first: the spots below are ranked by them, and at the end of a long list nobody found them
        lay.addWidget(self._stats_section())
        self.train_list = QVBoxLayout()
        self.train_list.setSpacing(8)
        lay.addLayout(self.train_list)
        lay.addStretch(1)
        self._load_stats()
        return sc

    def _fill_train(self):
        t, c = self.t, self.c
        clear(self.train_list)
        if not c:
            self.train_head.setText("")
            self._no_character(self.train_list)
            return
        acc, dmg = self._stats()
        magic = c.base_class == combat.MAGE
        rows = combat.spots(self.kb, c.level, acc, dmg, magic, n=6)
        # what the list is for, not the stats again (they are in "My stats" right below: the owner's report)
        head = t("train_for_level", n=c.level)
        if not (acc and dmg):
            head += "\n" + t("train_need_stats")
        elif magic:
            head += "\n" + t("train_mage_note")       # the stat window's range is the staff swing, not a spell
        else:
            head += "\n" + t("train_basic_note")
        self._set(self.train_head, head)
        if not rows:
            self.train_list.addWidget(self._label(t("train_none"), "RowHint"))
            return
        if not any(s.fits for s in rows):
            # nothing passes the miss / hits limits: still the best options, with why they're shown
            # (a Magician's hits are spells: "too many basic hits" and "skills make it faster" don't apply)
            self.train_list.addWidget(self._label(t("train_stretch_magician" if magic else "train_stretch"), "RowHint"))
        most = self._most_mesos(rows)
        for i, s in enumerate(rows):
            self.train_list.addWidget(self._spot_card(s, best=(i == 0), most_mesos=s is most))

    def _most_mesos(self, rows: list[combat.Spot]) -> combat.Spot | None:
        """The spot that brings the most mesos per swing by the players' reports (a kill's mesos x hit chance /
        hits), labelled on its card: a second ranking beside the EXP one, not a reorder. None unless at least two
        of the spots have reports to compare."""
        rated = [(s, m * s.hit / (s.avg_hits or 1)) for s in rows if (m := self.kb.mesos_per_kill(s.monster.key))]
        return max(rated, key=lambda r: r[1])[0] if len(rated) >= 2 else None

    def _spot_card(self, s: combat.Spot, best: bool, most_mesos: bool = False) -> QFrame:
        t, c, m = self.t, self.c, s.monster
        card = QFrame(objectName="Card")
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)
        pic = QLabel()
        pic.setFixedSize(52, 52)
        pic.setAlignment(Qt.AlignCenter)
        path = self.kb.picture(m.key)
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull():
                pic.setPixmap(pm.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        row.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        # "Name · Lv. N" as one English block, the same order as the wishlist's monsters (in Hebrew too)
        name = QLabel(bidi.ltr_name(f"{m.name} · Lv. {m.level}", t.rtl), objectName="CardName")
        name.setWordWrap(True)
        col.addWidget(name)
        # "… IV · Victoria Road", one English block (run by run, a Hebrew line put the region first)
        col.addWidget(self._label(bidi.ltr_block(self.kb.map_label(s.map), self.t.rtl), "CardSub"))
        # two groups of tags: why it's picked, then the numbers; each wraps when the window is narrow
        # (one long row pushed the card, and the window, wider than 470 px)
        why, nums = FlowLayout(spacing=5), FlowLayout(spacing=5)
        if best:
            why.addWidget(tag(self._p(t("spot_best")), "TagAccent"))
        if s.recommended:
            why.addWidget(tag(self._p(t("spot_guide")), "TagGood"))
        if most_mesos:
            most = tag(self._p(t("spot_most_mesos")), "TagAccent")
            most.setToolTip(bidi.to_html(t("spot_most_mesos_tip"), "rtl" if t.rtl else "ltr"))
            why.addWidget(most)
        if self._stats()[0]:
            why.addWidget(tag(self._p(t("spot_hit", pct=round(s.hit * 100))), "TagGood" if s.hit >= 0.999 else "TagWarn"))
        if s.hits:
            nums.addWidget(tag(self._p(t("spot_hits", n=s.hits)), "Tag"))
        nums.addWidget(tag(self._p(t("spot_exp", n=m.exp)), "Tag"))
        nums.addWidget(tag(self._p(t("spot_crowd", n=m.maps[0][1])), "Tag"))
        # the mesos players reported, with its own source in the text: the chip below is the page's stats' source
        mesos = self.kb.community_mesos(m.key)
        if mesos:
            mt = tag(self._p(mesos_text(t, mesos)), "Tag")
            mt.setToolTip(bidi.to_html(mesos_tip(t, mesos), "rtl" if t.rtl else "ltr"))
            nums.addWidget(mt)
        # the monster's numbers come from its page: its build ("COT2") or MeowDB's own; a KB update this week
        # that changed it says so
        stamp = sources.stat_source(self.kb, m.key)
        nums.addWidget(source_tag(t, stamp.source if stamp else sources.MEOWDB, stamp))
        updated = updated_tag(t, self.kb, m.key)
        if updated:
            why.addWidget(updated)
        for line in (why, nums):
            if line.count():
                col.addLayout(line)
        info = []
        if s.hit < 0.999 and self._stats()[0]:
            info.append(t("spot_acc_need", n=s.acc_needed))
        kills = combat.kills_to_level(self.kb, c.level, c.exp_pct, m)
        if kills:
            info.append(t("spot_kills", n=f"{kills:,}"))
        if info:
            label = self._label("\n".join(info), "CardSub")
            # the kills come from the EXP table: past its confirmed levels it is a historical reference
            ref = kills and sources.exp_source(self.kb, c.level) == sources.REFERENCE
            col.addLayout(chip_row([source_tag(t, sources.REFERENCE)], label, lead=True) if ref else _alone(label))
        row.addLayout(col, 1)
        # its own row under the tags, at the reading start: beside them it took the room the tags needed
        ask = QPushButton(self._p(t("ask_short")), objectName="Link")
        ask.setCursor(Qt.PointingHandCursor)
        ask.setAutoDefault(False)
        ask.clicked.connect(lambda _=False, k=m.key: self.tag_requested.emit(k))
        col.addWidget(ask, 0, Qt.AlignLeft)          # AlignLeft is the leading edge (mirrored in Hebrew)
        return card

    # calculator ----------------------------------------------------------

    def _page_calc(self):
        t = self.t
        sc, lay = scroll_page(self.t.rtl)
        rows = monster_rows(self.kb)
        self.calc_input = EntityPicker(rows, self._p(t("calc_placeholder", n=len(rows))), rtl=t.rtl)
        self.calc_input.picked.connect(self._fill_calc)
        lay.addWidget(self.calc_input)
        # the stats first, as on "Where to train": every number below comes from them
        lay.addWidget(self._stats_section())
        self.calc_box = QVBoxLayout()
        self.calc_box.setSpacing(12)
        lay.addLayout(self.calc_box)
        lay.addStretch(1)
        self._load_stats()
        return sc

    def _calc_monster(self) -> combat.Monster | None:
        q = self.calc_input.text().strip().lower()
        if not q:
            spots = combat.spots(self.kb, self.c.level, n=1) if self.c else []
            return spots[0].monster if spots else None
        # only monsters the KB confirms are in the game, like the list: a typed "Star Pixie" (Orbis) or "Ratz" (no
        # map at all) got a full hits-to-kill card as if it could be met
        open_ = availability.of(self.kb)
        ms = [m for m in combat.monsters(self.kb) if open_.monster_key_open(m.key)]
        exact = [m for m in ms if m.name.lower() == q]
        if exact:
            return min(exact, key=lambda m: -sum(n for _, n in m.maps))
        part = [m for m in ms if q in m.name.lower()]
        return min(part, key=lambda m: (len(m.name), m.level)) if part else None

    def _fill_calc(self):
        t, c = self.t, self.c
        clear(self.calc_box)
        if not c:
            self._no_character(self.calc_box)
            return
        m = self._calc_monster()
        if not m:
            # nothing typed (and no monster near the level) isn't "no monster by that name"
            typed = self.calc_input.text().strip()
            self.calc_box.addWidget(self._label(t("calc_none") if typed else t("calc_pick"), "RowHint"))
            return
        acc, dmg = self._stats()
        magic = c.base_class == combat.MAGE
        sec = Section(bidi.ltr_block(f"{m.name} · Lv. {m.level}", t.rtl), t.rtl)
        nums = QHBoxLayout()
        # the monster's picture leads the row (the start side), as on the training-spot cards
        path = self.kb.picture(m.key)
        pm = QPixmap(str(path)) if path else QPixmap()
        if not pm.isNull():
            pic = QLabel()
            pic.setFixedSize(56, 56)
            pic.setAlignment(Qt.AlignCenter)
            pic.setPixmap(pm.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            nums.addWidget(pic, 0, Qt.AlignVCenter)
        # P.DEF for every class: the hits below are the stat window's basic attack, a Magician's staff swing too
        for value, label in ((f"{m.hp:,}", "HP"), (f"{m.exp:,}", "EXP"), (str(m.avoid), "Avoid"), (str(m.pdef), "P.DEF")):
            nums.addLayout(self._big(value, label))
        holder = QWidget()
        both = QVBoxLayout(holder)
        both.setContentsMargins(0, 0, 0, 0)
        both.addLayout(nums)
        # where these numbers (and every result below, worked out from them) come from
        stamp = sources.stat_source(self.kb, m.key)
        self.calc_chips = [source_tag(t, stamp.source if stamp else sources.MEOWDB, stamp)]
        updated = updated_tag(t, self.kb, m.key)
        if updated:
            self.calc_chips.append(updated)
        both.addLayout(chip_row(self.calc_chips))
        sec.add_widget(holder)
        if m.avoid <= 0:
            # nothing to compute: say it plainly instead of a column of zeros
            self._row(sec, t("calc_never_dodges"))
        else:
            need100 = combat.acc_needed(c.level, m.level, m.avoid)
            need90 = combat.acc_needed(c.level, m.level, m.avoid, 0.9)
            self._row(sec, t("calc_acc_need"), tag(f"{need100}", "TagAccent"), hint=t("calc_acc_need90", n=need90))
            if acc:
                hit = combat.hit_chance(acc, c.level, m.level, m.avoid)
                hint = ""
                if hit < 0.999:
                    more = need100 - acc
                    pts = math.ceil(more / combat.acc_per_point(c.base_class))
                    hint = t("calc_more_acc", n=more, pts=pts, stat="INT" if magic else "DEX")
                self._row(sec, t("calc_hit"), tag(f"{round(hit * 100)}%", "TagGood" if hit >= 0.999 else "TagWarn"),
                          hint=hint)
        if dmg:
            # the stat window's range is a basic attack: for a Magician the staff swing, a physical hit (P.DEF)
            hits, avg = combat.hits_to_kill(dmg[0], dmg[1], m, c.level)
            # the number inside the sentence ("3 basic hits always kill it"): a lone "1" at the far edge read as
            # unrelated, and "1.0 on average" said nothing when it's always one hit
            hint = t("calc_hits_mage" if magic else "calc_hits_basic")
            if avg < hits - 0.05:
                hint = t("calc_hits_avg", n=f"{avg:.1f}") + "\n" + hint
            self._row(sec, t("calc_hits", n=hits), hint=hint)
        # the mesos players reported for it (community.json), with its source chip; or that nobody reported any
        mesos = self.kb.community_mesos(m.key)
        if mesos:
            lo, hi = mesos[0], mesos[1]
            value = tag(f"{lo:,}" if lo == hi else f"‪{lo:,}–{hi:,}‬", "TagAccent")
            value.setToolTip(bidi.to_html(mesos_tip(t, mesos), "rtl" if t.rtl else "ltr"))
            holder3 = QWidget()
            holder3.setLayout(chip_row([source_tag(t, sources.COMMUNITY), value], spacing=6))
            avg = self.kb.mesos_per_kill(m.key)
            self._row(sec, t("calc_mesos"), holder3, hint=t("calc_mesos_avg", n=f"{avg:,.0f}") if avg else "")
        else:
            self._row(sec, t("calc_mesos"), tag(self._p(t("mesos_none")), "Tag"))
        if not (acc and dmg):
            sec.add_widget(self._label(t("calc_need_stats"), "RowHint"))
        sec.add_widget(self._ask_link(lambda: self.tag_requested.emit(m.key)))
        self.calc_box.addWidget(sec)
        if m.avoid > 0:
            # ACC to never miss as your level changes: three big numbers, not a list
            lv_sec = Section(t("calc_acc_by_level_head"), t.rtl)
            strip = QHBoxLayout()
            for lv in (c.level - 5, c.level, c.level + 5):
                if lv >= 1:
                    strip.addLayout(self._big(str(combat.acc_needed(lv, m.level, m.avoid)), f"Lv. {lv}", explain=False))
            holder2 = QWidget()
            holder2.setLayout(strip)
            lv_sec.add_widget(holder2)
            lv_sec.add_widget(self._label(t("calc_acc_by_level_hint"), "RowHint"))
            self.calc_box.addWidget(lv_sec)
        if m.maps:
            maps_sec = Section(t("calc_maps_head_plain"), t.rtl)
            for mp, n in m.maps[:3]:
                # one English block: "Tree Dungeon, Forest Up North IV" kept its comma in place
                label = self.kb.map_label(mp)
                row = self._row(maps_sec, bidi.ltr_name(label, t.rtl), tag(self._p(t("spot_crowd", n=n)), "Tag"))
                ask = QPushButton(self._p(t("ask_short")), objectName="Link")
                ask.setAutoDefault(False)
                ask.clicked.connect(lambda _=False, q=t("calc_ask_map", map=label): self.ask_requested.emit(q, False))
                row.layout().insertWidget(1, ask, 0, Qt.AlignVCenter)      # between the map's name and its count
            self.calc_box.addWidget(maps_sec)

    # build ---------------------------------------------------------------

    def _page_build(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.build_head = self._label("", "ToolHeader")
        lay.addWidget(self.build_head)
        self.build_src = QHBoxLayout()
        lay.addLayout(self.build_src)
        # the community tier list's row for the player's job (before the 2nd job: the branches to pick from)
        self.build_tier = QVBoxLayout()
        lay.addLayout(self.build_tier)
        self.build_view = QTextBrowser(objectName="GuideText")
        self.build_view.setOpenLinks(False)
        # a skill's "Changed in COT2" chip in the table: its changes on hover, and on a click (touch, keyboard)
        self.build_view.highlighted.connect(lambda url: self._skill_change_tip(url.toString()))
        self.build_view.anchorClicked.connect(lambda url: self._skill_change_tip(url.toString()))
        self.build_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # breaks between words only, a wide table in a smaller font, as in the guides reader ("crafti" / "ng 1")
        self.build_view.setWordWrapMode(QTextOption.WordWrap)
        from .guides import ImageZoom
        self.build_zoom = ImageZoom(self.build_view)
        lay.addWidget(self.build_view, 1)
        self.build_guide_btn = QPushButton(self._p(self.t("build_open_guide")), objectName="Link")
        self.build_guide_btn.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.build_guide_btn, 0, (Qt.AlignRight if self.t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute)
        self._build_key = None
        self.build_guide_btn.clicked.connect(lambda: self._build_key and self.guide_requested.emit(self._build_key))
        return w

    def _fill_build(self):
        t, c = self.t, self.c
        clear(self.build_tier)
        if not c:
            self._set(self.build_head, t("tool_no_char"))
            self._source_line(self.build_src, None)
            self.build_view.setHtml("")
            self._build_key = None
            self.build_guide_btn.hide()          # no character, no guide to open
            return
        key, tables = buildplan.tables(self.kb, c.base_class, c.job, c.level, t.lang)
        self._build_key = key
        self.build_guide_btn.setVisible(bool(key))
        self._set(self.build_head, t("build_head", job=c.job_label or c.base_class, n=c.level))
        # the class guide's numbers: "use current COT2 data" on its page, else MeowDB's own
        self._source_line(self.build_src, (sources.guide_source(self.kb, key) or sources.MEOWDB) if tables else None)
        tier = self._tier_card(sitedata.tiers_for(self.kb, c.base_class, c.job))
        if tier:
            self.build_tier.addWidget(tier)
        if not tables:
            self.build_view.setHtml(f"<p>{t('build_none')}</p>")
            return
        he = t.lang != "en"
        icons = self._skill_icons()
        changed = sitedata.changes_for(self.kb, c.base_class, c.job)
        col = guides.NOTE_COLORS.get(theme.MODE, guides.NOTE_COLORS["light"])
        side = "dir='rtl' align='right'" if he else ""
        out = []
        for tb in tables:
            out.append(f"<h3 {side}>{guides._rich(tb.heading, he and bool(bidi._RTL.search(tb.heading)))}</h3>")
            cells = []
            rows = tb.rows
            if tb.kind == "sp" and he:
                # Hebrew shows the table from the right: "spend SP on" first and its result in the middle, beside it
                rows = [[r[0], r[2], r[1], *r[3:]] if len(r) >= 3 else r for r in rows]
            # a cell may name a skill short ("Booster 9" beside "Claw Booster +2"): the table's own full names
            # give those their icons too
            names = icons + self._short_skill_icons(rows, icons) if tb.kind == "sp" else icons
            chips = self._change_chips(rows, changed) if tb.kind == "sp" else {}
            for n, row in enumerate(rows):
                # the player's row: a clear orange, bold (the guides' cream note color was too faint here)
                now = n == tb.current
                bg = f" bgcolor='{col['head']}'" if n == 0 else (f" bgcolor='{CURRENT_ROW[theme.MODE]}'" if now else "")
                tagname = "th" if n == 0 else "td"
                weight = "font-weight:700;" if now else ""
                cells.append("<tr>" + "".join(
                    f"<{tagname}{bg}><p {'dir=rtl align=right' if he and bidi._RTL.search(x) else ''} style='margin:0;{weight}'>"
                    f"{self._with_skill_icon(x, names) if tb.kind == 'sp' and n else ''}"
                    f"{guides._rich(x, he and bool(bidi._RTL.search(x)), 18)}{chips.get((n, i), '')}</p></{tagname}>"
                    for i, x in enumerate(row)) + "</tr>")
            out.append(f"<table {side} width='100%' cellspacing='0' cellpadding='5' border='1' "
                       f"style='border-color: {col['line']}; border-style: solid; margin: 4px 0 12px 0;'>{''.join(cells)}</table>")
        self.build_view.setLayoutDirection(Qt.RightToLeft if he else Qt.LeftToRight)
        opt = self.build_view.document().defaultTextOption()
        opt.setTextDirection(Qt.RightToLeft if he else Qt.LeftToRight)
        self.build_view.document().setDefaultTextOption(opt)
        self.build_view.setHtml("\n".join(out))
        from .guides import fit_tables
        fit_tables(self.build_view)

    def _change_chips(self, rows: list[list[str]], changed: list) -> dict[tuple[int, int], str]:
        """(row, column) -> the "Changed in COT2" chips of the skills a SP table cell names, each skill only where
        the table first names it (a chip on every "Final Attack: Axe +n" row was noise). Only the "spend" and
        "result" columns (1 and 2, in either language's order) name skills; the reasons are prose."""
        fg, bg = CHANGED_CHIP.get(theme.MODE, CHANGED_CHIP["dark"])
        out: dict[tuple[int, int], str] = {}
        done: set[str] = set()
        for n, row in enumerate(rows[1:], start=1):
            for i in (1, 2):
                cell = row[i] if i < len(row) else ""
                for ch in changed:
                    if ch.key in done or not re.search(rf"(?<![\w:]){re.escape(ch.name)}(?![\w:])", cell):
                        continue
                    done.add(ch.key)
                    label = html.escape(sitedata.chip_label(self.t, ch)).replace(" ", "&nbsp;")
                    out[(n, i)] = out.get((n, i), "") + (
                        f" <a href='change:{ch.key}' style='text-decoration: none;'><span style='color: {fg}; "
                        f"background-color: {bg}; font-size: small; font-weight: 700;'>&nbsp;{label}&nbsp;</span></a>")
        return out

    def _skill_change_tip(self, link: str) -> None:
        if not link.startswith("change:"):
            terms.hide()
            return
        ch = sitedata.skill_change(self.kb, link.partition(":")[2])
        if ch:
            terms.show_html(bidi.to_html(sitedata.change_tip(self.t, ch), "rtl" if self.t.rtl else "ltr"), self.t.rtl)

    def _tier_card(self, rows: list) -> QFrame | None:
        """The community tier list for the player's job: one grade chip per column (S / A / B: the upper, middle
        and lower third of the ten 2nd jobs), the value and its place among them on hover. Compact: the build
        table under it is the page's own content."""
        if not rows:
            return None
        t, data = self.t, sitedata.tier_data(self.kb)
        level = data.get("level") or ""
        card = QFrame(objectName="Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)
        title = QLabel(self._p(t("tier_title", n=level)), objectName="CardName")
        src = source_tag(t, sources.COMMUNITY)
        src.setToolTip(tip_html(t("tier_tip", n=level), t.rtl))
        lay.addLayout(chip_row([src], title))
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        cols = list(data.get("columns") or [])
        for j, col in enumerate(cols, start=1):
            name, tip = self._tier_column(col)
            head = QLabel(self._p(name), objectName="CardSub")
            head.setAlignment(Qt.AlignCenter)
            head.setWordWrap(True)
            if tip:
                head.setToolTip(tip_html(tip, t.rtl))
            grid.addWidget(head, 0, j, Qt.AlignBottom)
        for i, r in enumerate(rows, start=1):
            grid.addWidget(QLabel(bidi.ltr_name(r.name, t.rtl), objectName="CardStat"), i, 0)
            for j, col in enumerate(cols, start=1):
                cell = r.cells.get(col) or {}
                grade, value = cell.get("grade") or "", cell.get("value") or "N/A"
                place, name = r.ranks.get(col), self._tier_column(col)[0]
                tip = (t("tier_cell_tip", col=name, value=value, place=place[0], total=place[1]) if place
                       else f"{name}: {value}")
                kind = {"S": "TagGood", "A": "TagWarn"}.get(grade, "Tag")
                grid.addWidget(info_tag(t, grade or "—", tip, kind), i, j, Qt.AlignCenter)
        for j in range(1, len(cols) + 1):
            grid.setColumnStretch(j, 1)          # equal columns: a two-word name wraps under its own grade
        lay.addLayout(grid)
        return card

    def _tier_column(self, col: str) -> tuple[str, str]:
        """A tier list column's short name and what it measures, in the UI language ("ST DPS" -> "DPS יחיד")."""
        k = "tier_col_" + re.sub(r"[^a-z0-9]+", "_", col.lower()).strip("_")
        t = self.t
        return (t(k) if t(k) != k else col), (t(k + "_tip") if t(k + "_tip") != k + "_tip" else "")

    def _skill_icons(self) -> list[tuple[str, str]]:
        """(skill name, picture file URI), longest names first so "Power Strike" wins over "Power"."""
        if "_skills" not in self.__dict__:
            out = []
            for k, e in self.kb.entities.items():
                if e.get("category") == "skill":
                    path = self.kb.picture(k)
                    if path:
                        out.append((e["name"], path.as_uri()))
            self._skills = sorted(out, key=lambda x: -len(x[0]))
        return self._skills

    @staticmethod
    def _short_skill_icons(rows: list[list[str]], icons: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """("Booster", its icon) for each full skill name the table uses ("Claw Booster"), when its last word
        names only that one skill there."""
        text = " ".join(" ".join(r) for r in rows)
        used = [(name, uri) for name, uri in icons if " " in name and name in text]
        by_last: dict[str, list[tuple[str, str]]] = {}
        for name, uri in used:
            by_last.setdefault(name.rsplit(" ", 1)[1], []).append((name, uri))
        return [(last, hits[0][1]) for last, hits in by_last.items() if len({n for n, _ in hits}) == 1]

    @staticmethod
    def _with_skill_icon(cell: str, icons: list[tuple[str, str]]) -> str:
        """Icons of the skills a table cell names ("Rush +1", "Power Strike 20, Slash Blast 3")."""
        if "[[img:" in cell:
            return ""
        found, taken = [], cell
        for name, uri in icons:
            at = re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", taken)
            if at and uri not in (u for _, u in found):
                found.append((at.start(), uri))
                taken = taken[:at.start()] + " " * len(name) + taken[at.end():]
        return "".join(f"<img src='{uri}' height='20' style='vertical-align: middle'> "
                       for _, uri in sorted(found)[:3])

    # quests --------------------------------------------------------------

    def _page_quests(self):
        t = self.t
        sc, lay = scroll_page(self.t.rtl)
        self.q_mode = Segmented([(t("q_now"), "now"), (t("q_soon"), "soon")], "now", t.rtl)
        self.q_mode.changed.connect(lambda *_: self._fill_quests(new_list=True))
        lay.addWidget(self.q_mode, 0, Qt.AlignHCenter)
        # search within the list shown (the quests that fit the character's level), not all quests
        self.q_search = QLineEdit()
        self.q_search.setPlaceholderText(t("q_search"))
        self.q_search.setAccessibleName(t("q_search"))        # a placeholder isn't read as the field's name
        self.q_search.setClearButtonEnabled(True)
        self._q_search_timer = QTimer(self, singleShot=True, interval=200)     # rebuild once typing pauses
        self._q_search_timer.timeout.connect(lambda: self._fill_quests(new_list=True))
        self.q_search.textChanged.connect(lambda *_: self._q_search_timer.start())
        follow_typing(self.q_search, t.rtl)
        lay.addWidget(self.q_search)
        self.q_head = self._label("", "ToolHeader")
        lay.addWidget(self.q_head)
        self.q_src = QHBoxLayout()
        lay.addLayout(self.q_src)
        self._source_line(self.q_src, sources.MEOWDB)
        # "show done quests" right under the header: under a list of 50 it was out of reach
        self.q_done_toggle = self._done_toggle()
        lay.addWidget(self.q_done_toggle, 0, Qt.AlignHCenter)
        self.q_done = QVBoxLayout()
        self.q_done.setSpacing(8)
        lay.addLayout(self.q_done)
        self.q_list = QVBoxLayout()
        self.q_list.setSpacing(8)
        lay.addLayout(self.q_list)
        lay.addStretch(1)
        self._q_limit = MAX_QUESTS
        return sc

    def _fill_quests(self, new_list: bool = False):
        t, c = self.t, self.c
        if new_list:                    # another list (Available / Coming up, a new search): its first cards again
            self._q_limit = MAX_QUESTS
        clear(self.q_list)
        clear(self.q_done)
        if not c:
            self.q_head.setText("")
            self.q_done_toggle.hide()
            self._no_character(self.q_list)
            return
        # profession quests follow the levels set on the crafting page; none set yet: shown, with their requirement
        r = quests.for_level(self.kb, c.level, c.base_class, c.job, c.quests_done, crafts=c.crafts or None)
        mode = self.q_mode.value()
        rows = r[mode]
        # how many are marked done is on the toggle right under the header, not here again
        self._set(self.q_head, t(f"q_head_{mode}", n=len(rows), lv=c.level))
        query = self.q_search.text().strip()
        if query:
            found = [q for q in rows if q.matches(query)]
            self._set(self.q_head, t("q_found", n=len(found), total=len(rows), lv=c.level))
            rows = found
            if not rows:
                self.q_list.addWidget(self._label(t("q_no_match"), "RowHint"))
        elif not rows:
            self.q_list.addWidget(self._label(t("q_none"), "RowHint"))
        for q in rows[:self._q_limit]:
            self.q_list.addWidget(self._quest_card(q))
        self._more_quests(self.q_list, len(rows), self._q_limit, "_q_limit")
        self._add_done(self.q_done_toggle, self.q_done, list(c.quests_done))

    def _more_quests(self, layout: QVBoxLayout, total: int, shown: int, limit: str):
        """Under a list cut at `shown` cards: "Showing 40 of 56 quests" and "Show more quests". The header counts
        every quest and the cut ones are the lowest-EXP ones, so without this they were out of reach (only a search
        found them). The numbers are in the line, not on the button: a button lays a Hebrew label with a number in
        it out of order."""
        if total <= shown:
            return
        layout.addWidget(self._label(self.t("q_shown", n=shown, total=total), "RowHint"))
        more = QPushButton(self._p(self.t("q_more")), objectName="Secondary")
        more.setCursor(Qt.PointingHandCursor)
        more.setAutoDefault(False)

        def show_more():
            setattr(self, limit, shown + MAX_QUESTS)
            self._refresh_in_place()
        more.clicked.connect(lambda *_: show_more())
        layout.addWidget(more, 0, Qt.AlignHCenter)

    def _picture_uri(self, kind: str, name: str) -> str | None:
        """The KB picture of a monster / item / NPC by its name, as a file URI."""
        n = name.strip().lower()
        if kind == "npc":
            key = self.kb._npc_by_name.get(n)
        elif kind == "item":
            key = self.kb._item_by_name.get(n)
        else:
            key = next((m.key for m in combat.monsters(self.kb) if m.name.lower() == n), None)
        path = self.kb.picture(key) if key else None
        if not path and re.search(r" x ?[\d,]+$", n):          # "Arrows for Bows x 500": the item itself
            return self._picture_uri(kind, re.sub(r" x ?[\d,]+$", "", n))
        return path.as_uri() if path else None

    def _thing_html(self, text: str) -> str:
        """ "Defeat Blue Snail x 10" / "Red Potion x 20" -> its picture, then the name (kept as one English block)."""
        m = re.fullmatch(r"(Defeat |Collect )?(.+?) x ([\d,]+)( \([\d.]+%\))?( \(.+\))?", text.strip())
        if not m:
            return html.escape(text)
        verb, name, n, odds, note = m.groups()
        n += odds or ""                 # a random reward keeps its odds: "Bronze Ore x7 (16.7%)"
        uri = self._picture_uri("monster" if verb == "Defeat " else "item", name) or \
            self._picture_uri("item" if verb == "Defeat " else "monster", name)
        img = f"<img src='{uri}' height='24' style='vertical-align: middle'>&nbsp;" if uri else ""
        # picture and name in one left-to-right unit, so in Hebrew the picture stays beside its own name
        # a note in the player's language after it ("(male character)", Quest.rewards_gender), outside the block
        return (f"<span style='white-space: nowrap'>{bidi.LRE}{img}{html.escape(name)} x{n}{bidi.PDF}{bidi.RLM}</span>"
                + html.escape(note or ""))

    def _things_label(self, head: str, things: list[str], extra: str = "") -> QLabel:
        """A heading, then one thing per line: its picture beside its own name, never split by a wrap."""
        side = "dir='rtl' align='right'" if self.t.rtl else "dir='ltr' align='left'"
        lines = [f"<p {side} style='margin:0 0 2px 0;'><b>{html.escape(head)}</b></p>"]
        lines += [f"<p {side} style='margin:0 0 2px 0;'>{self._thing_html(x)}</p>" for x in things]
        if extra:
            lines.append(f"<p {side} style='margin:0 0 2px 0;'>{bidi.LRE}{html.escape(extra)}{bidi.PDF}</p>")
        lb = QLabel("".join(lines), objectName="CardSub")
        lb.setTextFormat(Qt.RichText)
        lb.setWordWrap(True)
        return lb

    def _quest_card(self, q: quests.Quest, done: bool = False) -> QFrame:
        t = self.t
        card = QFrame(objectName="Card")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(12)
        npc = QLabel()
        npc.setFixedSize(52, 60)
        npc.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        uri = self._picture_uri("npc", q.npc) if q.npc else None
        if uri:
            pm = QPixmap(QUrl(uri).toLocalFile())
            if not pm.isNull():
                npc.setPixmap(pm.scaled(52, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        outer.addWidget(npc, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        outer.addLayout(col, 1)
        top = QHBoxLayout()
        # an English name is one block: "[Construction Site B1] Shumi's Lost Coin" keeps its brackets in place
        name = QLabel(bidi.ltr_name(q.name, t.rtl), objectName="CardName")
        name.setWordWrap(True)
        top.addWidget(name, 1)
        # the level it can be done at: one taken at 12 but finished only at 32 is a Lv. 32 quest ("soon" at 31)
        top.addWidget(tag(self._p(t("lv_short", n=q.opens_at())), "Tag"))
        if q.exp:
            top.addWidget(tag(f"+{q.exp:,} EXP", "TagGood"))
        col.addLayout(top)
        where = [x for x in (q.npc, q.area) if x]
        if where:
            col.addWidget(self._label(" · ".join(where), "CardSub"))
        if q.needs:
            col.addWidget(self._things_label(t("q_needs_head"), q.needs[:4]))
        gets = q.rewards[:3]
        extra = " · ".join(x for x in (f"{q.mesos:,} mesos" if q.mesos else "", f"+{q.fame} Fame" if q.fame else "") if x)
        if gets or extra:
            col.addWidget(self._things_label(t("q_gets_head"), gets, extra))
        # "Pick one (class-specific)": the player's own class's choices (and "Any Class"), not the first class listed
        base = self.c.base_class if self.c else ""
        for head, things in (("q_pick_head", q.rewards_pick(base)), ("q_random_head", q.rewards_random(base))):
            if things:
                shown = things[:4] + ([t("pn_more", n=len(things) - 4)] if len(things) > 4 else [])
                col.addWidget(self._things_label(t(head), shown))
        # a reward that depends on the character's gender: both listed, each marked (the profile has no gender)
        by_gender = q.rewards_gender(t)
        if by_gender:
            col.addWidget(self._things_label(t("q_gender_head"), by_gender))
        hints = []
        if q.after:
            hints.append(t("q_after", name=bidi.ltr_block(q.after, t.rtl)))
        if q.complete_level > q.level:
            hints.append(t("q_complete_lv", n=q.complete_level, take=q.level))
        if q.grade:
            hints.append(t("q_grade", town=q.grade[0], n=q.grade[1]))
        if q.profession:
            hints.append(t("q_profession", prof=q.profession[0], n=q.profession[1]))
        hints += q.prereq_hints(t)          # Fame, a fee to accept, any other pre-requisite line of the page
        if hints:
            col.addWidget(self._label("\n".join(hints), "RowHint"))
        acts = QHBoxLayout()
        acts.setSpacing(16)        # the link has no padding of its own: apart from the button, not glued to it
        if done:
            # marked done by mistake (or a repeatable donation to do again): back to the list
            btn = QPushButton(self._p(t("q_undo")), objectName="Secondary")
            btn.clicked.connect(lambda _=False, k=q.key: self._quest_undo(k))
        else:
            btn = QPushButton(self._p(t("q_mark_done")), objectName="Secondary")
            btn.clicked.connect(lambda _=False, k=q.key: self._quest_done(k))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setAutoDefault(False)
        acts.addWidget(btn)
        ask = QPushButton(self._p(t("ask_short")), objectName="Link")
        ask.setCursor(Qt.PointingHandCursor)
        ask.clicked.connect(lambda _=False, k=q.key: self.tag_requested.emit(k))
        acts.addWidget(ask)
        acts.addStretch(1)
        col.addLayout(acts)
        return card

    def _quest_done(self, key: str):
        c = self.c
        if c and key not in c.quests_done:
            c.quests_done.append(key)
            # done here is done for the chat too: the started quest leaves "Active quests" in the AI's prompt
            q = quests.quest(self.kb, key)
            if q:
                c.finish_quest(q.name)
            self.profiles.save()
        self._refresh_in_place()

    def _quest_undo(self, key: str):
        c = self.c
        if c and key in c.quests_done:
            c.quests_done.remove(key)
            self.profiles.save()
        self._refresh_in_place()

    def _refresh_in_place(self):
        """Redraw the page where the player is reading (the list is rebuilt: it jumped to its end, seen live)."""
        from PySide6.QtWidgets import QScrollArea
        page = self.stack.currentWidget()
        bar = page.verticalScrollBar() if isinstance(page, QScrollArea) else None
        at = bar.value() if bar else 0
        self.refresh()
        if bar:
            bar.setValue(at)
            QTimer.singleShot(0, lambda: bar.setValue(at))      # again once the new cards have their size

    def _done_toggle(self) -> QPushButton:
        """The "show quests marked done (n)" toggle; open, _add_done lists those quests under it."""
        b = QPushButton(objectName="Link")
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setAutoDefault(False)
        b.toggled.connect(lambda *_: self.refresh())
        return b

    def _add_done(self, toggle: QPushButton, layout: QVBoxLayout, keys: list[str]):
        """The toggle's label (with how many are done), and when it's open the done quests (newest first) right
        under the toggle: a small header, then muted cards, each with a way back to the list."""
        t = self.t
        toggle.setVisible(bool(keys))
        if not keys and toggle.isChecked():
            # nothing left to show: closed again, or the next quest marked done would open the list on its own.
            # Quietly: its toggled signal redraws the page, and this is already inside a redraw
            toggle.blockSignals(True)
            toggle.setChecked(False)
            toggle.blockSignals(False)
        toggle.setText(self._p(t("q_hide_done" if toggle.isChecked() else "q_show_done", n=len(keys))))
        if not (keys and toggle.isChecked()):
            return
        cards = [self._quest_card(q, done=True) for k in reversed(keys[-MAX_QUESTS:]) if (q := quests.quest(self.kb, k))]
        if not cards:
            return
        layout.addWidget(self._label(t("q_done_head"), "PlanHead"))
        for card in cards:
            fade = QGraphicsOpacityEffect(card)       # muted: done, not something to do
            fade.setOpacity(0.6)
            card.setGraphicsEffect(fade)
            layout.addWidget(card)
        layout.addSpacing(12)                         # apart from what follows (the quests to take)

    # crafting ------------------------------------------------------------

    def _page_crafting(self):
        t = self.t
        sc, lay = scroll_page(self.t.rtl)
        # the professions live inside this tab's own card, in a lighter style than the main tabs
        sec = Section(t("craft_profession"), t.rtl)
        grid = FlowLayout(spacing=6)            # wraps: three long names on one row were wider than the window
        self.craft_pick = QButtonGroup(self)
        for i, prof in enumerate(crafting.PROFESSIONS):
            b = QPushButton(crafting.NAMES[prof], objectName="SubChip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("prof", prof)
            self.craft_pick.addButton(b, i)
            grid.addWidget(b)
        self.craft_pick.button(0).setChecked(True)
        self.craft_pick.idClicked.connect(lambda *_: self._fill_crafting())
        holder = QWidget()
        holder.setLayout(grid)
        sec.add_widget(holder)
        self.craft_level = Stepper(1, 10, 1)
        self.craft_level.valueChanged.connect(self._set_craft_level)
        self._row(sec, t("craft_my_level"), self.craft_level, hint=t("craft_level_hint"))
        lay.addWidget(sec)
        self.craft_info = QVBoxLayout()           # who teaches the profession, where you work it
        lay.addLayout(self.craft_info)
        self.craft_head = self._label("", "ToolHeader")
        lay.addWidget(self.craft_head)
        self.craft_src = QHBoxLayout()
        lay.addLayout(self.craft_src)
        self._source_line(self.craft_src, sources.MEOWDB)
        self.craft_list = QVBoxLayout()
        self.craft_list.setSpacing(8)
        lay.addLayout(self.craft_list)
        lay.addStretch(1)
        return sc

    def _prof(self) -> str:
        return self.craft_pick.checkedButton().property("prof")

    def _set_craft_level(self, v: int):
        c = self.c
        if c:
            c.crafts = {**(c.crafts or {}), self._prof(): v}
            self.profiles.save()
        self._fill_crafting()

    def _fill_crafting(self):
        t, c = self.t, self.c
        clear(self.craft_list)
        clear(self.craft_info)
        self.craft_info.addWidget(self._craft_info_card(self._prof()))
        if not c:
            self._no_character(self.craft_list)
            return
        prof = self._prof()
        top = crafting.max_level(self.kb, prof)
        lv = int((c.crafts or {}).get(prof, 1))
        self.craft_level.blockSignals(True)
        self.craft_level.setMaximum(top)          # the + button and the typed-value check follow the new top
        self.craft_level.setValue(min(lv, top))
        self.craft_level.blockSignals(False)
        _, nxt = crafting.for_level(self.kb, prof, min(lv, top))
        # everything you can craft so far, not only what this exact level opened (newest first)
        recipes = crafting.up_to(self.kb, prof, min(lv, top))
        head = t("craft_head", prof=crafting.NAMES[prof], lv=lv, n=len(recipes))
        if nxt and nxt.needs_exp:
            head += "\n" + t("craft_next", lv=nxt.level, exp=f"{nxt.needs_exp:,}", char=nxt.char_level or "?")
        self._set(self.craft_head, head)
        if not recipes:
            self.craft_list.addWidget(self._label(t("craft_none"), "RowHint"))
            return
        for i, r in enumerate(recipes):
            self.craft_list.addWidget(self._recipe_card(r, best=(i == 0)))

    def _craft_info_card(self, prof: str) -> QFrame:
        """The profession explained: what it makes, its teacher (and town), the quests, the work stations."""
        t = self.t
        i = crafting.info(self.kb, prof)
        card = QFrame(objectName="Card")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(12)
        pic = QLabel()
        pic.setFixedSize(52, 60)
        pic.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        uri = self._picture_uri("npc", i.teacher) if i.teacher else None
        if uri:
            pm = QPixmap(QUrl(uri).toLocalFile())
            if not pm.isNull():
                pic.setPixmap(pm.scaled(52, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        outer.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        outer.addLayout(col, 1)
        col.addWidget(self._label(f"**{crafting.NAMES[prof]}**", "CardName"))
        col.addWidget(self._label(t(f"craft_makes_{prof}"), "RowLabel"))
        if i.teacher:
            col.addWidget(self._label(t("craft_teacher", npc=i.teacher, town=i.teacher_town or "?"), "RowLabel"))
        if i.start_quest:
            col.addWidget(self._label(t("craft_start", quest=i.start_quest.rstrip("!"), lv=i.start_level or "?"), "RowLabel"))
        if i.master_quest:
            col.addWidget(self._label(t("craft_master", quest=i.master_quest.rstrip("!"), lv=i.master_level or "?"), "RowLabel"))
        if i.station_towns:
            col.addWidget(self._label(t("craft_station", station=i.station, towns=" · ".join(i.station_towns)),
                                      "RowLabel"))
        name = crafting.NAMES[prof]
        col.addWidget(self._ask_link(lambda: self.ask_requested.emit(t("craft_ask", prof=name), False)))
        return card

    def _recipe_card(self, r: crafting.Recipe, best: bool) -> QFrame:
        t = self.t
        card = QFrame(objectName="Card")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(12)
        pic = QLabel()
        pic.setFixedSize(44, 44)
        pic.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        uri = self._picture_uri("item", r.name)
        if uri:
            pm = QPixmap(QUrl(uri).toLocalFile())
            if not pm.isNull():
                pic.setPixmap(pm.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        outer.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        outer.addLayout(col, 1)
        col.addWidget(self._label(f"**{r.name}**", "CardName"))
        why = FlowLayout(spacing=5)               # the tags wrap at a narrow width
        if best:
            why.addWidget(tag(self._p(t("craft_best")), "TagAccent"))
        why.addWidget(tag(self._p(t("craft_lv_tag", n=r.level)), "Tag"))     # the list spans several levels
        why.addWidget(tag(f"+{r.exp} EXP", "TagGood"))
        why.addWidget(tag(self._p(t("craft_cost", n=f"{r.catalyst:,}")), "Tag"))
        col.addLayout(why)
        col.addWidget(self._things_label(t("craft_needs"), [f"{name} x {n}" for n, name in r.ingredients]))
        net = t("craft_net_gain", n=f"{r.net:,}") if r.net >= 0 else t("craft_net_loss", n=f"{-r.net:,}")
        col.addWidget(self._label(net, "RowHint"))
        col.addWidget(self._ask_link(lambda: self.ask_requested.emit(t("craft_ask_recipe", item=r.name), False)))
        return card

    # citizenship ---------------------------------------------------------

    def _page_town(self):
        t = self.t
        sc, lay = scroll_page(self.t.rtl)
        sec = Section(t("town_title"), t.rtl)
        self.town_pick = Segmented([(name, name) for name in quests.TOWNS], quests.TOWNS[0], t.rtl)
        self.town_pick.changed.connect(self._set_town)
        sec.add_row(t("town_mine"), self.town_pick)
        self.town_advice = self._label("", "RowLabel")
        sec.add_widget(self.town_advice)
        self.town_basics = self._label(t("town_basics"), "RowHint")
        sec.add_widget(self.town_basics)
        lay.addWidget(sec)
        self.town_head = self._label("", "ToolHeader")
        lay.addWidget(self.town_head)
        self.town_src = QHBoxLayout()
        lay.addLayout(self.town_src)
        self._source_line(self.town_src, sources.MEOWDB)
        self.town_list = QVBoxLayout()
        self.town_list.setSpacing(8)
        lay.addLayout(self.town_list)
        self.town_done_toggle = self._done_toggle()      # donations repeat: one marked done can come back
        lay.addWidget(self.town_done_toggle, 0, Qt.AlignHCenter)
        self.town_done = QVBoxLayout()
        self.town_done.setSpacing(8)
        lay.addLayout(self.town_done)
        lay.addStretch(1)
        self._town_limit = MAX_QUESTS
        return sc

    def _set_town(self, *_):
        c = self.c
        if c:
            c.town = self.town_pick.value()
            self.profiles.save()
        self._town_limit = MAX_QUESTS         # another town's list: its first cards
        self.refresh("town")

    def _fill_town(self):
        t, c = self.t, self.c
        clear(self.town_list)
        clear(self.town_done)
        self.town_done_toggle.hide()
        if not c:
            self._no_character(self.town_list)
            return
        rec, paras = buildplan.citizenship_advice(self.kb, c.base_class, c.job, t.lang)
        town = c.town or rec or quests.TOWNS[0]
        self.town_pick.blockSignals(True)
        for b in self.town_pick.findChildren(QPushButton):
            b.setChecked(b.property("value") == town or b.text() == town)
        self.town_pick.blockSignals(False)
        # the guide's advice, one sentence per line, the grades and towns in bold
        lines = [t("town_recommended", town=rec, job=c.job_label or c.base_class)] if rec else []
        for para in paras[:2]:
            for sentence in re.split(r"(?<=[.!?])\s+", guides._ICON.sub("", para).strip()):
                if sentence.strip():
                    lines.append("• " + _bold_names(sentence.strip()))
        self._set(self.town_advice, "\n".join(lines) if lines else t("town_no_advice"))
        rows = quests.citizenship(self.kb, town, c.level, c.quests_done)
        self._set(self.town_head, t("town_head", n=len(rows), town=town))
        if c.level < 12:
            self.town_list.addWidget(self._label(t("town_too_low"), "RowHint"))
            return
        if not rows:
            self.town_list.addWidget(self._label(t("q_none"), "RowHint"))
        for q in rows[:self._town_limit]:
            self.town_list.addWidget(self._quest_card(q))
        self._more_quests(self.town_list, len(rows), self._town_limit, "_town_limit")
        mine = [k for k in c.quests_done
                if (q := quests.quest(self.kb, k)) and q.area == "Citizenship" and quests.town_of(self.kb, q) == town]
        self._add_done(self.town_done_toggle, self.town_done, mine)

    # prices --------------------------------------------------------------

    def _page_prices(self):
        t = self.t
        sc, lay = scroll_page(self.t.rtl)
        # a term gets its "?" once on this page: in the intro, not again in the card, the market line and the hint
        self._price_seen: set = set()
        lay.addWidget(self._label(t("prices_intro"), "ToolHeader", seen=self._price_seen))
        rows = item_rows(self.kb)
        self.price_input = EntityPicker(rows, self._p(t("price_placeholder", n=f"{len(rows):,}")), icon=32, rtl=t.rtl)
        self.price_input.picked.connect(self._fill_prices)
        lay.addWidget(self.price_input)
        self.price_box = QVBoxLayout()
        self.price_box.setSpacing(12)
        lay.addLayout(self.price_box)
        lay.addWidget(self._label(t("price_hint"), "RowHint", seen=set(self._price_seen)))
        lay.addStretch(1)
        self.market_ready.connect(self._on_market)
        return sc

    def _fill_prices(self):
        t = self.t
        clear(self.price_box)
        name = self.price_input.text().strip()
        key = self.kb._item_by_name.get(name.lower()) if name else None
        if name and not key:
            # part of a name, like the damage calculator takes it ("Blue Pot" -> Blue Potion): the shortest match
            q = name.lower()
            part = sorted((n for n in self.kb._item_by_name if q in n), key=lambda n: (len(n), n))
            if part:
                key = self.kb._item_by_name[part[0]]
                name = (self.kb.get(key) or {}).get("name", name)
        if not key:
            if name:
                self.price_box.addWidget(self._label(t("price_none"), "RowHint"))
            return
        npc = market.npc_prices(self.kb, key)
        card = QFrame(objectName="Card")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(12)
        pic = QLabel()
        pic.setFixedSize(48, 48)
        pic.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        path = self.kb.picture(key)
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull():
                pic.setPixmap(pm.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        outer.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        outer.addLayout(col, 1)
        seen = self._card_seen = set(self._price_seen)
        updated = updated_tag(t, self.kb, key)
        title = self._label(f"**{name}**", "CardName", seen=seen)
        if updated:
            col.addLayout(chip_row([updated], title))
        else:
            col.addWidget(title)
        # each price with where it comes from: the item page's values (its build, e.g. "COT2"), a shop price's
        # own label ("COT2 prices"), the Free Market's player reports (community)
        stamp = sources.stat_source(self.kb, key)
        lines: list[tuple[str, list[str]]] = []
        if npc.sell_back is not None:
            lines.append((t("price_npc_buys", n=f"{npc.sell_back:,}"), [sources.source_of(self.kb, key)]))
        shops = [s for s in npc.shops if combat.released(self.kb, s[1])]      # no El Nath / Orbis shop before they open
        if shops:
            cheapest = shops[0]
            line = t("price_shop", n=f"{cheapest[2]:,}", npc=cheapest[0], where=cheapest[1].split(" · ")[-1])
            rank = npc.ranks.get(cheapest[:2])        # a town shop's item for a citizen grade and up
            if rank:
                line += " " + t("price_rank", rank=rank)
            # the KB labels these prices with a build (the COT2 test's, not confirmed for launch): its chip says so
            lines.append((line, [npc.source(cheapest)]))
        # NPCs the page lists without a price still sell it: name them
        unpriced = [s for s in npc.unpriced if combat.released(self.kb, s[1])][:3]
        if unpriced:
            who = ", ".join(f"{n} ({w.split(' · ')[-1]})" for n, w in unpriced)
            lines.append((t("price_sold_by_also" if shops else "price_sold_by", npcs=who), []))
        if not lines:
            lines.append((t("price_no_npc"), []))
        self.price_chips = []
        for text, srcs in lines:
            chips = source_tags(t, srcs, stamp)
            self.price_chips += chips
            col.addLayout(chip_row(chips, self._label(text, "RowLabel", seen=seen), lead=True))
        self.fm_label = self._label(t("price_fm_loading"), "RowLabel", seen=set(seen))
        self.fm_chip = source_tag(t, sources.COMMUNITY)
        col.addLayout(chip_row([self.fm_chip], self.fm_label, lead=True))
        # the rest of the item's Free Market (offers, trend, newest listings) under it, once it's in (_on_market)
        self.fm_more = QVBoxLayout()
        self.fm_more.setSpacing(4)
        col.addLayout(self.fm_more)
        slug = key.split("/", 1)[1]
        item_id = int(slug) if slug.isdigit() else None        # the KB's item ids are NiaMeowDB's
        web = QPushButton(self._p(t("price_open_site")), objectName="Link")
        web.setCursor(Qt.PointingHandCursor)
        web.clicked.connect(lambda _=False, n=name, i=item_id: __import__("webbrowser").open(
            market.item_page(i) if i else market.page_url(n)))
        col.addWidget(web, 0, (Qt.AlignRight if t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute)
        self.price_box.addWidget(card)
        self._price_for = name
        import threading

        def lookup(n=name, i=item_id):
            # the item page's own market (usual price, offers, trend, listings) by id; by name for an item
            # without one. Up to 10 s on a slow connection
            found = market.item_market(i) if i else market.free_market(n)
            try:
                self.market_ready.emit((n, found))
            except RuntimeError:
                # the window was closed (and deleted) meanwhile: nothing to show it in. Uncaught, this logged a
                # CRITICAL "uncaught exception in thread" into the log that goes with problem reports
                pass
        threading.Thread(target=lookup, daemon=True).start()

    def _on_market(self, result):
        t = self.t
        name, m = result
        if name != getattr(self, "_price_for", None) or not hasattr(self, "fm_label"):
            return                      # an older lookup, the player picked another item since
        more: list[str] = []
        if m is None:
            text = t("price_fm_offline")
        elif isinstance(m, market.ItemMarket):
            text, more = self._fm_lines(m)
        elif not m.count:
            text = t("price_fm_empty")
        else:
            text = t("price_fm", median=f"{m.median:,}", n=m.count, low=f"{m.low:,}", high=f"{m.high:,}")
        seen = set(getattr(self, "_card_seen", ()))
        try:
            self._set(self.fm_label, text, seen)
            clear(self.fm_more)
            for line in more:
                self.fm_more.addWidget(self._label(line, "RowHint", seen=seen))
        except RuntimeError:
            pass                        # the card was redrawn meanwhile

    def _fm_lines(self, m) -> tuple[str, list[str]]:
        """An item's Free Market in words: the usual price (the line beside the community chip), then the offers
        now, the trend and volume, and the newest listings (smaller, under it)."""
        t = self.t
        if m.empty:
            return t("price_fm_empty"), []
        if m.usual:
            n = m.checks + m.trades
            head = t("price_fm_usual", price=f"{m.usual:,}", n=n, days=m.window)
        else:
            head = t("price_fm_no_usual", days=m.window)
        more = []
        offers = []
        if m.cheapest_sell:
            offers.append(t("price_fm_cheapest", price=f"{m.cheapest_sell:,}"))
        if m.best_buy:
            offers.append(t("price_fm_best_buy", price=f"{m.best_buy:,}"))
        if m.for_sale:
            offers.append(t("price_fm_for_sale", n=m.for_sale))
        if offers:
            more.append(" · ".join(offers))
        if m.trend_pct is not None and m.trend_days:
            arrow = "▲" if m.trend_pct > 0 else "▼" if m.trend_pct < 0 else "="
            more.append(t("price_fm_trend", trend=f"{arrow} {abs(m.trend_pct)}%", days=m.trend_days, n=m.volume,
                          window=market.TREND_DAYS))
        elif m.volume:
            more.append(t("price_fm_volume", n=m.volume, window=market.TREND_DAYS))
        if m.listings:
            more.append(t("price_fm_listings"))
            for x in m.listings:
                where = [t("price_fm_ch", n=x.channel)] if x.channel is not None else []
                if x.room is not None:
                    where.append(t("price_fm_entrance") if x.room == 0 else t("price_fm_room", n=x.room))
                bits = [t("price_fm_selling" if x.side == "sell" else "price_fm_buying", n=x.quantity,
                          price=f"{x.price:,}")] + where + ([market_ago(t, x.created)] if x.created else [])
                more.append("• " + " · ".join(bits))
        return head, more

    # grind tracker ---------------------------------------------------------
    # A session measured from screenshot reads at Start, Update and End (each read costs the player's AI plan, so
    # nothing reads on its own). grind.py does the math; this page shows what is measured as a plain number and
    # what is estimated with "~" and a tooltip saying how.

    GRIND_CELLS = ("time", "exp", "exp_h", "mesos", "mesos_h", "net", "kills", "kills_h", "potions")

    def _page_exp(self):
        t = self.t
        sc, lay = scroll_page(self.t.rtl)
        # a term gets its "?" once on this page, in the intro (as on the prices page)
        self._grind_seen: set = set()
        lay.addWidget(self._label(t("grind_intro"), "ToolHeader", seen=self._grind_seen))
        sec = Section(t("grind_current"), t.rtl)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 8, 0, 8)
        tl.setSpacing(4)
        self.grind_tag = tag("", "TagGood")
        self.grind_state = self._gl("", "RowLabel")
        tl.addLayout(chip_row([self.grind_tag], self.grind_state, lead=True))
        self.grind_map = self._gl("", "CardSub")
        tl.addWidget(self.grind_map)
        sec.add_widget(top)
        mon = QWidget()
        ml = QVBoxLayout(mon)
        ml.setContentsMargins(0, 8, 0, 8)
        ml.setSpacing(4)
        ml.addWidget(self._gl(t("grind_monster"), "RowLabel"))
        rows = monster_rows(self.kb)
        self.grind_monster = EntityPicker(rows, self._p(t("grind_monster_ph", n=len(rows))), rtl=t.rtl)
        self.grind_monster.picked.connect(self._grind_pick_monster)
        self.grind_monster.textChanged.connect(lambda text: None if text.strip() else self._grind_pick_monster())
        ml.addWidget(self.grind_monster)
        ml.addWidget(self._gl(t("grind_monster_hint"), "RowHint"))
        sec.add_widget(mon)
        cells = QWidget()
        grid = QGridLayout(cells)
        grid.setContentsMargins(0, 10, 0, 6)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(12)
        self.grind_cells = {}
        for i, key in enumerate(self.GRIND_CELLS):
            box = QWidget()
            bl = self._big("–", self._p(t(f"grind_{key}")), explain=False)      # the tooltip explains it
            bl.setContentsMargins(0, 0, 0, 0)
            box.setLayout(bl)
            grid.addWidget(box, i // 3, i % 3)
            grid.setColumnStretch(i % 3, 1)
            self.grind_cells[key] = (box, bl.itemAt(0).widget())
        sec.add_widget(cells)
        lines = QWidget()
        self.grind_lines = QVBoxLayout(lines)
        self.grind_lines.setContentsMargins(0, 8, 0, 8)
        self.grind_lines.setSpacing(6)
        sec.add_widget(lines)
        auto = QWidget()
        arow = QHBoxLayout(auto)
        arow.setContentsMargins(0, 8, 0, 8)
        arow.setSpacing(10)
        acol = QVBoxLayout()
        acol.setSpacing(1)
        acol.addWidget(self._gl(t("grind_auto"), "RowLabel"))
        acol.addWidget(self._gl(t("grind_auto_hint"), "RowHint"))
        self.grind_auto_state = self._gl("", "RowHint")
        acol.addWidget(self.grind_auto_state)
        arow.addLayout(acol, 1)
        self.grind_auto = Switch(self.runner.auto)
        self.grind_auto.setAccessibleName(t("grind_auto"))
        self.grind_auto.setAccessibleDescription(t("grind_auto_hint"))
        self.grind_auto.toggled.connect(self._grind_set_auto)
        arow.addWidget(self.grind_auto, 0, Qt.AlignVCenter)
        sec.add_widget(auto)
        foot = QWidget()
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(0, 8, 0, 8)
        fl.setSpacing(8)
        self.grind_status = self._gl("", "RowHint")
        fl.addWidget(self.grind_status)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.grind_start = QPushButton(self._p(t("grind_start")), objectName="Primary")
        self.grind_update = QPushButton(self._p(t("grind_update")), objectName="Primary")
        self.grind_end = QPushButton(self._p(t("grind_end")), objectName="Secondary")
        for b, then in ((self.grind_start, "start"), (self.grind_update, "update"), (self.grind_end, "end")):
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, w=then: self._grind_read(w))
            btns.addWidget(b)
        btns.addStretch(1)
        fl.addLayout(btns)
        sec.add_widget(foot)
        lay.addWidget(sec)
        recent = Section(t("grind_recent"), t.rtl)
        box = QWidget()
        self.grind_table = QGridLayout(box)
        self.grind_table.setContentsMargins(0, 8, 0, 8)
        self.grind_table.setHorizontalSpacing(10)
        self.grind_table.setVerticalSpacing(6)
        recent.add_widget(box)
        self.grind_ask = QPushButton(self._p(t("grind_ask")), objectName="Link")
        self.grind_ask.setCursor(Qt.PointingHandCursor)
        self.grind_ask.setAutoDefault(False)
        self.grind_ask.clicked.connect(self._grind_ask)
        ask = self.grind_ask_row = QWidget()
        al = QHBoxLayout(ask)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(self.grind_ask, 0, Qt.AlignLeft)       # AlignLeft is the leading edge (mirrored in Hebrew)
        al.addStretch(1)
        recent.add_widget(ask)
        lay.addWidget(recent)
        lay.addStretch(1)
        self._grind_choice = ""            # a monster picked before the session starts
        # the session time and "updated 20 s ago" move on between reads (only the clock: the numbers change at a read)
        self._grind_clock = QTimer(self, interval=10_000, timeout=self._grind_tick)
        self._grind_clock.start()
        return sc

    def _gl(self, text: str, obj: str) -> QLabel:
        """A grind tracker label: the terms the intro explained get no second "?"."""
        return self._label(text, obj, seen=set(self._grind_seen))

    def _gs(self, label: QLabel, text: str) -> None:
        self._set(label, text, set(self._grind_seen))

    def _grind_read(self, what: str):
        """Start / Update / End: a screenshot read (the chat runs it), then the runner takes it into the session. An
        Update or End pressed while the minute's automatic read is on its way rides on that read."""
        c = self.c
        if not c:
            return
        how = self.runner.ask(what, c.id, self._grind_choice if what == "start" else "")
        if not how:
            return
        self._gs(self.grind_status, self.t("grind_reading"))
        self._grind_buttons(busy=True)
        if how == "read":
            self._step_aside(self.grind_sync_requested.emit)

    def grind_read(self, r):
        """The AI's reply to a grind read (character id, profile_update, grind), just before sync_done (a window
        with its own runner: the app's gets it from the chat itself)."""
        if self._own_runner:
            self.runner.grind_read(r)

    def grind_changed(self):
        """A read landed, failed or was skipped: the page follows, once it is built."""
        try:
            if "exp" not in self.__dict__.get("_pending", ()):
                self.refresh("exp")
        except RuntimeError:       # the window closed meanwhile
            pass

    def _grind_set_auto(self, on: bool):
        self.runner.set_auto(on)
        self._fill_auto_state()

    def _asked_read(self) -> bool:
        """A read the player asked for is on its way (the buttons wait for it; the minute's own read doesn't)."""
        p = self.runner.busy()
        return bool(p and p[0] != "auto")

    def _grind_pick_monster(self):
        c = self.c
        name = self.grind_monster.text().strip()
        if name and not plan.monster_exp(self.kb, name):
            # a name typed part way ("horny"): the closest monster, as the calculator takes it
            q = name.lower()
            part = sorted((r[1] for r in monster_rows(self.kb) if q in r[1].lower()), key=len)
            name = part[0] if part else name
            self.grind_monster.setText(name)
            self.grind_monster.setCursorPosition(0)
        if c and self.grind.running(c.id):
            self.grind.set_monster(c.id, name)
        else:
            self._grind_choice = name           # for the next session
        self.refresh("exp")

    def _grind_buttons(self, busy: bool = False):
        c = self.c
        running = bool(c and self.grind.running(c.id))
        self.grind_start.setVisible(not running)
        self.grind_update.setVisible(running)
        self.grind_end.setVisible(running)
        for b in (self.grind_start, self.grind_update, self.grind_end):
            b.setEnabled(bool(c) and not busy)

    def _grind_tick(self):
        try:
            if self.stack.currentIndex() == PAGES.index("exp") and self.isVisible() and not self._asked_read():
                self._fill_exp_clock()
        except RuntimeError:       # the window closed meanwhile
            pass

    def _num(self, n: int | float | None, sign: bool = False, approx: bool = False) -> str:
        """A number for a big cell: in full up to 99,999, then 123.4K / 1.23M (the exact one is in the tooltip).
        One left-to-right block in Hebrew, so "-1,200" and "~45K" keep their sign on the left."""
        if n is None:
            return "–"
        a = abs(n)
        text = f"{a:,.0f}" if a < 100_000 else f"{a / 1000:.1f}K" if a < 1_000_000 else f"{a / 1_000_000:.2f}M"
        text = ("-" if n < 0 else "+" if sign and n > 0 else "") + text
        return bidi.ltr_block(("~" if approx else "") + text, self.t.rtl)

    def _clock(self, seconds: float | None) -> str:
        """A duration: "47 min" under an hour, "1:12 h" from one (a bare "0:47" read like seconds)."""
        if seconds is None:
            return "–"
        m = int(seconds) // 60
        return self.t("grind_hours", h=m // 60, m=f"{m % 60:02d}") if m >= 60 else self.t("grind_minutes", m=m)

    def _cell(self, key: str, value: str, tip: str = ""):
        box, label = self.grind_cells[key]
        label.setText(value)
        box.setToolTip(tip_html(tip, self.t.rtl) if tip else "")
        label.setAccessibleName(f"{self.t(f'grind_{key}')}: {value}" + (f". {tip}" if tip else ""))

    def _fill_exp_clock(self):
        """The session's age and the time since its last read (every 30 s, between reads)."""
        t, c = self.t, self.c
        s = self.grind.session(c.id) if c else None
        if not s:
            self.grind_tag.hide()
            self.grind_auto_state.hide()
            self._gs(self.grind_state, t("grind_idle_state"))
            self._cell("time", "–")
            return
        self.grind_tag.show()
        self.grind_tag.setObjectName("Tag" if s.ended else "TagGood")
        self.grind_tag.setText(self._p(t("grind_tag_ended" if s.ended else "grind_tag_running")))
        self.grind_tag.style().unpolish(self.grind_tag)
        self.grind_tag.style().polish(self.grind_tag)
        end = s.ended or time.time()
        self._cell("time", self._p(self._clock(end - s.start)), t("grind_tip_time"))
        if s.ended:
            self.grind_auto_state.hide()
            self._gs(self.grind_state, t("grind_ended_state", time=self._clock(end - s.start)))
            return
        self._gs(self.grind_state, t("grind_started_ago", n=round((time.time() - s.start) / 60)))
        self._fill_auto_state(s)

    def _fill_auto_state(self, s=None):
        """Under the auto-update switch: when the numbers were last read, or why they wait."""
        t, c, r = self.t, self.c, self.runner
        s = s or (self.grind.running(c.id) if c else None)
        if not s or s.ended:
            self.grind_auto_state.hide()
            return
        p = r.busy()
        if p and p[0] == "auto":
            text = t("grind_auto_reading")
        elif r.auto and r.waiting:
            text = t("grind_waiting_covered" if r.waiting == "covered" else "grind_waiting")
        elif r.auto and r.idle(s):
            text = t("grind_auto_idle")
        else:
            ago = max(0, round(time.time() - s.reads[-1].t))
            text = t("grind_last_now") if ago < 10 else t("grind_last_secs", n=ago) if ago < 60 else \
                t("grind_last_mins", n=ago // 60)
        self._gs(self.grind_auto_state, text)
        self.grind_auto_state.show()

    def _fill_exp(self):
        t, c = self.t, self.c
        clear(self.grind_lines)
        if not c:
            self.grind_tag.hide()
            self._gs(self.grind_state, t("tool_no_char"))
            self.grind_map.hide()
            self.grind_lines.parentWidget().hide()
            self._fill_cells(None, "")
            self._cell("time", "–")
            self._grind_buttons()
            self._fill_recent()
            return
        s = self.grind.session(c.id)
        self._grind_buttons(busy=self._asked_read())
        self._fill_exp_clock()
        sm = grind.summarize(self.kb, s) if s else None
        where = sm.map if sm else ""
        self.grind_map.setVisible(bool(s))
        self._gs(self.grind_map, t("grind_map", map=bidi.ltr_block(where, t.rtl)) if where else t("grind_map_unknown"))
        # a finished session shows its monster until the player picks one for the next session
        mob = s.monster if s and not (s.ended and self._grind_choice) else self._grind_choice
        if not self.grind_monster.hasFocus() and self.grind_monster.text() != mob:
            self.grind_monster.blockSignals(True)          # the AI's pick shown, not taken as the player's
            self.grind_monster.setText(mob)
            self.grind_monster.setCursorPosition(0)
            self.grind_monster.blockSignals(False)
        self._fill_cells(sm, mob)
        if sm:
            self._fill_lines(sm)
        self.grind_lines.parentWidget().setVisible(self.grind_lines.count() > 0)     # no empty row in the card
        if self._asked_read():
            self._gs(self.grind_status, t("grind_reading"))
        elif self.runner.note:
            self._gs(self.grind_status, "\n".join(t(k) for k in self.runner.note))
        elif s and not s.ended:
            self._gs(self.grind_status, t("grind_running_hint"))
        else:
            self._gs(self.grind_status, t("grind_idle"))
        self._fill_recent()

    def _fill_cells(self, sm, mob: str):
        t = self.t
        wait = t("grind_tip_wait")
        if sm is None:
            for key in self.GRIND_CELLS[1:]:
                self._cell(key, "–")
            return
        mob = sm.monster or mob          # the session's own monster: its kills are worked out from it
        exp_tip = {"no_table": t("exp_no_table"), "no_gain": t("exp_no_gain"), "no_exp": t("grind_no_exp")}.get(
            sm.exp_note, wait)
        self._cell("exp", self._num(sm.exp, sign=bool(sm.exp)), f"{sm.exp:,} EXP" if sm.exp else exp_tip)
        self._cell("exp_h", self._num(sm.exp_h), "" if sm.exp_h else exp_tip)
        no_mesos = t("grind_tip_no_mesos")
        self._cell("mesos", self._num(sm.mesos, sign=True),
                   t("grind_tip_mesos", n=f"{sm.mesos:,}") if sm.mesos is not None else no_mesos)
        self._cell("mesos_h", self._num(sm.mesos_h), "" if sm.mesos_h is not None else
                   (wait if sm.mesos is not None else no_mesos))
        self._cell("net", self._num(sm.net, sign=True), t("grind_tip_net") if sm.net is not None else
                   t("grind_tip_no_net"))
        if sm.kills is not None:
            how = t("grind_tip_kills", mob=bidi.ltr_block(mob, t.rtl), n=f"{sm.monster_exp:,}")
            self._cell("kills", self._num(sm.kills, approx=True), how)
            self._cell("kills_h", self._num(sm.kills_h, approx=True), how if sm.kills_h is not None else wait)
        else:
            why = t("grind_tip_no_monster") if not mob else \
                t("grind_tip_unknown_monster", mob=bidi.ltr_block(mob, t.rtl)) if not sm.monster_exp else exp_tip
            self._cell("kills", "–", why)
            self._cell("kills_h", "–", why)
        self._cell("potions", self._num(sm.potions_cost),
                   t("grind_tip_potions") if sm.potions_read else t("grind_tip_no_potions"))

    def _fill_lines(self, sm):
        """Under the numbers: the EXP rate in levels, the potions behind the cost, the community's mesos estimate."""
        t, add = self.t, self.grind_lines.addWidget
        if sm.exp:
            bits = []
            if sm.level_to and sm.level_from and sm.level_to > sm.level_from:
                bits.append(t("grind_levelup", b=sm.level_to))
            if sm.pct_h is not None:
                bits.append(t("grind_pct_h", pct=f"{sm.pct_h}%"))
            if sm.to_level:
                bits.append(t("grind_to_level", time=self._clock(sm.to_level)))
            if bits:
                # the % and the time to level come from the EXP table: MeowDB's confirmed levels, then a reference
                chip = source_tag(t, sources.exp_source(self.kb, sm.exp_level or 1))
                self.grind_lines.addLayout(chip_row([chip], self._gl(" · ".join(bits), "RowHint"), lead=True))
        if sm.potions_read:
            if sm.potions:
                used = ", ".join(bidi.ltr_block(f"{name} ×{n}", t.rtl) for name, n, _, _ in sm.potions)
                srcs = [src for _, _, price, src in sm.potions if price]
                text = t("grind_potions_used", list=used)
                unpriced = [bidi.ltr_block(name, t.rtl) for name, _, price, _ in sm.potions if not price]
                if unpriced:
                    text += "\n" + t("grind_unpriced", names=", ".join(unpriced))
                label = self._gl(text, "RowHint")
                self.grind_lines.addLayout(chip_row(source_tags(t, srcs), label, lead=True) if srcs else _alone(label))
            else:
                add(self._gl(t("grind_potions_none"), "RowHint"))
            if sm.restocked:
                add(self._gl(t("grind_restocked", names=", ".join(bidi.ltr_block(n, t.rtl) for n in sm.restocked)),
                                "RowHint"))
        if sm.expected:
            text = t("grind_expected", n=f"{sm.expected:,}", k=f"{sm.kills:,}", r=sm.reports)
            self.grind_lines.addLayout(chip_row([source_tag(t, sources.COMMUNITY)], self._gl(text, "RowHint"),
                                                lead=True))
        if sm.kills is not None or sm.expected:
            add(self._gl(t("grind_approx"), "RowHint"))

    def _fill_recent(self):
        t, c = self.t, self.c
        grid = self.grind_table
        clear(grid)
        rows = self.grind.recent(c.id) if c else []
        self.grind_ask_row.setVisible(bool(rows))
        if not rows:
            grid.addWidget(self._gl(t("grind_recent_none"), "RowHint"), 0, 0, 1, 4)
            return
        heads = ("grind_col_spot", "grind_col_time", "grind_col_exp", "grind_col_mesos")
        for col, key in enumerate(heads):
            h = QLabel(self._p(t(key)), objectName="RowHint")
            h.setAlignment((Qt.AlignLeading if col == 0 else Qt.AlignHCenter) | Qt.AlignVCenter)
            grid.addWidget(h, 0, col)
        grid.setColumnStretch(0, 1)
        best = max((r.get("exp_h") or 0 for r in rows), default=0)
        for i, r in enumerate(rows, 1):
            line = QFrame(objectName="Separator")
            line.setFixedHeight(1)
            grid.addWidget(line, 2 * i - 1, 0, 1, 4)
            spot = QWidget()
            sl = QVBoxLayout(spot)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(0)
            name = QLabel(bidi.ltr_name(r.get("map") or t("grind_unknown_map"), t.rtl), objectName="RowLabel")
            name.setWordWrap(True)
            sl.addWidget(name)
            sub = [r.get("monster") or "", self._day(r.get("start"))]
            sl.addWidget(QLabel(self._p(" · ".join(bidi.ltr_block(b, t.rtl) for b in sub if b)), objectName="CardSub"))
            grid.addWidget(spot, 2 * i, 0)
            exp_h = r.get("exp_h")
            top = bool(exp_h) and exp_h == best and len(rows) > 1        # the best EXP/h stands out
            for col, text in ((1, self._p(self._clock(r.get("seconds")))), (2, self._num(exp_h)),
                              (3, self._num(r.get("mesos_h")))):
                grid.addWidget(tag(text, "TagGood" if col == 2 and top else "CardStat"), 2 * i, col, Qt.AlignCenter)

    def _day(self, ts) -> str:
        if not isinstance(ts, (int, float)):
            return ""
        import datetime
        d = datetime.date.fromtimestamp(ts)
        today = datetime.date.today()
        if d == today:
            return self.t("grind_today")
        if d == today - datetime.timedelta(days=1):
            return self.t("grind_yesterday")
        return f"{d.day}.{d.month}" if self.t.rtl else f"{d:%b} {d.day}"        # 2.10 / Oct 2

    def _grind_ask(self):
        """"Which spot paid more?": the saved sessions as the question's lines, so the chat compares real numbers."""
        t, c = self.t, self.c
        rows = self.grind.recent(c.id)[:6] if c else []
        if not rows:
            return
        lines = []
        for r in rows:
            bits = [self._clock(r.get("seconds"))]
            if r.get("exp_h"):
                bits.append(t("grind_q_exp", n=f"{r['exp_h']:,}"))
            if r.get("mesos_h") is not None:
                bits.append(t("grind_q_mesos", n=f"{r['mesos_h']:,}"))
            if r.get("potions_cost"):
                bits.append(t("grind_q_pots", n=f"{r['potions_cost']:,}"))
            spot = r.get("map") or t("grind_unknown_map")
            if r.get("monster"):
                spot += f" ({r['monster']})"
            lines.append(f"• {spot}: " + ", ".join(bits))
        self.ask_requested.emit(t("grind_ask_q", lines="\n".join(lines)), False)

    # quick checks --------------------------------------------------------

    def _page_more(self):
        t = self.t
        sc, lay = scroll_page(self.t.rtl)
        lay.addWidget(self._label(t("more_intro"), "ToolHeader"))
        sell = Section(t("sell_title"), t.rtl)
        sell.add_widget(self._label(t("sell_body"), "RowLabel"))
        go = QPushButton(self._p(t("sell_go")), objectName="Primary")
        go.setCursor(Qt.PointingHandCursor)
        go.clicked.connect(self._sell_check)
        sell.add_widget(go)
        lay.addWidget(sell)
        shop = Section(t("shop_title"), t.rtl)
        shop.add_widget(self._label(t("shop_body"), "RowLabel"))
        maps = map_rows(self.kb)
        self.shop_map = EntityPicker(maps, self._p(t("shop_map_ph", n=len(maps))), icon=40, rtl=t.rtl)
        self.shop_map.setMinimumWidth(280)
        shop.add_row(t("shop_where"), self.shop_map)
        self.shop_len = Segmented([("30", 30), ("60", 60), ("120", 120)], 60, t.rtl)
        shop.add_row(t("shop_minutes"), self.shop_len)
        go2 = QPushButton(self._p(t("shop_go")), objectName="Primary")
        go2.setCursor(Qt.PointingHandCursor)
        go2.clicked.connect(self._shopping)
        shop.add_widget(go2)
        lay.addWidget(shop)
        lay.addStretch(1)
        return sc

    def _page_pets(self):
        sc, lay = scroll_page(self.t.rtl)
        lay.addWidget(self._pets_section())
        if not sitedata.pets(self.kb):        # a KB from before the pets page
            lay.addWidget(self._label(self.t("pets_empty"), "RowHint"))
        lay.addStretch(1)
        return sc

    def _pets_section(self) -> Section:
        """Every pet: lifespan, hunger rate, commands to Lv 30, and whether the Cash Shop sells it now (sitedata.py).
        Its own page of the play tools (Pets); sold now is shown first."""
        t = self.t
        sec = Section(t("pets_title"), t.rtl)
        sec.add_widget(self._label(t("pets_intro"), "RowLabel"))
        self.pet_filter = Segmented([(t("pets_sold"), "sold"), (t("pets_easy"), "easy"), (t("pets_all"), "all")],
                                    "sold", t.rtl)
        self.pet_filter.set_label(t("pets_title"))
        self.pet_filter.changed.connect(lambda *_: self._fill_pets())
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 8, 0, 8)
        bl.setSpacing(8)
        bl.addWidget(self.pet_filter)
        self.pet_src = QHBoxLayout()
        bl.addLayout(self.pet_src)
        self.pet_list = QVBoxLayout()
        self.pet_list.setSpacing(6)
        bl.addLayout(self.pet_list)
        sec.add_widget(box)
        self._fill_pets()
        sec.setVisible(bool(sitedata.pets(self.kb)))       # a KB from before the pets page: no empty section
        return sec

    def _fill_pets(self):
        t = self.t
        clear(self.pet_list)
        every = sitedata.pets(self.kb)
        mode = self.pet_filter.value()
        shown = [p for p in every if p.sold] if mode == "sold" else sitedata.easiest(every) if mode == "easy" else every
        self._source_line(self.pet_src, sources.MEOWDB if every else None)
        if not shown and every:
            self.pet_list.addWidget(self._label(t("pets_none_sold"), "RowHint"))
        for p in shown:
            self.pet_list.addWidget(self._pet_row(p))

    def _pet_row(self, p) -> QFrame:
        """One pet: picture, name and "Ask in chat", then its numbers as chips, each explained on hover."""
        t = self.t
        row = QFrame(objectName="Card")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)
        pic = QLabel()
        pic.setFixedSize(40, 40)
        pic.setAlignment(Qt.AlignCenter)
        path = self.kb.picture(p.key)
        pm = QPixmap(str(path)) if path else QPixmap()
        if not pm.isNull():
            pic.setPixmap(pm.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(QLabel(bidi.ltr_name(p.name, t.rtl), objectName="CardName"), 0, Qt.AlignVCenter)
        head.addStretch(1)
        ask = QPushButton(self._p(t("ask_short")), objectName="Link")
        ask.setCursor(Qt.PointingHandCursor)
        ask.setAutoDefault(False)
        ask.clicked.connect(lambda _=False, k=p.key: self.tag_requested.emit(k))
        head.addWidget(ask, 0, Qt.AlignVCenter)
        col.addLayout(head)
        parts = pet_parts(t, self.kb, p.key)
        tags = FlowLayout(spacing=5)
        if parts:
            tips = [t("pet_life_tip"), t("pet_hunger_tip"), t("pet_commands_tip", level=p.level)]
            for text, tip in zip(parts[0], tips):
                tags.addWidget(info_tag(t, text, tip))
            for chip in parts[1]:
                tags.addWidget(chip)
        updated = updated_tag(t, self.kb, p.key)
        if updated:
            tags.addWidget(updated)
        col.addLayout(tags)
        lay.addLayout(col, 1)
        return row

    def _sell_check(self):
        # the inventory must be in the screenshot, not this window; the chat bubble says "Inventory check",
        # not the nine lines of instructions the AI gets
        self._step_aside(lambda: self.detail_ask_requested.emit(self.t("sell_q"), self.t("inv_check")))

    def _fill_more(self):
        c = self.c
        if c and not self.shop_map.text():
            best = combat.spots(self.kb, c.level, *self._stats(), magic=c.base_class == combat.MAGE, n=1)
            if best:
                self.shop_map.setText(best[0].map)
                self.shop_map.setCursorPosition(0)     # show the start of the map name

    def _shopping(self):
        where = self.shop_map.text().strip()
        # no map picked: a sentence of its own ("grind at the place I train", not "at where I train")
        q = self.t("shop_q", map=where, n=self.shop_len.value()) if where else self.t("shop_q_here", n=self.shop_len.value())
        # with a fresh screenshot: the HUD shows max HP/MP as they are right now (and the potions already in the
        # bag when the inventory is open), so the list fits the character at this moment (live feedback)
        self._step_aside(lambda: self.ask_requested.emit(q, True))

    # how to get there ----------------------------------------------------

    def _page_route(self):
        t = self.t
        sc, lay = scroll_page(t.rtl)
        self.route_graph = routes.of(self.kb)
        sec = Section(t("route_title"), t.rtl)
        rows = route_rows(self.kb, self.route_graph)
        # no map list before the KB carries routes.json (an app on an older KB): say so, not "Pick a map (0)"
        ph = self._p(t("route_map_ph", n=len(rows)) if rows else t("route_no_maps"))
        self.route_from = EntityPicker(rows, ph, icon=40, rtl=t.rtl)
        self.route_to = EntityPicker(rows, ph, icon=40, rtl=t.rtl)
        for picker, label in ((self.route_from, "route_from"), (self.route_to, "route_to")):
            picker.setMinimumWidth(280)
            picker.picked.connect(self._find_route)
            sec.add_row(t(label), picker)
        self.route_taxi = Switch(True)
        self.route_taxi.toggled.connect(lambda *_: self._find_route())
        sec.add_row(t("route_taxi"), self.route_taxi, hint=t("route_taxi_hint"))
        go = QPushButton(self._p(t("route_go")), objectName="Primary")
        go.setCursor(Qt.PointingHandCursor)
        go.clicked.connect(self._find_route)
        swap = QPushButton(self._p(t("route_swap")), objectName="Link")
        swap.setCursor(Qt.PointingHandCursor)
        swap.setAutoDefault(False)
        swap.clicked.connect(self._swap_route)
        buttons = QWidget()
        bl = QHBoxLayout(buttons)
        bl.setContentsMargins(0, 8, 0, 8)
        bl.addWidget(go, 1)
        bl.addWidget(swap)
        sec.add_widget(buttons)
        lay.addWidget(sec)
        self.route_out = QVBoxLayout()
        self.route_out.setSpacing(10)
        lay.addLayout(self.route_out)
        lay.addStretch(1)
        self._route_auto = ""          # the start filled in from the character's map (followed when it moves)
        return sc

    def _fill_route(self):
        c = self.c
        here = (c.map if c else "") or ""
        mid = self.route_graph.find(here) if here else None
        start = self.route_from.text().strip()
        if mid and (not start or start == self._route_auto):
            self._route_auto = self.route_graph.name(mid)
            self.route_from.setText(self._route_auto)
            self.route_from.setCursorPosition(0)
        self._find_route()

    def route_to_map(self, key: str) -> None:
        """Open on the way to this map (a map card's "How to get here"), from the character's map."""
        self.show_page(PAGES.index("route"))
        mid = self.route_graph.of_key(key)
        if mid:
            self.route_to.setText(self.route_graph.name(mid))
            self.route_to.setCursorPosition(0)
        self._find_route()

    def _swap_route(self):
        a, b = self.route_from.text(), self.route_to.text()
        self.route_from.setText(b)
        self.route_to.setText(a)
        self._find_route()

    def _route_hint(self, text: str) -> None:
        self.route_out.addWidget(self._label(text, "RowHint"))

    def _find_route(self):
        t, g, rtl = self.t, self.route_graph, self.t.rtl
        clear(self.route_out)
        if not g.maps:
            self._route_hint(t("route_no_data"))
            return
        texts = (self.route_from.text().strip(), self.route_to.text().strip())
        if not texts[1]:
            self._route_hint(t("route_pick"))
            return
        found = [g.find(x) if x else None for x in texts]
        for text, mid in zip(texts, found):
            if text and not mid:
                # a map the KB has but doesn't confirm in the game (Orbis) is "not out yet", not "no such map"
                why = "route_not_out" if g.not_in_game(text) else "route_unknown"
                self._route_hint(t(why, name=bidi.ltr_block(text, rtl)))
                return
        a, b = found
        if not a:
            self._route_hint(t("route_pick_from"))
            return

        def name(mid: str) -> str:
            return bidi.ltr_block(g.name(mid), rtl)
        r = g.route(a, b, taxi=self.route_taxi.isChecked())
        if r is None:
            self._route_hint(t("route_none", a=name(a), b=name(b)))
            self.route_out.addWidget(self._ask_link(lambda: self._ask_route(a, b)))
            return
        if not r.legs:
            self._route_hint(t("route_same"))
            return
        self.route_out.addWidget(self._label(t("route_head", a=name(a), b=name(b)), "ToolHeader"))
        tags = FlowLayout(spacing=5)
        tags.addWidget(tag(self._p(t("route_steps", n=len(r.legs))), "TagAccent"))
        kinds = {leg.kind for leg in r.legs}
        for kind in ("taxi", "boat"):
            if kind in kinds:
                tags.addWidget(tag(self._p(t(f"route_tag_{kind}")), "Tag"))
        if not r.paid:
            tags.addWidget(tag(self._p(t("route_tag_walk")), "TagGood"))
        tags.addWidget(source_tag(t, sources.MEOWDB))
        self.route_out.addLayout(tags)
        notes = []
        if "taxi" in kinds and (walk := g.route(a, b, taxi=False)):
            notes.append(t("route_walk_alt" if not walk.paid else "route_walk_alt_boat", n=len(walk.legs)))
        if unpriced := [leg for leg in r.paid if not leg.fare]:
            paid = {leg.kind for leg in unpriced}
            notes.append(t("route_fares" if len(paid) > 1 else f"route_fares_{paid.pop()}"))
        notes.append(t("route_ring"))
        self.route_out.addWidget(self._label("\n".join(notes), "RowHint"))
        self.route_out.addWidget(self._ask_link(lambda: self._ask_route(a, b)))
        for i, leg in enumerate(r.legs, 1):
            self.route_out.addWidget(self._route_step(str(i), leg.frm, leg))
        self.route_out.addWidget(self._route_step("✓", b, None))

    def _ask_route(self, a: str, b: str) -> None:
        g = self.route_graph
        self.ask_requested.emit(self.t("route_q", a=g.name(a), b=g.name(b)), False)

    def _route_says(self, leg: routes.Leg | None) -> str:
        t, rtl = self.t, self.t.rtl
        if leg is None:
            return t("route_arrive")
        # a map name of a few words stays on one line: wrapped inside, "The Road to the" ended one line and
        # "Dungeon" began the next
        to = bidi.ltr_block(_whole(self.route_graph.name(leg.to)), rtl)
        if leg.kind == "portal":
            where = routes.side(leg.spot)
            return t(f"route_portal_{where}" if where in ("left", "right") else "route_portal", to=to)
        said = t(f"route_by_{leg.kind}", npc=bidi.ltr_block(leg.via, rtl), to=to)
        return said + (" " + t("route_fare", n=f"{leg.fare:,}") if leg.fare else "")

    def _route_step(self, number: str, mid: str, leg: routes.Leg | None) -> QFrame:
        """One map of the way: its name, what to do there, and its minimap with the spot to go to ringed."""
        g, rtl = self.route_graph, self.t.rtl
        card = QFrame(objectName="Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        num = tag(number, "TagAccent" if leg else "TagGood")
        num.setMinimumWidth(26)
        top.addWidget(num, 0, Qt.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(1)
        title = QLabel(bidi.ltr_name(g.name(mid), rtl), objectName="CardName")
        title.setWordWrap(True)
        names.addWidget(title)
        m = g.maps[mid]
        where = "  ·  ".join(x for x in (m.street, m.continent) if x)
        if where:
            names.addWidget(self._label(bidi.ltr_block(where, rtl), "CardSub"))
        top.addLayout(names, 1)
        col.addLayout(top)
        col.addWidget(self._label(self._route_says(leg), "RowLabel"))
        pic = self._minimap(mid, leg)
        if pic is not None:
            col.addWidget(pic, 0, Qt.AlignHCenter)
        return card

    MINIMAP_W, MINIMAP_H = 360, 170       # the most a step's minimap takes (small ones grow, pixel for pixel)

    def _minimap(self, mid: str, leg: routes.Leg | None) -> QLabel | None:
        path = self.route_graph.minimap(mid)
        pm = QPixmap(str(path)) if path else QPixmap()
        if pm.isNull():
            return None
        grow = max(1, min(3, self.MINIMAP_W // max(1, pm.width()), self.MINIMAP_H // max(1, pm.height())))
        if grow > 1:
            pm = pm.scaled(pm.width() * grow, pm.height() * grow, Qt.KeepAspectRatio, Qt.FastTransformation)
        if pm.width() > self.MINIMAP_W or pm.height() > self.MINIMAP_H:
            pm = pm.scaled(self.MINIMAP_W, self.MINIMAP_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if leg is not None and leg.spot:
            # the portal (orange) or the NPC to talk to (green), ringed where it is on the map
            x, y = round(leg.spot[0] * pm.width()), round(leg.spot[1] * pm.height())
            color = QColor(theme.ORANGE if leg.kind == "portal" else theme.GOOD_TEXT_LIGHT)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 170), 5))
            p.drawEllipse(QPoint(x, y), 11, 11)
            p.setPen(QPen(color, 2.6))
            p.drawEllipse(QPoint(x, y), 11, 11)
            p.end()
        pic = QLabel()
        pic.setPixmap(pm)
        pic.setAccessibleName(self.route_graph.name(mid))
        return pic


def market_ago(t, ts: float) -> str:
    """How long ago a listing went up: "5 min ago", "3 h ago", "2 days ago"."""
    mins = int(max(0, time.time() - ts) // 60)
    if mins < 60:
        return t("price_fm_ago_min", n=max(1, mins))
    if mins < 24 * 60:
        return t("price_fm_ago_h", n=mins // 60)
    return t("price_fm_ago_d", n=mins // (24 * 60))
