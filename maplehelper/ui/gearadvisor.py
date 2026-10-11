"""The gear upgrade advisor, the Gear page of the play tools (ui/tools.py): what the active character wears per
slot, and what the KB says beats it — wearable now, or a few levels away — with the stat deltas that rank each
row, the NPC price and seller, or the monsters that drop it, and a "Plan scrolls" hand-off to the Scrolls page.
Every number comes from the KB's tables via gearadvice.py; nothing is guessed here. Built like the tools
window's other pages (the app's own light / dark look: its cards, chips and boxes), as a plain widget the page
holds."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import bidi, gearadvice, itemterms
from ..gearadvice import STAT_KEYS, SlotPlan, Upgrade, delta_text
from ..i18n import I18n
from ..kb import KnowledgeBase
from ..store import Character, Profiles
from .scrollplanner import CARD_PAD, PickRow

TYPE_DEBOUNCE_MS = 150              # a pause in the picker's typing before its rows are read again
PICK_ROWS = 8                       # rows the picker lists before the "and N more" line
STAT_DEBOUNCE_MS = 350              # a pause in a stat box before the character is saved and the advice re-read
GENDERS = ("", "Male", "Female")    # what a player can say about their character ("" = not said)


def _sex_word(t: I18n, sex: str) -> str:
    """"Male"/"Female" as the player says it: the game's English word, Hebrew's זכר/נקבה."""
    return t("ga_gender_female") if sex == "Female" else t("ga_gender_male") if sex == "Male" else sex


def _slot_word(t: I18n, slot: str) -> str:
    """A slot's name in the player's language (itemterms' own translations: Claw -> טופר)."""
    return itemterms.kind(slot, t.lang)


def _match(name: str, query: str) -> bool:
    """A picker row for this query: every word of it in the item's name, case aside (the searches' own rule)."""
    words = [w for w in re.split(r"\s+", query.strip().lower()) if w]
    n = str(name).lower()
    return not words or all(w in n for w in words)


def _button(text: str, obj: str, tip: str = "") -> QPushButton:
    """A push button of the page: never the dialog's default (Enter in a stat box clicked the first one)."""
    b = QPushButton(text, objectName=obj)
    b.setCursor(Qt.PointingHandCursor)
    b.setAutoDefault(False)
    if tip:
        b.setToolTip(tip)
        b.setAccessibleName(tip)
    return b


class _UpRow(QFrame):
    """One advised item: its name with a Plan scrolls button beside it, under it the level and the deltas that
    rank it (plus what to flag: a stat requirement not checked, a gendered item), then where to get it — the
    NPC's price, or the droppers the drops table names, or the KB has neither."""

    def __init__(self, t: I18n, u: Upgrade, on_plan):
        super().__init__(objectName="Card")
        rtl = t.rtl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(CARD_PAD, 8, CARD_PAD, 8)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        name = QLabel(bidi.ltr_name(str(u.row.get("item") or ""), rtl), objectName="CardName", wordWrap=True)
        top.addWidget(name, 1)
        if isinstance(u.row.get("slots"), int) and u.row["slots"] > 0:
            plan = _button(bidi.plain(t("ga_plan_scrolls"), rtl), "NowChip", t("ga_plan_tip"))
            plan.clicked.connect(lambda _=False, k=str(u.row.get("key")): on_plan(k))
            top.addWidget(plan, 0, Qt.AlignTop)
        lay.addLayout(top)
        req = u.row.get("req_lv")
        lv = t("lv_short", n=req)                                      # the app's own "Lv. 30" / "רמה 30"
        bits = [t("ga_soon_at", n=req) if u.soon else lv]
        if u.deltas:
            bits.append(bidi.ltr_block(delta_text(u.deltas), rtl))
        for flag in u.flags:
            if flag == "gender":
                bits.append(bidi.plain(t("ga_gender_only", g=_sex_word(t, str(u.row.get("gender") or ""))), rtl))
            elif flag.startswith("stat:"):
                _, st, need = flag.split(":", 2)
                bits.append(bidi.plain(t("ga_needs", stat=bidi.ltr_block(
                    f"{gearadvice.LABELS.get(st, st.upper())} {need}", rtl)), rtl))
        sub = QLabel(bidi.plain(" · ".join(bits), rtl), objectName="CardStat", wordWrap=True)
        lay.addWidget(sub)
        if u.buy is not None:                                       # the whole price line one Latin block
            where = t("ga_buy", price=bidi.ltr_block(f"{u.buy:,} mesos · {u.seller}", rtl))
        elif u.drops:
            where = t("ga_drops", list=" · ".join(f"{bidi.ltr_name(m, rtl)} ({t('lv_short', n=lv)})"
                                                  for m, lv in u.drops))
        else:
            where = t("ga_no_price")
        src = QLabel(bidi.plain(where, rtl), objectName="CardSub", wordWrap=True)
        lay.addWidget(src)


class _SlotHead(QFrame):
    """A slot's title row: the slot's name (clicking the row folds the slot's list), the folded badge of how
    many upgrades wait in it, and on the end the button naming what is worn (clicking it picks something else)."""

    fold_clicked = Signal(str)

    def __init__(self, slot: str):
        super().__init__(objectName="GroupHeader")
        self.setCursor(Qt.PointingHandCursor)
        self._slot = slot
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(8, 4, 8, 4)
        self.lay.setSpacing(6)
        self.lay.addWidget(QLabel(slot, objectName="CardName"))
        self.badge = QLabel(objectName="CardSub")
        self.lay.addWidget(self.badge, 1)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.fold_clicked.emit(self._slot)
        super().mouseReleaseEvent(e)


class GearAdvisor(QWidget):
    """The page's content. Clicking a slot's row folds it; the worn button opens that slot's picker; a chosen
    item is saved to the character's gear and the advice follows at once. The stat boxes are saved to the
    character after a pause in the typing (and at once when the page is left)."""

    scroll_requested = Signal(str)  # an item's KB key: "plan scrolls for this" (the Scrolls page opens on it)

    def __init__(self, kb: KnowledgeBase, profiles: Profiles, t: I18n):
        super().__init__()
        self._kb = kb
        self._profiles = profiles
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._c: Character | None = None     # the active character, re-read on every show / change
        self._plans: list[SlotPlan] = []
        self._sections: dict[str, QWidget] = {}
        self._folded: set[str] | None = None  # the slots the player folded (first open: the empty ones)
        self._pick_slot: str | None = None    # the slot whose picker is open
        self._pick_widget: QWidget | None = None
        self._pick_rows: list[QWidget] = []
        self._badge_of: dict[str, QLabel] = {}      # a folded slot's head: its upgrade count
        self._body_of: dict[str, QWidget] = {}      # each slot's rows, hidden when the slot is folded
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._fill_picker)
        self._stats_typing = QTimer(self, singleShot=True, interval=STAT_DEBOUNCE_MS, timeout=self._stats_saved)
        self._build()

    # ------------------------------------------------------------ the page

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self._char = QLabel(objectName="CardName", wordWrap=True)
        col.addWidget(self._char)
        self._stats = QWidget()
        srow = QHBoxLayout(self._stats)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(8)
        self._editors: dict[str, QLineEdit] = {}
        for st in STAT_KEYS:
            pair = QWidget()
            prow = QHBoxLayout(pair)
            prow.setContentsMargins(0, 0, 0, 0)
            prow.setSpacing(4)
            prow.addWidget(QLabel(gearadvice.LABELS[st], objectName="FieldLabel"))    # still readable once filled
            box = QLineEdit(maxLength=3)
            box.setMinimumWidth(48)                    # three digits ("120") whole; the gender box keeps its word
            box.setMaximumWidth(64)
            box.setAlignment(Qt.AlignCenter)
            box.textChanged.connect(lambda _t: self._stat_typed())
            prow.addWidget(box, 1)
            srow.addWidget(pair, 1)
            self._editors[st] = box
        self._gender = QComboBox()
        self._gender.setCursor(Qt.PointingHandCursor)
        self._gender.addItems(list(GENDERS))
        self._gender.currentIndexChanged.connect(lambda _i: self._gender_picked())
        self._gender.setSizeAdjustPolicy(QComboBox.AdjustToContents)     # "Female" whole, never clipped
        srow.addWidget(self._gender)
        col.addWidget(self._stats)
        self._hint = QLabel(objectName="RowHint", wordWrap=True)
        col.addWidget(self._hint)
        rows = QWidget()
        self._rows_lay = QVBoxLayout(rows)
        self._rows_lay.setContentsMargins(0, 4, 0, 0)
        self._rows_lay.setSpacing(6)
        col.addWidget(rows)
        self._rebuild()

    # ------------------------------------------------------------ the page shown, the character, the KB

    def refresh(self) -> None:
        """The page shown or the character read again: everything from the active character (stats still being
        typed for that same character are saved first, never reset under the player's fingers)."""
        if self._stats_typing.isActive() and self._c is not None and self._c is self._profiles.active:
            self._stats_typing.stop()
            self._stats_saved()            # (it rebuilds)
            return
        self._rebuild()

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._folded = None            # the slots themselves may all have moved
        self._rebuild()

    def leave(self) -> None:
        """The page left (or the window closed): stats typed just before are saved now, never lost, and nothing
        fires behind the player's back."""
        if self._stats_typing.isActive():
            self._stats_typing.stop()
            self._stats_saved()
        self._typing.stop()
        self._close_picker()

    def hideEvent(self, e) -> None:
        if self._stats_typing.isActive():      # hidden mid-typing (the window stepped aside, closed): saved now
            self._stats_typing.stop()
            self._stats_saved()
        self._typing.stop()
        super().hideEvent(e)

    # ------------------------------------------------------------ text (all of it: a rebuild)

    def _retext_chrome(self) -> None:
        t = self.t
        for st, box in self._editors.items():
            box.setPlaceholderText("—")                    # its label beside it names it; "—" not typed yet
            box.setAccessibleName(gearadvice.LABELS[st])
        self._gender.setToolTip(t("ga_gender_tip"))
        self._gender.setAccessibleName(t("ga_gender"))
        self._gender.blockSignals(True)                       # its choices re-worded, its pick kept
        idx = self._gender.currentIndex()
        self._gender.clear()
        self._gender.addItem("—")
        self._gender.addItems([_sex_word(t, sex) for sex in GENDERS[1:]])
        self._gender.setCurrentIndex(idx)
        self._gender.blockSignals(False)

    def _page_bar(self):
        """The tools page's own scrollbar (the page scrolls, not this widget)."""
        w = self.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        return w.verticalScrollBar() if w is not None else None

    def _rebuild(self) -> None:
        """Every row again, from the active character: the character line, the stat boxes, then a section per
        equip slot (gearadvice.upgrades)."""
        t, rtl = self.t, self.t.rtl
        self._close_picker()            # the picker is part of the rows; the rows are new
        self._c = self._profiles.active
        self._retext_chrome()
        bar = self._page_bar()
        was = bar.value() if bar is not None else 0
        self._clear_rows()
        if self._c is None:
            self._char.setText(bidi.plain(t("ga_no_character"), rtl))
            self._stats.setVisible(False)
            self._hint.setText("")
            self._hint.setVisible(False)
            return
        c = self._c
        self._char.setText(bidi.plain(f"{bidi.ltr_name(f'{c.name} · {c.job}', rtl)} · {t('lv_short', n=c.level)}",
                                      rtl))
        self._stats.setVisible(True)
        for st, box in self._editors.items():
            box.blockSignals(True)
            box.setText(str(c.ap.get(st, "")) if c.ap.get(st) is not None else "")
            box.blockSignals(False)
        self._gender.blockSignals(True)
        self._gender.setCurrentIndex(GENDERS.index(c.gender) if c.gender in GENDERS else 0)
        self._gender.blockSignals(False)
        hint = bidi.plain(t("ga_stats_hint"), rtl) if not any(c.ap.get(st) for st in STAT_KEYS) else ""
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))
        self._plans = gearadvice.upgrades(self._kb, c)
        if self._folded is None:        # the first draw: the empty slots fold, the rest open
            self._folded = {p.slot for p in self._plans if not p.now and not p.soon and not p.worn}
        for plan in self._plans:
            self._add_section(plan)
        if bar is not None:
            bar.setValue(was)

    def _clear_rows(self) -> None:
        self._sections = {}
        self._badge_of = {}
        self._body_of = {}
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    # ------------------------------------------------------------ a slot's section

    def _add_section(self, plan: SlotPlan) -> None:
        t, rtl = self.t, self.t.rtl
        section = QWidget()
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = _SlotHead(bidi.plain(_slot_word(t, plan.slot), rtl))    # Claw -> טופר, per itemterms
        head.fold_clicked.connect(lambda s=plan.slot: self._fold_clicked(s))
        self._badge_of[plan.slot] = head.badge
        # what is worn, by name; with nothing set the button says what it does ("nothing" read as a dead label)
        worn = _button(bidi.ltr_name(str(plan.worn.get("item") or ""), rtl) if plan.worn
                       else bidi.plain(t("ga_set_worn"), rtl), "Chip" if plan.worn else "SubChip", t("ga_worn_tip"))
        worn.setFocusPolicy(Qt.NoFocus)
        worn.clicked.connect(lambda _=False, s=plan.slot: self._worn_clicked(s))
        head.lay.addWidget(worn)
        lay.addWidget(head)
        body = QWidget()
        blay = QVBoxLayout(body)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(6)
        if plan.slot == "Overall":
            blay.addWidget(QLabel(bidi.plain(t("ga_overall_note"), rtl), objectName="RowHint", wordWrap=True))
        if plan.worn_gone:
            blay.addWidget(QLabel(bidi.plain(t("ga_worn_gone"), rtl), objectName="RowHint", wordWrap=True))
        for u in plan.now:
            blay.addWidget(_UpRow(t, u, self.scroll_requested.emit))
        for u in plan.soon:
            blay.addWidget(_UpRow(t, u, self.scroll_requested.emit))
        if not plan.now and not plan.soon and not plan.worn_gone:
            blay.addWidget(QLabel(bidi.plain(t("ga_none_better"), rtl), objectName="RowHint", wordWrap=True))
        lay.addWidget(body)
        self._body_of[plan.slot] = body
        self._apply_fold(plan)
        self._sections[plan.slot] = section
        self._rows_lay.addWidget(section)

    def _apply_fold(self, plan: SlotPlan) -> None:
        t = self.t
        folded = plan.slot in (self._folded or set())
        self._body_of[plan.slot].setVisible(not folded)
        self._badge_of[plan.slot].setText("" if not folded or not plan.now else
                                          bidi.plain(t("ga_upgrades", n=bidi.ltr_block(str(len(plan.now)), t.rtl)), t.rtl))

    def _fold_clicked(self, slot: str) -> None:
        self._folded = (self._folded or set()) ^ {slot}
        self._apply_fold(next(p for p in self._plans if p.slot == slot))

    def _worn_clicked(self, slot: str) -> None:
        """The worn button: that slot's picker opens (the same slot again: closed)."""
        if self._pick_slot == slot:
            self._close_picker()
            return
        self._close_picker()
        self._pick_slot = slot
        t = self.t
        pick = QWidget()
        lay = QVBoxLayout(pick)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._pick_box = QLineEdit()
        self._pick_box.setClearButtonEnabled(True)
        self._pick_box.setPlaceholderText(bidi.plain(t("ga_pick_ph"), t.rtl))
        self._pick_box.textChanged.connect(lambda _t: self._typing.start())
        lay.addWidget(self._pick_box)
        # the rows straight in the page (never a list scrolling inside the scrolling page): a few, then "and N more"
        self._pick_rows_lay = QVBoxLayout()
        self._pick_rows_lay.setSpacing(6)
        lay.addLayout(self._pick_rows_lay)
        self._pick_more = QLabel(objectName="RowHint", wordWrap=True)
        lay.addWidget(self._pick_more)
        self._pick_widget = pick
        section = self._sections[slot]
        section.layout().insertWidget(1, pick)
        self._fill_picker()
        self._pick_box.setFocus()

    def _fill_picker(self) -> None:
        """The picker's rows for what is typed: the slot's items (the character's class's, any level), and
        nothing at the top to take off what is worn."""
        if self._pick_slot is None:
            return
        t, rtl = self.t, self.t.rtl
        q = self._pick_box.text()
        while self._pick_rows:
            w = self._pick_rows.pop()
            self._pick_rows_lay.removeWidget(w)
            w.hide()
            w.deleteLater()
        plan = next((p for p in self._plans if p.slot == self._pick_slot), None)
        if plan is None:
            return
        none_row = PickRow(bidi.plain(t("ga_nothing"), rtl), "", t("ga_worn_tip"))
        none_row.clicked.connect(lambda: self._worn_set(self._pick_slot, None))
        self._pick_rows.append(none_row)
        self._pick_rows_lay.addWidget(none_row)
        rows = [r for r in plan.picker if _match(r.get("item") or "", q)]
        for r in rows[:PICK_ROWS]:
            bits = [t("lv_short", n=r.get("req_lv") or 0)]
            bits += [bidi.ltr_block(" ".join(
                x for x in (gearadvice.LABELS[st], str(r[f"req_{st}"])) if x), rtl)
                for st in STAT_KEYS if isinstance(r.get(f"req_{st}"), int) and r[f"req_{st}"]]
            if r.get("gender"):
                bits.append(bidi.plain(t("ga_gender_only", g=_sex_word(t, str(r["gender"]))), rtl))
            row = PickRow(bidi.ltr_name(str(r.get("item") or ""), rtl), bidi.plain(" · ".join(bits), rtl))
            row.clicked.connect(lambda k=str(r.get("key")): self._worn_set(self._pick_slot, k))
            self._pick_rows.append(row)
            self._pick_rows_lay.addWidget(row)
        left = len(rows) - min(len(rows), PICK_ROWS)
        self._pick_more.setText(bidi.plain(t("ga_more", n=bidi.ltr_block(str(left), rtl)), rtl) if left else "")
        if not rows:
            self._pick_more.setText(bidi.plain(t("ga_pick_none"), rtl))
        self._pick_more.setVisible(bool(self._pick_more.text()))

    def _close_picker(self) -> None:
        if self._pick_slot is None:
            return
        pick = self._pick_widget
        if pick is not None:
            pick.setParent(None)
            pick.hide()
            pick.deleteLater()
            self._pick_widget = None
        self._typing.stop()
        self._pick_rows = []
        self._pick_slot = None

    def _worn_set(self, slot: str | None, key: str | None) -> None:
        """A picker row clicked: what is worn in this slot is saved to the character (nothing: taken off) and
        the advice follows at once."""
        c = self._profiles.active
        if c is None or slot is None:
            return
        if key:
            c.gear[slot] = key
        else:
            c.gear.pop(slot, None)
        self._profiles.save()
        self._rebuild()

    # ------------------------------------------------------------ the player's own numbers

    def _stat_typed(self) -> None:
        """A keystroke in a stat box: the saving waits for the pause (the advice is re-read whole)."""
        self._stats_typing.start()

    def _stats_saved(self) -> None:
        """The pause held: every stat box saved to the character (empty: not said) and the advice re-read."""
        c = self._profiles.active
        if c is None:
            return
        for st, box in self._editors.items():
            v = box.text().strip()
            if v.isdigit() and int(v) > 0:
                c.ap[st] = int(v)
            else:
                c.ap.pop(st, None)
        self._profiles.save()
        self._rebuild()

    def _gender_picked(self) -> None:
        c = self._profiles.active
        if c is None:
            return
        c.gender = GENDERS[self._gender.currentIndex()]
        self._profiles.save()
        self._rebuild()
