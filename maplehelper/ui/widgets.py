"""Chat building blocks: message bubbles, entity cards, system lines."""
from __future__ import annotations

import re
import webbrowser

from PySide6.QtCore import QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
                               QWidgetAction)

from .. import bidi
from ..kb import KnowledgeBase


def _label(text: str = "", name: str | None = None, rich: bool = False, wrap: bool = True) -> QLabel:
    lb = QLabel()
    if name:
        lb.setObjectName(name)
    lb.setTextFormat(Qt.RichText if rich else Qt.PlainText)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lb.setText(text)
    return lb


def on_solid_background(pm: QPixmap, radius: float) -> QPixmap:
    """A grabbed card drawn over the chat's own solid color: the card itself is see-through glass, and
    pasted into Discord or WhatsApp it would be light text on nothing (unreadable in dark mode)."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QPainter, QPainterPath
    from . import theme
    out = QPixmap(pm.size())
    out.setDevicePixelRatio(pm.devicePixelRatio())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    size = pm.deviceIndependentSize()
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size.width(), size.height()), radius, radius)
    p.fillPath(path, QColor(*theme.P()["glass"]))        # opaque: the color under the glass in the chat
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


ZWSP = chr(0x200B)       # zero-width space: a place to wrap, nothing drawn


def soft_breaks(text: str, run: int = 30, every: int = 20) -> str:
    """A zero-width break every `every` characters inside a word longer than `run` (a URL, names joined by "_"):
    Qt wraps only at spaces and a few marks, and such a word ran past the bubble and was cut. "**" stays whole
    (the bold markup)."""
    def cut(m):
        w, out, n = m.group(0), [], 0
        for i, ch in enumerate(w):
            out.append(ch)
            n += 1
            if n >= every and i + 1 < len(w) and ch != "*" and w[i + 1] != "*":
                out.append(ZWSP)
                n = 0
        return "".join(out)
    return re.sub(r"\S{%d,}" % run, cut, text)


class Bubble(QFrame):
    """A chat message. Direction is decided per paragraph, not by the UI language."""

    def __init__(self, text: str, role: str, ui_rtl: bool, tag: str = "", direction: str | None = None):
        # tag: "Mano, Blue Snail"; direction: the message's language when known ("rtl" for a Hebrew instant answer)
        super().__init__()
        self.role = role
        self._dir = direction
        self.setObjectName("BubbleUser" if role == "user" else "BubbleBot")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 8, 13, 9)
        self.tag_label = None
        if tag:
            self.tag_label = QLabel("↩ " + tag, objectName="BubbleTag")
            self.tag_label.setWordWrap(True)    # five tagged names must not stretch the bubble past the chat
            lay.addWidget(self.tag_label)
        self.label = _label(rich=True)
        self.label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        lay.addWidget(self.label)
        self.set_text(text)

    def fit_width(self, row_width: int) -> None:
        """Your own messages: as wide as their text, up to ~78% of the feed. (Qt's word-wrap guess
        makes a short question a tall, thin column.)"""
        m = self.layout().contentsMargins()
        fm = self.label.fontMetrics()
        text_w = max((fm.horizontalAdvance(ln) for ln in (self._text or "").splitlines()), default=0) + 4
        if self.tag_label:
            text_w = max(text_w, self.tag_label.fontMetrics().horizontalAdvance(self.tag_label.text()) + 4)
        cap = int(row_width * self.MAX_SHARE) - m.left() - m.right()
        self.label.setMinimumWidth(max(0, min(text_w, cap)))

    MAX_SHARE = 0.78

    def set_text(self, text: str) -> None:
        self._text = text
        if not text:
            self.label.setText("")
            return
        body = bidi.to_html(soft_breaks(text), self._dir)
        if self.role != "user":
            from . import terms
            from .. import glossary
            body = glossary.annotate(body, terms.LANG, limit=4)
            if not getattr(self, "_terms", False):
                terms.watch(self.label, terms.LANG)
                self._terms = True
        self.label.setText(body)

    def add_pin(self, on_pin, tip: str) -> None:
        """A small pin under a finished answer (the icon font's, like the header's icons; it was the 📌 emoji)."""
        from PySide6.QtWidgets import QToolButton
        row = QHBoxLayout()
        row.addStretch(1)
        from . import theme
        b = QToolButton(objectName="Icon", text=theme.ICON["pin"])
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.clicked.connect(lambda: (on_pin(), b.setEnabled(False)))
        row.addWidget(b)
        self.layout().addLayout(row)


class BubbleRow(QWidget):
    """iMessage convention: your messages sit on the trailing side (left in Hebrew), answers span the width."""

    def __init__(self, bubble: Bubble, ui_rtl: bool):
        super().__init__()
        self.bubble = bubble
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # the bubble's text width must never hold the feed wide: when the chat narrows (or its scrollbar appears) the
        # row shrinks first, then refits the bubble (else a long question was cut off at the edge, seen live)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        # the layout mirrors in an RTL UI, so "trailing" is the left edge there
        if bubble.role == "user":
            lay.addSpacing(48)
            lay.addStretch(1)
            lay.addWidget(bubble, 0)
        else:
            lay.addWidget(bubble, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.bubble.role == "user" and e.oldSize().width() != e.size().width():
            self.bubble.fit_width(self.width())


class SystemLine(QLabel):
    def __init__(self, text: str):
        super().__init__(bidi.plain(text))
        self.setObjectName("SystemLine")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignHCenter)

    def set_text(self, text: str) -> None:
        """Shown again in a new language when the player switches it."""
        self.setText(bidi.plain(text))


class ElideLink(QPushButton):
    """A link button that can be narrower than its text: the text ends in "…" and the tooltip has all of it. (At
    470 px with the large font, "This is the same character (update the name)" held the whole conversation
    wider than its view, and every row's far edge was cut.)"""

    def __init__(self, text: str = "", **kw):
        super().__init__(**kw)
        self._full = ""
        # a push button is never narrower than its text (QSizePolicy.Minimum); this one may be
        self.setSizePolicy(QSizePolicy.Preferred, self.sizePolicy().verticalPolicy())
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = text
        self._elide()

    def text(self) -> str:          # what it says, not what fits
        return self._full

    def sizeHint(self):
        # as wide as the whole text (the flow and box layouts give it that much when there is room)
        h = super().sizeHint()
        fm = self.fontMetrics()
        return QSize(h.width() + fm.horizontalAdvance(self._full) - fm.horizontalAdvance(super().text()), h.height())

    def minimumSizeHint(self):
        h = super().minimumSizeHint()
        return QSize(min(h.width(), 48), h.height())

    def _elide(self) -> None:
        fm = self.fontMetrics()
        pad = super().sizeHint().width() - fm.horizontalAdvance(super().text())
        room = self.width() - pad
        shown = self._full if room >= fm.horizontalAdvance(self._full) else             fm.elidedText(self._full, Qt.ElideRight, max(0, room))
        super().setText(shown)
        self.setToolTip(self._full.strip() if shown != self._full else "")
        self.setAccessibleName(self._full.strip())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()


class NoticeCard(QFrame):
    """An orange note in the conversation with one action (e.g. "what changed?")."""

    clicked = Signal()
    clicked2 = Signal()        # the second action, when there is one

    def __init__(self, text: str, action: str, rtl: bool, stacked: bool = False, action2: str = "",
                 closable: bool = True):
        """closable: an ✕ at the far edge takes the note out of the chat (a KB update's note stayed for good)."""
        super().__init__(objectName="InfoNote")
        from . import theme
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        # the button always under the text (the same in both languages); two actions sit side by side under it
        self._stacked = stacked or bool(action2)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)
        # ⓘ beside the first line of the text, not the middle of a three-line note
        self.icon = QLabel(theme.ICON["info"], objectName="InfoIcon")
        self.icon.setContentsMargins(0, 1, 0, 0)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        # the action sits beside the text when there is room, else on its own row under it (at 470 px a button
        # beside the text took half the card and cut the text, seen live)
        self._col = QVBoxLayout()
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)
        self._top = QHBoxLayout()
        self._top.setContentsMargins(0, 0, 0, 0)
        self._top.setSpacing(10)
        self.msg = QLabel(objectName="InfoText")
        self.msg.setWordWrap(True)
        self._top.addWidget(self.msg, 1)
        self._col.addLayout(self._top)
        lay.addLayout(self._col, 1)
        self.btn = ElideLink(objectName="Link")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.clicked.emit)
        self.btn2 = None
        if action2:
            self.btn2 = ElideLink(objectName="Link")
            self.btn2.setCursor(Qt.PointingHandCursor)
            self.btn2.clicked.connect(self.clicked2.emit)
            # the two side by side, wrapping onto a second line in a narrow chat (in one row they held it wide)
            from .controls import FlowLayout
            holder = QWidget()
            self._row2 = FlowLayout(holder, spacing=18, line_spacing=0)
            self._row2.setContentsMargins(0, 0, 0, 0)
            self._row2.addWidget(self.btn)
            self._row2.addWidget(self.btn2)
            self._col.addWidget(holder)
        self.close_btn = None
        if closable:
            from PySide6.QtWidgets import QToolButton
            self.close_btn = QToolButton(objectName="Icon", text=theme.ICON["close"])
            self.close_btn.setCursor(Qt.PointingHandCursor)
            self.close_btn.setFixedSize(24, 24)
            self.close_btn.clicked.connect(self._dismiss)
            lay.addWidget(self.close_btn, 0, Qt.AlignTop)
        self._below = None
        self.set_texts(text, action, rtl, action2)

    def _dismiss(self):
        self.hide()
        self.deleteLater()

    def set_texts(self, text: str, action: str, rtl: bool, action2: str = ""):
        """Shown again in a new language when the player switches it."""
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        self.msg.setText(bidi.plain(text, rtl))
        self.btn.setText(bidi.plain(action, rtl))
        if self.close_btn is not None:
            from ..i18n import I18n
            label = I18n("he" if rtl else "en")("notice_close")
            self.close_btn.setToolTip(label)
            self.close_btn.setAccessibleName(label)
        if self.btn2 is not None and action2:
            self.btn2.setText(bidi.plain(action2, rtl))
        self._place_button()

    @staticmethod
    def button_below(card_width: int, button_width: int) -> bool:
        """Under the text when the button would take more than a quarter of the card (beside it, the text wraps
        into a narrow column)."""
        return button_width * 4 > card_width - 40

    def _place_button(self):
        if self.btn2 is not None:      # two actions: on their own wrapping row under the text, placed once
            return
        below = self._stacked or self.button_below(self.width(), self.btn.sizeHint().width())
        if below == self._below:
            return
        self._below = below
        self._top.removeWidget(self.btn)
        self._col.removeWidget(self.btn)
        if below:      # AlignLeft is the leading edge (mirrored in a Hebrew card)
            self._col.addWidget(self.btn, 0, Qt.AlignLeft)
        else:
            self._top.addWidget(self.btn, 0, Qt.AlignVCenter)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_button()


