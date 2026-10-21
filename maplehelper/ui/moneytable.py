"""The grind tracker's figures as three tables joined into one card (the owner's layout, 2026-10-21), the same in the
card over the game, the full tracker and each past session of the history:

1. EXP and mesos: so far, and per hour (an estimate);
2. ETC, items, HP and MP potions: how many, and what they made (each item at whichever pays more, an NPC or the
   players) or cost (the potions, with a minus);
3. the total income, what the potions cost and the net profit: a number each, no headings.
Money coming in is green (mesos, the loot's worth, the income), money going out red (the potions, the spending); the
net green or red as it stands.

One grid holds the three, so their columns line up and they read as one; a stronger line parts them. Its rows are
fixed: an update only sets the numbers (and the net's colour), never builds anew (a rebuild every second flickered).
The look comes from object names (the card's and the tracker's style sheets each style them their own way)."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel

from .. import bidi
from ..i18n import I18n


@dataclass
class Look:
    """The object names it uses: the card's frame, a heading, a row's name, a number, a total, a loss, a gain, the
    hairline under a row and the line between two tables; pad: the grid's margins."""
    frame: str = "Group"
    head: str = "TrkHead"
    name: str = "TrkCell"
    num: str = "TrkNum"
    strong: str = "TrkStrong"
    bad: str = "TrkBad"
    good: str = "TrkGood"
    hair: str = "Hair"
    split: str = "TrkSplit"
    gain: str = "TrkGain"
    pad: tuple[int, int, int, int] = (10, 6, 10, 6)
    row_gap: int = 0


TRACKER = Look()
CARD = Look("Stat", "StatName", "StatName", "StatValue", "StatValue", "StatBad", "StatGood", "", "StatSplit",
            "StatGain", (10, 6, 10, 6), 4)

# (table, its rows): a row is its key; the first two tables have headings, the third none
ROWS = (("rate", ("exp", "mesos")), ("loot", ("etc", "items", "hp", "mp")), ("total", ("income", "spent", "net")))
NAMES = {"exp": "trk_row_exp", "mesos": "loot_card_mesos", "etc": "trk_row_etc", "items": "trk_row_items",
         "hp": "loot_card_potions_hp", "mp": "loot_card_potions_mp", "income": "trk_row_income",
         "spent": "trk_row_spent", "net": "trk_row_net"}
HEADS = {"rate": ("trk_col_total", "trk_col_hour"), "loot": ("trk_c_qty", "trk_c_money")}


