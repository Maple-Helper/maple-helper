"""The scroll planner, the Scrolls page of the play tools (ui/tools.py): pick a piece of gear, pick the stat and
how many of its upgrade slots to use, and every grade of scroll the KB confirms is in the game is laid out side
by side — its per-slot success, the chance all n pass, the expected gain, the exact odds of every outcome, and
the cost per expected +1 (an NPC price from the KB, a Free Market usual price from MeowDB, or the player's own
typed price — never one made up).

Built like the tools window's other pages (the app's own light / dark look: its cards, chips, stepper and the
Item price page's picker, every scrollable equip with its picture), as a plain widget the page holds. The Free
Market lookups run off the GUI thread."""
from __future__ import annotations

import re
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .. import bidi, itemterms, market, scrollplan
from ..i18n import I18n
from ..kb import KnowledgeBase
from ..store import Profiles
from .controls import Stepper
from .tools import EntityPicker
from .widgets import fit_picture, zoom_on_hover

UNKNOWN_CAP = 30                            # the stepper's ceiling for an equip whose slot count the KB lacks
CARD_PAD = 12                               # a card's own margin, each side (its texts are measured inside it)
PIC = 48                                    # the equip's picture on its card, as the Item price card's


def equip_rows(kb: KnowledgeBase) -> tuple[list[tuple[str, str, object]], dict[str, str]]:
    """The picker's rows, every equip the planner has scrolls for (scrollplan.scrollable) once by name with its
    picture, and each row's name -> its KB key. A male and a female version of one name (the same slot, the
    same scrolls) are one row; one name in two slots (Blue Moon, an earring and a top) is told apart by its
    slot, in the KB's own English like the name beside it (one direction in the list)."""
    by_name: dict[str, dict[str, scrollplan.EquipScrolls]] = {}
    for es in sorted(scrollplan.scrollable(kb).values(), key=lambda e: (e.item.lower(), e.key)):
        by_name.setdefault(es.item, {}).setdefault(es.slot, es)
    rows, keys = [], {}
    for name, slots in by_name.items():
        for es in slots.values():
            shown = f"{name} ({es.slot})" if len(slots) > 1 else name
            rows.append((shown, shown, kb.picture(es.key)))
            keys[shown] = es.key
    return rows, keys


