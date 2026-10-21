"""The grind tracker's own window (the bar's Grind tracker window over the game opens it with "Full tracker"): the
running session in tables, its history and the grind spots compared. It reads what the loot scanner counts
(ui/lootscan.py): the game's pickup messages and the quick slots' potion counts, read on this computer.

Three tabs:
- This session: start / stop / reset, the next level and when it comes at this rate (the bottom bar's EXP, read on
  this computer), potions running low with the nearest shop, a table of every category so far and per hour (an
  estimate from the start, live: EXP, mesos, ETC, items, the income they make, what the potions cost, the net profit),
  how that compares with the last session on the same map, the monsters killed (each EXP message is a kill, named by
  the EXP of the monsters on the maps it ran on), then what was picked up: the ETC and the other items each with how
  many, what an NPC pays, what MesoWatch's players usually sell it for (its "usual sale price", with credit and a
  link) and whether to keep it, sell it to players or to an NPC; the potions used at the NPC shop's price. (The live
  tracking of every pickup as it comes is the card's over the game, ui/lootoverlay.py, not this window's.)
- History: every past session (the loot database), each with its totals, its monsters and, opened, what it picked
  up and used; one can be deleted. Its cards show the same categories the session's table does, and a press on
  Details or Delete leaves the page where it was scrolled.
The window resizes from every edge (EdgeResize), and keeps its scroll while the figures update every second.
- Where it pays best: the maps compared per hour, and what that shows (lootinsights.py)."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import bidi, glossary, lootextras, lootlog, mesowatch, routes
from ..i18n import I18n
from . import terms
from .controls import Segmented, rtl_buttons
from .glass import EdgeResize, GlassDialog
from .location import LOCATION
from .patchnotes import gutter
from .widgets import MAP_REQUESTS, tip_html

log = logging.getLogger("maplehelper")

WIDTH, HEIGHT = 760, 900
MIN_W, MIN_H = 560, 420     # the smallest the edges drag it to
HISTORY = 10                # past sessions shown at first; "More" adds as many again
TABS = ("now", "history", "compare")


def _clear(layout) -> None:
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()                # gone now, not at the next event loop pass (it drew under the new rows)
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            _clear(it.layout())


def _tag(text: str, kind: str = "Tag") -> QLabel:
    lb = QLabel(text, objectName=kind)
    lb.setAlignment(Qt.AlignCenter)
    return lb


class GrindTracker(EdgeResize, GlassDialog):
    guide_requested = Signal()          # "Guide": the tracker's guide (ui/trackerguide.py)

    def __init__(self, scanner, kb, settings, lang: str, stylesheet: str):
        self.t = t = I18n(lang or "he")
        super().__init__(t("tracker_title"), t.rtl)
        self.setStyleSheet(stylesheet + self._style())
        self.setMinimumSize(MIN_W, MIN_H)
        self.setMouseTracking(True)           # hovering the rim shows the resize cursor (the map window does too)
        self.fit_screen(WIDTH, HEIGHT)
        self.scanner, self.kb, self.settings = scanner, kb, settings
        self._hist_n = HISTORY
        self._hist_sig = None
        self._prev_sig = None            # (the main map, the log): the last session there, looked up once
        self._prev = None
        self._delete_armed: int | None = None   # a session whose Delete was pressed once (a second press deletes)
        self._open: set[int] = set()     # the history sessions opened to their details
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # the status and the buttons, always on top
        top = QHBoxLayout()
        top.setSpacing(8)
        self.chip = _tag("", "TagGood")
        self.state = self._label("", "RowLabel")
        top.addWidget(self.chip, 0, Qt.AlignVCenter)
        top.addWidget(self.state, 1)
        self.start_btn = self._button("loot_start", "Primary", lambda: self.scanner.start())
        self.stop_btn = self._button("loot_card_stop", "TrkStop", lambda: self.scanner.end())     # red (the owner)
        self.reset_btn = self._button("loot_card_reset", "Secondary", lambda: self.scanner.start())
        self.reset_btn.setToolTip(t("loot_card_reset_tip"))
        self.guide_btn = self._button("trk_guide", "Secondary", self.guide_requested.emit)
        for b in (self.start_btn, self.stop_btn, self.reset_btn, self.guide_btn):
            top.addWidget(b)
        outer.addLayout(top)
        self.tabs = Segmented([(t(f"trk_tab_{k}"), k) for k in TABS], "now", t.rtl)
        self.tabs.changed.connect(self._show_tab)
        outer.addWidget(self.tabs)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self._pages: dict[str, QVBoxLayout] = {}
        for k in TABS:
            sc, lay = self._scroll()
            self.stack.addWidget(sc)
            self._pages[k] = lay
        self._build_now()
        rtl_buttons(self, t.rtl)
        scanner.changed.connect(self.refresh)
        # the clock and the per-hour figures move between pickups too: every second, live (the owner)
        self._ticker = QTimer(self, interval=1000, timeout=lambda: self.isVisible() and self.refresh())
        self._ticker.start()
        self.refresh()

    # ---------------------------------------------------------------- building blocks

    def _style(self) -> str:
        return """
