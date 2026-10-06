"""Pinned answers under the character card, the history search window, and the shareable character card."""
from __future__ import annotations

import datetime as dt
import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from .. import bidi, dates, pins
from ..i18n import I18n
from . import theme
from .controls import follow_typing, rtl_buttons
from .glass import GlassDialog
from .patchnotes import gutter
from .widgets import zoom_on_hover


def short_text(text: str, limit: int) -> str:
    """One line of `text` (whitespace collapsed), cut at a word to about `limit` characters."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0] if " " in flat[:limit] else flat[:limit]
    return cut.rstrip(" ,.:;-–") + "…"


def set_wrapped_name(lb: QLabel, text: str, rtl: bool) -> None:
    """A word-wrapped label that is one name or sentence (a question, an answer's first lines). English text in the
    Hebrew UI is laid out left to right as itself: wrapped inside a right-to-left paragraph as an isolate
    (bidi.ltr_name), a line keeps its trailing space at its right end, so a nearly full line stuck out on the left
    by a space and lost the edge of its first letter. The caller's AlignRight|AlignAbsolute keeps it on the
    Hebrew side."""
    if rtl and text and not bidi._RTL.search(text):
        lb.setLayoutDirection(Qt.LeftToRight)
        lb.setText(text)
    else:
        lb.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        lb.setText(bidi.ltr_name(text, rtl))


def _answer_label(text: str) -> QLabel:
    lb = QLabel(bidi.to_html(text), objectName="PinAnswer")
    lb.setTextFormat(Qt.RichText)
    lb.setWordWrap(True)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


class PinsBar(QFrame):
    """'(pin) Pinned (2) ▾': tap to open the pinned answers, ✕ on one to unpin it."""

    unpin = Signal(str)

    def __init__(self):
        super().__init__(objectName="Card")
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(14, 8, 14, 8)
        self.col.setSpacing(6)
        self.head = QPushButton(objectName="PlanLink")
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.clicked.connect(self._toggle)
        self.col.addWidget(self.head)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 6, 0)
        self.body_lay.setSpacing(8)
        # long answers scroll inside the bar: open, it takes at most ~40% of the chat, never the whole feed
        self.scroll = _FitScroll(on_width=self.fit)
        self.scroll.setWidget(self.body)
        self.scroll.hide()
        self.col.addWidget(self.scroll)
        self.hide()
        self._items: list[dict] = []
        self._t = I18n("he")

    def show_pins(self, items: list[dict], t, rtl: bool):
        self._items, self._t, self._rtl = items, t, rtl
        self.setVisible(bool(items))
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        self.body_lay.setContentsMargins(*gutter(rtl))     # the room before its scrollbar, on the bar's side
        # "left" is the leading edge: Qt mirrors style-sheet alignment in a right-to-left UI ("right" put the
        # Hebrew title on the left, seen live)
        self.head.setStyleSheet("text-align: left; font-weight: 600;")
        self._refresh_head()
        while self.body_lay.count():
            w = self.body_lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        for p in items:
            box = QFrame(objectName="PinItem")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(2)
            top = QHBoxLayout()
            q = QLabel(objectName="CardName")
            q.setTextFormat(Qt.PlainText)
            q.setWordWrap(True)
            q.setAlignment((Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute)
            set_wrapped_name(q, pins.shown_question(p.get("q") or ""), rtl)
            top.addWidget(q, 1)
            x = QToolButton(objectName="Icon", text="✕")
            x.setToolTip(t("unpin"))
            x.setAccessibleName(t("unpin"))         # a screen reader said "✕"
            x.setCursor(Qt.PointingHandCursor)
            x.clicked.connect(lambda _=False, a=p["a"]: self.unpin.emit(a))
            top.addWidget(x, 0, Qt.AlignTop)
            bl.addLayout(top)
            bl.addWidget(_answer_label(p["a"]))
            self.body_lay.addWidget(box)
        self.fit()

    def _refresh_head(self):
        arrow = "▴" if self.scroll.isVisible() else "▾"
        self.head.setText(bidi.plain(f"{self._t('pinned', n=len(self._items))} {arrow}", getattr(self, "_rtl", True)))
        self.head.setIcon(theme.glyph_icon("pin", theme.P()["text"], 14))          # the icon font's pin, not 📌

    def _toggle(self):
        self.scroll.setVisible(not self.scroll.isVisible())
        self._refresh_head()
        self.fit()
        QTimer.singleShot(0, self.fit)      # again once the chat has made room for it

    MAX_SHARE = 0.4      # of the room it shares with the conversation
    room = None          # the chat sets it: px that the open list and the conversation share

    def fit(self):
        """As tall as the pinned answers, up to MAX_SHARE of the room; the rest scrolls."""
        if self.scroll.isHidden():
            return
        room = self.room() if self.room else self.window().height()
        cap = max(80, int(room * self.MAX_SHARE))
        w = self.scroll.viewport().width() or self.width()
        lay = self.body.layout()
        want = lay.heightForWidth(w) if lay.hasHeightForWidth() else self.body.sizeHint().height()
        self.scroll.want = min(max(want, 0), cap)
        self.scroll.setMinimumHeight(min(48, self.scroll.want))
        self.scroll.setMaximumHeight(self.scroll.want)
        self.scroll.updateGeometry()



class _FitScroll(QScrollArea):
    """A scroll area that asks for exactly `want` pixels of height (and can shrink when the window is short)."""

    want = 0

    def __init__(self, on_width=None):
        super().__init__()
        self._on_width = on_width
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def sizeHint(self):
        return QSize(super().sizeHint().width(), self.want)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._on_width and e.oldSize().width() != e.size().width():   # wrapped text: new width, new height
            self._on_width()


def draw_focus(w: QWidget) -> None:
    """The keyboard-focus ring on a card that opens from the keyboard (the app's focus ring covers buttons only):
    the same orange as a focused search box, on the card's own rounded edge."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QPainter, QPen

    p = QPainter(w)
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor(theme.ORANGE if theme.MODE == "dark" else theme.ORANGE_DEEP)
    color.setAlphaF(0.9)
    p.setPen(QPen(color, 2))
    p.drawRoundedRect(QRectF(w.rect()).adjusted(1, 1, -1, -1), 13, 13)
    p.end()


class _HistoryCard(QFrame):
    """A history card: a tap, or Enter / Space once Tab reaches it, opens the whole answer (and with it "Continue
    in chat" and "Pin", which were out of the keyboard's reach while the card could only be clicked)."""

    def __init__(self):
        super().__init__(objectName="Card")
        self.setFocusPolicy(Qt.TabFocus)          # from the keyboard: a click opens it without leaving a ring
        self.toggled = lambda: None

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.hasFocus():
            draw_focus(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.toggled()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.toggled()
            e.accept()
            return
        super().keyPressEvent(e)


class HistoryDialog(GlassDialog):
    """Search everything asked with this character: 'what did I ask about Mano last week?'

    Airy by design (live feedback: a wall of full answers was hard to read): one card per question with a
    two-line preview and the pictures of what it was about; tap to read the whole answer, and pick the
    conversation up again in the chat."""

    pin_requested = Signal(str, str)
    continue_requested = Signal(str, str, list)      # question, answer, card keys

    def __init__(self, pairs: list[dict], name: str, lang: str, stylesheet: str, kb=None):
        self.t = t = I18n(lang or "he")
        super().__init__(t("history_title", name=name), t.rtl)
        self.pairs, self.kb = pairs, kb
        self.setStyleSheet(stylesheet)
        self.resize(540, 720)
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText(bidi.plain(t("history_search"), t.rtl))
        self.search.setAccessibleName(t("history_search"))    # a placeholder isn't read as the field's name
        self.search.setClearButtonEnabled(True)
        follow_typing(self.search, t.rtl)
        # rebuilt once typing pauses, not on every key (each pass rebuilds up to PAGE cards)
        self._debounce = QTimer(self, singleShot=True, interval=self.DEBOUNCE_MS, timeout=self._new_search)
        self.search.textChanged.connect(lambda *_: self._debounce.start())
        outer.addWidget(self.search)
        self.initial_focus = self.search
        self._shown = self.PAGE
        self.count = QLabel(objectName="RowHint")
        outer.addWidget(self.count)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(*gutter(t.rtl))  # the room before the scrollbar, on its side (left in Hebrew)
        self.rows.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        rtl_buttons(self, t.rtl)
        self._fill()

    def _day(self, ts: float) -> str:
        # calendar days, not "now minus 24 hours": the night after a 23-hour day (clocks forward) that landed two
        # days back, and for an hour "Yesterday" headed the day before yesterday
        day = dt.date.fromtimestamp(ts)
        today = dt.date.fromtimestamp(time.time())
        if day == today:
            return self.t("day_today")
        if day == today - dt.timedelta(days=1):
            return self.t("day_yesterday")
        return dates.day(day, self.t.rtl, day.year != today.year)

    PAGE = 80              # cards built at a time; "Show more" adds the next PAGE
    DEBOUNCE_MS = 150

    def _new_search(self):
        self._shown = self.PAGE
        self._fill()

    def _more(self):
        """The next PAGE cards under the ones shown. Only those are made: rebuilding every card shown so far on
        each tap grew with the list (4-10 s freezes deep into a long history)."""
        start = min(len(self._hits), self._shown)
        self._shown += self.PAGE
        # the "Show more" button and the closing stretch come off the end; the new cards and a new button go on
        while self.rows.count() > self._list_end:
            item = self.rows.takeAt(self.rows.count() - 1)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._add_cards(start)

    def _fill(self):
        t, rtl = self.t, self.t.rtl
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().hide()   # gone now, not at the next event loop
                item.widget().deleteLater()
        q = self.search.text().strip()
        self._hits = pins.search(self.pairs, q)
        self._last_day = None
        # nothing asked yet (a new character): its own line, not a failed search's "0 results / Nothing found" (VIS-6)
        self.count.setVisible(bool(self.pairs))
        if not self._hits:
            self.count.setText(bidi.plain(t("history_count", n=0), rtl))
            self.rows.addWidget(QLabel(bidi.plain(t("history_none" if self.pairs else "history_empty"), rtl),
                                       objectName="RowHint"))
            self.rows.addStretch(1)
            return
        self._add_cards(0)

    def _add_cards(self, start: int):
        """Cards for hits[start:_shown] at the end of the list, then "Show more" when there are more."""
        t, rtl = self.t, self.t.rtl
        hits = self._hits
        shown = min(len(hits), self._shown)
        # "80 of 200": the list shows the newest PAGE, the rest behind "Show more"
        count = t("history_count", n=len(hits)) if shown == len(hits) else t("history_count_of", n=shown, total=len(hits))
        self.count.setText(bidi.plain(count, rtl))
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        for p in hits[start:shown]:
            day = self._day(p["t"])
            if day != self._last_day:          # carried over from the cards above: a day goes on under its header
                head = QLabel(bidi.plain(day, rtl), objectName="SectionHeader")
                head.setAlignment(align)
                self.rows.addSpacing(4)
                self.rows.addWidget(head)
                self._last_day = day
            self.rows.addWidget(self._card(p, align))
        self._list_end = self.rows.count()
        if shown < len(hits):
            # the numbers in a line over the button, not on it: a button lays a Hebrew label with a number in it
            # out of order (the number came first, seen live), the same as the quest list's "Show more quests"
            at_end = QLabel(bidi.plain(count, rtl), objectName="RowHint")
            at_end.setAlignment(Qt.AlignHCenter)
            self.rows.addWidget(at_end)
            more = QPushButton(bidi.plain(t("history_more"), rtl), objectName="Secondary")
            more.setCursor(Qt.PointingHandCursor)
            more.setAutoDefault(False)
            more.clicked.connect(self._more)
            self.more_btn = more
            self.rows.addWidget(more, 0, Qt.AlignHCenter)
        self.rows.addStretch(1)

    def _card(self, p: dict, align) -> QFrame:
        t, rtl = self.t, self.t.rtl
        card = _HistoryCard()
        card.setCursor(Qt.PointingHandCursor)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.setSpacing(6)
        top = QHBoxLayout()
        # a long question (the inventory check's own text) is cut to a short title until the card opens
        question = pins.shown_question(p["q"])         # not the stored "[about Mano] …"
        short_q = short_text(question, 70)
        card.setAccessibleName(question)
        # an English question keeps its own order in Hebrew ("?What does Mano drop" read backwards)
        qlb = QLabel(objectName="CardName")
        qlb.setTextFormat(Qt.PlainText)
        qlb.setWordWrap(True)
        qlb.setAlignment(align)
        set_wrapped_name(qlb, short_q, rtl)
        top.addWidget(qlb, 1)
        when = QLabel(time.strftime("%H:%M", time.localtime(p["t"])), objectName="CardSub")
        top.addWidget(when, 0, Qt.AlignTop)
        cl.addLayout(top)
        # two lines of the answer, plain: the whole answer opens on a tap
        # an English answer keeps its "…" at its own end (ltr_name: one block), not on the Hebrew side
        preview = QLabel(objectName="CardSub")
        preview.setTextFormat(Qt.PlainText)
        preview.setWordWrap(True)
        preview.setAlignment(align)
        set_wrapped_name(preview, short_text(p["a"].replace("**", ""), 110), rtl)
        cl.addWidget(preview)
        # the pictures under the preview; opened, under the whole answer (above it they split the title from it)
        pics = self._pictures(p.get("entities") or [])
        if pics:
            cl.addWidget(pics)
        made: dict = {}

        def full_part() -> tuple[QWidget, QVBoxLayout]:
            """The whole answer and its buttons, made the first time the card opens: made up front for every
            card, they were most of the cost of a long list, and most cards are never opened."""
            if not made:
                full = QWidget()
                fl = QVBoxLayout(full)
                fl.setContentsMargins(0, 6, 0, 0)
                fl.setSpacing(10)
                fl.addWidget(_answer_label(p["a"]))
                actions = QHBoxLayout()
                go = QPushButton(bidi.plain(t("history_continue"), rtl), objectName="Primary")
                go.setCursor(Qt.PointingHandCursor)
                go.setAutoDefault(False)
                go.clicked.connect(lambda _=False: self.continue_requested.emit(p["q"], p["a"],
                                                                                list(p.get("entities") or [])))
                actions.addWidget(go)
                actions.addStretch(1)
                pin = QPushButton(bidi.plain(t("pin"), rtl), objectName="Link")
                pin.setIcon(theme.glyph_icon("pin", theme.accent_text(), 14))     # in the link's orange
                pin.setCursor(Qt.PointingHandCursor)
                pin.setAutoDefault(False)
                pin.clicked.connect(lambda _=False, b=pin: (self.pin_requested.emit(question, p["a"]), b.setEnabled(False)))
                actions.addWidget(pin)
                fl.addLayout(actions)
                full.hide()
                cl.addWidget(full)
                made["full"], made["fl"] = full, fl
            return made["full"], made["fl"]

        def toggle():
            full, fl = full_part()
            opened = not full.isVisible()
            if pics:
                if opened:
                    fl.insertWidget(1, pics)       # right after the answer
                else:
                    cl.insertWidget(cl.indexOf(preview) + 1, pics)
            full.setVisible(opened)
            preview.setVisible(not opened)
            set_wrapped_name(qlb, question if opened else short_q, rtl)
        card.toggled = toggle
        return card

    def _pictures(self, keys: list[str]) -> QWidget | None:
        """Small pictures of the monsters / items / NPCs the answer was about."""
        kb = self.kb
        if kb is None:
            return None
        row_w = QWidget()
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(6)
        shown = 0
        for k in keys:
            e = kb.get(k)
            if not e or k.startswith("map/"):      # a map's picture is a whole minimap: a sliver at 30 px
                continue
            path = kb.picture(k)
            pm = QPixmap(str(path)) if path else QPixmap()
            # a wide picture is a banner, not a sprite (a guide's 1200x630 cover): at 30 px a dark 30x16 strip
            if pm.isNull() or pm.width() > 1.5 * pm.height():
                continue
            lb = QLabel()
            lb.setFixedSize(30, 30)
            lb.setAlignment(Qt.AlignCenter)
            lb.setPixmap(pm.scaled(30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            zoom_on_hover(lb, path, e.get("name", ""))
            lb.setToolTip(e.get("name", k))
            row.addWidget(lb)
            shown += 1
            if shown == 8:
                break
        if not shown:
            return None
        row.addStretch(1)
        return row_w


def character_card_image(c, avatar, kb, progress: dict | None, t) -> QPixmap:
    """A shareable picture of the character: portrait, name, level and job, EXP bar, map."""
    from ..store import ASSETS
    from .widgets import Avatar, character_image, level_job
    w = QFrame(objectName="ShareCard")
    w.setLayoutDirection(Qt.LeftToRight)
    w.setFixedWidth(380)
    lay = QHBoxLayout(w)
    lay.setContentsMargins(18, 16, 18, 14)
    lay.setSpacing(16)
    pic = Avatar(96)
    pic.set_image(character_image(c, avatar, kb))
    lay.addWidget(pic, 0, Qt.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(3)
    name = QLabel(c.name, objectName="ShareName")
    col.addWidget(name)
    # the text column's width: the card less its margins, the portrait and the gap beside it
    text_w = 380 - 18 - 18 - 96 - 16
    col.addWidget(QLabel(level_job(c, t.rtl), objectName="ShareMeta"))
    if progress:
        bar = QProgressBar(objectName="ExpBar")
        bar.setRange(0, 1000)
        bar.setValue(round(progress["pct"] * 10))
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        col.addWidget(bar)
        col.addWidget(QLabel(f"EXP {progress['pct']:g}%", objectName="ExpText"))
    if c.map:
        # a long KB map name ("Physical Fitness Test <Normal Waiting Room>") goes on to a second line, not cut
        where = QLabel(c.map, objectName="ExpText")
        where.setWordWrap(True)
        col.addWidget(where)
    col.addStretch(1)
    brand = QHBoxLayout()
    brand.addStretch(1)
    icon = QLabel()
    pm = QPixmap(str(ASSETS / "brand" / "icon-64.png"))
    if not pm.isNull():
        icon.setPixmap(pm.scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    brand.addWidget(icon)
    brand.addWidget(QLabel("Maple Helper", objectName="ShareBrand"))
    col.addLayout(brand)
    lay.addLayout(col, 1)
    w.ensurePolished()
    # a long name ends in "…" instead of a letter cut in half (the form takes 24 characters; 12 wide ones overflow)
    name.setText(QFontMetrics(name.font()).elidedText(c.name, Qt.ElideRight, text_w))
    w.adjustSize()
    from .widgets import on_solid_background
    return on_solid_background(w.grab(), 18)