class SessionCard(QFrame):
    """'Last session', one block per character: portrait and name, what changed, the questions asked, and
    "Continue the chat" back into that character's conversation. (One list of lines read as if one
    character's questions belonged to the other.)"""

    def __init__(self, title: str, blocks: list[dict], rtl: bool, continue_text: str = "", on_continue=None):
        super().__init__(objectName="Card")
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        self._rtl = rtl
        self._align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(6)
        head = QLabel(bidi.plain(title, rtl), objectName="CardName")
        head.setAlignment(self._align)
        col.addWidget(head)
        for i, b in enumerate(blocks):
            if i:
                sep = QFrame(objectName="Separator")
                sep.setFixedHeight(1)
                col.addWidget(sep)
            row = QHBoxLayout()
            row.setSpacing(10)
            pic = Avatar(36)
            pic.set_image(b.get("avatar"))
            row.addWidget(pic, 0, Qt.AlignTop)
            body = QVBoxLayout()
            body.setSpacing(2)
            name = QLabel(bidi.plain(b["name"], rtl), objectName="ProfileName")
            name.setAlignment(self._align)
            body.addWidget(name)
            for ln in b["lines"]:
                body.addWidget(self._line(ln, "CardStat"))
            for q in b.get("questions", []):
                body.addWidget(self._bullet(q))
            if b.get("questions") and on_continue and b.get("id"):
                go = QPushButton(bidi.plain(continue_text, rtl), objectName="Link")
                go.setCursor(Qt.PointingHandCursor)
                go.setAutoDefault(False)
                go.clicked.connect(lambda _=False, cid=b["id"]: on_continue(cid))
                body.addWidget(go, 0, Qt.AlignLeft)        # the leading edge (mirrored in Hebrew)
            row.addLayout(body, 1)
            col.addLayout(row)

    def _line(self, text: str, name: str) -> QLabel:
        lb = QLabel(bidi.plain(text, self._rtl), objectName=name)
        lb.setWordWrap(True)
        lb.setAlignment(self._align)
        return lb

    def _bullet(self, question: str) -> QLabel:
        """'• question' in the card's own direction, the bullet at its leading edge. The question is already one
        isolated block (name_block); left to plain(), the bullet alone has no direction, so a Hebrew question
        in the English card made the whole line right-to-left and put the bullet after it."""
        mark = bidi.RLM if self._rtl else "\u200e"      # (a left-to-right mark)
        lb = QLabel(f"{mark}• {question}{mark}", objectName="CardSub")
        lb.setWordWrap(True)
        lb.setAlignment(self._align)
        return lb


# ------------------------------------------------------------------ source and "updated" chips

def tip_html(text: str, rtl: bool) -> str:
    """A tooltip, line by line in the UI's direction (an English change line inside stays one block)."""
    return bidi.to_html(text, "rtl" if rtl else "ltr")