#TrkHead { font-weight: 600; }
#TrkCell { padding: 6px 8px; }
#TrkNum { padding: 6px 8px; font-weight: 600; }
#TrkStrong { padding: 8px 8px; font-weight: 700; }
#TrkBad { padding: 6px 8px; font-weight: 600; color: #D9534F; }
#TrkGood { padding: 8px 8px; font-weight: 700; color: #2E9E5B; }
#TrkAlert { background: rgba(217,83,79,0.12); border: 1px solid rgba(217,83,79,0.45); border-radius: 12px; }
#TrkGoal { background: rgba(255,149,51,0.10); border: 1px solid rgba(255,149,51,0.40); border-radius: 12px; }
#TrkNpc { padding: 6px 8px; font-weight: 600; }
#TrkTagFm { background: rgba(46,158,91,0.16); color: #2E9E5B; border-radius: 8px; padding: 2px 10px; font-weight: 700; }
#TrkTagNpc { background: rgba(240,122,18,0.16); color: #D9680A; border-radius: 8px; padding: 2px 10px;
             font-weight: 700; }
#TrkTagKeep { background: rgba(10,108,214,0.14); color: #0A6CD6; border-radius: 8px; padding: 2px 10px;
              font-weight: 700; }
QPushButton#TrkStop { background: #D9534F; color: #FFFFFF; border: 1px solid #C9433F; border-radius: 12px;
                      min-height: 26px; padding: 4px 18px; font-weight: 600; }
QPushButton#TrkStop:hover { background: #C9433F; }
QProgressBar#TrkGoalBar { background: rgba(127,127,127,0.18); border: none; border-radius: 4px; }
QProgressBar#TrkGoalBar::chunk { background: #F07A12; border-radius: 4px; }
QPushButton#TrkSmall { background: rgba(127,127,127,0.14); border: none; border-radius: 9px; padding: 4px 12px;
                       font-weight: 600; }
QPushButton#TrkSmall:hover { background: rgba(255,149,51,0.22); }
QPushButton#TrkSmallDanger { background: #D9534F; color: #FFFFFF; border: none; border-radius: 9px;
                             padding: 4px 12px; font-weight: 600; }
