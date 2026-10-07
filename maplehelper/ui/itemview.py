"""The item details window: an item's stats, who drops it, and NiaMeowDB's "Meow Notes", opened from the ⓘ on an item's
card or tile in the chat. It opens to the right of the chat (to its left when the chat is against the screen's right
edge), then wherever the player last left it."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import availability, bidi, itemdetails, quick, sources
from ..i18n import I18n
from .controls import FlowLayout, rtl_buttons
from .glass import SHADOW, GlassDialog
from .patchnotes import gutter
from .widgets import EntityCard, fit_picture, source_tag, vote_tag, zoom_on_hover

GAP = 8 - 2 * SHADOW                 # the frames' gap: the shadow margins overlap, so the panels show 8 px apart
POS_SETTING = "item_window_pos"      # where the player last left the window: {"x", "y"}
WIDTH, MAX_HEIGHT = 480, 720


def beside(chat: QRect, size, screens: list[QRect]) -> QPoint:
    """To the right of the chat, top edges lined up; to its left when there's no room on the right (the chat opens in
    the screen's top-right corner); always on the chat's screen."""
    screen = max(screens, key=lambda s: s.intersected(chat).width() * s.intersected(chat).height(), default=chat)
    right = chat.right() + 1 + GAP
    x = right if right + size.width() - 1 <= screen.right() else chat.left() - GAP - size.width()
    x = min(max(x, screen.left()), screen.right() - size.width() + 1)
    y = min(max(chat.top(), screen.top()), screen.bottom() - size.height() + 1)
    return QPoint(x, y)


def saved_spot(saved, size, screens: list[QRect]) -> QPoint | None:
    """Where the player left the window, when it is still on a screen (a monitor since unplugged: None)."""
    if not isinstance(saved, dict) or not isinstance(saved.get("x"), int) or not isinstance(saved.get("y"), int):
        return None
    from .overlay import visible_rect
    spot = visible_rect(QRect(QPoint(saved["x"], saved["y"]), size), screens)
    return spot.topLeft() if spot is not None else None


class ItemDetailsDialog(GlassDialog):
    ask_requested = Signal(str)          # a question for the chat (where to hunt a dropper)

    def __init__(self, kb, lang: str, stylesheet: str, settings=None, chat: QRect | None = None):
        self.t = t = I18n(lang or "he")
        super().__init__(t("item_details"), t.rtl)
        self.setStyleSheet(stylesheet)
        self.fit_screen(WIDTH, MAX_HEIGHT)
        self.kb, self.settings, self.key = kb, settings, None
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(*gutter(t.rtl))
        self.lay.setSpacing(8)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
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
        rtl_buttons(self, t.rtl)
        self._align = (Qt.AlignRight if t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        self._place(chat)
        self.finished.connect(lambda *_: self._remember())

    # ------------------------------------------------------------ where it opens

    def _place(self, chat: QRect | None) -> None:
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        size = self.frameGeometry().size()
        spot = saved_spot(self.settings[POS_SETTING], size, screens) if self.settings is not None else None
        if spot is None and chat is not None and chat.isValid():
            spot = beside(chat, size, screens)
        if spot is not None:
            self.move(spot)

    def _remember(self) -> None:
        """Closed (its button, the X, Esc): it opens there next time."""
        if self.settings is not None:
            p = self.pos()
            self.settings[POS_SETTING] = {"x": p.x(), "y": p.y()}

    # ------------------------------------------------------------ what it shows

    def show_item(self, key: str) -> None:
        """This item (one window: another item's ⓘ shows it here instead of opening a second one)."""
        from .tools import clear
        t, rtl, kb = self.t, self.t.rtl, self.kb
        self.key = key
        clear(self.lay)
        e = kb.get(key) or {}
        name = e.get("name", key)
        self.title_label.setText(bidi.plain(name, rtl))
        self.setWindowTitle(name)
        d = itemdetails.details(kb, key)
        self.lay.addWidget(EntityCard(kb, key, t.lang))
        if d.description:
            self._text(d.description, "DialogBody")
        if d.stats:
            self._head(t("item_stats"))
            box = QFrame(objectName="Card")
            col = QVBoxLayout(box)
            col.setContentsMargins(12, 8, 12, 8)
            col.setSpacing(2)
            if d.kind:
                kind = QLabel(bidi.ltr_name(d.kind, rtl), objectName="CardSub")
                kind.setAlignment(self._align)
                col.addWidget(kind)
            for ln in d.stats:
                # the page's own lines, as NiaMeowDB writes them ("W.DEF +44"): English, left to right
                lb = QLabel(bidi.ltr_name(ln, rtl), objectName="RowLabel")
                lb.setWordWrap(True)
                lb.setAlignment(self._align)
                col.addWidget(lb)
            self.lay.addWidget(box)
        self._droppers(key, name)
        if d.about or d.posts:
            self._head(t("item_meow_notes"))
            for para in d.about:
                self._text(para, "DialogBody")
            for post in d.posts:
                self._post(post)
        self.lay.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)

    def _head(self, text: str) -> None:
        head = QLabel(bidi.plain(text, self.t.rtl), objectName="ToolHeader")
        head.setAlignment(self._align)
        head.setContentsMargins(4, 6, 4, 0)
        self.lay.addWidget(head)

    def _text(self, text: str, name: str) -> QLabel:
        # NiaMeowDB's English: kept left to right in a Hebrew window
        lb = QLabel(bidi.ltr_block(text, self.t.rtl), objectName=name)
        lb.setWordWrap(True)
        lb.setAlignment(self._align)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lb.setContentsMargins(4, 0, 4, 0)
        self.lay.addWidget(lb)
        return lb

    def _post(self, post: itemdetails.Post) -> None:
        card = QFrame(objectName="Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(12, 8, 12, 8)
        col.setSpacing(4)
        by = QLabel(bidi.ltr_name(f"{post.author} · {post.when}", self.t.rtl), objectName="CardSub")
        by.setAlignment(self._align)
        col.addWidget(by)
        for para in post.text.split("\n"):
            lb = QLabel(bidi.ltr_block(para, self.t.rtl), objectName="RowLabel")
            lb.setWordWrap(True)
            lb.setAlignment(self._align)
            lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
            col.addWidget(lb)
        self.lay.addWidget(card)

    def _droppers(self, key: str, item: str) -> None:
        """Who drops it, as the wishlist lists them: each monster with its level, its map and an "Ask" for the chat,
        and the list it is on (players' Classic sightings or the MSEA reference)."""
        t, kb = self.t, self.kb
        droppers = kb.droppers.get(key, [])
        self._head(t("wish_dropped_by") if droppers else t("wish_no_droppers"))
        if not droppers:
            return
        srcs = {m: kb.drop_source(m, key) or sources.MSEA for m in droppers}
        self._text(quick.drops_note(t, srcs.values()), "RowHint")
        open_ = availability.of(kb)
        for m in droppers:
            self.lay.addWidget(self._dropper(m, item, srcs[m], kb.community_vote(m, key), open_))

    def _dropper(self, m: str, item: str, source: str, vote: dict | None, open_) -> QFrame:
        t, kb = self.t, self.kb
        e = kb.get(m) or {}
        name = e.get("name", m)
        lvl = (e.get("props") or {}).get("Level")
        maps = [mp for mp in kb.all_maps(m) if open_.map_open(mp)][:1]
        card = QFrame(objectName="Card")
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)
        pic = QLabel()
        pic.setFixedSize(40, 40)
        pic.setAlignment(Qt.AlignCenter)
        path = kb.picture(m)
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull():
                pic.setPixmap(fit_picture(pm, 40, 40, pic))
                zoom_on_hover(pic, path)
        row.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        flow = FlowLayout(spacing=6, line_spacing=2)
        title = QLabel(bidi.ltr_name(name + (f" · Lv. {lvl}" if lvl else ""), t.rtl), objectName="CardName")
        for w in [title, source_tag(t, source)] + ([vote_tag(t, vote)] if vote else []):
            flow.addWidget(w)
        col.addLayout(flow)
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