def source_tag(t, source: str, stamp=None) -> QLabel:
    """A small chip saying where a datum comes from ("COT2", "MSEA", "קהילה"); the tooltip says what that means,
    and for a build's values what changed from the build before ("ACC 62 → 64 (COT1 → COT2)")."""
    from .. import sources
    lb = QLabel(bidi.plain(sources.tag(t, source), t.rtl), objectName="SourceTag")
    lb.setAlignment(Qt.AlignCenter)
    lb.setFixedHeight(17)              # as tall as the BETA badge it looks like
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    text = sources.stamp_tip(t, source, stamp)
    lb.setToolTip(tip_html(text, t.rtl))
    lb.setAccessibleName(f"{sources.tag(t, source)}: {text}")
    return lb


def source_tags(t, srcs, stamp=None) -> list[QLabel]:
    """One chip per distinct source, in the order given."""
    return [source_tag(t, s, stamp) for s in dict.fromkeys(s for s in srcs if s)]


def updated_tag(t, kb, key: str) -> QLabel | None:
    """The "Updated" chip of an entity a KB update changed in the last week (recent.py), with what changed."""
    from .. import recent
    r = recent.of(kb, key) if key else None
    if not r or not recent.lines(t, kb, r):
        return None
    lb = QLabel(bidi.plain(t("updated_tag"), t.rtl), objectName="UpdatedTag")
    lb.setAlignment(Qt.AlignCenter)
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    text = recent.tip(t, kb, r)
    lb.setToolTip(tip_html(text, t.rtl))
    lb.setAccessibleName(text)
    return lb


def vote_tag(t, vote: dict) -> QLabel:
    """A community drop's votes beside it: "16 ✓" (players who confirmed it), or "single report" when one player
    alone reported it; the tooltip gives confirmed and denied (kb.community_drops)."""
    single = bool(vote.get("single"))
    text = t("votes_single") if single else t("votes_up", n=vote.get("up", 0))
    # "16 ✓" one left-to-right block in Hebrew too (run by run, the mark went before the number: "✓16")
    lb = QLabel(bidi.plain(text, t.rtl) if single else bidi.ltr_name(text, t.rtl), objectName="VoteTag")
    lb.setProperty("single", "true" if single else "false")
    lb.setAlignment(Qt.AlignCenter)
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    tip = t("votes_single_tip") if single else t("votes_tip", up=vote.get("up", 0), down=vote.get("down", 0))
    lb.setToolTip(tip_html(tip, t.rtl))
    lb.setAccessibleName(f"{text}: {tip}")
    return lb


def mesos_text(t, mesos) -> str:
    """ "mesos 18–23 (קהילה)" / "Mesos 18–23 (Community)" (sources.mesos_line)."""
    from .. import sources
    return sources.mesos_line(t, mesos)


def mesos_tip(t, mesos) -> str:
    """What the mesos numbers are: the median of the players' reports, and how often a kill drops mesos."""
    _, _, chance, n = mesos
    tip = t("mesos_tip", n=n)
    if chance is not None:
        tip += " " + t("mesos_chance", pct=f"{chance:g}")
    return tip

def level_job(c, rtl: bool) -> str:
    """ "רמה 15 · Bowman" / "Lv. 15 · Bowman": the same words as the rest of the app."""
    from ..i18n import I18n
    return bidi.plain(f"{I18n('he' if rtl else 'en')('lv_short', n=c.level)} · {c.job_label}", rtl)


def zoom_on_hover(label, path, caption: str = "", height: int = 96) -> None:
    """A small picture shows large on hover, as the quests' and recipes' pictures do: at 30-56 px a sprite hid
    what it shows (the owner)."""
    if not path:
        return
    from html import escape
    from pathlib import Path as _P
    uri = _P(str(path)).resolve().as_uri()
    cap = f"<br>{escape(caption)}" if caption else ""
    label.setToolTip(f"<div align='center'><img src='{uri}' height='{height}'>{cap}</div>")

def info_tag(t, text: str, tip: str, kind: str = "Tag") -> QLabel:
    """A small chip with its own explanation (a pet's "In Cash Shop", a tier grade)."""
    lb = QLabel(bidi.plain(text, t.rtl), objectName=kind)
    lb.setAlignment(Qt.AlignCenter)
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    if tip:
        lb.setToolTip(tip_html(tip, t.rtl))
    lb.setAccessibleName(f"{text}: {tip}" if tip else text)
    return lb


def changed_tag(t, kb, key: str) -> QLabel | None:
    """ "Changed in COT2" on a skill whose values changed between two builds (sitedata.py); the tooltip lists the
    changes ("Chance: 35% → 50% · Damage: 180% → 140%")."""
    from .. import sitedata
    ch = sitedata.skill_change(kb, key)
    if not ch:
        return None
    return info_tag(t, sitedata.chip_label(t, ch), sitedata.change_tip(t, ch, kb), "ChangedTag")


def pet_parts(t, kb, key: str) -> tuple[list[str], list[QLabel]] | None:
    """A pet's numbers as pill texts (lifespan, hunger, commands to Lv 30) and its chips (sold now or not, and
    "Closed test" for a value from the closed tests); None for an item that is no pet."""
    from .. import sitedata, sources
    p = sitedata.pet(kb, key)
    if not p:
        return None
    pills = [t("pet_life", v=sitedata.lifespan_text(t, p)), t("pet_hunger", n=p.hunger)]
    if p.commands_text:
        # the whole number ("25,000"), not the site's "25.0k" shorthand
        pills.append(t("pet_commands", level=p.level,
                       n=f"{p.commands:,}" if isinstance(p.commands, int) else p.commands_text.lstrip("~")))
    chips = [info_tag(t, t("pet_sold" if p.sold else "pet_not_sold"), t("pet_sold_tip" if p.sold else "pet_not_sold_tip"),
                      "TagGood" if p.sold else "Tag")]
    if p.closed_test:
        chip = source_tag(t, sources.CLOSED_TEST)
        if "lifespan" in p.closed_test:
            chip.setToolTip(tip_html(t("pet_life_closed_tip"), t.rtl))
        chips.append(chip)
    return pills, chips


