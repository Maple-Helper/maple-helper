"""The wishlist window: each wished item, who drops it (lowest level first) and where they live."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import availability, bidi, quick, sources
from ..i18n import I18n
from ..kb import KnowledgeBase
from .controls import rtl_buttons
from .glass import GlassDialog
from .patchnotes import gutter
from .widgets import EntityCard, chip_row, source_tag, source_tags, updated_tag, vote_tag

SHOWN_DROPPERS = 5


class WishlistDialog(GlassDialog):
    ask_requested = Signal(str)          # a question for the chat (where to hunt a dropper)

    def __init__(self, keys: list[str], kb: KnowledgeBase, lang: str, stylesheet: str):
        self.t = t = I18n(lang or "he")
        super().__init__(t("wishlist"), t.rtl)
        self.setStyleSheet(stylesheet)
        self.resize(500, 640)
        self.kb = kb
        rtl = t.rtl
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        self.lay = lay = QVBoxLayout(body)
        lay.setContentsMargins(*gutter(rtl))          # the room before the scrollbar, on its side (left in Hebrew)
        lay.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

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
            if droppers:
                # the drop lists include the MSEA reference drops, not all confirmed for Classic: said here as the
                # instant answers say it under the same data
                note = QLabel(bidi.plain(quick.drops_note(t, srcs.values()), rtl), objectName="RowHint")
                note.setWordWrap(True)
                note.setAlignment(self._align)
                note.setContentsMargins(4, 0, 4, 0)
                lay.addWidget(note)
            item = (kb.get(k) or {}).get("name", k)
            # each dropper as a row with its picture, level, map and a way to ask the chat about it (live feedback:
            # a small text list was hard to read and led nowhere)
            for m in droppers[:SHOWN_DROPPERS]:
                lay.addWidget(self._dropper(m, item, srcs[m] if self._mixed else None, kb.community_vote(m, k)))
            if len(droppers) > SHOWN_DROPPERS:
                more = QLabel(bidi.plain(t("pn_more", n=len(droppers) - SHOWN_DROPPERS), rtl), objectName="RowHint")
                more.setAlignment(self._align)
                lay.addWidget(more)
            lay.addSpacing(10)
        lay.addStretch(1)

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

    def _dropper(self, m: str, item: str, source: str | None = None, vote: dict | None = None) -> QFrame:
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
                pic.setPixmap(pm.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        row.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(bidi.ltr_name(name + (f" · Lv. {lvl}" if lvl else ""), t.rtl), objectName="CardName")
        title.setAlignment(self._align)
        # this row's own drop list when the droppers mix them, the players' votes when players reported it ("16 ✓"),
        # and a KB update this week that changed the monster
        chips = ([source_tag(t, source)] if source else []) + ([vote_tag(t, vote)] if vote else [])             + [c for c in [updated_tag(t, kb, m)] if c]
        if chips:
            col.addLayout(chip_row(chips, title))
        else:
            col.addWidget(title)
        if maps:
            where = QLabel(bidi.ltr_name(kb.map_label(maps[0]), t.rtl), objectName="CardSub")
            where.setWordWrap(True)
            where.setAlignment(self._align)
            col.addWidget(where)
        row.addLayout(col, 1)
        ask = QPushButton(bidi.plain(t("ask_short"), t.rtl), objectName="Link")
        ask.setCursor(Qt.PointingHandCursor)
        ask.setAutoDefault(False)
        ask.clicked.connect(lambda: self.ask_requested.emit(t("wish_ask", monster=name, item=item)))
        row.addWidget(ask, 0, Qt.AlignVCenter)
        return card