class MoneyTable(QFrame):
    def __init__(self, t: I18n, look: Look = TRACKER, fmt=None, parent=None):
        super().__init__(parent, objectName=look.frame)
        self.t, self.look = t, look
        self.fmt = fmt or (lambda n: "–" if n is None else f"{n:,.0f}")
        grid = QGridLayout(self)
        grid.setContentsMargins(*look.pad)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(look.row_gap)
        self._heads: dict[tuple[str, int], QLabel] = {}
        self._names: dict[str, QLabel] = {}
        self._cells: dict[tuple[str, int], QLabel] = {}
        r = 0
        for i, (table, keys) in enumerate(ROWS):
            if i:                       # the line between two tables: they stay one card, parted
                line = QFrame(objectName=look.split)
                line.setFixedHeight(2)
                grid.addWidget(line, r, 0, 1, 3)
                r += 1
            if table in HEADS:
                for c in (1, 2):
                    lb = QLabel(objectName=look.head)
                    lb.setAlignment(Qt.AlignCenter)
                    grid.addWidget(lb, r, c)
                    self._heads[(table, c)] = lb
                r += 1
            for j, key in enumerate(keys):
                if j and look.hair:
                    hair = QFrame(objectName=look.hair)
                    hair.setFixedHeight(1)
                    grid.addWidget(hair, r, 0, 1, 3)
                    r += 1
                name = QLabel(objectName=look.name)
                name.setAlignment(Qt.AlignVCenter | Qt.AlignLeading)
                one = table not in HEADS            # the totals: one number, so the name has two columns' room
                grid.addWidget(name, r, 0, 1, 2 if one else 1)
                self._names[key] = name
                for c in ((2,) if one else (1, 2)):
                    lb = QLabel(objectName=look.num)
                    lb.setAlignment(Qt.AlignCenter)
                    grid.addWidget(lb, r, c)
                    self._cells[(key, c)] = lb
                r += 1
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        self.retext()

    def retext(self) -> None:
        """The headings and the rows' names, in the language picked."""
        t, rtl = self.t, self.t.rtl
        for (table, c), lb in self._heads.items():
            lb.setText(bidi.plain(t(HEADS[table][c - 1]), rtl))
            if HEADS[table][c - 1] == "trk_col_hour":
                lb.setToolTip(t("trk_col_hour_tip"))
        for key, lb in self._names.items():
            lb.setText(bidi.plain(t(NAMES[key]), rtl))

    def set_money(self, m) -> None:
        """m: a lootextras.Money (None: dashes). Only texts and colours that changed are set (no repaint else)."""
        f = self.fmt
        neg = lambda v: f(-v) if v else f(0)  # noqa: E731
        vals = {} if m is None else {
            ("exp", 1): f(m.exp), ("exp", 2): f(m.exp_h),
            ("mesos", 1): f(m.mesos), ("mesos", 2): f(m.mesos_h),
            ("etc", 1): f(m.etc_n), ("etc", 2): f(m.etc_v),
            ("items", 1): f(m.items_n), ("items", 2): f(m.items_v),
            ("hp", 1): f(m.hp_n), ("hp", 2): neg(m.hp_cost),
            ("mp", 1): f(m.mp_n), ("mp", 2): neg(m.mp_cost),
            ("income", 2): f(m.income), ("spent", 2): neg(m.spent), ("net", 2): f(m.net)}
        raw = {} if m is None else {("mesos", 1): m.mesos, ("mesos", 2): m.mesos_h, ("etc", 2): m.etc_v,
                                    ("items", 2): m.items_v, ("hp", 2): m.hp_cost, ("mp", 2): m.mp_cost}
        lk = self.look
        for (key, c), lb in self._cells.items():
            text = bidi.ltr_block(vals.get((key, c), ""), self.t.rtl) if vals.get((key, c)) else ""
            if lb.text() != text:
                lb.setText(text)
            # money in green, money out in red (the owner); EXP, the counts and a 0 plain
            zero = m is not None and key in ("mesos", "etc", "items", "hp", "mp") and not raw.get((key, c))
            want = (lk.num if zero else
                    lk.bad if key in ("hp", "mp", "spent") and c == 2 else
                    (lk.good if m is None or m.net >= 0 else lk.bad) if key == "net" else
                    lk.good if key == "income" else
                    lk.gain if key == "mesos" or (key in ("etc", "items") and c == 2) else lk.num)
            if lb.objectName() != want:
                lb.setObjectName(want)
                lb.style().unpolish(lb)
                lb.style().polish(lb)
        for key in ("spent", "net", "income"):
            name = self._names[key]
            want = lk.bad if key == "spent" else (lk.good if m is None or m.net >= 0 else lk.bad) \
                if key == "net" else lk.strong
            if name.objectName() != want:
                name.setObjectName(want)
                name.style().unpolish(name)
                name.style().polish(name)

    def lines(self) -> list[str]:
        """Each row as it reads: its name and its numbers (the headings first)."""
        out = [" ".join(lb.text() for lb in (self._heads.get((tb, 1)), self._heads.get((tb, 2))) if lb)
               for tb, _ in ROWS if tb in HEADS]
        for _, keys in ROWS:
            for key in keys:
                nums = [self._cells[(key, c)].text() for c in (1, 2) if (key, c) in self._cells
                        and self._cells[(key, c)].text()]
                out.append(" ".join([self._names[key].text(), *nums]))
        return out