def chip_row(chips: list[QWidget], text: QWidget | None = None, spacing: int = 6, lead: bool = False) -> QHBoxLayout:
    """[text] [chip] [chip], anchored at the reading start (mirrored in Hebrew): the chips follow the data they
    label, never pushed to the far edge. lead=True puts the chips first, before a long line that wraps (the line
    then takes the rest of the width; after it, a wrapping line left the chips nowhere fixed)."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    if lead:
        for c in chips:
            row.addWidget(c, 0, Qt.AlignTop)
        if text is not None:
            row.addWidget(text, 1)
        else:
            row.addStretch(1)
        return row
    if text is not None:
        row.addWidget(text, 0, Qt.AlignVCenter)
    for c in chips:
        row.addWidget(c, 0, Qt.AlignVCenter)
    row.addStretch(1)
    return row


# ------------------------------------------------------------------ entity cards

def card_subtitle(t, category: str, kind: str | None) -> str:
    """'Monster', 'Item · Etc / Monster Drop': the category in the UI language, then the database's type
    unless it only repeats the category (a monster's type is "Monster")."""
    key = f"cat_{category}"
    label = t(key) if t(key) != key else category
    from ..i18n import STRINGS
    names = {category.lower(), label.lower(), *(s.lower() for s in STRINGS.get(key, {}).values())}
    if kind and kind.strip().lower() not in names:
        return f"{label} · {kind}"
    return label


class _Selection(QObject):
    """One selected entity for the whole chat. Cards emit `picked`; the overlay decides and broadcasts `changed`."""

    picked = Signal(str)
    changed = Signal(list)    # the tagged keys (empty list = none)


SELECTION = _Selection()


class _Wishlist(QObject):
    """The active character's wished items, shared by every card (the overlay binds the store)."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.settings = self.profiles = None

    def bind(self, settings, profiles):
        self.settings, self.profiles = settings, profiles
        self.changed.emit()

    def keys(self) -> list[str]:
        from .. import wishlist
        if not self.settings or not self.profiles:
            return []
        return wishlist.items(self.settings, self.profiles.active_id)

    def has(self, key: str) -> bool:
        return key in self.keys()

    def toggle(self, key: str) -> None:
        from .. import wishlist
        if self.settings and self.profiles:
            wishlist.toggle(self.settings, self.profiles.active_id, key)
            self.changed.emit()


WISHLIST = _Wishlist()


class _RouteRequests(QObject):
    """A map card's "How to get here from my map": the app opens Play tools on the way there."""

    requested = Signal(str)       # the map's KB key


ROUTE_REQUESTS = _RouteRequests()


class Selectable:
    """Mixin: a tap selects this entity (orange border); every selectable follows the shared selection."""

    def _init_selectable(self, key: str, name: str = ""):
        self.key = key
        self.setCursor(Qt.PointingHandCursor)
        # Tab reaches it and Enter / Space tags it, as a click does (only a click could); a click doesn't
        # focus it, so the focus ring shows only for the keyboard
        self.setFocusPolicy(Qt.TabFocus)
        self.setProperty("focus_ring", True)
        self.setAccessibleName(name or key)
        SELECTION.changed.connect(self._on_selection)

    def _on_selection(self, keys: list):
        self.setProperty("selected", "true" if self.key in keys else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, ev):
        # released over the card: a press dragged off it (changing one's mind) tags nothing
        if ev.button() == Qt.LeftButton and self.rect().contains(ev.position().toPoint()):
            SELECTION.picked.emit(self.key)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and getattr(self, "key", None):
            SELECTION.picked.emit(self.key)
            return
        super().keyPressEvent(ev)


class EntityCard(Selectable, QFrame):
    """Image + official English name + key stats + credit; tap to ask about it, ↗ opens its NiaMeowDB page."""

    def __init__(self, kb: KnowledgeBase, key: str, lang: str):
        super().__init__()
        from ..i18n import I18n
        self.setObjectName("Card")
        e = kb.get(key) or {}
        self._init_selectable(key, e.get("name", key))
        self._t = t = I18n(lang)
        self.setToolTip(t("card_ask_tip"))
        self.url = e.get("url")
        he = lang == "he"
        # its own direction, from its own language: after a live switch an older card kept its Hebrew texts on the
        # right while the chat's new direction moved its picture to the left
        self.setLayoutDirection(Qt.RightToLeft if he else Qt.LeftToRight)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)

        pic = QLabel()
        pic.setFixedSize(56, 56)
        pic.setAlignment(Qt.AlignCenter)
        img = kb.picture(key)          # never empty: own picture, related one, or category icon
        if img:
            pm = QPixmap(str(img))
            if not pm.isNull():
                pic.setPixmap(pm.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                zoom_on_hover(pic, img)
        row.addWidget(pic, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(2)
        name = _label(e.get("name", key), "CardName", wrap=True)
        name.setLayoutDirection(Qt.LeftToRight)      # official English name, always LTR
        name.setAlignment(Qt.AlignLeft if not he else Qt.AlignRight)
        col.addWidget(name)

        sub = card_subtitle(t, e.get("category", ""), e.get("type"))
        # in Hebrew every line starts on the right, even an all-English one like "NPC"
        side = (Qt.AlignRight if he else Qt.AlignLeft) | Qt.AlignAbsolute
        sub_label = _label(bidi.plain(sub, he), "CardSub")
        sub_label.setAlignment(side)
        col.addWidget(sub_label)

        stats = self._stats(e, t)
        main, bonuses = stat_parts(e)
        # a pet: its lifespan, hunger and commands to Lv 30 as pills, sold now (or not) as a chip (sitedata.py)
        pet = pet_parts(t, kb, key) if key.startswith("item/") else None
        if pet:
            main = main + pet[0]
        if main or bonuses:
            # one small pill per stat ("Lv. 30", "DEF 75", "STR DEX INT LUK +1"): a single long line mixed Hebrew
            # labels with English stats and wrapped into a scrambled order in a Hebrew chat
            from .controls import FlowLayout
            pills = QWidget()
            flow = FlowLayout(pills, spacing=4)
            for text in main + bonuses:
                pill = QLabel(bidi.plain(text, he) if bidi._RTL.search(text) else text, objectName="StatPill")
                pill.setLayoutDirection(Qt.LeftToRight)
                flow.addWidget(pill)
            col.addWidget(pills)
        # a monster's mesos, as players reported them: "mesos 18–23 (קהילה)", its own line (it isn't the page's stat
        # and doesn't share the stat line's source)
        mesos = kb.community_mesos(key) if key.startswith("monster/") else None
        if mesos:
            self.mesos_label = _label(bidi.plain(mesos_text(t, mesos), he), "CardSub")
            self.mesos_label.setAlignment(side)
            self.mesos_label.setToolTip(tip_html(mesos_tip(t, mesos), he))
            col.addWidget(self.mesos_label)
        # the credit line is the card's source line: where the stat line's numbers come from (the page's build,
        # "COT2", else MeowDB's own) and, for an entity a KB update changed this week, "Updated", at its start
        from .. import sources
        chips = []
        if stats:
            stamp = sources.stat_source(kb, key)
            self.source_chip = source_tag(t, stamp.source if stamp else sources.MEOWDB, stamp)
            chips.append(self.source_chip)
            # what the build changed, in sight (it was only in the tag's tooltip: "why no COT1 data?", the owner)
            if stamp and stamp.changes and stamp.before and sources.test_build(stamp.source):
                shown = ", ".join(sources.change_line(c.stat, c.old, c.new) for c in stamp.changes[:3])
                more = len(stamp.changes) - 3
                text = t("src_changed_line", before=stamp.before, changes=shown) + \
                    (" " + t("pn_more", n=more) if more > 0 else "")
                self.changed_label = _label(bidi.plain(text, he), "CardSub")
                self.changed_label.setAlignment(side)
                self.changed_label.setToolTip(tip_html(sources.stamp_tip(t, stamp.source, stamp), he))
                col.addWidget(self.changed_label)
        if pet:
            chips += pet[1]
        changed = changed_tag(t, kb, key) if key.startswith("skill/") else None
        if changed:
            chips.append(changed)          # "Changed in COT2", its changes in the tooltip
        updated = updated_tag(t, kb, key)
        if updated:
            chips.append(updated)
        credit = _label("NiaMeowDB (meowdb.com)", "CardCredit", wrap=False)
        line = QWidget()
        credit_row = chip_row(chips)
        line.setLayout(credit_row)
        credit_row.addWidget(credit, 0, Qt.AlignVCenter)
        col.addWidget(line)
        row.addLayout(col, 1)
        from PySide6.QtWidgets import QToolButton
        from . import theme
        self._buttons = QWidget()
        bl = QVBoxLayout(self._buttons)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        if self.url:
            link = QToolButton(objectName="Icon", text=theme.ICON["open"])
            link.setCursor(Qt.PointingHandCursor)
            link.setToolTip("NiaMeowDB")
            link.setAccessibleName("NiaMeowDB")
            link.clicked.connect(lambda: webbrowser.open(self.url))
            bl.addWidget(link)
        if key.startswith("item/"):
            self._star = QToolButton(objectName="Icon")
            self._star.setCursor(Qt.PointingHandCursor)
            self._star.clicked.connect(lambda: WISHLIST.toggle(self.key))
            WISHLIST.changed.connect(self._refresh_star)
            self._refresh_star()
            bl.addWidget(self._star)
        if key.startswith("map/"):
            from .. import routes
            if routes.of(kb).of_key(key):          # a map in the game the route graph has
                way = QToolButton(objectName="Icon", text=theme.ICON["route"])
                way.setCursor(Qt.PointingHandCursor)
                way.setToolTip(t("card_route"))
                way.setAccessibleName(t("card_route"))
                way.clicked.connect(lambda: ROUTE_REQUESTS.requested.emit(self.key))
                bl.addWidget(way)
        copy = QToolButton(objectName="Icon", text=theme.ICON["copy"])
        copy.setCursor(Qt.PointingHandCursor)
        copy.setToolTip(self._t("copy_card"))
        copy.setAccessibleName(self._t("copy_card"))
        copy.clicked.connect(self.copy_image)
        bl.addWidget(copy)
        bl.addStretch(1)
        row.addWidget(self._buttons, 0, Qt.AlignTop)

    @Slot()       # a Qt slot: the wishlist's signal lets go of it when the widget is destroyed (else a crash)
    def _refresh_star(self):
        from . import theme
        on = WISHLIST.has(self.key)
        self._star.setText(theme.ICON["star_on" if on else "star"])
        self._star.setProperty("wished", "true" if on else "false")
        self._star.style().unpolish(self._star)
        self._star.style().polish(self._star)
        self._star.setToolTip(self._t("wish_remove" if on else "wish_add"))
        self._star.setAccessibleName(self._t("wish_remove" if on else "wish_add"))

    def copy_image(self):
        """The card as a picture on the clipboard, ready to paste in Discord or WhatsApp."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication, QToolTip
        self._buttons.setVisible(False)          # the picture shows the card, not its buttons
        was = self.property("selected")
        self.setProperty("selected", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        pm = on_solid_background(self.grab(), 14)
        self._buttons.setVisible(True)
        # still tagged for the question: its orange border comes back (it stayed off until the next tag change)
        self.setProperty("selected", was)
        self.style().unpolish(self)
        self.style().polish(self)
        QApplication.clipboard().setPixmap(pm)
        QToolTip.showText(QCursor.pos(), self._t("copied"), self)

    # the knowledge base's own prop names (data/kb/index.json): a monster's level, HP and EXP; an item's level
    # requirement, attack and defense. ("Required Level", "Attack" and "Defense" are no KB keys: 2,147 of 2,726 item
    # cards had no stat line at all, and none showed its level requirement.)
    MONSTER_STATS = ("Level", "HP", "EXP")
    ITEM_STATS = ("Level Requirement", "Weapon Attack", "Magic Attack", "Weapon Defense", "Magic Defense",
                  "Upgrade Slots")
    ITEM_BONUSES = ("STR", "DEX", "INT", "LUK", "HP", "MP", "Accuracy", "Avoidability", "Speed", "Jump")

    @staticmethod
    def _stats(e: dict, t, limit: int | None = None) -> str:
        """Every stat the KB gives the item (or the monster's level, HP and EXP): the bonuses are what tells one
        hood from the next ("Red Thief Hood" HP +15, "Green Thief Hood" DEX +1 and HP +5), so none is cut."""
        props = e.get("props") or {}
        item = e.get("category") == "item"
        keys = (EntityCard.ITEM_STATS + EntityCard.ITEM_BONUSES) if item else EntityCard.MONSTER_STATS
        bits = []
        for k in keys:
            v = props.get(k)
            if v in (None, "", 0, "0"):
                continue
            label = {"Level": t("card_level"), "Level Requirement": t("card_req_level")}.get(k, k)
            # an item's HP / STR is a bonus it gives: "HP +5", not the item's own "HP: 5"; a no-break space keeps a
            # value on its label's line ("LUK" ended one line and its "+3" began the next)
            sign = "" if str(v)[:1] in "+-" else "+"
            text = f"{k} {sign}{v}" if item and k in EntityCard.ITEM_BONUSES else f"{label}: {v}"
            # an English label with its value is one left-to-right piece ("HP: 233"): in a Hebrew line its colon
            # otherwise lands on the wrong side ("233 :HP", seen live)
            # (+ RLM: two English pieces side by side would otherwise merge into one run, in English order)
            bits.append(f"‪{text}‬‏" if label.isascii() else text)
            if limit and len(bits) >= limit:
                break
        return " · ".join(bits)



# ------------------------------------------------------------------ profile card (pinned at the top of the chat)

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPainterPath  # noqa: E402

JOB_IMAGE_FALLBACK = {  # 3rd jobs have no picture in the database: use their 2nd job's
    "crusader": "fighter", "white-knight": "page", "dragon-knight": "spearman", "f-p-mage": "f-p-wizard",
    "i-l-mage": "i-l-wizard", "priest": "cleric", "ranger": "hunter", "sniper": "crossbowman",
    "hermit": "assassin", "chief-bandit": "bandit",
}


def _slug(job: str) -> str:
    return job.lower().replace("/", "-").replace(" ", "-")


class Avatar(QLabel):
    """Rounded-square portrait."""

    def __init__(self, size: int = 46):
        super().__init__()
        self.setFixedSize(size, size)
        self._pm = None

    def set_image(self, path) -> None:
        pm = QPixmap(str(path)) if path else QPixmap()
        self._pm = None if pm.isNull() else pm
        self.update()

    def paintEvent(self, e):
        from . import theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        p.setClipPath(path)
        p.fillPath(path, QColor(255, 255, 255, 26) if theme.MODE == "dark" else QColor(0, 0, 0, 10))
        if self._pm:
            dpr = self.devicePixelRatioF()
            pm = self._pm.scaled(self.size() * dpr, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            w, h = pm.width() / dpr, pm.height() / dpr
            p.drawPixmap(int((self.width() - w) / 2), int((self.height() - h) / 2), pm)


class ProfileCard(QFrame):
    """Name, "Lv. 32 · Assassin" (English, as in game) and a live portrait of the character."""

    clicked = Signal()
    refresh_requested = Signal()

    def __init__(self):
        super().__init__(objectName="ProfileCard")
        # Tab reaches the card and Enter / Space opens the character menu (switch, add, edit, delete were
        # mouse-only); a click doesn't focus it, so the focus ring shows only for the keyboard
        self.setFocusPolicy(Qt.TabFocus)
        self.setProperty("focus_ring", True)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 12, 8)
        row.setSpacing(10)
        self.avatar = Avatar(46)
        row.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = QLabel(objectName="ProfileName")
        self.meta = QLabel(objectName="ProfileMeta")
        col.addWidget(self.name)
        col.addWidget(self.meta)
        from .plancard import ExpBar
        self.exp = ExpBar()
        self.exp.hide()
        col.addWidget(self.exp)
        # what the ⟳ is doing right now ("Reading the screen…"): a spinning icon alone said nothing for 40 s
        self.status = QLabel(objectName="ExpText")
        self.status.hide()
        col.addWidget(self.status)
        row.addLayout(col, 1)
        from PySide6.QtWidgets import QToolButton
        from . import theme
        self.refresh = QToolButton(objectName="Refresh", text=theme.ICON["refresh"])
        self.refresh.setCursor(Qt.PointingHandCursor)       # (its name and tooltip come from the chat's language)
        self.refresh.clicked.connect(self.refresh_requested.emit)
        row.addWidget(self.refresh, 0, Qt.AlignVCenter)
        from PySide6.QtWidgets import QPushButton
        self.now_btn = QPushButton(objectName="NowChip")      # "What now?": the text comes from the chat (language)
        self.now_btn.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.now_btn, 0, Qt.AlignVCenter)
        self._spin_frames = ["\ue72c", "\ue895"]      # refresh / sync glyphs alternate while busy
        from PySide6.QtCore import QTimer
        self._spin = QTimer(self, interval=260, timeout=self._tick)
        self._frame = 0

    def set_busy(self, busy: bool, tip: str = "", status: str = "") -> None:
        from . import theme
        self.refresh.setEnabled(not busy)
        if busy:
            self._spin.start()
        else:
            self._spin.stop()
            self.refresh.setText(theme.ICON["refresh"])
        if tip:
            self.refresh.setToolTip(tip)
        rtl = self.layoutDirection() == Qt.RightToLeft
        self.status.setText(bidi.plain(status, rtl) if busy and status else "")
        self.status.setAlignment((Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter)
        self.status.setVisible(bool(busy and status))

    def _tick(self):
        self._frame = (self._frame + 1) % len(self._spin_frames)
        self.refresh.setText(self._spin_frames[self._frame])

    def show_character(self, c, avatar_path, kb, rtl: bool) -> None:
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter
        self.setAccessibleDescription(f"{c.name} · {level_job(c, rtl)}")
        self.name.setText(bidi.plain(c.name, rtl))
        self.name.setAlignment(align)
        self.meta.setText(level_job(c, rtl))
        self.meta.setAlignment(align)
        self.avatar.set_image(character_image(c, avatar_path, kb))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)


class SplitMenu(QMenu):
    """A menu whose card rows (QWidgetAction) stand on their own above it: the panel is drawn only behind the
    plain actions, so the other characters' cards read as cards, not as part of the list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SplitMenu")
        self._lit: dict = {}          # action -> the widget that lights up when the arrow keys reach it
        self.hovered.connect(self._light)

    def add_highlight(self, action, widget) -> None:
        """`widget` shows as hovered while the arrow keys are on `action` (its look came from :hover alone, so
        the keyboard's row didn't show)."""
        self._lit[action] = widget

    def _light(self, action) -> None:
        for a, w in self._lit.items():
            on = "true" if a is action else "false"
            if w.property("active") != on:
                w.setProperty("active", on)
                w.style().unpolish(w)
                w.style().polish(w)

    def add_row(self, icon_name: str, text: str, on_click, enabled: bool = True) -> None:
        """A menu line laid out by us: in Hebrew the icon on the right and the text right beside it (a QMenu
        item with this panel left the Hebrew text at the far left, away from its icon)."""
        from . import theme
        row = QFrame(objectName="MenuRow")
        row.setProperty("panel", True)
        row.setCursor(Qt.PointingHandCursor)
        row.setEnabled(enabled)
        row.installEventFilter(theme.menu_row_hover())     # its label turns white on the orange hover
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(theme.glyph_icon(icon_name).pixmap(16, 16))
        lay.addWidget(icon)
        label = QLabel(bidi.plain(text, self.layoutDirection() == Qt.RightToLeft), objectName="MenuRowText")
        label.setAlignment((Qt.AlignRight if self.layoutDirection() == Qt.RightToLeft else Qt.AlignLeft)
                           | Qt.AlignAbsolute | Qt.AlignVCenter)
        lay.addWidget(label, 1)
        holder = QWidget()
        holder.setProperty("panel", True)
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(5, 0, 5, 0)
        hl.addWidget(row)

        a = QWidgetAction(self)
        a.setDefaultWidget(holder)
        a.setEnabled(enabled)
        # the action runs it: Enter on the row the arrow keys reached triggers it (only a click did), and a click
        # triggers it too, so both take one way
        a.triggered.connect(lambda _=False: on_click())

        def clicked(e, row=row):
            if e.button() == Qt.LeftButton and row.isEnabled() and row.rect().contains(e.position().toPoint()):
                self.close()
                a.trigger()
        row.mouseReleaseEvent = clicked
        self.add_highlight(a, row)
        self.addAction(a)

    def paintEvent(self, e):
        from . import theme

        def in_panel(a) -> bool:
            w = a.defaultWidget() if isinstance(a, QWidgetAction) else None
            return w is None or bool(w.property("panel"))
        plain = [self.actionGeometry(a) for a in self.actions() if in_panel(a) and a.isVisible()]
        if plain:
            top = min(r.top() for r in plain) - 5
            # the menu's 5 px padding is outside the panel: it lines up with the cards (and the card above)
            rect = QRectF(5.5, top + 0.5, self.width() - 11, self.height() - top - 1)
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(rect, 12, 12)
            p.fillPath(path, QColor(44, 44, 46, 250) if theme.MODE == "dark" else QColor(255, 255, 255, 250))
            p.setPen(QColor(255, 255, 255, 36) if theme.MODE == "dark" else QColor(0, 0, 0, 20))
            p.drawPath(path)
            p.end()
        super().paintEvent(e)


class CharacterChoice(QFrame):
    """Another character in the switch menu, as big as the character card above it (a small menu line with a
    tiny icon looked like a different, lesser thing)."""

    clicked = Signal()

    def __init__(self, c, avatar_path, kb, rtl: bool, choose_text: str = ""):
        super().__init__(objectName="ProfileCard")
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 12, 8)     # the card's own margins and portrait size
        row.setSpacing(10)
        self.avatar = Avatar(46)
        self.avatar.set_image(character_image(c, avatar_path, kb))
        row.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter
        name = QLabel(bidi.plain(c.name, rtl), objectName="ProfileName")
        meta = QLabel(level_job(c, rtl), objectName="ProfileMeta")
        for lb in (name, meta):
            lb.setAlignment(align)
            col.addWidget(lb)
        row.addLayout(col, 1)
        if choose_text:
            # "Choose character" where the card above has "What now?": says what a click on this card does
            choose = QPushButton(bidi.plain(choose_text, rtl), objectName="NowChip")
            choose.setCursor(Qt.PointingHandCursor)
            choose.setAutoDefault(False)
            choose.clicked.connect(self.clicked.emit)
            row.addWidget(choose, 0, Qt.AlignVCenter)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled() and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


def character_image(c, avatar_path, kb):
    """The character's own portrait, else the picture of its job (or class)."""
    if avatar_path:
        return avatar_path
    slug = _slug(c.job)
    return kb.image_path(f"class/{JOB_IMAGE_FALLBACK.get(slug, slug)}") or kb.image_path(
        f"class/{_slug(c.base_class)}")


class CharacterRow(QFrame):
    chosen = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, c, avatar_path, kb, active: bool, rtl: bool, can_delete: bool):
        super().__init__(objectName="CharacterRow")
        self.cid = c.id
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 8, 0, 8)
        row.setSpacing(10)
        self.avatar = Avatar(36)
        img = avatar_path
        if not img:
            slug = _slug(c.job)
            img = kb.image_path(f"class/{JOB_IMAGE_FALLBACK.get(slug, slug)}") or kb.image_path(
                f"class/{_slug(c.base_class)}")
        self.avatar.set_image(img)
        row.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(0)
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter
        name = QLabel(bidi.plain(c.name, rtl), objectName="ProfileName")
        name.setAlignment(align)
        meta = QLabel(level_job(c, rtl), objectName="ProfileMeta")
        meta.setAlignment(align)
        col.addWidget(name)
        col.addWidget(meta)
        row.addLayout(col, 1)
        check = QLabel("✓" if active else "", objectName="Check")
        check.setFixedWidth(18)
        row.addWidget(check)
        from . import theme
        pencil = QPushButton(theme.ICON["edit"], objectName="IconPlain")
        pencil.setCursor(Qt.PointingHandCursor)
        pencil.clicked.connect(lambda: self.edit_requested.emit(self.cid))
        row.addWidget(pencil)
        if can_delete:
            trash = QPushButton(theme.ICON["delete"], objectName="IconDanger")
            trash.setCursor(Qt.PointingHandCursor)
            trash.clicked.connect(lambda: self.delete_requested.emit(self.cid))
            row.addWidget(trash)

    def mouseReleaseEvent(self, e):
        self.chosen.emit(self.cid)


