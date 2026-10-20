"""The grind tracker's own window (the bar's Grind tracker window over the game opens it with "Full tracker"): the
running session in tables, its history and the grind spots compared. It reads what the loot scanner counts
(ui/lootscan.py): the game's pickup messages and the quick slots' potion counts, read on this computer.

Three tabs:
- This session: start / stop / reset / share, the next level and when it comes at this rate (the bottom bar's
  EXP, read on this computer), potions running low with the nearest shop, a table of every category so far and per
  hour (EXP, mesos, ETC, items, the income they make, what the potions cost, the net profit), how that compares with
  the last session on the same map, the rate over the session as a graph, then what was picked up: the ETC and the
  other items each with how many, what an NPC pays, what MesoWatch's players usually sell it for (its "usual sale
  price", with credit and a link) and whether to keep it, sell it to players or to an NPC; the potions used at the
  NPC shop's price, and the live tracking of every pickup as it comes.
- History: every past session (the loot database), filtered by map or level, each with its totals and, opened, what
  it picked up and used; one can be shared as a picture or deleted.
- Where it pays best: the maps compared per hour, and what that shows (lootinsights.py)."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from .. import bidi, glossary, lootextras, lootlog, mesowatch, routes
from ..i18n import I18n
from . import terms
from .controls import Section, Segmented, Switch, rtl_buttons
from .glass import GlassDialog
from .location import LOCATION
from .patchnotes import gutter
from .rategraph import RateGraph
from .widgets import MAP_REQUESTS, on_solid_background, tip_html

log = logging.getLogger("maplehelper")

WIDTH, HEIGHT = 760, 900
LIVE = 12                   # pickups shown in the live tracking
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


class GrindTracker(GlassDialog):
    area_requested = Signal()           # "Mark the messages area yourself": the app draws the box

    def __init__(self, scanner, kb, settings, lang: str, stylesheet: str):
        self.t = t = I18n(lang or "he")
        super().__init__(t("tracker_title"), t.rtl)
        self.setStyleSheet(stylesheet + self._style())
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
        self.stop_btn = self._button("loot_card_stop", "Secondary", lambda: self.scanner.end())
        self.reset_btn = self._button("loot_card_reset", "Secondary", lambda: self.scanner.start())
        self.reset_btn.setToolTip(t("loot_card_reset_tip"))
        self.share_btn = self._button("trk_share", "Secondary", self._share_now)
        self.share_btn.setToolTip(t("trk_share_tip"))
        self.save_btn = self._button("trk_save_card", "Secondary", self.save_last_card)
        self.save_btn.hide()                    # after a share: the same picture, to a file
        for b in (self.start_btn, self.stop_btn, self.reset_btn, self.share_btn, self.save_btn):
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
        # the clock and the per-hour figures move between pickups too
        self._ticker = QTimer(self, interval=5000, timeout=lambda: self.isVisible() and self.refresh())
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
#TrkKeep { padding: 6px 8px; font-weight: 600; color: #0A6CD6; }
#TrkPlayers { padding: 6px 8px; font-weight: 600; color: #2E9E5B; }
#TrkNpc { padding: 6px 8px; font-weight: 600; }
QProgressBar#TrkGoalBar { background: rgba(127,127,127,0.18); border: none; border-radius: 4px; }
QProgressBar#TrkGoalBar::chunk { background: #F07A12; border-radius: 4px; }
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
        grid = QGridLayout(card)
        grid.setContentsMargins(10, 6, 10, 6)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(0)
        for c, h in enumerate(heads):
            lb = self._label(h, "TrkHead", wrap=True, explain=False)
            lb.setAlignment((Qt.AlignVCenter | (Qt.AlignCenter if c else Qt.AlignLeading)))
            grid.addWidget(lb, 0, c)
        for r, row in enumerate(rows, start=1):
            line = QFrame(objectName="Hair")
            line.setFixedHeight(1)
            grid.addWidget(line, 2 * r - 1, 0, 1, len(heads))
            obj = (styles or {}).get(r - 1) or ("TrkStrong" if strong and r - 1 in strong else None)
            for c, cell in enumerate(row):
                if isinstance(cell, tuple) and cell and cell[0] == "advice":     # sell or keep
                    grid.addWidget(self._advice_cell(cell[1]), 2 * r, c)
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
        grid.setColumnStretch(0, 3)
        for c in range(1, len(heads)):
            grid.setColumnStretch(c, 2)
        return card

    # ---------------------------------------------------------------- this session

    def _build_now(self) -> None:
        t = self.t
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
        self.graph_box = QFrame(objectName="Group")
        gbl = QVBoxLayout(self.graph_box)
        gbl.setContentsMargins(10, 8, 10, 8)
        self.graph = RateGraph((t("trk_c_exp_h"), t("trk_c_net_h"), t("trk_minutes_short")), t.rtl)
        gbl.addWidget(self.graph)
        self.graph_head = self._label(t("trk_graph"), "SectionHeader")
        lay.addWidget(self.graph_head)
        lay.addWidget(self.graph_box)
        self.etc_box = QVBoxLayout()
        lay.addLayout(self.etc_box)
        self.items_box = QVBoxLayout()
        lay.addLayout(self.items_box)
        self.potions_box = QVBoxLayout()
        lay.addLayout(self.potions_box)
        self.live_box = QVBoxLayout()
        lay.addLayout(self.live_box)
        # how the tracker reads the game, last: rarely touched
        sec = Section(t("trk_settings"), t.rtl)
        self.card_switch = Switch(bool(self.settings["loot_overlay"]))
        self.card_switch.setAccessibleName(t("loot_overlay"))
        self.card_switch.toggled.connect(self._set_card)
        sec.add_row(t("loot_overlay"), self.card_switch, hint=t("loot_overlay_hint"))
        area = QWidget()
        al = QVBoxLayout(area)
        al.setContentsMargins(0, 8, 0, 8)
        self.area_state = self._label("", "RowHint")
        al.addWidget(self.area_state)
        row = QHBoxLayout()
        self.pick_btn = self._button("loot_pick", "Secondary", self.area_requested.emit)
        self.auto_btn = self._button("loot_area_auto_again", "Secondary", self._back_to_auto)
        row.addWidget(self.pick_btn)
        row.addWidget(self.auto_btn)
        row.addStretch(1)
        al.addLayout(row)
        sec.add_widget(area)
        lay.addWidget(sec)
        lay.addStretch(1)

    def _set_card(self, on: bool) -> None:
        self.settings["loot_overlay"] = bool(on)
        self.scanner.changed.emit()

    def _back_to_auto(self) -> None:
        self.settings["loot_region"] = None
        self.scanner.restart()
        self.scanner.changed.emit()

    def _section(self, box: QVBoxLayout, title: str) -> None:
        box.addWidget(self._label(title, "SectionHeader"))

    def _fill_now(self) -> None:
        t, scan = self.t, self.scanner
        cid = scan.cid()
        lg = scan.log_for(cid)
        running = scan.running(cid)
        for box in (self.summary, self.etc_box, self.items_box, self.potions_box, self.live_box, self.alerts):
            _clear(box)
        region = scan.region()
        self.area_state.setText(self._html(t("loot_area_set", w=region["w"], h=region["h"]) if region
                                           else t("loot_area_auto")))
        self.pick_btn.setText(self._p(t("loot_pick_again" if region else "loot_pick")))
        self.auto_btn.setVisible(region is not None)
        if self.card_switch.isChecked() != bool(self.settings["loot_overlay"]):
            self.card_switch.blockSignals(True)
            self.card_switch.setChecked(bool(self.settings["loot_overlay"]))
            self.card_switch.blockSignals(False)
        status = ""
        if running and scan.state in ("no_game", "covered"):
            status = t(f"loot_{scan.state}")
        elif scan.state == "no_reader":
            status = t("loot_no_reader")
        elif running and scan.quick_state == "none":
            status = t("loot_potions_unseen")
        elif not lg:
            status = t("trk_intro")
        self.now_status.setText(self._html(status))
        self.now_status.setVisible(bool(status))
        for w in (self.goal, self.compare_line, self.graph_head, self.graph_box):
            w.setVisible(lg is not None)
        self.share_btn.setVisible(lg is not None)
        if lg is None:
            return
        sm = lootlog.summarize(self.kb, lg)
        self._fill_goal(sm, running)
        self._fill_alerts(sm, running)
        h = sm.seconds
        rate = lambda v: lootlog.per_hour(v, h)  # noqa: E731
        income = sm.value
        rows = [
            [t("trk_row_exp"), self._num(sm.exp), self._num(rate(sm.exp))],
            [t("trk_row_mesos"), self._num(sm.mesos), self._num(rate(sm.mesos))],
            [t("trk_row_etc"), self._num(sm.etc_value), self._num(rate(sm.etc_value))],
            [t("trk_row_items"), self._num(sm.other_value), self._num(rate(sm.other_value))],
            [t("trk_row_income"), self._num(income), self._num(rate(income))],
            [t("trk_row_spent"), self._num(-sm.potions_cost if sm.potions_cost else 0),
             self._num(-rate(sm.potions_cost) if sm.potions_cost and rate(sm.potions_cost) else 0)],
            [t("trk_row_net"), self._num(sm.net), self._num(rate(sm.net))],
        ]
        self._section(self.summary, t("trk_summary"))
        self.summary.addWidget(self._table([t("trk_col_cat"), t("trk_col_total"), t("trk_col_hour")], rows,
                                           strong={4}, styles={5: "TrkBad", 6: "TrkGood" if sm.net >= 0 else "TrkBad"}))
        if h < 60:
            self.summary.addWidget(self._label(t("loot_tip_wait"), "RowHint"))
        self._fill_compare_line(lg, sm)
        self.graph.set_points(lootextras.rate_series(self.kb, lg.events, lg.start, lg.ended or time.time()))
        self._fill_items(sm)
        self._fill_potions(sm, running and scan.quick_state == "none")
        self._fill_live(lg)

    def _mw(self, key: str) -> tuple[int | None, bool, str]:
        """(MesoWatch's usual sale price, it is a guess on few sales, the item's MesoWatch link)."""
        try:
            _, s = mesowatch.for_item(self.kb, key, refresh=False)
        except Exception:  # noqa: BLE001 - no market on hand: no community price
            return None, False, ""
        if s is None or not s.price:
            return None, False, ""
        return int(s.price), bool(s.low_data or s.scrolled_only), mesowatch.item_url(s.mw_id or s.game_id)

    def _item_rows(self, items, advice: bool = False) -> tuple[list[list], bool]:
        """advice: a last column, keep it / sell it to players / to an NPC (lootextras.sell_advice)."""
        t = self.t
        rows, guessed = [], False
        tot_n = tot_npc = tot_mw = 0
        self._advice_tips: list[str] = []
        for name, n, key, price in items:
            mw, low, _ = self._mw(key) if key else (None, False, "")
            guessed |= low
            mark = "~" if low else ""
            tot_n += n
            tot_npc += n * (price or 0)
            tot_mw += n * (mw or 0)
            row = [(key, name), self._num(n), self._num(price) if price else "–",
                   self._num(n * price) if price else "–",
                   (mark + self._num(mw)) if mw else "–", (mark + self._num(n * mw)) if mw else "–"]
            if advice:
                a = lootextras.sell_advice(self.kb, key, price, mw, low)
                row.append(("advice", a))
            rows.append(row)
        total = [t("trk_total"), self._num(tot_n), "", self._num(tot_npc), "", self._num(tot_mw) if tot_mw else "–"]
        rows.append(total + ([""] if advice else []))
        return rows, guessed

    def _advice_cell(self, a) -> QLabel:
        t = self.t
        if a.verdict == "keep":
            what = ([t("sell_quests_n", n=a.quests)] if a.quests else []) + \
                   ([t("sell_recipes_n", n=a.recipes)] if a.recipes else [])
            lb = self._label(t("trk_keep_short"), "TrkKeep", explain=False)
            lb.setToolTip(tip_html(t("trk_keep", what=", ".join(what)) + chr(10) + a.tip, t.rtl))
        elif a.verdict == "players":
            lb = self._label(t("trk_sell_players"), "TrkPlayers", explain=False)
            lb.setToolTip(t("trk_sell_players_tip"))
        elif a.verdict == "npc":
            lb = self._label(t("trk_sell_npc"), "TrkNpc", explain=False)
            lb.setToolTip(t("trk_sell_npc_tip"))
        else:
            lb = self._label("–", "TrkNpc", explain=False)
        lb.setAlignment(Qt.AlignCenter)
        return lb

    def _fill_items(self, sm) -> None:
        t = self.t
        heads = [t("trk_c_item"), t("trk_c_qty"), t("trk_c_npc"), t("trk_c_npc_total"), t("trk_c_mw"),
                 t("trk_c_mw_total"), t("trk_c_advice")]
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
            self.goal_text.setText(self._html(t("trk_goal_wait") if running else ""))
            self.goal.setVisible(running)
            self.goal_bar.hide()
            return
        need = g.left + (hud.exp or 0)
        when = self._clock(g.seconds) if g.seconds is not None else ""
        text = t("trk_goal", level=g.level + 1, left=self._num(g.left)) + \
            (" · " + t("trk_goal_eta", time=when) if when else " · " + t("trk_goal_no_rate"))
        self.goal_text.setText(self._html(text))
        self.goal_bar.setRange(0, max(1, need))
        self.goal_bar.setValue(hud.exp or 0)
        self.goal_bar.show()
        self.goal.show()

    def _fill_alerts(self, sm, running: bool) -> None:
        """Potions running low (used this session, LOW_POTIONS or fewer left) and the nearest shop that sells them."""
        t = self.t
        if not running:
            return
        here = LOCATION.here
        used = {k for _, _, k, _ in sm.potions}
        for low in lootextras.low_potions(self.kb, self.scanner.potion_counts, used, here.map if here else None):
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
        self.compare_line.setText(self._html(t(
            "trk_vs_prev", map=bidi.ltr_block(self._map_name(mid), t.rtl), when=when,
            exp=pct(sm.exp_h, prev.per_hour(prev.exp)), net=pct(sm.net_h, prev.per_hour(prev.net)))))
        self.compare_line.show()

    def _card_image(self, title: str, rows: list[list], items: list) -> QPixmap:
        """A session as a picture for Discord or WhatsApp: its title, the summary table and the top pickups."""
        t = self.t
        w = QFrame(objectName="ShareCard")
        w.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        w.setFixedWidth(460)
        w.setStyleSheet(self.styleSheet())
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        lay.addWidget(self._label(title, "ShareName"))
        lay.addWidget(self._table([t("trk_col_cat"), t("trk_col_total"), t("trk_col_hour")], rows,
                                  styles={len(rows) - 2: "TrkBad", len(rows) - 1: "TrkGood"}))
        if items:
            top = ", ".join(f"{bidi.ltr_block(n, t.rtl)} ×{c:,}" for n, c, _, _ in items[:4])
            lay.addWidget(self._label(t("loot_session_items", items=top), "RowHint"))
        lay.addWidget(self._label("Maple Helper · maplehelper.app", "RowHint"))
        w.adjustSize()
        return on_solid_background(w.grab(), 18)

    def _share(self, pm: QPixmap) -> None:
        QApplication.clipboard().setPixmap(pm)
        self._shared = pm
        self.save_btn.show()
        self._shared_note = self.t("trk_shared")
        self.state.setText(self._html(self._shared_note))
        QTimer.singleShot(6000, self._clear_note)

    def _clear_note(self) -> None:
        try:
            self._shared_note = ""
            self.refresh()
        except RuntimeError:
            pass

    def save_last_card(self) -> None:
        pm = getattr(self, "_shared", None)
        if pm is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, self.t("trk_save_card"), "grind.png", "PNG (*.png)")
        if path:
            pm.save(path, "PNG")

    def _share_now(self) -> None:
        t, scan = self.t, self.scanner
        lg = scan.log_for(scan.cid())
        if lg is None:
            return
        sm = lootlog.summarize(self.kb, lg)
        rate = lambda v: lootlog.per_hour(v, sm.seconds)  # noqa: E731
        mid = max(lg.maps, key=lg.maps.get) if lg.maps else None
        title = " · ".join(x for x in (t("tracker_title"), self._clock(sm.seconds),
                                       bidi.ltr_block(self._map_name(mid), t.rtl) if mid else "") if x)
        rows = [[t("trk_row_exp"), self._num(sm.exp), self._num(rate(sm.exp))],
                [t("trk_row_income"), self._num(sm.value), self._num(rate(sm.value))],
                [t("trk_row_spent"), self._num(-sm.potions_cost if sm.potions_cost else 0),
                 self._num(-rate(sm.potions_cost) if sm.potions_cost and rate(sm.potions_cost) else 0)],
                [t("trk_row_net"), self._num(sm.net), self._num(rate(sm.net))]]
        self._share(self._card_image(title, rows, sm.items))

    def _fill_potions(self, sm, unseen: bool) -> None:
        t = self.t
        self._section(self.potions_box, t("loot_potions_head"))
        if not sm.potions:
            self.potions_box.addWidget(self._label(t("loot_potions_unseen" if unseen else "loot_potions_none"),
                                                   "RowHint"))
            return
        rows = [[(key, name), self._num(n), self._num(price) if price else "–",
                 self._num(-n * price) if price else "–"] for name, n, key, price in sm.potions]
        rows.append([t("trk_total"), self._num(sm.used), "", self._num(-sm.potions_cost)])
        self.potions_box.addWidget(self._table([t("trk_c_potion"), t("trk_c_used"), t("trk_c_shop"),
                                                t("trk_c_cost")], rows, strong={len(rows) - 1}))
        unpriced = [bidi.ltr_block(n, t.rtl) for n, _, _, p in sm.potions if not p]
        if unpriced:
            self.potions_box.addWidget(self._label(t("loot_potions_unpriced", names=", ".join(unpriced)), "RowHint"))

    def _fill_live(self, lg) -> None:
        t = self.t
        self._section(self.live_box, t("loot_feed_head"))
        evs = lg.events[-LIVE:][::-1]
        if not evs:
            self.live_box.addWidget(self._label(t("trk_live_none"), "RowHint"))
            return
        for e in evs:
            row = QHBoxLayout()
            row.setSpacing(8)
            when = bidi.ltr_block(time.strftime("%H:%M:%S", time.localtime(e.t)), t.rtl)
            row.addWidget(self._label(when, "RowHint", wrap=False))
            pic = self._pic(e.key, 22) if e.kind in ("item", "potion") else None
            if pic is not None:
                row.addWidget(pic)
            if e.kind in ("item", "potion"):
                what = t(f"loot_feed_{e.kind}", name=bidi.ltr_block(e.name, t.rtl), n=f"{e.amount:,}")
            else:
                what = t(f"loot_feed_{e.kind}", n=f"{e.amount:,}")
            row.addWidget(self._label(what, "RowLabel"), 1)
            self.live_box.addLayout(row)

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
        t = self.t
        lay = self._pages["history"]
        _clear(lay)
        if not sessions:
            lay.addWidget(self._label(t("loot_history_none"), "RowHint"))
            lay.addStretch(1)
            return
        lay.addWidget(self._label(t("trk_history_intro"), "RowHint"))
        # the filters: a map, a level band (the sessions' own)
        maps = sorted({next(iter(s.maps)) for s in sessions if s.maps}, key=lambda m: self._map_name(m).lower())
        bands = sorted({s.level // 10 * 10 for s in sessions if s.level})
        filt = QHBoxLayout()
        filt.setSpacing(8)
        self.map_filter = QComboBox()
        self.map_filter.addItem(self._p(t("trk_all_maps")), "")
        for m in maps:
            self.map_filter.addItem(self._map_name(m), m)
        self.level_filter = QComboBox()
        self.level_filter.addItem(self._p(t("trk_all_levels")), None)
        for b in bands:
            self.level_filter.addItem(self._p(t("loot_spot_levels", lo=max(1, b), hi=b + 9)), b)
        for combo, val in ((self.map_filter, self.__dict__.get("_map_pick", "")),
                           (self.level_filter, self.__dict__.get("_level_pick"))):
            i = combo.findData(val)
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.currentIndexChanged.connect(self._filter_changed)
            filt.addWidget(combo)
        filt.addStretch(1)
        lay.addLayout(filt)
        mpick, lpick = self.map_filter.currentData(), self.level_filter.currentData()
        sessions = [s for s in sessions if (not mpick or (s.maps and next(iter(s.maps)) == mpick))
                    and (lpick is None or (s.level and s.level // 10 * 10 == lpick))]
        if not sessions:
            lay.addWidget(self._label(t("trk_filter_none"), "RowHint"))
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
            top.addWidget(self._label(" · ".join(head), "RowLabel"), 1)
            more = QToolButton(objectName="Link", text=self._p(t("trk_less" if s.id in self._open else "trk_more")))
            more.setCursor(Qt.PointingHandCursor)
            more.clicked.connect(lambda *_, i=s.id: self._toggle(i))
            top.addWidget(more)
            share = QToolButton(objectName="Link", text=self._p(t("trk_share")))
            share.setCursor(Qt.PointingHandCursor)
            share.setToolTip(t("trk_share_tip"))
            share.clicked.connect(lambda *_, x=s: self._share_session(x))
            top.addWidget(share)
            if s.ended is not None:         # (the running session isn't history yet: no Delete)
                armed = self._delete_armed == s.id
                delete = QToolButton(objectName="Link", text=self._p(t("trk_delete_sure" if armed else "trk_delete")))
                delete.setCursor(Qt.PointingHandCursor)
                delete.clicked.connect(lambda *_, i=s.id: self._delete(i))
                top.addWidget(delete)
            cl.addLayout(top)
            ph = lambda v, s=s: s.per_hour(v)  # noqa: E731
            rows = [[t("trk_row_exp"), self._num(s.exp), self._num(ph(s.exp))],
                    [t("trk_row_income"), self._num(s.value), self._num(ph(s.value))],
                    [t("trk_row_spent"), self._num(-s.potions_cost if s.potions_cost else 0),
                     self._num(-ph(s.potions_cost) if s.potions_cost and ph(s.potions_cost) else 0)],
                    [t("trk_row_net"), self._num(s.net), self._num(ph(s.net))]]
            cl.addWidget(self._table([t("trk_col_cat"), t("trk_col_total"), t("trk_col_hour")], rows,
                                     styles={2: "TrkBad", 3: "TrkGood" if s.net >= 0 else "TrkBad"}))
            if s.id in self._open:
                if s.items:
                    heads = [t("trk_c_item"), t("trk_c_qty"), t("trk_c_npc"), t("trk_c_npc_total"), t("trk_c_mw"),
                             t("trk_c_mw_total")]
                    rows, _ = self._item_rows(s.items)
                    cl.addWidget(self._label(t("trk_picked_head"), "SectionHeader"))
                    cl.addWidget(self._table(heads, rows, strong={len(rows) - 1}))
                if s.potion_list:
                    rows = [[(k, n), self._num(c), self._num(p) if p else "–", self._num(-c * p) if p else "–"]
                            for n, c, k, p in s.potion_list]
                    cl.addWidget(self._label(t("loot_potions_head"), "SectionHeader"))
                    cl.addWidget(self._table([t("trk_c_potion"), t("trk_c_used"), t("trk_c_shop"), t("trk_c_cost")],
                                             rows))
            lay.addWidget(card)
        if len(sessions) > self._hist_n:
            lay.addWidget(self._button("loot_history_more", "Secondary", self._more), 0, Qt.AlignLeading)
        lay.addStretch(1)

    def _filter_changed(self, *_) -> None:
        self._map_pick = self.map_filter.currentData()
        self._level_pick = self.level_filter.currentData()
        self._fill_sessions(self.__dict__.get("_all_sessions") or [])

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

    def _share_session(self, s) -> None:
        t = self.t
        when = bidi.ltr_block(time.strftime("%d/%m %H:%M", time.localtime(s.started)), t.rtl)
        mid = next(iter(s.maps)) if s.maps else None
        title = " · ".join(x for x in (t("tracker_title"), when, self._clock(s.seconds),
                                       bidi.ltr_block(self._map_name(mid), t.rtl) if mid else "") if x)
        ph = s.per_hour
        rows = [[t("trk_row_exp"), self._num(s.exp), self._num(ph(s.exp))],
                [t("trk_row_income"), self._num(s.value), self._num(ph(s.value))],
                [t("trk_row_spent"), self._num(-s.potions_cost if s.potions_cost else 0),
                 self._num(-ph(s.potions_cost) if s.potions_cost and ph(s.potions_cost) else 0)],
                [t("trk_row_net"), self._num(s.net), self._num(ph(s.net))]]
        self._share(self._card_image(title, rows, s.items))

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
            self.chip.setObjectName("TagGood" if running else "Tag")
            self.chip.setText(self._p(t("grind_tag_running" if running else "grind_tag_ended")))
            self.chip.style().unpolish(self.chip)
            self.chip.style().polish(self.chip)
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
            if self.__dict__.get("_shared_note"):
                state = self._shared_note       # "Copied: paste it in Discord", for a few seconds
            self.state.setText(self._html(state))
            self._fill_now()
            if self.stack.currentIndex():
                self._fill_history()
            else:
                self._hist_sig = None if self._hist_sig and self._hist_sig[1] != running else self._hist_sig
        except RuntimeError:        # the window closed meanwhile
            pass

    def lines(self) -> list[str]:
        """Every text shown on the current tab (for tests)."""
        page = self.stack.currentWidget().widget()
        return [lb.text() for lb in page.findChildren(QLabel) if lb.isVisibleTo(self)] + \
            [b.text() for b in page.findChildren(QPushButton) if b.isVisibleTo(self)] + [
                b.text() for b in page.findChildren(QToolButton) if b.isVisibleTo(self)]

    def tip(self, text: str) -> str:
        return tip_html(text, self.t.rtl)
