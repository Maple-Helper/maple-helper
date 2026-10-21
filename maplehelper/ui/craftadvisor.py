"""The craft plan, the second tab of the Crafting page of the play tools (ui/tools.py): pick what to craft (every
recipe product, the class's equips first), say what is in the bag and where each profession stands (level and
Mastery bar %), and the forge plan follows — what to farm first and where it drops, then the crafts in order:
materials, the profession level-ups the plan itself needs (the cheapest recipe per EXP, the player's own
measured EXP per level winning over the KB's table), the final items — every number from the KB's tables or
the player's own input (craftplan.py). Marking a step crafted moves its mats and products into the bag, like
the site this is modelled on (maplestorycrafting.com/planner.html).

The Crafting page's first tab already lists each profession's recipes by level and its teacher; this one is
only the plan. Built like the tools window's other pages (the app's own light / dark look: its cards, chips,
stepper and boxes), as a plain widget the page holds. Nothing here touches the network."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import bidi, craftplan
from ..craftplan import CATEGORY_ORDER, DEFAULT_MODE, DISCIPLINE_KEY, MODES, FarmItem, Plan, Row, Stage, Step
from ..i18n import I18n
from ..kb import KnowledgeBase
from ..store import Character, Profiles, Settings
from .controls import Segmented, Stepper

CARD_PAD = 12                   # a card's own margin, each side (as the tools window's other cards)

TYPE_DEBOUNCE_MS = 150      # a pause in the search before its rows are read again
EDIT_DEBOUNCE_MS = 350      # a pause in a number box before the character is saved and the plan re-read
ITEM_ROWS = 12              # item rows listed before the "and N more" line
MAX_MAKE = 50               # the Make steppers' ceiling
# the sections, top to bottom; the ones after "items" are rebuilt together on every plan change
SECTIONS = ("items", "mode", "plan", "bag", "exp")
CAT_KEYS = {"ore": "cp_cat_ore", "gem": "cp_cat_gem", "material": "cp_cat_material", "drop": "cp_cat_drop",
            "other": "cp_cat_other"}
MODE_KEYS = {"highest": "cp_mode_highest", "cheapest": "cp_mode_cheapest", "least": "cp_mode_least"}

log = logging.getLogger("maplehelper")


def _match(name: str, query: str) -> bool:
    """A row for this query: every word of it in the item's name, case aside (the searches' own rule)."""
    words = [w for w in query.lower().split() if w]
    n = str(name).lower()
    return not words or all(w in n for w in words)


def _mesos(v: int) -> str:
    return f"{int(v):,}"


def _button(text: str, obj: str, tip: str = "") -> QPushButton:
    """A push button of the page: never the dialog's default (Enter in a number box clicked the first one)."""
    b = QPushButton(text, objectName=obj)
    b.setCursor(Qt.PointingHandCursor)
    b.setAutoDefault(False)
    if tip:
        b.setToolTip(tip)
        b.setAccessibleName(tip)
    return b


def _num_box(width: int, length: int) -> QLineEdit:
    box = QLineEdit(maxLength=length)
    box.setFixedWidth(width)
    box.setAlignment(Qt.AlignCenter)
    return box