# the game's own short stat names, for pills and tiles
_SHORT = {"Level": "Lv.", "Level Requirement": "Lv.", "Weapon Attack": "ATT", "Magic Attack": "M.ATT",
          "Weapon Defense": "DEF", "Magic Defense": "M.DEF", "Accuracy": "ACC", "Avoidability": "AVOID",
          "Upgrade Slots": "Slots"}


def stat_parts(e: dict, tile: bool = False) -> tuple[list[str], list[str]]:
    """(main stats, bonuses) as short pieces: ["Lv. 30", "DEF 75", "Slots 10"], ["STR DEX INT LUK +1", "HP MP +10"].
    Bonuses of the same value are one piece (the Sauna Robe's four +1s), so a card or a half-width tile stays short.
    A tile leaves out the upgrade slots (the card has them). A monster: level, HP, EXP."""
    props = e.get("props") or {}
    if e.get("category") != "item":
        main = [f"{_SHORT.get(k, k)}\u00a0{props[k]:,}" if isinstance(props.get(k), int) else f"{_SHORT.get(k, k)}\u00a0{props[k]}"
                for k in EntityCard.MONSTER_STATS if props.get(k) not in (None, "", 0, "0")]
        return main, []
    main = [f"{_SHORT.get(k, k)}\u00a0{props[k]}" for k in EntityCard.ITEM_STATS
            if props.get(k) not in (None, "", 0, "0") and not (tile and k == "Upgrade Slots")]
    groups: dict[str, list[str]] = {}
    for k in EntityCard.ITEM_BONUSES:
        v = props.get(k)
        if v in (None, "", 0, "0"):
            continue
        value = str(v) if str(v)[:1] in "+-" else f"+{v}"
        groups.setdefault(value, []).append(_SHORT.get(k, k))
    # no-break spaces inside a group: it wraps as a whole ("HP MP +10" never splits after "HP")
    bonuses = ["\u00a0".join(names) + f"\u00a0{value}" for value, names in groups.items()]
    return main, bonuses