class EquipHead(QFrame):
    """The equip in hand's card: its picture at the side (as the Item price card's), its name over its slot and
    clean upgrade slots."""

    def __init__(self):
        super().__init__(objectName="Card")
        row = QHBoxLayout(self)
        row.setContentsMargins(CARD_PAD, 10, CARD_PAD, 10)
        row.setSpacing(12)
        self.pic = QLabel()
        self.pic.setFixedSize(PIC, PIC)
        self.pic.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        row.addWidget(self.pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = QLabel(objectName="CardName", wordWrap=True)
        self.sub = QLabel(objectName="CardSub", wordWrap=True)
        col.addWidget(self.name)
        col.addWidget(self.sub)
        col.addStretch(1)
        row.addLayout(col, 1)

    def set_picture(self, path) -> None:
        """The item's picture, fitted into the card's box and large on hover; none: the box is hidden."""
        pm = QPixmap(str(path)) if path else QPixmap()
        if pm.isNull():
            self.pic.clear()
            self.pic.setToolTip("")
            self.pic.hide()
            return
        self.pic.setPixmap(fit_picture(pm, PIC, PIC, self.pic))
        zoom_on_hover(self.pic, path)
        self.pic.show()


class PickRow(QFrame):
    """One card of a list in the tools window's look: a name over a muted line (its slot, its level);
    clickable=False: a fact in the same look (the equip in hand). The gear advisor's lists use it too."""

    clicked = Signal()

    def __init__(self, name: str, sub: str, tip: str = "", clickable: bool = True):
        super().__init__(objectName="Card")
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
        if tip:
            self.setToolTip(tip)
            self.setAccessibleName(tip)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(CARD_PAD, 8, CARD_PAD, 8)
        lay.setSpacing(1)
        self.name = QLabel(name, objectName="CardName", wordWrap=True)
        lay.addWidget(self.name)
        self.sub = QLabel(sub, objectName="CardSub", wordWrap=True) if sub else None
        if self.sub is not None:
            lay.addWidget(self.sub)
        self._clickable = clickable

    def mouseReleaseEvent(self, e):
        if self._clickable and e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


def _pct(v: float) -> str:
    """1.0 -> '100%', 0.6 ** 7 -> '2.8%', 0.0000001 -> '<0.1%', exactly 0 -> '0.0%': one decimal, never
    pretending to round to 100, never a true zero reading as a near miss."""
    if v >= 0.9995:
        return "100%"
    if v > 0 and v < 0.001:
        return "<0.1%"
    return f"{v * 100:.1f}%"


def _num(v: float) -> str:
    """70.0 -> '70', 4.2 -> '4.2': an expected gain with no trailing zero."""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _mesos(v: float) -> str:
    return f"{round(v):,}"


class ScrollPlanner(QWidget):
    """The page's content. A picked equip shows its clean slots, a stepper for how many to use, a choice of the
    stat to scroll, and one card per grade with the odds, the outcome ladder and the costs. The player's typed
    price per scroll is kept for the window's life, never saved."""

    market_ready = Signal(object)   # a Free Market lookup's answer, from its own thread

    def __init__(self, kb: KnowledgeBase, profiles: Profiles, t: I18n):
        super().__init__()
        self._kb, self._profiles, self.t = kb, profiles, t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._picked: scrollplan.EquipScrolls | None = None    # the player's own pick, kept across switches
        self._default: scrollplan.EquipScrolls | None = None   # what the active character wears, until they pick
        self._stat = 0                     # the chosen stat group, an index into the equip's groups
        self._n = 1                        # the slots to use
        self._your: dict[str, int] = {}    # a typed price per scroll's KB key, this window's life only
        self._fm: dict[str, int | None] = {}   # a Free Market usual price per scroll key (None: none on the site)
        self._fm_bad: set[str] = set()     # scroll keys whose lookup couldn't reach the site at all
        self._fm_seen: set[str] = set()    # scroll keys a thread is already fetching
        self._fm_read = 0                  # bumped on a KB swap: answers from before it are dropped
        self._cards: list[dict] = []
        self._stats: list[QPushButton] = []
        self._measure: dict[tuple[str, int], QLabel] = {}   # hidden labels the card texts' lines are measured with
        self._build()

    # ------------------------------------------------------------ the page

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        # every equip the planner can plan, with its picture, as the Item price page's picker (the owner)
        rows, self._keys = equip_rows(self._kb)
        self._box = EntityPicker(rows, self._placeholder(len(rows)), icon=32, rtl=self.t.rtl)
        self._box.picked.connect(self._picked_name)
        # an emptied box drops the player's pick: the character's worn equip is planned again
        self._box.textChanged.connect(lambda text: None if text.strip() else self._unpick())
        col.addWidget(self._box)
        self._status = QLabel(objectName="RowHint", wordWrap=True)
        self._status.hide()
        col.addWidget(self._status)
        self._plan = QWidget()
        self._plan_lay = QVBoxLayout(self._plan)
        self._plan_lay.setContentsMargins(0, 0, 0, 0)
        self._plan_lay.setSpacing(8)
        col.addWidget(self._plan)
        self._build_plan()
        self.market_ready.connect(self._on_market)
        self._retext()
        self._sync_default()
        self._show_plan()

    def _build_plan(self) -> None:
        """The plan half of the page: the picked equip's header, the stepper and the stat choice, then the
        grade cards (filled in per pick) and the notes."""
        self._head = EquipHead()
        self._plan_lay.addWidget(self._head)
        knobs = QHBoxLayout()
        knobs.setSpacing(8)
        self._use_label = QLabel(objectName="RowLabel")
        self._spin = Stepper(1, UNKNOWN_CAP, 1)
        self._spin.valueChanged.connect(self._n_changed)
        knobs.addWidget(self._use_label)
        knobs.addWidget(self._spin)
        knobs.addStretch(1)
        self._plan_lay.addLayout(knobs)
        self._bonus_label = QLabel(objectName="RowLabel")
        self._stats_lay = QGridLayout()
        self._stats_lay.setHorizontalSpacing(6)
        self._stats_lay.setVerticalSpacing(6)
        self._plan_lay.addLayout(self._stats_lay)
        self._cards_lay = QVBoxLayout()
        self._cards_lay.setSpacing(8)
        self._plan_lay.addLayout(self._cards_lay)
        self._summary = QLabel(objectName="RowLabel", wordWrap=True)
        self._plan_lay.addWidget(self._summary)
        self._chaos = QLabel(objectName="RowHint", wordWrap=True)
        self._plan_lay.addWidget(self._chaos)
        self._source = QLabel(objectName="RowHint", wordWrap=True)
        self._plan_lay.addWidget(self._source)
        self._hint = QLabel(objectName="RowHint", wordWrap=True)
        self._plan_lay.addWidget(self._hint)

    # ------------------------------------------------------------ the page shown, the character, the KB

    def refresh(self) -> None:
        """The page shown (or the character read again): what the character wears is read again (the plan's
        pick until the player picks their own), and the cards' texts are rewritten for the page's width."""
        if self._sync_default() and self._picked is None:
            self._show_plan()
        else:
            self._paint_cards()

    def open_item(self, key: str) -> None:
        """Jump straight to one equip (the gear advisor's 'plan scrolls for this'): picked and shown. A key the
        KB doesn't know as an equip only says so in the status line."""
        es = scrollplan.scrolls_for(self._kb, key)
        if es is None:
            self._status.setText(bidi.plain(self.t("sp_not_equip"), self.t.rtl))
            self._status.show()
            return
        self._pick(es)

    def set_kb(self, kb: KnowledgeBase) -> None:
        """A KB update swapped the KB: everything is read again, the market answers of the old one dropped."""
        self._kb = kb
        self._fm.clear()
        self._fm_bad.clear()
        self._fm_seen.clear()
        self._fm_read += 1
        if self._picked is not None:
            again = scrollplan.scrolls_for(kb, self._picked.key)
            self._picked = again or None
        self._default = None
        self._sync_default()
        rows, self._keys = equip_rows(kb)
        self._box.set_rows(rows)
        self._box.setPlaceholderText(self._placeholder(len(rows)))
        self._show_plan()

    def leave(self) -> None:
        """The page left: the picker's list closes with it (the pick stays, like the other pages' own)."""
        self._box.completer().popup().hide()

    def _sync_default(self) -> bool:
        """The active character's first scrollable worn equip as the plan's default pick. True when it
        changed. The player's own pick always wins over it."""
        ch = self._profiles.active if self._profiles is not None else None
        gear = getattr(ch, "gear", None) or {}
        found = None
        for key in gear.values():
            es = scrollplan.scrolls_for(self._kb, key) if key else None
            if es is not None and es.groups:
                found = es
                break
        was, self._default = self._default, found
        return was is not found and (was is None or found is None or was.key != found.key)

    # ------------------------------------------------------------ the picker

    def _placeholder(self, n: int) -> str:
        return bidi.plain(self.t("sp_placeholder", n=f"{n:,}"), self.t.rtl)

    def _picked_name(self) -> None:
        """A row picked from the list, or Enter on a typed name: its exact name, else the first equip whose name
        holds what was typed (the planner's own search). Nothing found only says so."""
        text = self._box.text().strip()
        if not text:
            return
        key = self._keys.get(text) or next((k for n, k in self._keys.items() if n.lower() == text.lower()), None)
        es = scrollplan.scrolls_for(self._kb, key) if key else None
        if es is None:
            es = next((e for e in scrollplan.find_equips(self._kb, text, 10_000) if e.groups), None)
        if es is None:
            self._status.setText(bidi.plain(self.t("sp_none", q=bidi.ltr_block(text, self.t.rtl)), self.t.rtl))
            self._status.show()
            return
        self._pick(es)

    def _unpick(self) -> None:
        """The box emptied: the player's pick is dropped, the character's worn equip is planned again."""
        self._status.hide()
        if self._picked is None:
            return
        self._picked = None
        self._stat = 0
        self._your.clear()
        self._show_plan()

    def _name_of(self, es: scrollplan.EquipScrolls) -> str:
        """The picker's row name for an equip (its other-gender twin's row, when that one is the one listed)."""
        named = next((n for n, k in self._keys.items() if k == es.key), None)
        if named is None:
            named = next((n for n, k in self._keys.items() if n == es.item or n.startswith(f"{es.item} (")), es.item)
        return named

    def _slots_text(self, es: scrollplan.EquipScrolls) -> str:
        t = self.t
        if es.slots is None:
            return t("sp_slots_none")
        return t("sp_slots_one") if es.slots == 1 else t("sp_slots", n=bidi.ltr_block(f"{es.slots}", t.rtl))

    def _pick(self, es: scrollplan.EquipScrolls) -> None:
        """The equip to plan: picked, the stepper set to its clean slots, the first stat group chosen, the
        typed prices of the last equip dropped. The picker holds its name (also when the gear advisor opened it)."""
        self._picked = es
        self._stat = 0
        self._your.clear()
        name = self._name_of(es)
        if self._box.text() != name:
            self._box.blockSignals(True)       # not an emptied box, not a new pick
            self._box.setText(name)
            self._box.blockSignals(False)
        self._box.setCursorPosition(0)          # a long name shows from its start
        self._show_plan()

    def _show_plan(self) -> None:
        """The plan half drawn for the equip in hand (the player's pick, else the character's worn one)."""
        t, rtl = self.t, self.t.rtl
        es = self._equip()
        self._status.setText("")
        self._status.hide()
        self._head.setVisible(es is not None)
        if es is None:
            self._hint.setText(bidi.plain(t("sp_pick"), rtl))
            self._hint.setVisible(True)
            self._set_cards_visible(False)
            return
        self._hint.setVisible(False)
        self._paint_head()
        if not es.groups:
            self._set_cards_visible(False)
            self._hint.setText(bidi.plain(t("sp_no_scrolls"), rtl))
            self._hint.setVisible(True)
            return
        cap = es.slots if isinstance(es.slots, int) and es.slots > 0 else UNKNOWN_CAP
        self._spin.blockSignals(True)      # the cards are rebuilt below for the new equip, painted once
        self._spin.setMaximum(cap)
        self._spin.setValue(min(cap, es.slots or 1))
        self._spin.blockSignals(False)
        self._n = self._spin.value()
        self._build_stats()
        self._rebuild_cards()

    def _paint_head(self) -> None:
        """The equip in hand's own two lines: its name, and its slot (in the player's language, per
        itemterms' own translations) with its clean upgrade slots, its picture at the side."""
        t, rtl = self.t, self.t.rtl
        es = self._equip()
        self._head.set_picture(self._kb.picture(es.key) if es else None)
        self._head.name.setText(bidi.ltr_name(es.item, rtl) if es else "")
        sub = " · ".join(x for x in ((itemterms.kind(es.slot, t.lang), self._slots_text(es)) if es else ()) if x)
        self._head.sub.setText(bidi.plain(sub, rtl) if sub else "")
        self._head.sub.setVisible(bool(sub))

    def _build_stats(self) -> None:
        """The equip's stats as a wrap of chips, the chosen one lit (they read at a glance, a combo's list
        doesn't), the caption leading them."""
        es = self._equip()
        while (item := self._stats_lay.takeAt(0)) is not None:
            w = item.widget()
            if w is not None and w is not self._bonus_label:     # the caption stays
                w.hide()
                w.deleteLater()
        self._stats_lay.addWidget(self._bonus_label, 0, 0)
        self._stats = []
        for i, g in enumerate((es.groups if es else ()) or ()):
            b = QPushButton(bidi.plain(g.stat, self.t.rtl), objectName="SubChip", checkable=True)
            b.setCursor(Qt.PointingHandCursor)
            b.setAutoDefault(False)          # Enter in a price box never picks a stat
            b.setToolTip(self.t("sp_bonus_tip"))
            b.setAccessibleName(f"{self.t('sp_bonus')}: {g.stat}")
            b.setChecked(i == self._stat)
            b.clicked.connect(lambda _=False, i=i: self._stat_changed(i))
            self._stats_lay.addWidget(b, (i + 1) // 5, (i + 1) % 5)
            self._stats.append(b)
        self._stats_lay.setColumnStretch(5, 1)       # the chips keep their own width, the rest of the row empty
        if self._stats:
            self._stat = min(self._stat, len(self._stats) - 1)

    def _set_cards_visible(self, on: bool) -> None:
        for w in ("_use_label", "_spin", "_bonus_label", "_summary", "_chaos", "_source"):
            getattr(self, w).setVisible(on)
        self._clear_cards()
        self._build_stats()

    # ------------------------------------------------------------ the cards

    def _n_changed(self, n: int) -> None:
        self._n = n
        self._paint_cards()

    def _stat_changed(self, i: int) -> None:
        self._stat = i
        for n, b in enumerate(self._stats):
            b.setChecked(n == i)
        self._rebuild_cards()

    def _equip(self) -> scrollplan.EquipScrolls | None:
        return self._picked or self._default

    def _group(self) -> scrollplan.StatGroup | None:
        es = self._equip()
        if es is None or not es.groups or not 0 <= self._stat < len(es.groups):
            return None
        return es.groups[self._stat]

    def _rebuild_cards(self) -> None:
        """One card per grade of the chosen stat (their texts filled by _paint_cards, so a price that arrives
        or a stepper turn only rewrites them — a typed price is never interrupted)."""
        self._clear_cards()
        group = self._group()
        if group is None:
            return
        self._set_cards_visible(True)
        for s in group.grades:
            card = QFrame(objectName="Card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(CARD_PAD, 10, CARD_PAD, 10)
            lay.setSpacing(3)
            top = QHBoxLayout()
            top.setSpacing(8)
            name = QLabel(objectName="CardName")
            price = QWidget()
            price_lay = QVBoxLayout(price)
            price_lay.setContentsMargins(0, 0, 0, 0)
            price_lay.setSpacing(0)
            top.addWidget(name, 0)
            top.addWidget(price, 1)
            lay.addLayout(top)
            odds = QWidget()
            odds_lay = QVBoxLayout(odds)
            odds_lay.setContentsMargins(0, 0, 0, 0)
            odds_lay.setSpacing(0)
            lay.addWidget(odds)
            ladder = QWidget()
            ladder_lay = QVBoxLayout(ladder)
            ladder_lay.setContentsMargins(0, 0, 0, 0)
            ladder_lay.setSpacing(0)
            lay.addWidget(ladder)
            bottom = QHBoxLayout()
            bottom.setSpacing(8)
            cost = QWidget()
            cost_lay = QVBoxLayout(cost)
            cost_lay.setContentsMargins(0, 0, 0, 0)
            cost_lay.setSpacing(0)
            edit = QLineEdit()
            edit.setFixedWidth(170)
            edit.textChanged.connect(lambda txt, k=s.key: self._your_changed(k, txt))
            bottom.addWidget(cost, 1)
            bottom.addWidget(edit, 0, Qt.AlignBottom)
            lay.addLayout(bottom)
            self._cards_lay.addWidget(card)
            self._cards.append({"scroll": s, "card": card, "name": name, "price": price_lay, "odds": odds_lay,
                                "ladder": ladder_lay, "cost": cost_lay, "edit": edit})
        self._paint_cards()

    def _clear_cards(self) -> None:
        while self._cards:
            c = self._cards.pop()
            c["card"].hide()
            c["card"].deleteLater()
        for c in (self._summary, self._chaos, self._source):
            c.setText("")

    def _your_changed(self, key: str, text: str) -> None:
        m = re.fullmatch(r"\s*([\d,]+)\s*", text or "")
        if m:
            self._your[key] = int(m.group(1).replace(",", ""))
        else:
            self._your.pop(key, None)
        self._paint_cards()

    def _prices(self, group: scrollplan.StatGroup) -> dict[str, int]:
        """The price each grade's cost is figured at: the player's own typed one, else the KB's NPC price,
        else the Free Market's usual price. A grade without any shows its odds with no cost, never a made-up
        number."""
        out = {}
        for s in group.grades:
            if s.key in self._your:
                out[s.key] = self._your[s.key]
            elif isinstance(s.buy, int):
                out[s.key] = s.buy
            elif self._fm.get(s.key) is not None:
                out[s.key] = self._fm[s.key]
        return out

    def _line_fm(self, obj: str):
        """A hidden label of one card text's own look, for measuring its lines: a child of the window the page
        is in, so the window's style sheet (its font size) is the one measured with."""
        win = self.window()
        key = (obj, id(win))
        if key not in self._measure:
            lb = QLabel(win, objectName=obj)
            lb.hide()
            lb.ensurePolished()
            self._measure[key] = lb
        return self._measure[key].fontMetrics()

    @staticmethod
    def _wrap_tokens(tokens: list[str], sep: str, fm, max_w: int) -> list[str]:
        """Tokens (ladder entries, ' · '-separated phrases) as lines no wider than max_w: greedy, a token
        never cut in half, measured here — Qt's own wrapped-label hints lag inside a scroll area and the
        rows drew over each other."""
        out, cur = [], ""
        for tok in tokens:
            cand = cur + sep + tok if cur else tok
            if cur and fm.horizontalAdvance(cand) > max_w:
                out.append(cur)
                cur = tok
            else:
                cur = cand
        return out + [cur] if cur else out

    @staticmethod
    def _set_lines(lay, lines: list[str], obj: str, right: bool = False) -> None:
        """A card's text as exactly as many single-line labels as it has lines — never a wrapping label:
        one line per label, and Qt's layout always knows the height it needs. A line never widens the page
        (its width is the page's to give): a page laid out before it was shown measured too wide a card once."""
        while lay.count() > len(lines):
            w = lay.takeAt(lay.count() - 1).widget()
            w.hide()
            w.deleteLater()
        while lay.count() < len(lines):
            lb = QLabel(objectName=obj)
            lb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lb.setAlignment((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
            lay.addWidget(lb)
        for i, text in enumerate(lines):
            lay.itemAt(i).widget().setText(text)

    def _paint_cards(self) -> None:
        """Every text of the grade cards, the comparison line and the notes, from the plan's own math."""
        t, rtl = self.t, self.t.rtl
        group = self._group()
        if group is None or not self._cards:
            return
        got = scrollplan.plans(group, self._n, self._prices(group))
        inner = max(200, self.width() - 2 * CARD_PAD - 2)     # a card's own width, its margins and rim aside
        for card, gp in zip(self._cards, got):
            s = gp.scroll
            name = bidi.plain(f"{s.grade} · {bidi.ltr_block(f'{s.success}%', rtl)}", rtl)
            card["name"].setText(name)
            price_w = max(120, inner - self._line_fm("CardName").horizontalAdvance(name) - 8)
            key, pieces = self._price_bits(s)
            if pieces:
                lines = self._wrap_tokens(pieces, " · ", self._line_fm("CardSub"), price_w)
                texts = [bidi.plain(t(key, p=bidi.ltr_block(lines[0], rtl)), rtl)]      # the key opens the
                texts += [bidi.plain(bidi.ltr_block(ln, rtl), rtl) for ln in lines[1:]]  # price, not each line
            else:
                texts = [bidi.plain(t(key), rtl)]
            self._set_lines(card["price"], texts, "CardSub", right=not rtl)
            # each part is laid out whole, then blocked whole: "0.6 ** 7" showed as "2.8" next to a stray "%",
            # and "+70 Max HP" as "Max HP +70", while the parts floated apart in the Hebrew lines
            exp = " · ".join(f"+{_num(v)} {label}" for label, v in gp.expected)
            parts = [bidi.plain(t("sp_all_pass", n=bidi.ltr_block(str(self._n), rtl),
                                  p=bidi.ltr_block(_pct(gp.plan.all_pass), rtl)), rtl),
                     bidi.plain(t("sp_expected", v=bidi.ltr_block(exp, rtl) if exp else "—"), rtl)]
            self._set_lines(card["odds"], self._wrap_tokens(parts, " · ", self._line_fm("CardStat"), inner),
                            "CardStat")
            gain = s.gains[0][1] if s.gains else 0
            entries = [f"+{k * gain}: {_pct(pr)}" for k, pr in enumerate(gp.plan.dist) if pr > 0]
            word = bidi.plain(t("sp_ladder"), rtl)
            head = f"({group.primary}):" if entries else ""
            budget = inner - (self._line_fm("CardSub").horizontalAdvance(word) + 6 if rtl and head else 0)
            # the head leads its first entry (never a ' · ' between them: "(Max HP): · +70: 100%")
            first = [f"{head} {entries[0]}"] if head and entries else ([head] if head else [])
            lines = self._wrap_tokens(first + entries[1:], " · ", self._line_fm("CardSub"), budget)
            texts = ([f"{word} {bidi.ltr_block(lines[0], rtl)}"] if lines else []) \
                    + [bidi.ltr_block(ln, rtl) for ln in lines[1:]]
            self._set_lines(card["ladder"], [bidi.plain(x, rtl) for x in texts], "CardSub")
            cost = (bidi.plain(t("sp_expected_cost", p=bidi.ltr_block(
                f"{_mesos(gp.expected_cost)} mesos", rtl)), rtl)
                    if gp.expected_cost is not None else bidi.plain(t("sp_price_none"), rtl))
            self._set_lines(card["cost"], [cost], "CardStat")
            if s.key in self._your:
                card["edit"].setPlaceholderText("")
                if card["edit"].text() != f"{self._your[s.key]:,}":
                    card["edit"].setText(f"{self._your[s.key]:,}")
            else:
                card["edit"].setPlaceholderText(t("sp_your_price"))
        bits = []
        for gp in got:
            if gp.per_point is not None:
                bits.append(bidi.ltr_block(f"{gp.scroll.grade} {_mesos(gp.per_point)}", rtl))
            elif rtl:
                bits.append(f"{t('sp_no_price_short')} {bidi.ltr_block(gp.scroll.grade, rtl)}")
            else:
                bits.append(f"{gp.scroll.grade} {t('sp_no_price_short')}")
        self._summary.setText(bidi.plain(
            t("sp_per_point", point=bidi.ltr_block(f"+1 {group.stat}", rtl), list=" · ".join(bits)), rtl))
        self._chaos.setText(bidi.plain(t("sp_chaos_note"), rtl))
        self._source.setText(bidi.plain(t("sp_source_note"), rtl))
        self._queue_fm(group)

    def _price_bits(self, s: scrollplan.Scroll) -> tuple[str, list[str]]:
        """(the line's own key, its ' · '-separated pieces): the pieces are wrapped into lines first and each
        wrapped line becomes one left-to-right block, whole — pieced-out numbers scrambled in the Hebrew
        lines ("mesos 15,000 :NPC-מה", seen in the PNG review)."""
        if s.key in self._your:
            return "sp_your_price", [f"{self._your[s.key]:,}"]
        if isinstance(s.buy, int):
            return "sp_price_npc", [f"{s.buy:,} mesos", s.seller]
        if s.key in self._fm:
            if self._fm[s.key] is not None:
                return "sp_price_fm", [f"{self._fm[s.key]:,} mesos"]
            return "sp_price_none", []
        if s.key in self._fm_bad:
            return "sp_price_offline", []
        return "sp_price_loading", []

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._paint_cards()           # measured again in the window's own look, at the page's width

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            self._paint_cards()       # the lines broken again for the new width

    # ------------------------------------------------------------ the Free Market prices

    def _queue_fm(self, group: scrollplan.StatGroup) -> None:
        """The usual Free Market price for every grade neither the KB's NPC price nor an earlier fetch covers,
        fetched off the GUI thread (each item is up to 3 requests of 8 s on a slow line)."""
        keys = [s.key for s in group.grades if not isinstance(s.buy, int) and s.key not in self._fm_seen]
        if not keys:
            return
        self._fm_seen.update(keys)
        read = self._fm_read            # only a KB swap starts a new round: a new pick never drops the
                                        # answers of keys still in _fm_seen (they'd stay "loading" for good)

        def look():
            for key in keys:
                slug = key.partition("/")[2]
                usual, offline = None, False
                if slug.isdigit():
                    try:
                        m = market.item_market(int(slug))
                    except Exception:      # noqa: BLE001 - an odd answer shows "no price", not a crash
                        m = None
                    if m is None:
                        offline = True
                    else:
                        usual = m.usual
                try:
                    self.market_ready.emit((read, key, usual, offline))
                except RuntimeError:
                    return               # the window was deleted meanwhile: nothing to show it in
        threading.Thread(target=look, daemon=True).start()

    def _on_market(self, r) -> None:
        """A lookup's answer, back on the GUI thread: remembered (also for the other grades' cards) and the
        texts rewritten. An answer from before the last KB swap is dropped."""
        read, key, usual, offline = r
        if read != self._fm_read:
            return
        if offline:
            self._fm_bad.add(key)
        else:
            self._fm[key] = usual
        if self.isVisible():          # a page not shown is painted when it shows (showEvent)
            self._paint_cards()

    # ------------------------------------------------------------ text

    def _retext(self) -> None:
        t, rtl = self.t, self.t.rtl
        self._use_label.setText(bidi.plain(t("sp_use"), rtl))
        self._use_label.setToolTip(t("sp_use_tip"))
        self._spin.set_label(t("sp_use"))
        self._spin.setToolTip(t("sp_use_tip"))
        self._bonus_label.setText(bidi.plain(t("sp_bonus"), rtl))
        self._bonus_label.setToolTip(t("sp_bonus_tip"))
        self._hint.setText(bidi.plain(t("sp_pick"), rtl))