def _card() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame(objectName="Card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(CARD_PAD, 8, CARD_PAD, 8)
    lay.setSpacing(2)
    return card, lay


def _label(text: str, obj: str, wrap: bool = True) -> QLabel:
    return QLabel(text, objectName=obj, wordWrap=wrap)


class _SectionHead(QFrame):
    """A section's title row: its name, a muted badge, and a fold mark on the end; clicking the row folds it."""

    fold_clicked = Signal()

    def __init__(self, label: str):
        super().__init__(objectName="GroupHeader")
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        row.addWidget(QLabel(label, objectName="CardName"))
        self.badge = QLabel(objectName="CardSub")
        row.addWidget(self.badge, 1)
        self.mark = QLabel(objectName="CardSub")
        row.addWidget(self.mark)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.fold_clicked.emit()
        super().mouseReleaseEvent(e)


class CraftAdvisor(QWidget):
    """The tab's content: the character's professions on top (each one's level and Mastery bar %), then the
    sections — what to craft (search finds every recipe), the recipe mode, the forge plan, the bag, the EXP
    table — every one foldable. The numbers typed are saved to the character (the EXP table to the settings)
    after a pause in the typing, and at once when the page is left."""

    def __init__(self, kb: KnowledgeBase, settings: Settings, profiles: Profiles, t: I18n):
        super().__init__()
        self._kb = kb
        self._settings = settings
        self._profiles = profiles
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._c: Character | None = None      # the active character, re-read on every show / change
        self._cid: str | None = None          # whose swaps _choices are
        self._rows: tuple[Row, ...] = ()
        self._plan: Plan | None = None
        self._mode = DEFAULT_MODE
        self._choices: dict[str, int] = {}    # product name casefold -> the recipe number the player swapped to
        self._folded: set[str] = {"exp"}     # the folded sections (the EXP table, rarely typed in, starts folded)
        self._section_widgets: dict[str, list[QWidget]] = {}
        self._badges: dict[str, QLabel] = {}
        self._bag_boxes: dict[str, QLineEdit] = {}
        self._exp_boxes: dict[int, QLineEdit] = {}
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._fill_items)
        self._edit = QTimer(self, singleShot=True, interval=EDIT_DEBOUNCE_MS, timeout=self._edits_saved)
        self._build()

    # ------------------------------------------------------------ the page

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self._char = _label("", "CardName")
        col.addWidget(self._char)
        self._strip = QWidget()
        grid = QGridLayout(self._strip)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self._prof_boxes: dict[str, tuple[QLineEdit, QLineEdit]] = {}
        # two professions a row: their name (the game's own word), the level box, a % mark, the bar box
        for i, disc in enumerate(sorted(DISCIPLINE_KEY, key=lambda d: DISCIPLINE_KEY[d])):
            lv, pct = _num_box(36, 2), _num_box(44, 3)
            for box in (lv, pct):
                box.textChanged.connect(lambda _t: self._edit.start())
            pair = QWidget()
            prow = QHBoxLayout(pair)
            prow.setContentsMargins(0, 0, 0, 0)
            prow.setSpacing(4)
            prow.addWidget(QLabel(disc, objectName="FieldLabel"), 1)
            prow.addWidget(lv)
            prow.addWidget(pct)
            prow.addWidget(QLabel("%", objectName="FieldLabel"))
            grid.addWidget(pair, i // 2, i % 2)
            self._prof_boxes[disc] = (lv, pct)
        col.addWidget(self._strip)
        self._strip_hint = _label("", "RowHint")
        col.addWidget(self._strip_hint)
        rows = QWidget()
        self._rows_lay = QVBoxLayout(rows)
        self._rows_lay.setContentsMargins(0, 4, 0, 0)
        self._rows_lay.setSpacing(6)
        col.addWidget(rows)

    # ------------------------------------------------------------ the page shown, the character, the KB

    def refresh(self) -> None:
        """The tab shown or the character read again: everything from the active character (numbers still
        being typed for that same character are saved first, never reset under the player's fingers)."""
        if self._edit.isActive() and self._c is not None and self._c is self._profiles.active:
            self._edit.stop()
            self._edits_saved()
        self._rebuild()

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._choices = {}
        self._rebuild()

    def leave(self) -> None:
        """The page left (or the window closed): numbers typed just before are saved now, never lost, and
        nothing fires behind the player's back."""
        if self._edit.isActive():
            self._edit.stop()
            self._edits_saved()
        self._typing.stop()

    def hideEvent(self, e) -> None:
        if self._edit.isActive():          # hidden mid-typing (another tab, the window closed): saved now
            self._edit.stop()
            self._edits_saved()
        self._typing.stop()
        super().hideEvent(e)

    def _page_bar(self):
        """The tools page's own scrollbar (the page scrolls, not this widget)."""
        w = self.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        return w.verticalScrollBar() if w is not None else None

    # ------------------------------------------------------------ text (all of it: a rebuild)

    def _rebuild(self) -> None:
        """Every section again, from the active character: the professions, the items, the mode, the plan, the
        bag, the EXP table."""
        t, rtl = self.t, self.t.rtl
        self._c = self._profiles.active
        cid = self._c.id if self._c is not None else None
        if cid != self._cid:
            self._choices = {}             # another character: the last one's swaps were theirs
            self._cid = cid
        bar = self._page_bar()
        was = bar.value() if bar is not None else 0
        self._clear_rows()
        if self._c is None:
            self._char.setText(bidi.plain(t("cp_no_character"), rtl))
            self._strip.setVisible(False)
            self._strip_hint.setVisible(False)
            return
        c = self._c
        self._strip.setVisible(True)
        self._strip_hint.setVisible(True)
        self._char.setText(bidi.plain(f"{bidi.ltr_name(f'{c.name} · {c.job}', rtl)} · {t('lv_short', n=c.level)}",
                                      rtl))
        self._retext_strip()
        self._rows = craftplan.craftable(self._kb, c)
        self._add_items()
        self._add_rest()
        if bar is not None:
            bar.setValue(was)

    def _retext_strip(self) -> None:
        """The profession boxes filled from the character (signals off: a re-read is not a player's edit)."""
        t = self.t
        self._strip_hint.setText(bidi.plain(t("cp_strip_hint"), t.rtl))
        for disc, (lv, pct) in self._prof_boxes.items():
            key = DISCIPLINE_KEY[disc]
            word = bidi.ltr_name(disc, t.rtl)
            for box, text in ((lv, str(self._c.crafts[key]) if self._c.crafts.get(key) else ""),
                              (pct, str(self._c.craft_pct[key]) if self._c.craft_pct.get(key) else "")):
                box.blockSignals(True)
                box.setText(text)
                box.blockSignals(False)
            lv.setPlaceholderText("—")
            pct.setPlaceholderText("—")
            lv.setAccessibleName(t("cp_prof_tip", disc=word))
            lv.setToolTip(t("cp_prof_tip", disc=word))
            pct.setAccessibleName(t("cp_bar_tip", disc=word))
            pct.setToolTip(t("cp_bar_tip", disc=word))

    def _clear_rows(self) -> None:
        self._section_widgets = {}
        self._badges = {}
        self._bag_boxes = {}
        self._exp_boxes = {}
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def _clear_from(self, first: str) -> None:
        """Drop the sections from `first` on (their widgets go, their fold choice stays)."""
        for key in SECTIONS[SECTIONS.index(first):]:
            for w in self._section_widgets.pop(key, ()):
                self._rows_lay.removeWidget(w)
                w.hide()
                w.deleteLater()
            self._badges.pop(key, None)

    def _fold_mark(self, folded: bool) -> str:
        return ("◂" if self.t.rtl else "▸") if folded else "▾"

    def _section(self, key: str, label: str, badge: str = "") -> QVBoxLayout:
        """One foldable section: its head row and the body layout the caller fills. Folded once, it stays
        folded across rebuilds (the player's own choice)."""
        t = self.t
        folded = key in self._folded
        head = _SectionHead(bidi.plain(label, t.rtl))
        head.badge.setText(bidi.plain(badge, t.rtl) if badge else "")
        head.mark.setText(self._fold_mark(folded))
        head.fold_clicked.connect(lambda k=key: self._fold(k))
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for w in (head, body):
            self._rows_lay.addWidget(w)
        self._section_widgets[key] = [head, body]
        self._badges[key] = head.badge
        body.setVisible(not folded)
        return lay

    def _fold(self, key: str) -> None:
        self._folded ^= {key}
        folded = key in self._folded
        widgets = self._section_widgets.get(key, [])
        if len(widgets) > 1:
            widgets[1].setVisible(not folded)
            widgets[0].mark.setText(self._fold_mark(folded))

    # ------------------------------------------------------------ what to craft

    def _targets_badge(self) -> str:
        n = len(self._c.craft_targets) if self._c is not None else 0
        return "" if not n else self.t("cp_targets_one") if n == 1 else self.t("cp_targets", n=n)

    def _add_items(self) -> None:
        """What to craft: the search box and the item rows (the class's equips, or the search's every product).
        The rows re-fill on typing without a rebuild, so the keyboard stays where it is."""
        t, rtl = self.t, self.t.rtl
        lay = self._section("items", t("cp_items"), self._targets_badge())
        lay.addWidget(_label(bidi.plain(t("cp_items_hint"), rtl), "RowHint"))
        self._search = QLineEdit()
        self._search.setClearButtonEnabled(True)
        self._search.setPlaceholderText(bidi.plain(t("cp_search_ph"), rtl))
        self._search.textChanged.connect(lambda _t: self._typing.start())
        lay.addWidget(self._search)
        self._item_rows = QVBoxLayout()
        self._item_rows.setSpacing(6)
        lay.addLayout(self._item_rows)
        self._item_more = _label("", "RowHint")
        lay.addWidget(self._item_more)
        self._fill_items()

    def _fill_items(self) -> None:
        """The item rows for what is typed (nothing typed: the class's equips), each with its Make stepper wired
        to the character's craft_targets list."""
        t, rtl = self.t, self.t.rtl
        if self._c is None or "items" not in self._section_widgets:
            return
        query = self._search.text()
        rows = [r for r in self._rows if _match(r.name, query)] if query.strip() else [r for r in self._rows if r.equip]
        picked = set(self._c.craft_targets)
        rows.sort(key=lambda r: r.key not in picked)      # what is already being made first, never past "and N more"
        while self._item_rows.count():
            item = self._item_rows.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for r in rows[:ITEM_ROWS]:
            self._item_rows.addWidget(self._item_row(r))
        left = len(rows) - min(len(rows), ITEM_ROWS)
        self._item_more.setText(bidi.plain(t("cp_more", n=bidi.ltr_block(str(left), rtl)), rtl) if left else "")
        self._item_more.setVisible(bool(left))

    def _item_row(self, r: Row) -> QWidget:
        """One item: its name with the Make stepper beside it, under them the level, the class's stat, the
        profession and level, what it costs from raw mats and what crafting it saves."""
        t, rtl = self.t, self.t.rtl
        card, lay = _card()
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(_label(bidi.ltr_name(r.name, rtl), "CardName"), 1)
        step = Stepper(0, MAX_MAKE, self._c.craft_targets.count(r.key))
        step.set_label(t("cp_make_tip"))
        step.setToolTip(t("cp_make_tip"))
        step.valueChanged.connect(lambda v, k=r.key: self._make_set(k, v))
        top.addWidget(step, 0, Qt.AlignTop)
        lay.addLayout(top)
        bits = []
        if isinstance(r.lv, int):
            bits.append(t("lv_short", n=r.lv))
        if r.stat_word and isinstance(r.stat, int):
            bits.append(bidi.ltr_block(f"{r.stat_word} {r.stat}", rtl))
        bits.append(bidi.ltr_block(f"{r.discipline} {r.prof_lv}", rtl))
        bits.append(bidi.plain(t("cp_from_raw", n=self._price(r.cost)), rtl))
        if r.save is not None:
            bits.append(bidi.plain(t("cp_saves", n=self._price(r.save)), rtl))
        elif r.buy is None:
            bits.append(bidi.plain(t("cp_not_sold"), rtl))
        lay.addWidget(_label(bidi.plain(" · ".join(bits), rtl), "CardSub"))
        return card

    def _price(self, v: int) -> str:
        """"1,400 mesos" as one Latin block: as two islands a Hebrew line read "mesos 1,400"."""
        return bidi.ltr_block(f"{_mesos(v)} mesos", self.t.rtl)

    def _make_set(self, key: str, v: int) -> None:
        """The Make stepper: craft_targets holds a key once per make (duplicates are the count)."""
        c = self._profiles.active
        if c is None:
            return
        have = c.craft_targets.count(key)
        if v > have:
            c.craft_targets.extend([key] * (v - have))
        else:
            for _ in range(have - v):
                c.craft_targets.remove(key)
        self._profiles.save()
        self._add_rest()
        if self._badges.get("items") is not None:
            self._badges["items"].setText(bidi.plain(self._targets_badge(), self.t.rtl))

    # ------------------------------------------------------------ the bag and the mode

    def _add_rest(self) -> None:
        """The sections after the items — the mode, the plan, the bag, the EXP table — rebuilt together: they
        all follow the plan, and the items (whose search box holds the keyboard) stay put. The plan right under
        the picks; the bag, which lists every mat the plan touches, after it."""
        self._clear_from("mode")
        self._bag_boxes = {}
        self._exp_boxes = {}
        if self._c is None:
            return
        self._plan = None
        if self._c.craft_targets:
            try:
                self._plan = craftplan.plan(self._kb, self._c, list(self._c.craft_targets), dict(self._c.craft_bag),
                                            self._mode, self._settings["craft_exp_table"], self._choices)
            except Exception:          # a plan must never take the page down
                log.warning("craft plan failed", exc_info=True)
                self._plan = None
        self._add_mode()
        self._add_plan()
        self._add_bag()
        self._add_exp()

    def _add_bag(self) -> None:
        """The bag: what the plan uses plus what is carried, under the KB's own group words, a count box each.
        Only the plan's items and the carried ones — a whole item list would drown the plan."""
        t, rtl = self.t, self.t.rtl
        c = self._c
        entries: dict[str, tuple[str, str, bool]] = {}     # name casefold -> (name, KB key, in the plan)
        if self._plan is not None:
            for f in self._plan.farm:
                entries.setdefault(f.name.casefold(), (f.name, f.key, True))
            for st in self._plan.stages:
                for s in st.steps:
                    entries.setdefault(s.name.casefold(), (s.name, s.key, True))
        for k in c.craft_bag:
            name = str(self._kb.entities.get(k, {}).get("name") or "")
            if name:
                entries.setdefault(name.casefold(), (name, k, False))
        if not entries:
            return
        lay = self._section("bag", t("cp_bag"))
        lay.addWidget(_label(bidi.plain(t("cp_bag_hint"), rtl), "RowHint"))
        cats = craftplan.categories(self._kb)
        groups: dict[str, list[tuple[str, str, bool]]] = {}
        for name, key, in_plan in entries.values():
            groups.setdefault(cats.get(name.casefold(), "other"), []).append((name, key, in_plan))
        for cat in sorted(groups, key=lambda g: CATEGORY_ORDER.get(g, 9)):
            lay.addWidget(_label(bidi.plain(t(CAT_KEYS.get(cat, "cp_cat_other")), rtl), "PlanHead"))
            for name, key, in_plan in sorted(groups[cat], key=lambda g: g[0].casefold()):
                lay.addWidget(self._bag_row(name, key, in_plan))

    def _bag_row(self, name: str, key: str, in_plan: bool) -> QWidget:
        """One bag item: its name, the "in plan" mark when the plan uses it, and its count box."""
        t, rtl = self.t, self.t.rtl
        card = QFrame(objectName="Card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(CARD_PAD, 4, CARD_PAD, 4)
        lay.setSpacing(6)
        lay.addWidget(_label(bidi.ltr_name(name, rtl), "CardName"), 1)
        if in_plan:
            lay.addWidget(_label(bidi.plain(t("cp_in_plan"), rtl), "RowHint", wrap=False))
        box = _num_box(72, 6)
        have = int(self._c.craft_bag.get(key, 0) or 0) if key else 0
        box.setText(str(have) if have else "")
        box.setPlaceholderText("0")
        box.setToolTip(t("cp_bag_tip"))
        box.setAccessibleName(t("cp_bag_tip"))
        box.textChanged.connect(lambda _t: self._edit.start())
        if key:
            self._bag_boxes[key] = box
        else:
            box.setEnabled(False)      # an item the KB has no key for can't be counted
        lay.addWidget(box)
        return card

    def _add_mode(self) -> None:
        """How a recipe variant is picked when an item has more than one; the plan follows at once."""
        t = self.t
        lay = self._section("mode", t("cp_mode"))
        seg = Segmented([(t(MODE_KEYS[m]), m) for m in MODES], self._mode, t.rtl)
        seg.set_label(t("cp_mode_tip"))
        seg.setToolTip(t("cp_mode_tip"))
        seg.changed.connect(self._mode_set)
        lay.addWidget(seg, 0, Qt.AlignHCenter)

    def _mode_set(self, mode) -> None:
        self._mode = str(mode)
        QTimer.singleShot(0, self._add_rest)     # not from inside the clicked segment's own signal: it is rebuilt

    # ------------------------------------------------------------ the plan

    def _add_plan(self) -> None:
        t, rtl = self.t, self.t.rtl
        lay = self._section("plan", t("cp_plan"))
        plan = self._plan
        if plan is None or not plan.stages:
            lay.addWidget(_label(bidi.plain(t("cp_plan_empty"), rtl), "RowHint"))
            return
        # the totals: cost, craft actions, equip level
        card, clay = _card()
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)
        for i, (word, val) in enumerate(((t("cp_total_cost"), _mesos(plan.cost)),
                                         (t("cp_total_crafts"), f"{plan.crafts:,}"),
                                         (t("cp_equip_lv"), t("lv_short", n=plan.equip_lv) if plan.equip_lv else "—"))):
            grid.addWidget(_label(bidi.ltr_block(val, rtl), "BigStat", wrap=False), 0, i)
            grid.addWidget(_label(bidi.plain(word, rtl), "BigStatLabel"), 1, i)
        clay.addLayout(grid)
        lay.addWidget(card)
        # 1 · farm these first
        farm = [f for f in plan.farm if f.farm > 0]
        if farm:
            lay.addWidget(_label(bidi.plain(t("cp_farm"), rtl), "SectionHeader"))
            lay.addWidget(_label(bidi.plain(t("cp_farm_hint"), rtl), "RowHint"))
            for f in farm:
                lay.addWidget(self._farm_row(f))
            if plan.bag_used:
                pairs = " · ".join(bidi.ltr_name(f"{n} ×{q}", rtl) for n, q in plan.bag_used)
                lay.addWidget(_label(bidi.plain(t("cp_from_bag", list=pairs), rtl), "RowHint"))
        # 2 · then craft in this order
        lay.addWidget(_label(bidi.plain(t("cp_order"), rtl), "SectionHeader"))
        for st in plan.stages:
            for w in self._stage(st):
                lay.addWidget(w)
        if plan.leftovers:
            pairs = " · ".join(bidi.ltr_name(f"{n} ×{q}", rtl) for n, q in plan.leftovers)
            lay.addWidget(_label(bidi.plain(t("cp_leftover", list=pairs), rtl), "RowHint"))
        for code, arg in plan.notes:
            if code == "cycle":
                lay.addWidget(_label(bidi.plain(t("cp_note_cycle", name=bidi.ltr_name(arg, rtl)), rtl), "RowHint"))

    def _farm_row(self, f: FarmItem) -> QWidget:
        """One raw mat to farm: its name, its group word, how many are still missing (the total beside it when
        the bag covered some), and where it comes from — droppers, or the NPC selling it."""
        t, rtl = self.t, self.t.rtl
        card, lay = _card()
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(_label(bidi.ltr_name(f.name, rtl), "CardName"), 1)
        count = bidi.ltr_block(_mesos(f.farm), rtl)
        if f.bag > 0:
            count += " " + bidi.plain(f"({t('cp_farm_total', n=bidi.ltr_block(_mesos(f.need), rtl))})", rtl)
        top.addWidget(_label(count, "CardStat", wrap=False), 0, Qt.AlignTop)
        lay.addLayout(top)
        bits = [bidi.plain(t(CAT_KEYS.get(f.category, "cp_cat_other")), rtl)]
        if f.droppers:
            # each monster's name kept whole, its level in the player's words beside it (the gear page's way)
            drops = " · ".join(f"{bidi.ltr_name(m, rtl)} ({t('lv_short', n=lv)})" for m, lv in f.droppers)
            bits.append(bidi.plain(t("cp_drops_from", list=drops), rtl))
        if f.sellers:
            npc, place, price = f.sellers[0]
            bits.append(bidi.plain(t("cp_buy_at", who=bidi.ltr_block(f"{npc} · {place} · {_mesos(price)} mesos", rtl)),
                                   rtl))
        if f.price is None and not f.droppers:
            bits.append(bidi.plain(t("cp_no_price"), rtl))
        lay.addWidget(_label(bidi.plain(" · ".join(bits), rtl), "CardSub"))
        return card

    def _stage(self, st: Stage) -> list[QWidget]:
        """One stage: its title (what it is, how many crafts, the fees), the level-up's own line (what it needs,
        what blocks it), then its steps."""
        t, rtl = self.t, self.t.rtl
        out: list[QWidget] = []
        if st.kind == "level":
            # the discipline and its levels are one Latin block: as two islands a Hebrew line read
            # "להעלות Smithing 2 → 1", the pair of them swapping ends
            lv = bidi.ltr_block(f"{st.discipline} {st.level_from} → {st.level_to}", rtl)
            title = t("cp_stage_level", lv=lv)
        elif st.kind == "prep":
            title = t("cp_stage_prep")
        else:
            finals = [s for s in st.steps if s.final]
            title = (t("cp_stage_final_name", name=bidi.ltr_name(finals[0].name, rtl)) if len(finals) == 1
                     else t("cp_stage_final", n=len(finals)))
        out.append(_label(bidi.plain(title, rtl), "PlanHead"))
        fee = t("cp_free") if st.fee == 0 else t("cp_fees", n=self._price(st.fee))
        lines = [t("cp_crafts_one") if st.crafts == 1 else t("cp_crafts", n=f"{st.crafts:,}"), fee]
        if st.kind == "level":
            if st.blocked.startswith("char:"):
                lines.append(t("cp_blocked_char", b=bidi.ltr_block(st.blocked.split(":", 1)[1], rtl)))
            elif st.blocked == "exp":
                lines.append(t("cp_blocked_exp"))
            elif st.exp_each:
                lines.append(t("cp_exp_step", exp=f"{st.exp_each:,}"))
        out.append(_label(bidi.plain(" · ".join(lines), rtl), "RowHint"))
        for s in st.steps:
            out.append(self._step_row(s))
        return out

    def _step_row(self, s: Step) -> QWidget:
        """One craft: how many of what, its Swap when the product has more than one recipe, its Crafted button
        (the mats leave the bag, the products enter it); under them the profession and level, the ingredient
        totals and the fee."""
        t, rtl = self.t, self.t.rtl
        card, lay = _card()
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(_label(bidi.ltr_name(f"{s.crafts}× {s.name}", rtl), "CardName"), 1)
        if len(craftplan.recipes(self._kb).get(s.name.casefold(), ())) > 1:
            swap = _button(bidi.plain(t("cp_swap"), rtl), "SubChip", t("cp_swap_tip"))
            swap.clicked.connect(lambda _=False, s=s: self._swap(s))
            top.addWidget(swap, 0, Qt.AlignTop)
        done = _button(bidi.plain(t("cp_crafted"), rtl), "NowChip", t("cp_step_tip"))
        done.clicked.connect(lambda _=False, s=s: self._crafted(s))
        top.addWidget(done, 0, Qt.AlignTop)
        lay.addLayout(top)
        bits = [bidi.ltr_block(f"{s.discipline} {s.prof_lv}", rtl)]
        ings = ", ".join(f"{i.name} ×{i.qty * s.crafts}" for i in s.ingredients)
        if ings:
            bits.append(bidi.ltr_block(ings, rtl))
        if s.makes > 1:
            bits.append(bidi.plain(t("cp_makes", n=s.makes), rtl))
        if s.level_up and s.exp:
            bits.append(bidi.plain(t("cp_exp_step", exp=f"{s.exp:,}"), rtl))
        bits.append(bidi.plain(t("cp_free"), rtl) if s.fee_total == 0
                    else bidi.plain(t("cp_fees", n=self._price(s.fee_total)), rtl))
        lay.addWidget(_label(bidi.plain(" · ".join(bits), rtl), "CardSub"))
        if s.optional:
            opts = ", ".join(f"{i.name} ×{i.qty}" for i in s.optional)
            lay.addWidget(_label(bidi.plain(t("cp_optional"), rtl) + " " + bidi.ltr_block(f"({opts})", rtl), "RowHint"))
        return card

    # ------------------------------------------------------------ the plan's own edits

    def _key_of(self, name: str, fallback: str = "") -> str:
        """An item's KB key: the recipes table's product key, else the KB's own name lookup."""
        for rec in craftplan.recipes(self._kb).get(name.casefold(), ()):
            if rec.key:
                return rec.key
        got = getattr(self._kb, "_item_by_name", {}).get(name.lower(), "")
        return f"item/{got}" if got else fallback

    def _crafted(self, s: Step) -> None:
        """A step marked crafted: its ingredients leave the bag (what is there of them), its products enter it,
        and the plan follows — it shrinks around the new bag, like the site's."""
        c = self._profiles.active
        if c is None:
            return
        for ing in s.ingredients:
            key = ing.key or self._key_of(ing.name)
            if not key:
                continue
            have = int(c.craft_bag.get(key, 0) or 0)
            used = min(have, ing.qty * s.crafts)
            if used:
                left = have - used
                if left > 0:
                    c.craft_bag[key] = left
                else:
                    c.craft_bag.pop(key, None)
        pkey = s.key or self._key_of(s.name)
        if pkey:
            c.craft_bag[pkey] = c.craft_bag.get(pkey, 0) + s.crafts * s.makes
        self._profiles.save()
        QTimer.singleShot(0, self._add_rest)     # not from inside the clicked button's own signal: it is rebuilt

    def _swap(self, s: Step) -> None:
        """The Swap button: the next of this product's recipes (round the ring), the plan follows."""
        variants = craftplan.recipes(self._kb).get(s.name.casefold(), ())
        if len(variants) < 2:
            return
        nums = [v.num for v in variants]
        cur = self._choices.get(s.name.casefold())
        if cur not in nums:
            cur = next((v.num for v in variants if v.discipline == s.discipline and v.prof_lv == s.prof_lv), nums[0])
        self._choices[s.name.casefold()] = nums[(nums.index(cur) + 1) % len(nums)]
        QTimer.singleShot(0, self._add_rest)

    # ------------------------------------------------------------ the EXP table

    def _add_exp(self) -> None:
        """One row per crafting level: the EXP to the next (a box — the player's measured value overrides the
        KB's) with the KB / yours mark, saved to settings["craft_exp_table"]."""
        t, rtl = self.t, self.t.rtl
        rows = [e for e in craftplan.exp_needs(self._kb, self._settings["craft_exp_table"])
                if e.level < craftplan.LEVEL_CAP and e.exp is not None]
        if not rows:
            return
        lay = self._section("exp", t("cp_exp_table"))
        lay.addWidget(_label(bidi.plain(t("cp_exp_hint"), rtl), "RowHint"))
        for e in rows:
            row = QWidget()
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 0, 0, 0)
            rlay.setSpacing(6)
            rlay.addWidget(_label(bidi.plain(f"{t('lv_short', n=e.level)} → {t('lv_short', n=e.level + 1)}", rtl),
                                  "RowLabel", wrap=False), 1)
            box = _num_box(80, 6)
            box.setText(str(e.exp))
            box.setToolTip(t("cp_exp_tip"))
            box.setAccessibleName(t("cp_exp_tip"))
            box.textChanged.connect(lambda _t: self._edit.start())
            self._exp_boxes[e.level] = box
            rlay.addWidget(box)
            mark = _label(bidi.plain(t("cp_exp_yours" if e.measured else "cp_exp_kb"), rtl),
                          "WarnHint" if e.measured else "RowHint", wrap=False)
            mark.setMinimumWidth(40)
            rlay.addWidget(mark)
            lay.addWidget(row)

    # ------------------------------------------------------------ the player's own numbers

    def _edits_saved(self) -> None:
        """The pause held: the profession boxes, the bag counts and the EXP boxes saved (an empty box unsets),
        then the plan re-read."""
        c = self._profiles.active
        if c is None or c.id != self._cid:
            return              # the boxes were another character's
        for disc, (lv, pct) in self._prof_boxes.items():
            key = DISCIPLINE_KEY[disc]
            a, b = lv.text().strip(), pct.text().strip()
            if a.isdigit() and 0 < int(a) <= craftplan.LEVEL_CAP:
                c.crafts[key] = int(a)
            elif not a:
                c.crafts.pop(key, None)
            if b.isdigit() and 0 <= int(b) <= 100:
                c.craft_pct[key] = int(b)
            elif not b:
                c.craft_pct.pop(key, None)
        for key, box in self._bag_boxes.items():
            n = box.text().strip()
            if n.isdigit() and int(n) > 0:
                c.craft_bag[key] = int(n)
            elif not n or n.isdigit():
                c.craft_bag.pop(key, None)
        table = dict(self._settings["craft_exp_table"] or {})
        known = {e.level: e for e in craftplan.exp_needs(self._kb, None)}
        for lv, box in self._exp_boxes.items():
            n = box.text().strip()
            kb_exp = known[lv].exp if lv in known else None
            if n.isdigit() and int(n) > 0 and int(n) != kb_exp:
                table[str(lv)] = int(n)
            elif not n or n.isdigit():
                table.pop(str(lv), None)       # emptied, or typed back to the KB's own: the KB's again
        self._settings["craft_exp_table"] = table or None
        self._profiles.save()
        self._add_rest()

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._rebuild()