class EntityTile(Selectable, QFrame):
    """Compact item tile for lists (drops, rewards): picture + official name. Tap to ask about it."""

    def __init__(self, kb, key: str, t=None, vote: dict | None = None):
        """vote: a community drop's votes (kb.community_vote), shown under the name."""
        super().__init__(objectName="Tile")
        e = kb.get(key) or {}
        if t is None:
            from ..i18n import I18n
            from . import terms
            t = I18n(terms.LANG)          # the chat's language (a tile made without one)
        self._init_selectable(key, e.get("name", key))
        self.url = e.get("url")
        self.setToolTip(e.get("name", key))
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(8)
        pic = QLabel()
        pic.setFixedSize(32, 32)
        pic.setAlignment(Qt.AlignCenter)
        img = kb.picture(key)
        if img:
            pm = QPixmap(str(img))
            if not pm.isNull():
                pic.setPixmap(pm.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                zoom_on_hover(pic, img, e.get("name", ""))
        row.addWidget(pic)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = QLabel(e.get("name", key), objectName="TileName")
        self.name.setWordWrap(True)
        self.name.setMinimumWidth(48)        # a long word ("Intermediate") never holds two tiles wider than the chat
        col.addWidget(self.name)
        # the item's level requirement and bonuses under its name: tiles of look-alike items (the five Thief Hoods)
        # differ only there
        main, bonuses = stat_parts(e, tile=True) if e.get("category") == "item" else ([], [])
        # one left-to-right run per line (a Hebrew chat mirrored "+1 DEX"): the main stats, then the bonuses
        stats = "\n".join("\u202a" + " · ".join(part) + "\u202c" for part in (main, bonuses) if part)
        self.stats = QLabel(stats, objectName="TileStats")
        self.stats.setWordWrap(True)
        self.stats.setMinimumWidth(48)
        self.stats.setVisible(bool(stats))
        col.addWidget(self.stats)
        self.vote = vote_tag(t, vote) if vote else None
        if self.vote is not None:
            col.addLayout(chip_row([self.vote]))       # at the reading start, under the name
        row.addLayout(col, 1)
        # an item in a list (a monster's drops, rewards) can go on the wishlist too: the star was only on a full
        # item card, so the drops in an answer couldn't be followed (the owner's report)
        if key.startswith("item/"):
            from PySide6.QtWidgets import QToolButton
            self._t = t
            self.key = key
            self._star = QToolButton(objectName="Icon")
            self._star.setFixedSize(26, 26)
            self._star.setCursor(Qt.PointingHandCursor)
            self._star.clicked.connect(lambda: WISHLIST.toggle(self.key))
            WISHLIST.changed.connect(self._refresh_star)
            self._refresh_star()
            row.addWidget(self._star, 0, Qt.AlignTop)
        self._align_name()

    @Slot()       # a Qt slot: the wishlist's signal lets go of it when the widget is destroyed (else a crash)
    def _refresh_star(self):
        from . import theme
        try:
            on = WISHLIST.has(self.key)
            self._star.setText(theme.ICON["star_on" if on else "star"])
        except RuntimeError:          # the tile was deleted (a cleared chat) while the wishlist changed
            return
        self._star.setProperty("wished", "true" if on else "false")
        self._star.style().unpolish(self._star)
        self._star.style().polish(self._star)
        self._star.setToolTip(self._t("wish_remove" if on else "wish_add"))
        self._star.setAccessibleName(self._t("wish_remove" if on else "wish_add"))

    def _align_name(self):
        """The (English) name sits right beside its picture: on the right in a Hebrew chat. Qt resolves "leading"
        by the text's own direction, so an English name went to the far left, away from its picture."""
        rtl = self.layoutDirection() == Qt.RightToLeft
        for lb in (self.name, getattr(self, "stats", None)):
            if lb is not None:
                lb.setAlignment((Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter)

    def changeEvent(self, e):
        from PySide6.QtCore import QEvent
        super().changeEvent(e)
        if e.type() == QEvent.LayoutDirectionChange:     # follows the chat when the player switches language
            self._align_name()



class TileGrid(QFrame):
    """Two-column grid of item tiles with a credit line."""

    def __init__(self, kb, keys: list[str], title: str = "", rtl: bool | None = None, t=None, srcs=(),
                 monster: str | None = None):
        """monster: the monster whose drops these are; its community drops then show their players' votes."""
        super().__init__(objectName="TileGrid")
        from PySide6.QtWidgets import QApplication, QGridLayout
        from .. import bidi
        if rtl is None:
            rtl = QApplication.layoutDirection() == Qt.RightToLeft
        # its own direction, from the language its title is in (a live switch mirrored it under a Hebrew title)
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 6)
        outer.setSpacing(4)
        # srcs: where the tiles come from (a monster's drops: "MSEA", "community"), one chip beside the title
        chips = source_tags(t, srcs) if t is not None else []
        if title:
            head = QLabel(bidi.plain(title, rtl), objectName="TileGridTitle")
            head.setAlignment((Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter)
            head.setContentsMargins(4, 0, 4, 2)
            if chips:
                outer.addLayout(chip_row(chips, head))
            else:
                outer.addWidget(head)
        elif chips:
            outer.addLayout(chip_row(chips))
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, k in enumerate(keys):
            grid.addWidget(EntityTile(kb, k, t, kb.community_vote(monster, k) if monster else None), i // 2, i % 2)
        outer.addLayout(grid)
        credit = QLabel("NiaMeowDB (meowdb.com)", objectName="CardCredit")
        # the title's chip says where the list is from (community, MSEA); the tiles' stat lines carry their own
        # build ("COT2"), beside the credit as on a card: under a "Community" title they read as players' numbers
        from .. import sources
        builds = [sources.source_of(kb, k) for k in keys
                  if (e := kb.get(k)) and any(stat_parts(e, tile=True)) and sources.stat_source(kb, k)]
        stat_chips = source_tags(t, builds) if t is not None else []
        if stat_chips:
            # "Stats: COT2": alone under the drops it read as the drops' source (the owner's question)
            what = QLabel(bidi.plain(t("tiles_stats_from"), rtl), objectName="CardCredit")
            outer.addLayout(chip_row([what] + stat_chips, credit))
        else:
            outer.addWidget(credit)


class DropGroupCard(QFrame):
    """A monster and the items it drops: header row (picture, name, level) + item tiles."""

    def __init__(self, kb, monster: str, items: list[str], t=None, srcs: dict | None = None):
        super().__init__(objectName="TileGrid")
        from PySide6.QtWidgets import QApplication, QGridLayout
        rtl = QApplication.layoutDirection() == Qt.RightToLeft
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)     # (as TileGrid)
        e = kb.get(monster) or {}
        self.url = e.get("url")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        header = _GroupHeader(monster, e.get("name", monster))
        outer.addWidget(header)
        head = QHBoxLayout(header)
        head.setContentsMargins(4, 2, 4, 2)
        head.setSpacing(10)
        pic = QLabel()
        pic.setFixedSize(40, 40)
        pic.setAlignment(Qt.AlignCenter)
        img = kb.picture(monster)
        if img:
            pm = QPixmap(str(img))
            if not pm.isNull():
                pic.setPixmap(pm.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                zoom_on_hover(pic, img)
        head.addWidget(pic)
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter
        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel(e.get("name", monster), objectName="CardName")
        name.setAlignment(align)
        lv = (e.get("props") or {}).get("Level")
        sub = QLabel(f"Lv. {lv}" if lv else "", objectName="CardSub")
        sub.setAlignment(align)
        col.addWidget(name)
        # every drop says which list it is on (srcs: item -> "MSEA" / "community", kb.drop_group): one chip for
        # the group, or one per list when it mixes both
        from .. import sources
        srcs = srcs or {i: kb.drop_source(monster, i) or sources.MSEA for i in items}
        items = sorted(items, key=lambda i: srcs.get(i) != sources.COMMUNITY)       # players' own sightings first
        chips = source_tags(t, [srcs.get(i) for i in items]) if t is not None else []
        if chips:
            col.addLayout(chip_row(chips, sub))
        else:
            col.addWidget(sub)
        head.addLayout(col, 1)
        grid = QGridLayout()
        grid.setSpacing(6)
        mixed = len(set(srcs.get(i) for i in items)) > 1
        for i, k in enumerate(items):
            # a community drop with its players' votes ("16 ✓", "single report")
            tile = EntityTile(kb, k, t, kb.community_vote(monster, k))
            if mixed and t is not None:
                tile.setToolTip(f"{tile.toolTip()} · {sources.tag(t, srcs.get(k) or sources.MSEA)}")
            grid.addWidget(tile, i // 2, i % 2)
        outer.addLayout(grid)


class _GroupHeader(Selectable, QFrame):
    def __init__(self, key: str, name: str = ""):
        super().__init__(objectName="GroupHeader")
        self._init_selectable(key, name)