"""

    def _scroll(self) -> tuple[QScrollArea, QVBoxLayout]:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(*gutter(self.t.rtl))
        lay.setSpacing(14)
        sc.setWidget(body)
        return sc, lay

    def _p(self, text: str) -> str:
        return bidi.plain(text, self.t.rtl)

    def _html(self, text: str, explain: bool = True) -> str:
        d = "rtl" if self.t.rtl else "ltr"
        out = []
        for line in (text or "").split("\n"):
            if not line.strip():
                out.append("<p style='margin:0; font-size:5px;'>&nbsp;</p>")
                continue
            out.append(bidi.paragraph_html(line, d).replace("margin:0 0 4px 0;", "margin:0 0 3px 0; line-height:135%;"))
        out = "".join(out)
        return glossary.annotate(out, self.t.lang, seen=set()) if explain else out

    def _label(self, text: str, obj: str = "RowLabel", wrap: bool = True, explain: bool = True) -> QLabel:
        """explain: a game term gets its "?" (not in a table's cells: a "?" on every NPC heading was clutter)."""
        lb = QLabel(objectName=obj)
        lb.setTextFormat(Qt.RichText)
        lb.setWordWrap(wrap)
        lb.setText(self._html(text, explain))
        return terms.watch(lb, self.t.lang)

    @staticmethod
    def _set(label, text: str) -> None:
        """A label's text, only when it changes: the window refreshes every second, and an unchanged label set again
        repainted it (with the glass under it)."""
        if label.text() != text:
            label.setText(text)

    def _small(self, text: str, kind: str, then) -> QPushButton:
        """A small rounded button in a history card's head (Details, Delete)."""
        b = QPushButton(self._p(text), objectName=kind)
        b.setCursor(Qt.PointingHandCursor)
        b.setAutoDefault(False)
        b.setFocusPolicy(Qt.NoFocus)        # (focus passed on from a deleted button scrolled the page to it)
        b.clicked.connect(lambda *_: then())
        return b

    def _button(self, key: str, kind: str, then) -> QPushButton:
        b = QPushButton(self._p(self.t(key)), objectName=kind)
        b.setCursor(Qt.PointingHandCursor)
        b.setAutoDefault(False)
        b.clicked.connect(lambda *_: then())
        return b

    def _num(self, n, sign: bool = False) -> str:
        if n is None:
            return "–"
        text = f"{abs(n):,.0f}"
        text = ("-" if n < 0 else "+" if sign and n > 0 else "") + text
        return bidi.ltr_block(text, self.t.rtl)

    def _pic(self, key: str, size: int = 26) -> QLabel | None:
        path = self.kb.picture(key) if key else None
        if not path:
            return None
        pic = QLabel()
        pic.setPixmap(QPixmap(str(path)).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        pic.setFixedSize(size + 4, size + 4)
        pic.setAlignment(Qt.AlignCenter)
        return pic

    def _table(self, heads: list[str], rows: list[list], strong: set[int] | None = None,
               styles: dict[int, str] | None = None) -> QFrame:
        """A table card: a header row, then each row (a cell: text, or (key, name) for an item with its picture);
        numbers stand centred under their headings. strong: rows set in bold (totals); styles: row -> object name."""
        card = QFrame(objectName="Group")
        cells: dict[tuple[int, int], QLabel] = {}
        grid = QGridLayout(card)
        grid.setContentsMargins(10, 6, 10, 6)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(0)
        for c, h in enumerate(heads):
            lb = self._label(h, "TrkHead", wrap=True, explain=False)
            if h == self.t("trk_col_hour"):
                lb.setToolTip(tip_html(self.t("trk_col_hour_tip"), self.t.rtl))
            lb.setAlignment((Qt.AlignVCenter | (Qt.AlignCenter if c else Qt.AlignLeading)))
            grid.addWidget(lb, 0, c)
        for r, row in enumerate(rows, start=1):
            line = QFrame(objectName="Hair")
            line.setFixedHeight(1)
            grid.addWidget(line, 2 * r - 1, 0, 1, len(heads))
            obj = (styles or {}).get(r - 1) or ("TrkStrong" if strong and r - 1 in strong else None)
            for c, cell in enumerate(row):
                if isinstance(cell, tuple) and cell and cell[0] == "advice":     # where to sell, keep
                    grid.addWidget(self._advice_cell(*cell[1:]), 2 * r, c)
                    continue
                if isinstance(cell, tuple):         # (key, name): the item's picture and name
                    key, name = cell
                    w = QWidget()
                    hl = QHBoxLayout(w)
                    hl.setContentsMargins(0, 2, 0, 2)
                    hl.setSpacing(6)
                    pic = self._pic(key)
                    if pic is not None:
                        hl.addWidget(pic)
                    hl.addWidget(self._label(bidi.ltr_block(name, self.t.rtl), "TrkCell", explain=False), 1)
                    grid.addWidget(w, 2 * r, c)
                    continue
                lb = self._label(str(cell), obj or ("TrkCell" if c == 0 else "TrkNum"), wrap=c == 0, explain=False)
                lb.setAlignment(Qt.AlignVCenter | (Qt.AlignCenter if c else Qt.AlignLeading))
                grid.addWidget(lb, 2 * r, c)
                cells[(r - 1, c)] = lb
        card._cells = cells
        card._shape = (tuple(heads), tuple(str(r[0]) for r in rows), tuple(len(r) for r in rows))
        grid.setColumnStretch(0, 5 if len(heads) > 5 else 3)     # a wide table: its names get the room
        for c in range(1, len(heads)):
            grid.setColumnStretch(c, 2)
        return card

    # ---------------------------------------------------------------- this session

    def _build_now(self) -> None:
        lay = self._pages["now"]
        self.now_status = self._label("", "RowHint")
        lay.addWidget(self.now_status)
        self.alerts = QVBoxLayout()              # potions running low
        lay.addLayout(self.alerts)
        self.goal = QFrame(objectName="TrkGoal")     # the next level and when
        gl = QVBoxLayout(self.goal)
        gl.setContentsMargins(14, 10, 14, 10)
        gl.setSpacing(6)
        self.goal_text = self._label("", "RowLabel")
        self.goal_bar = QProgressBar(objectName="TrkGoalBar")
        self.goal_bar.setTextVisible(False)
        self.goal_bar.setFixedHeight(8)
        self.goal_bar.setLayoutDirection(Qt.RightToLeft if self.t.rtl else Qt.LeftToRight)
        gl.addWidget(self.goal_text)
        gl.addWidget(self.goal_bar)
        lay.addWidget(self.goal)
        self.summary = QVBoxLayout()
        lay.addLayout(self.summary)
        self.compare_line = self._label("", "RowLabel")      # the last session on this map
        lay.addWidget(self.compare_line)
        self.kills_box = QVBoxLayout()               # the monsters killed
        lay.addLayout(self.kills_box)
        self.etc_box = QVBoxLayout()
        lay.addLayout(self.etc_box)
        self.items_box = QVBoxLayout()
        lay.addLayout(self.items_box)
        self.potions_box = QVBoxLayout()
        lay.addLayout(self.potions_box)
        lay.addStretch(1)

    def _section(self, box: QVBoxLayout, title: str) -> None:
        box.addWidget(self._label(title, "SectionHeader"))

    def _changed(self, box, sig) -> bool:
        """The section's content is new: it is cleared, to be filled again. The same as last time: left as it is
        (rebuilt every second, the whole window flickered while a session ran)."""
        sigs = self.__dict__.setdefault("_sigs", {})
        if sigs.get(id(box)) == sig:
            return False
        sigs[id(box)] = sig
        _clear(box)
        return True

    def _fill_now(self) -> None:
        t, scan = self.t, self.scanner
        cid = scan.cid()
        lg = scan.log_for(cid)
        running = scan.running(cid)
        if lg is None:
            for box in (self.summary, self.etc_box, self.items_box, self.potions_box, self.alerts,
                        self.kills_box):
                self._changed(box, None)
            self._sum_card = None
        status = ""
        if running and scan.state in ("no_game", "covered"):
            status = t(f"loot_{scan.state}")
        elif scan.state == "no_reader":
            status = t("loot_no_reader")
        elif running and scan.quick_state == "none":
            status = t("loot_potions_unseen")
        elif not lg:
            status = t("trk_intro")
        self._set(self.now_status, self._html(status))
        self.now_status.setVisible(bool(status))
        for w in (self.goal, self.compare_line):
            w.setVisible(lg is not None)
        if lg is None:
            return
        sm = lootlog.summarize(self.kb, lg)
        self._fill_goal(sm, running)
        self._fill_alerts(sm, running)
        h = sm.seconds
        rate = lambda v: lootlog.per_hour(v, h)  # noqa: E731
        rows, styles, top = self._money_rows(sm.exp, sm.mesos, lootextras.loot_value(self.kb, sm.items),
                                             lootextras.by_kind(self.kb, sm.potions), sm.potions_cost, rate)
        heads = [t("trk_col_cat"), t("trk_col_total"), t("trk_col_hour")]
        card = self.__dict__.get("_sum_card")
        shape = (tuple(heads), tuple(str(r[0]) for r in rows), tuple(len(r) for r in rows))
        if card is not None and getattr(card, "_shape", None) == shape:
            # the same table: only its numbers move (and the net's colour when it turns)
            for r, row in enumerate(rows):
                for c in (1, 2):
                    lb = card._cells.get((r, c))
                    if lb is None:
                        continue
                    self._set(lb, self._html(str(row[c]), False))
                    want = styles.get(r) or ("TrkStrong" if r == top else "TrkNum")
                    if lb.objectName() != want:
                        lb.setObjectName(want)
                        lb.style().unpolish(lb)
                        lb.style().polish(lb)
        else:
            self._changed(self.summary, shape)
            self._section(self.summary, t("trk_summary"))
            self._sum_card = self._table(heads, rows, strong={top}, styles=styles)
            self.summary.addWidget(self._sum_card)
            self.summary.addWidget(self._label(t("trk_summary_note"), "RowHint"))
        self._fill_compare_line(lg, sm)
        counts: dict[int, int] = {}
        for e in lg.events:
            if e.kind == "exp":
                counts[e.amount] = counts.get(e.amount, 0) + 1
        if self._changed(self.kills_box, (tuple(sorted(counts.items())), tuple(lg.maps), lg.level)):
            self._fill_kills(self.kills_box, counts, list(lg.maps), lg.level)
        if self._changed(self.etc_box, (tuple(sm.items), mesowatch.version())):
            _clear(self.items_box)
            self._fill_items(sm)
        unseen = running and scan.quick_state == "none"
        if self._changed(self.potions_box, (tuple(sm.potions), unseen)):
            self._fill_potions(sm, unseen)

    def _mw(self, key: str) -> tuple[int | None, bool, str]:
        """(MesoWatch's usual sale price, it is a guess on few sales, the item's MesoWatch link)."""
        try:
            _, s = mesowatch.for_item(self.kb, key, refresh=False)
        except Exception:  # noqa: BLE001 - no market on hand: no community price
            return None, False, ""
        if s is None or not s.price:
            return None, False, ""
        return int(s.price), bool(s.low_data or s.scrolled_only), mesowatch.item_url(s.mw_id or s.game_id)

    def _item_heads(self) -> list[str]:
        t = self.t
        return [t("trk_c_item"), t("trk_c_qty"), t("trk_c_npc"), t("trk_c_mw"), t("trk_c_value"), t("trk_c_advice")]

    def _item_rows(self, items, advice: bool = False) -> tuple[list[list], bool]:
        """Each item: how many, an NPC's price and the players' (MesoWatch), what they bring at whichever pays more
        and where that is: an NPC or FM tag (lootextras.best_sale); advice: Keep instead, when a quest or recipe
        needs it (lootextras.sell_advice). A total row last."""
        t = self.t
        rows, guessed = [], False
        tot_n = tot_v = 0
        for name, n, key, price in items:
            mw, low, _ = self._mw(key) if key else (None, False, "")
            guessed |= low
            each, where = lootextras.best_sale(self.kb, key, price)
            tot_n += n
            tot_v += n * (each or 0)
            a = lootextras.sell_advice(self.kb, key, price, mw, low) if advice else None
            rows.append([(key, name), self._num(n), self._num(price) if price else "–",
                         (("~" if low else "") + self._num(mw)) if mw else "–",
                         self._num(n * each) if each else "–",
                         ("advice", where, a if a is not None and a.verdict == "keep" else None)])
        rows.append([t("trk_total"), self._num(tot_n), "", "", self._num(tot_v), ""])
        return rows, guessed

    def _advice_cell(self, where: str, keep=None) -> QWidget:
        """Where it sells for more: an FM or NPC tag (lootextras.best_sale), always; and Keep beside it when a current
        quest or recipe needs it (keep: lootextras.sell_advice's "keep", its tooltip saying which)."""
        t = self.t
        box = QWidget()                 # the tags their own size, centred in their cell
        hl = QHBoxLayout(box)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(6)
        hl.addStretch(1)
        if where in ("fm", "npc"):
            fm = where == "fm"
            lb = QLabel(t("trk_at_fm" if fm else "trk_at_npc"), objectName="TrkTagFm" if fm else "TrkTagNpc")
            lb.setToolTip(t("trk_sell_players_tip" if fm else "trk_sell_npc_tip"))
            hl.addWidget(lb)
        if keep is not None:
            what = ([t("sell_quests_n", n=keep.quests)] if keep.quests else []) + \
                   ([t("sell_recipes_n", n=keep.recipes)] if keep.recipes else [])
            lb = QLabel(t("trk_keep_short"), objectName="TrkTagKeep")
            lb.setToolTip(tip_html(t("trk_keep", what=", ".join(what)) + chr(10) + keep.tip, t.rtl))
            hl.addWidget(lb)
        if hl.count() == 1:
            hl.addWidget(QLabel("–", objectName="TrkNpc"))
        hl.addStretch(1)
        return box

    def _fill_items(self, sm) -> None:
        t = self.t
        heads = self._item_heads()
        guessed = False
        etc = [i for i in sm.items if lootlog.is_etc(self.kb, i[2])]
        other = [i for i in sm.items if not lootlog.is_etc(self.kb, i[2])]
        for box, title, items, none in ((self.etc_box, "trk_etc_head", etc, "trk_etc_none"),
                                        (self.items_box, "trk_items_head", other, "trk_items_none")):
            self._section(box, t(title))
            if not items:
                box.addWidget(self._label(t(none), "RowHint"))
                continue
            rows, low = self._item_rows(items, advice=True)
            guessed |= low
            box.addWidget(self._table(heads, rows, strong={len(rows) - 1}))
        if etc or other:            # MesoWatch's prices: credited, with a link (its terms), once under both tables
            note = t("trk_mw_credit") + (" " + t("trk_mw_guess") if guessed else "")
            credit = QPushButton(self._p(note), objectName="Link")
            credit.setCursor(Qt.PointingHandCursor)
            credit.setAutoDefault(False)
            credit.clicked.connect(lambda *_: QDesktopServices.openUrl(QUrl(mesowatch.item_url())))
            self.items_box.addWidget(credit, 0, Qt.AlignLeading)
        unknown = [bidi.ltr_block(n, t.rtl) for n, _, k, _ in sm.items if not k]
        if unknown:
            self.items_box.addWidget(self._label(t("loot_unknown", names=", ".join(unknown)), "RowHint"))

    def _fill_goal(self, sm, running: bool) -> None:
        """The next level: the EXP still to go (the bar's points against the KB's EXP table) and, at this session's
        EXP per hour, how long it takes."""
        t = self.t
        hud = self.scanner.hud
        g = lootextras.level_goal(self.kb, hud.level, hud.exp, sm.exp_h) if hud is not None else None
        if g is None:
            self._set(self.goal_text, self._html(t("trk_goal_wait") if running else ""))
            self.goal.setVisible(running)
            self.goal_bar.hide()
            return
        need = g.left + (hud.exp or 0)
        when = self._clock(g.seconds) if g.seconds is not None else ""
        text = t("trk_goal", level=g.level + 1, left=self._num(g.left)) + \
            (" · " + t("trk_goal_eta", time=when) if when else " · " + t("trk_goal_no_rate"))
        self._set(self.goal_text, self._html(text))
        self.goal_bar.setRange(0, max(1, need))
        self.goal_bar.setValue(hud.exp or 0)
        self.goal_bar.show()
        self.goal.show()

    def _fill_alerts(self, sm, running: bool) -> None:
        """Potions running low (used this session, LOW_POTIONS or fewer left) and the nearest shop that sells them."""
        t = self.t
        here = LOCATION.here
        used = {k for _, _, k, _ in sm.potions}
        lows = lootextras.low_potions(self.kb, self.scanner.potion_counts, used, here.map if here else None) \
            if running else []
        if not self._changed(self.alerts, tuple((x.key, x.left, x.shop, x.hops) for x in lows)):
            return
        for low in lows:
            box = QFrame(objectName="TrkAlert")
            row = QHBoxLayout(box)
            row.setContentsMargins(12, 8, 12, 8)
            row.setSpacing(8)
            pic = self._pic(low.key)
            if pic is not None:
                row.addWidget(pic)
            name = bidi.ltr_block(low.name, t.rtl)
            text = t("trk_low_none" if low.left == 0 else "trk_low", name=name, n=self._num(low.left))
            if low.shop:
                npc, where, price = low.shop
                text += "\n" + t("trk_low_shop", npc=bidi.ltr_block(npc, t.rtl), where=bidi.ltr_block(where, t.rtl),
                                  price=self._num(price))
                if low.hops is not None:
                    text += " · " + (t("trk_low_here") if low.hops == 0 else t("trk_low_hops", n=low.hops))
            row.addWidget(self._label(text, "RowLabel"), 1)
            if low.npc_key:
                go = self._button("tool_route", "Secondary", lambda k=low.npc_key: MAP_REQUESTS.requested.emit(k))
                row.addWidget(go, 0, Qt.AlignVCenter)
            self.alerts.addWidget(box)

    def _fill_compare_line(self, lg, sm) -> None:
        """How this session does against the last one on its main map (the map it spent the most time on)."""
        from .. import lootdb
        t = self.t
        mid = max(lg.maps, key=lg.maps.get) if lg.maps else None
        if not mid or sm.seconds < 120:
            self.compare_line.hide()
            return
        sig = (mid, lg.db_id, lg.start)
        if sig != self._prev_sig:
            self._prev_sig = sig
            try:
                self._prev = lootextras.previous_on_map(lootdb.sessions(self.scanner.cid(), self.kb, limit=40), mid,
                                                        exclude=lg.db_id)
            except Exception:  # noqa: BLE001 - no history to compare with: no line
                self._prev = None
        prev = self._prev
        if prev is None:
            self.compare_line.hide()
            return
        def pct(now, before):
            v = lootextras.change_pct(now, before)
            if v is None:
                return "–"
            arrow = "▲" if v > 0 else "▼" if v < 0 else "="
            return f"{arrow} {bidi.ltr_block(f'{v:+d}%', t.rtl)}"
        when = bidi.ltr_block(time.strftime("%d/%m", time.localtime(prev.started)), t.rtl)
        self._set(self.compare_line, self._html(t(
            "trk_vs_prev", map=bidi.ltr_block(self._map_name(mid), t.rtl), when=when,
            exp=pct(sm.exp_h, prev.per_hour(prev.exp)), net=pct(sm.net_h, prev.per_hour(prev.net)))))
        self.compare_line.show()

    def _fill_kills(self, box: QVBoxLayout, counts: dict[int, int], maps: list[str], level: int | None,
                    compact: bool = False) -> None:
        """The monsters killed, with their pictures: each EXP message is a kill, named by the EXP the KB gives the
        monsters that spawn on the session's maps (lootextras.kills)."""
        t = self.t
        got = lootextras.kills(self.kb, counts, maps, level)
        if not compact:
            self._section(box, t("trk_kills_head"))
        if not got:
            if not compact:
                box.addWidget(self._label(t("trk_kills_none"), "RowHint"))
            return
        row = QHBoxLayout()
        row.setSpacing(10)
        for key, name, n in got[:6 if compact else 10]:
            cell = QHBoxLayout()
            cell.setSpacing(4)
            pic = self._pic(key, 22 if compact else 30)
            if pic is not None:
                cell.addWidget(pic)
            cell.addWidget(self._label(bidi.ltr_block(f"{name} ×{n:,}", t.rtl), "RowLabel", wrap=False,
                                       explain=False))
            row.addLayout(cell)
        row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(row)
        box.addWidget(wrap)
        if not compact:
            box.addWidget(self._label(t("trk_kills_note"), "RowHint"))

    def _money_rows(self, exp, mesos, loot, kinds, spent, rate):
        """The summary's rows, their colours and the income row's index: EXP and mesos so far and per hour; ETC and
        the other items what they bring (each at whichever pays more, an NPC or the players: lootextras.best_sale)
        and how many; the income they all make, the potions HP / MP, the net. Only EXP and mesos have a per hour
        (the owner); the "so far" column is the money, top to bottom. The live session's and each past one's alike,
        so the history shows every category the tracker does."""
        t = self.t
        income = mesos + loot.value
        net = income - spent
        rows = [
            [t("trk_row_exp"), self._num(exp), self._num(rate(exp))],
            [t("trk_row_mesos"), self._num(mesos), self._num(rate(mesos))],
            [t("trk_row_etc"), self._num(loot.etc_v), t("trk_units", n=self._num(loot.etc_n))],
            [t("trk_row_items"), self._num(loot.items_v), t("trk_units", n=self._num(loot.items_n))],
            [t("trk_row_income"), self._num(income), ""],
            *[[r[0], r[1], ""] for r in self._spent_rows(kinds, rate)],
            [t("trk_row_net"), self._num(net), ""],
        ]
        top = 4
        bad = {i: "TrkBad" for i in range(top + 1, len(rows) - 1)}
        return rows, {**bad, len(rows) - 1: "TrkGood" if net >= 0 else "TrkBad"}, top

    def _spent_rows(self, kinds: dict[str, tuple[int, int]], rate) -> list[list]:
        """What the potions cost, HP and MP apart (an Elixir's both on a row of its own); one row of 0 with none."""
        t = self.t
        if not kinds:
            return [[t("trk_row_spent"), self._num(0), self._num(0)]]
        out = []
        for k in lootextras.KINDS:
            if k in kinds:
                n, cost = kinds[k]
                out.append([t(f"trk_row_spent_{k}"), self._num(-cost if cost else 0),
                            self._num(-rate(cost) if cost and rate(cost) else 0)])
        return out

    def _fill_potions(self, sm, unseen: bool) -> None:
        """The potions used, HP and MP apart: each potion's count and cost, then each kind's total, then all."""
        t = self.t
        self._section(self.potions_box, t("loot_potions_head"))
        if not sm.potions:
            self.potions_box.addWidget(self._label(t("loot_potions_unseen" if unseen else "loot_potions_none"),
                                                   "RowHint"))
            return
        rows, strong = [], set()
        kinds = lootextras.by_kind(self.kb, sm.potions)
        for k in lootextras.KINDS:
            mine = [p for p in sm.potions if (lootextras.potion_kind(self.kb, p[2]) or "hp") == k]
            if not mine:
                continue
            rows += [[(key, name), t(f"trk_kind_{k}"), self._num(n), self._num(price) if price else "–",
                      self._num(-n * price) if price else "–"] for name, n, key, price in mine]
            n, cost = kinds[k]
            if len(kinds) > 1:              # a kind's own total, when there are two kinds to tell apart
                strong.add(len(rows))
                rows.append([t(f"trk_total_{k}"), "", self._num(n), "", self._num(-cost)])
        strong.add(len(rows))
        rows.append([t("trk_total"), "", self._num(sm.used), "", self._num(-sm.potions_cost)])
        self.potions_box.addWidget(self._table([t("trk_c_potion"), t("trk_c_kind"), t("trk_c_used"),
                                                t("trk_c_shop"), t("trk_c_cost")], rows, strong=strong))
        unpriced = [bidi.ltr_block(n, t.rtl) for n, _, _, p in sm.potions if not p]
        if unpriced:
            self.potions_box.addWidget(self._label(t("loot_potions_unpriced", names=", ".join(unpriced)), "RowHint"))

    # ---------------------------------------------------------------- history and the maps compared

    def _map_name(self, mid: str) -> str:
        try:
            return routes.of(self.kb).name(mid) or mid
        except Exception:  # noqa: BLE001 - a map the routes no longer have keeps its id
            return mid

    def _fill_history(self, force: bool = False) -> None:
        from .. import lootdb
        cid = self.scanner.cid()
        sig = (cid, self.scanner.running(cid), self._hist_n)
        if sig == self._hist_sig and not force:
            return
        self._hist_sig = sig
        try:
            sessions = lootdb.sessions(cid, self.kb, limit=max(200, self._hist_n + 1))
            spots = lootdb.spots(cid, self.kb)
        except Exception as e:  # noqa: BLE001 - an unreadable database shows no history, never a broken window
            log.warning("grind tracker history: %s: %s", type(e).__name__, e)
            sessions, spots = [], []
        self._all_sessions = sessions
        self._fill_sessions(sessions)
        self._fill_compare(spots, sessions)

    def _fill_sessions(self, sessions) -> None:
        """The history's cards, rebuilt where the page was scrolled to (a press on Details or Delete, the clock)."""
        sc = self.stack.widget(TABS.index("history"))
        bar, inner = sc.verticalScrollBar(), sc.widget()
        at = bar.value()
        inner.setMinimumHeight(inner.height())     # the page keeps its height while its cards are rebuilt ...
        self._build_sessions(sessions)
        inner.layout().activate()
        bar.setValue(at)
        QTimer.singleShot(0, lambda: self._back_to(sc, at))

    @staticmethod
    def _back_to(sc, at: int) -> None:
        """... until the new cards are laid out: then its own height again, where it was scrolled to."""
        try:
            sc.widget().setMinimumHeight(0)
            sc.widget().layout().activate()
            sc.verticalScrollBar().setValue(at)
        except RuntimeError:        # the window closed meanwhile
            pass

    def _build_sessions(self, sessions) -> None:
        t = self.t
        lay = self._pages["history"]
        _clear(lay)
        if not sessions:
            lay.addWidget(self._label(t("loot_history_none"), "RowHint"))
            lay.addStretch(1)
            return
        lay.addWidget(self._label(t("trk_history_intro"), "RowHint"))
        for s in sessions[:self._hist_n]:
            card = QFrame(objectName="Group")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 10, 14, 10)
            cl.setSpacing(6)
            when = time.strftime("%d/%m %H:%M", time.localtime(s.started))
            head = [f"**{bidi.ltr_block(when, t.rtl)}**", self._clock(s.seconds)]
            if s.maps:
                first = bidi.ltr_block(self._map_name(next(iter(s.maps))), t.rtl)
                head.append(t("loot_session_map_more", map=first, n=len(s.maps) - 1) if len(s.maps) > 1 else first)
            if s.level:
                head.append(t("loot_session_level", n=s.level))
            top = QHBoxLayout()
            top.setSpacing(6)
            top.addWidget(self._label(" · ".join(head), "RowLabel"), 1)
            more = self._small(t("trk_less" if s.id in self._open else "trk_more"), "TrkSmall",
                               lambda i=s.id: self._toggle(i))
            top.addWidget(more, 0, Qt.AlignVCenter)
            if s.ended is not None:         # (the running session isn't history yet: no Delete)
                armed = self._delete_armed == s.id
                delete = self._small(t("trk_delete_sure" if armed else "trk_delete"),
                                     "TrkSmallDanger" if armed else "TrkSmall", lambda i=s.id: self._delete(i))
                top.addWidget(delete, 0, Qt.AlignVCenter)
            cl.addLayout(top)
            self._fill_kills(cl, s.exp_counts, list(s.maps), s.level, compact=True)
            ph = lambda v, s=s: s.per_hour(v)  # noqa: E731
            rows, styles, inc = self._money_rows(s.exp, s.mesos, lootextras.loot_value(self.kb, s.items),
                                                 lootextras.by_kind(self.kb, s.potion_list), s.potions_cost, ph)
            cl.addWidget(self._table([t("trk_col_cat"), t("trk_col_total"), t("trk_col_hour")], rows, strong={inc},
                                     styles=styles))
            if s.id in self._open:
                if s.items:
                    heads = self._item_heads()
                    rows, _ = self._item_rows(s.items)
                    cl.addWidget(self._label(t("trk_picked_head"), "SectionHeader"))
                    cl.addWidget(self._table(heads, rows, strong={len(rows) - 1}))
                if s.potion_list:
                    rows = [[(k, n), t(f"trk_kind_{lootextras.potion_kind(self.kb, k) or 'hp'}"), self._num(c),
                             self._num(p) if p else "–", self._num(-c * p) if p else "–"]
                            for n, c, k, p in s.potion_list]
                    cl.addWidget(self._label(t("loot_potions_head"), "SectionHeader"))
                    cl.addWidget(self._table([t("trk_c_potion"), t("trk_c_kind"), t("trk_c_used"), t("trk_c_shop"),
                                              t("trk_c_cost")], rows))
            lay.addWidget(card)
        if len(sessions) > self._hist_n:
            lay.addWidget(self._button("loot_history_more", "Secondary", self._more), 0, Qt.AlignLeading)
        lay.addStretch(1)

    def _delete(self, sid: int) -> None:
        """A session gone from the history: the first press asks ("Delete? Press again"), the second deletes."""
        from .. import lootdb
        if self._delete_armed != sid:
            self._delete_armed = sid
            QTimer.singleShot(5000, lambda: self._disarm(sid))
            self._fill_sessions(self.__dict__.get("_all_sessions") or [])
            return
        self._delete_armed = None
        lg = self.scanner.log_for(self.scanner.cid())
        if lg is not None and lg.db_id == sid:
            self.scanner.forget_shown()          # the window showed it: it shows nothing now
        try:
            lootdb.delete_log(sid)
        except Exception as e:  # noqa: BLE001 - a failed delete leaves the session; the list says nothing new
            log.warning("grind tracker delete: %s: %s", type(e).__name__, e)
        self._open.discard(sid)
        self._fill_history(force=True)

    def _disarm(self, sid: int) -> None:
        try:
            if self._delete_armed == sid:
                self._delete_armed = None
                self._fill_sessions(self.__dict__.get("_all_sessions") or [])
        except RuntimeError:
            pass

    def _toggle(self, sid: int) -> None:
        self._open ^= {sid}
        self._fill_sessions(self.__dict__.get("_all_sessions") or [])

    def _more(self) -> None:
        self._hist_n += HISTORY
        self._fill_history()

    def _clock(self, seconds: float | None) -> str:
        if seconds is None:
            return "–"
        m = int(seconds) // 60
        return self.t("grind_hours", h=m // 60, m=f"{m % 60:02d}") if m >= 60 else self.t("grind_minutes", m=m)

    def _fill_compare(self, spots, sessions) -> None:
        from .. import lootinsights
        t = self.t
        lay = self._pages["compare"]
        _clear(lay)
        m = lootinsights.MIN_SPOT_S // 60
        ok = lootinsights.comparable(spots)
        if not ok:
            lay.addWidget(self._label(t("loot_compare_none", m=m), "RowHint"))
            lay.addStretch(1)
            return
        lay.addWidget(self._label(t("loot_compare_intro", m=m), "RowHint"))
        name = lambda mid: bidi.ltr_block(self._map_name(mid), t.rtl)  # noqa: E731
        best_exp = max(ok, key=lambda sp: sp.per_hour(sp.exp) or 0).map if len(ok) > 1 else None
        best_net = max(ok, key=lambda sp: sp.per_hour(sp.net) or 0).map if len(ok) > 1 else None
        heads = [t("trk_c_map"), t("trk_c_time"), t("trk_c_exp_h"), t("trk_c_net_h"), t("trk_c_etc_h"),
                 t("trk_c_potions_h")]
        rows = []
        for sp in sorted(ok, key=lambda sp: -(sp.per_hour(sp.net) or 0)):
            marks = (" ⭐" if sp.map == best_net else "") + (" ⚡" if sp.map == best_exp else "")
            rows.append([f"**{name(sp.map)}**{marks}", self._clock(sp.seconds), self._num(sp.per_hour(sp.exp)),
                         self._num(sp.per_hour(sp.net)), self._num(sp.per_hour(sp.etc_value)),
                         self._num(-sp.per_hour(sp.potions_cost)) if sp.potions_cost else "–"])
        lay.addWidget(self._table(heads, rows))
        if len(ok) > 1:
            legend = QHBoxLayout()          # at the reading start, under the table
            legend.setSpacing(6)
            legend.addWidget(_tag("⭐ " + t("loot_tag_best_net"), "TagGood"))
            legend.addWidget(_tag("⚡ " + t("loot_tag_best_exp"), "TagGood"))
            legend.addStretch(1)
            lay.addLayout(legend)
        found = lootinsights.insights(spots, sessions)
        if found:
            lay.addWidget(self._label(t("loot_insights"), "SectionHeader"))
        for ins in found:
            args = dict(ins.args)
            for k in ("map", "other", "exp_map", "net_map"):
                if k in args:
                    args[k] = name(args[k])
            for k in ("exp", "net"):
                if k in args:
                    args[k] = self._num(args[k])
            if "when" in args:
                args["when"] = bidi.ltr_block(time.strftime("%d/%m %H:%M", time.localtime(args["when"])), t.rtl)
            lay.addWidget(self._label("• " + t(ins.key, **args), "RowLabel"))
        lay.addStretch(1)

    # ---------------------------------------------------------------- the whole window

    def _show_tab(self, key) -> None:
        self.stack.setCurrentIndex(TABS.index(key))
        if key != "now":
            self._fill_history()

    def show_tab(self, key: str) -> None:
        self.tabs.group.buttons()[TABS.index(key)].setChecked(True)
        self._show_tab(key)

    def refresh(self) -> None:
        try:
            t, scan = self.t, self.scanner
            cid = scan.cid()
            lg = scan.log_for(cid)
            running = scan.running(cid)
            self.start_btn.setVisible(not running)
            self.stop_btn.setVisible(running)
            self.reset_btn.setVisible(running)
            self.chip.setVisible(lg is not None)
            kind = "TagGood" if running else "Tag"
            if self.chip.objectName() != kind:          # restyled only when it changes (a repaint each second)
                self.chip.setObjectName(kind)
                self.chip.style().unpolish(self.chip)
                self.chip.style().polish(self.chip)
            self._set(self.chip, self._p(t("grind_tag_running" if running else "grind_tag_ended")))
            here = LOCATION.here
            where = bidi.ltr_block(self._map_name(here.map), t.rtl) if here is not None else ""
            if lg is None:
                state = t("trk_state_idle")
            elif running:
                m = int(lg.seconds()) // 60
                state = t("trk_state_running", time=self._clock(lg.seconds()) if m else t("grind_minutes", m=0))
                if where:
                    state += " · " + where
            else:
                state = t("trk_state_stopped", time=self._clock(lg.seconds()))
            self._set(self.state, self._html(state))
            # the figures redraw every second: each tab keeps where it was scrolled to. (No setUpdatesEnabled around
            # it: turning updates back on repainted the whole window every second, the flicker the owner saw)
            bars = [self.stack.widget(i).verticalScrollBar() for i in range(self.stack.count())]
            at = [b.value() for b in bars]
            self._fill_now()
            if self.stack.currentIndex():
                self._fill_history()
            else:
                self._hist_sig = None if self._hist_sig and self._hist_sig[1] != running else self._hist_sig
            for b, v in zip(bars, at):
                if b.value() != v:
                    b.setValue(v)
                    QTimer.singleShot(0, lambda b=b, v=v: b.setValue(v))     # (again once the new rows are laid out)
        except RuntimeError:        # the window closed meanwhile
            pass

    def lines(self) -> list[str]:
        """Every text shown on the current tab (for tests)."""
        page = self.stack.currentWidget().widget()
        return [lb.text() for lb in page.findChildren(QLabel) if lb.isVisibleTo(self)] + \
            [b.text() for b in page.findChildren(QPushButton) if b.isVisibleTo(self)]

    def tip(self, text: str) -> str:
        return tip_html(text, self.t.rtl)
