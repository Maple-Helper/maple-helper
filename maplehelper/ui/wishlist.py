"""The wishlist window: each wished item, who drops it (community-reported droppers first, then the reference
list, each lowest level first) and where they live."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import availability, bidi, farm, quick, sources
from ..i18n import I18n
from ..kb import KnowledgeBase
from .controls import FlowLayout, rtl_buttons
from .glass import GlassDialog
from .patchnotes import gutter
from .widgets import (EntityCard, chip_row, fit_picture, info_tag, name_lv, source_tag, source_tags, updated_tag,
                      vote_tag, zoom_on_hover)


class WishlistDialog(GlassDialog):
    ask_requested = Signal(str)          # a question for the chat (where to hunt a dropper)
    farm_requested = Signal(str)         # an item's droppers on Play tools' Farm tab (by its name)
    route_requested = Signal(str)        # the way to a monster's map on Play tools' How to get there (its name)

    def __init__(self, keys: list[str], kb: KnowledgeBase, lang: str, stylesheet: str, level: int | None = None):
        """level: the character's, for how each dropper sits against it (the Farm tab's chips) and their order."""
        self.t = t = I18n(lang or "he")
        super().__init__(t("wishlist"), t.rtl)
        self.setStyleSheet(stylesheet)
        self.resize(500, 640)
        self.kb = kb
        self.level = level
        rtl = t.rtl
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        # items added here too: the ☆ was only on cards in a chat answer, so an empty window led nowhere (#96)
        from .tools import EntityPicker, item_rows
        self.search = EntityPicker(item_rows(kb), bidi.plain(t("wish_search"), rtl), icon=32, rtl=rtl)
        self.search.picked.connect(self._add_searched)
        outer.addWidget(self.search)
        outer.addSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        self.lay = lay = QVBoxLayout(body)
        lay.setContentsMargins(*gutter(rtl))          # the room before the scrollbar, on its side (left in Hebrew)
        lay.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._fill(keys)
        from .widgets import WISHLIST
        WISHLIST.changed.connect(self._refill)

        row = QHBoxLayout()
        row.setContentsMargins(0, 10, 0, 0)
        row.addStretch(1)
        ok = QPushButton(t("close"), objectName="Primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setMinimumWidth(160)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        row.addStretch(1)
        outer.addLayout(row)
        rtl_buttons(self, rtl)

    def _fill(self, keys: list[str]) -> None:
        """The wished items and their droppers; run again whenever the wishlist changes (a star taken off here or
        on a card left its item in the window until it was reopened: the owner's report)."""
        from .tools import clear
        t, kb, lay, rtl = self.t, self.kb, self.lay, self.t.rtl
        clear(lay)
        keys = [k for k in keys if kb.get(k)]
        if not keys:
            empty = QLabel(bidi.plain(t("wishlist_empty"), rtl), objectName="DialogBody")
            empty.setWordWrap(True)
            lay.addWidget(empty)
        self._align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        for k in keys:
            lay.addWidget(EntityCard(kb, k, t.lang))
            droppers = kb.droppers.get(k, [])
            head = QLabel(bidi.plain(t("wish_dropped_by") if droppers else t("wish_no_droppers"), rtl),
                          objectName="ToolHeader")
            head.setWordWrap(bool(not droppers))
            head.setAlignment(self._align)
            head.setContentsMargins(4, 4, 4, 0)
            # each drop's list (the MSEA reference list, players' Classic sightings): one chip beside "Dropped by"
            # when every dropper's drop is on the same list, else one on each dropper's row
            srcs = {m: kb.drop_source(m, k) or sources.MSEA for m in droppers}
            self._mixed = len(set(srcs.values())) > 1
            chips = [] if self._mixed else source_tags(t, srcs.values())
            if chips:
                lay.addLayout(chip_row(chips, head))
            else:
                lay.addWidget(head)
            item = (kb.get(k) or {}).get("name", k)
            if droppers:
                # the drop lists include the MSEA reference drops, not all confirmed for Classic: said here as the
                # instant answers say it under the same data
                note = QLabel(bidi.plain(quick.drops_note(t, srcs.values()), rtl), objectName="RowHint")
                note.setWordWrap(True)
                note.setAlignment(self._align)
                note.setContentsMargins(4, 0, 4, 0)
                lay.addWidget(note)
                # the item on the Farm tab: its droppers with the way there and a farm session, answered here and
                # not by the AI (TOOL-07)
                go = QPushButton(bidi.plain(t("wish_on_farm"), rtl), objectName="Link")
                go.setCursor(Qt.PointingHandCursor)
                go.setAutoDefault(False)
                go.clicked.connect(lambda _=False, n=item: self.farm_requested.emit(n))
                lay.addWidget(go, 0, Qt.AlignLeft)      # AlignLeft is the leading edge (mirrored in Hebrew)
            # each dropper as a row with its picture, level, map and a way to ask the chat about it (live feedback:
            # a small text list was hard to read and led nowhere)
            # every dropper ("and 4 more" hid them, and the window scrolls anyway: the owner's report)
            # with the character's level: the community's first as before, then the ones to farm now nearest the
            # level (Snail Lv 1 came first for a Lv 35 player), each with the Farm tab's chip for its fit
            ds = farm.droppers(kb, k, self.level) if self.level else []
            if ds:
                ds.sort(key=lambda d: (d.source != sources.COMMUNITY, d.fit == "hard", d.boss,
                                       abs(self.level - d.level), d.level, d.name))
            fits = {d.key: d for d in ds}
            for m in ([d.key for d in ds] or droppers):
                lay.addWidget(self._dropper(m, item, srcs.get(m) if self._mixed else None, kb.community_vote(m, k),
                                            fits.get(m)))
            lay.addSpacing(10)
        lay.addStretch(1)

    def _add_searched(self) -> None:
        """The item picked in the search box goes on the list (one already on it stays: picking never unstars)."""
        from .tools import _item_named
        from .widgets import WISHLIST
        name = self.search.text().strip()
        key = _item_named(self.kb, name) if name else None
        if key is None:
            return                     # a half-typed name: the box keeps it to finish
        if not WISHLIST.has(key):
            WISHLIST.toggle(key)
        self.search.clear()

    @Slot()
    def _refill(self) -> None:
        """After the wishlist's signal, not inside it: rebuilding while the signal ran (the star that sent it is in
        here) crashed the app later, when the old cards were deleted."""
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self, self._refill_now)

    def _refill_now(self) -> None:
        from .widgets import WISHLIST
        if self.isVisible():
            self._fill(WISHLIST.keys())

    def done(self, r: int) -> None:
        from .widgets import WISHLIST
        try:
            WISHLIST.changed.disconnect(self._refill)       # a closed window stays out of the wishlist's changes
        except (RuntimeError, TypeError):
            pass
        super().done(r)

    def _dropper(self, m: str, item: str, source: str | None = None, vote: dict | None = None,
                 d: farm.Dropper | None = None) -> QFrame:
        t, kb = self.t, self.kb
        e = kb.get(m) or {}
        name = e.get("name", m)
        lvl = (e.get("props") or {}).get("Level")
        # where it lives: its busiest map the KB confirms is in the game (kb.droppers already keeps only monsters
        # that are in the game, but one that lives in both regions listed its Orbis map first)
        open_ = availability.of(kb)
        maps = [mp for mp in kb.all_maps(m) if open_.map_open(mp)][:1]
        card = QFrame(objectName="Card")
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)
        pic = QLabel()
        pic.setFixedSize(44, 44)
        pic.setAlignment(Qt.AlignCenter)
        path = kb.picture(m)
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull():
                pic.setPixmap(fit_picture(pm, 44, 44, pic))
                zoom_on_hover(pic, path)
        row.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        # "Snail · רמה 1": the level in the UI's words, as on every tools card
        title = QLabel(name_lv(name, lvl, t), objectName="CardName")
        title.setAlignment(self._align)
        # how it sits against the level (the Farm tab's chip), this row's own drop list when the droppers mix them,
        # the players' votes when players reported it ("16 ✓"), and a KB update this week that changed the monster
        fit = []
        if d is not None:
            fit = [info_tag(t, t("farm_fit_boss"), t("farm_fit_boss_tip"), "TagWarn") if d.boss else
                   info_tag(t, t(f"farm_fit_{d.fit}"), t(f"farm_fit_{d.fit}_tip"),
                            {"easy": "TagGood", "range": "Tag", "hard": "TagWarn"}[d.fit])]
        chips = fit + ([source_tag(t, source)] if source else []) + ([vote_tag(t, vote)] if vote else []) \
            + [c for c in [updated_tag(t, kb, m)] if c]
        if chips:
            # a flow, not one row: name + Lv, "Community", "Single report" and "Updated" in one row were 570 px
            # and pushed the whole window wider than its 434 px view, clipping every row (the site's shot)
            flow = FlowLayout(spacing=6, line_spacing=2)
            for w in [title, *chips]:
                flow.addWidget(w)
            col.addLayout(flow)
        else:
            col.addWidget(title)
        if maps:
            where = QLabel(bidi.ltr_name(kb.map_label(maps[0]), t.rtl), objectName="CardSub")
            where.setWordWrap(True)
            where.setAlignment(self._align)
            col.addWidget(where)
        row.addLayout(col, 1)
        # the way to its map, worked out here, then the chat (the only action was the AI's, for what the app knows)
        links = FlowLayout(spacing=14, line_spacing=0)
        acts = [("farm_route", lambda: self.route_requested.emit(name))] if maps and not (d and d.closed) else []
        acts.append(("ask_short", lambda: self.ask_requested.emit(t("wish_ask", monster=name, item=item))))
        for key, then in acts:
            b = QPushButton(bidi.plain(t(key), t.rtl), objectName="Link")
            b.setCursor(Qt.PointingHandCursor)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, f=then: f())
            links.addWidget(b)
        col.addLayout(links)
        return card
