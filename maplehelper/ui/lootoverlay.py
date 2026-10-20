"""The loot log over the game: a window of its own (not inside a Maple Helper window) with the log's time, EXP, mesos
and items (ETC on its own), the potions used and the net profit, each per hour, the latest pickups, and its buttons:
Start, or Stop and Reset while a log runs, and in its title row a – that folds it to that row and a ✕ that hides it.

It is built like the other windows over the game (the NPCs here window, the searches): the same glass at the
opacity the player picked for them (npc_overlay_opacity), the title row that drags it, muted status text and the
same rounded buttons. It shows while settings["loot_overlay"] is on: the bar's Loot log button and the Loot log
page's switch flip it, its ✕ turns it off, and a log starting (from anywhere) turns it on. A stopped log stays in
it, to read what it made. It never takes the keyboard from the game and keeps its place ("loot_overlay_pos") and its
fold ("loot_overlay_folded"). Like the others it stays out of screen captures unless "Show the overlays in
screenshots" is on (its lines never read as pickup messages anyway: no "received", "gained" or "earned" in them)."""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout,
                               QWidget)

from .. import bidi, lootlog
from ..i18n import I18n
from . import theme
from .glass import SHADOW
from .npcoverlay import (BOTTOM, GEOM_DEBOUNCE_MS, OPACITY_DEFAULT, OPACITY_MAX, OPACITY_MIN, SIDE, TOP, _Header,
                         _flags, _paint_glass, accent_of, keep_off_minimap, look_palette, set_capturable)

WIDTH = 280 + 2 * SHADOW        # the panel 280 px wide, past its shadow rim
FEED = 4                        # the latest pickups shown under the totals
STATS = ("exp", "mesos", "etc", "items", "potions", "net")

def qss(settings) -> str:
    """The window's own style at the look the settings pick: its text and fills in the glass's palette, the Go
    button lit in the accent. Rebuilt whenever the look is picked or the app's appearance changes (refresh
    follows it), so the window follows with no more code of its own."""
    c = look_palette(settings)
    accent, on_accent = accent_of(settings)
    return f"""
QLabel {{ color: {c['text']}; }}
#Title {{ color: {c['text']}; font-weight: 600; }}
#Status {{ color: {c['muted']}; }}
#Head {{ color: {c['muted']}; font-size: 11px; font-weight: 600; padding: 6px 2px 0 2px; }}
#Stat {{ background: {c['fill1']}; border-radius: 8px; }}
#StatName {{ color: {c['muted']}; }}
#StatValue {{ color: {c['text']}; font-weight: 600; }}
#Feed {{ color: {c['muted']}; padding: 0 2px; }}
QPushButton {{ background: {c['fill1']}; color: {c['text']}; border: none; border-radius: 8px; padding: 6px 12px; }}
QPushButton:hover {{ background: {c['fill2']}; }}
QPushButton#Go {{ background: {accent}; color: {on_accent}; font-weight: 600; }}
QToolButton {{ background: transparent; color: {c['muted']}; border: none; border-radius: 6px;
              padding: 2px 7px; font-size: 13px; }}
QToolButton:hover {{ background: {c['fill2']}; color: {c['text']}; }}
"""


def _short(n: int | None) -> str:
    if n is None:
        return "–"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 100_000:
        return f"{n / 1000:.1f}K"
    return f"{n:,}"


class LootOverlay(QWidget):
    closed = Signal()               # the ✕ turned the setting off: the bar's Loot log button unchecks
    full_requested = Signal()       # "Full tracker": the grind tracker's own window (ui/grindtracker.py)

    def __init__(self, scanner, settings, kb, t: I18n | None = None):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("Maple Helper")
        self.setFixedWidth(WIDTH)
        self.scanner, self.settings, self.kb = scanner, settings, kb
        self.t = t or I18n(settings["language"] or "he")
        self.setLayoutDirection(Qt.RightToLeft if self.t.rtl else Qt.LeftToRight)
        self._opacity = OPACITY_DEFAULT
        self._placed = False                 # shown at least once: there is a place to remember
        self._was_running = scanner.running(scanner.cid())
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self.setStyleSheet(qss(self.settings))
        self._build()
        # the clock and the per-hour figures move between pickups too
        self._clock = QTimer(self, interval=5000, timeout=lambda: self.isVisible() and self.refresh())
        self._clock.start()

    # ---------------------------------------------------------------- the panel

    def _tool(self, icon: str, then) -> QToolButton:
        b = QToolButton(text=theme.SYMBOL_ICONS[icon])
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(then)
        return b

    def _button(self, then, name: str = "") -> QPushButton:
        b = QPushButton(objectName=name)
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(then)
        return b

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(6)
        head = _Header()
        hrow = QHBoxLayout(head)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(4)
        self._title = QLabel(objectName="Title")
        self._fold = self._tool("minimize", self._fold_clicked)
        self._close = self._tool("close", self._close_clicked)
        hrow.addWidget(self._title, 1)
        hrow.addWidget(self._fold, 0, Qt.AlignTop)
        hrow.addWidget(self._close, 0, Qt.AlignTop)
        col.addWidget(head)
        self._body = QWidget()
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        self._status = QLabel(objectName="Status")      # not running / stopped / no game / covered
        self._status.setWordWrap(True)
        body.addWidget(self._status)
        # the figures as a table: each category, so far and per hour, the numbers under their headings
        self._table = QFrame(objectName="Stat")
        grid = QGridLayout(self._table)
        grid.setContentsMargins(10, 6, 10, 6)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        self._heads = [QLabel(objectName="StatName") for _ in range(3)]
        for c, lb in enumerate(self._heads):
            lb.setAlignment(Qt.AlignVCenter | (Qt.AlignCenter if c else Qt.AlignLeading))
            grid.addWidget(lb, 0, c)
        self._stats: dict[str, tuple[QLabel, QLabel, QLabel]] = {}
        for r, key in enumerate(STATS, start=1):
            name, total, hour = QLabel(objectName="StatName"), QLabel(objectName="StatValue"), QLabel(objectName="StatValue")
            for c, lb in enumerate((name, total, hour)):
                lb.setAlignment(Qt.AlignVCenter | (Qt.AlignCenter if c else Qt.AlignLeading))
                grid.addWidget(lb, r, c)
            self._stats[key] = (name, total, hour)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        body.addWidget(self._table)
        self._feed_head = QLabel(objectName="Head")
        body.addWidget(self._feed_head)
        self._feed = [QLabel(objectName="Feed") for _ in range(FEED)]
        for lb in self._feed:
            body.addWidget(lb)
        btns = QHBoxLayout()
        btns.setContentsMargins(0, 4, 0, 0)
        btns.setSpacing(6)
        self._start = self._button(self._start_clicked, "Go")
        self._stop = self._button(self._stop_clicked)
        self._reset = self._button(self._reset_clicked)
        for b in (self._start, self._stop, self._reset):
            btns.addWidget(b, 1)
        body.addLayout(btns)
        self._full = self._button(self.full_requested.emit)
        body.addWidget(self._full)
        col.addWidget(self._body)

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    # ---------------------------------------------------------------- what it shows

    def folded(self) -> bool:
        return bool(self.settings["loot_overlay_folded"])

    def lines(self) -> list[str]:
        """Every text it shows now, top to bottom (title, status, the figures, the latest pickups)."""
        out = [self._title.text()]
        if self._status.isVisibleTo(self):
            out.append(self._status.text())
        for key in STATS:
            name, total, hour = self._stats[key]
            if name.isVisibleTo(self):
                out.append(f"{name.text()} {total.text()} {hour.text()}")
        return out + [lb.text() for lb in self._feed if lb.isVisibleTo(self)]

    def refresh(self) -> None:
        self._restyle()
        t, rtl = self.t, self.t.rtl
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self.settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT
        cid = self.scanner.cid()
        lg = self.scanner.log_for(cid)
        running = self.scanner.running(cid)
        sm = lootlog.summarize(self.kb, lg) if lg is not None else None
        num = lambda v: bidi.ltr_block(_short(v), rtl)      # a number stays one block beside Hebrew words
        title = t("tool_loot")
        if sm is not None and self.folded():
            title += " · " + t("loot_card_made", v=num(sm.net))       # folded: what it made, nothing longer
        elif sm is not None:
            m = int(sm.seconds) // 60
            title += " · " + (t("grind_hours", h=m // 60, m=f"{m % 60:02d}") if m >= 60 else t("grind_minutes", m=m))
        self._title.setText(bidi.plain(title, rtl))
        state = self.scanner.state
        status = ("loot_overlay_idle" if sm is None else
                  "loot_card_stopped" if not running else
                  f"loot_{state}" if state in ("no_game", "covered") else "")
        self._status.setText(bidi.plain(t(status), rtl) if status else "")
        self._status.setVisible(bool(status))
        ph = (lambda v: lootlog.per_hour(v, sm.seconds)) if sm is not None else (lambda v: None)
        neg = lambda v: num(-v) if v else num(0)  # noqa: E731
        values = {} if sm is None else {
            "exp": (num(sm.exp), num(sm.exp_h)),
            "mesos": (num(sm.mesos), num(sm.mesos_h)),
            "etc": (num(sm.etc_value), num(ph(sm.etc_value))),
            "items": (num(sm.other_value), num(ph(sm.other_value))),
            "potions": (neg(sm.potions_cost), neg(ph(sm.potions_cost) or 0) if sm.seconds >= 60 else num(None)),
            "net": (num(sm.net), num(sm.net_h))}
        self._table.setVisible(sm is not None)
        for c, key in enumerate(("", "trk_col_total", "trk_col_hour")):
            self._heads[c].setText(bidi.plain(t(key), rtl) if key else "")
        for key in STATS:
            name, total, hour = self._stats[key]
            name.setText(bidi.plain(t(f"loot_card_{key}"), rtl))
            got = values.get(key, ("", ""))
            total.setText(bidi.plain(got[0], rtl))
            hour.setText(bidi.plain(got[1], rtl))
        evs = lg.events[-FEED:][::-1] if lg is not None else []
        self._feed_head.setText(bidi.plain(t("loot_feed_head"), rtl))
        self._feed_head.setVisible(bool(evs))
        for i, lb in enumerate(self._feed):
            if i < len(evs):
                e = evs[i]
                text = (t(f"loot_feed_{e.kind}", name=bidi.ltr_block(e.name, rtl), n=f"{e.amount:,}")
                        if e.kind in ("item", "potion") else t(f"loot_feed_{e.kind}", n=f"{e.amount:,}"))
                lb.setText(bidi.plain(text, rtl))
            lb.setVisible(i < len(evs))
        self._start.setVisible(not running)
        self._stop.setVisible(running)
        self._reset.setVisible(running)
        for b, key in ((self._start, "loot_start"), (self._stop, "loot_card_stop"), (self._reset, "loot_card_reset")):
            b.setText(bidi.plain(t(key), rtl))
        self._reset.setToolTip(t("loot_card_reset_tip"))
        self._full.setText(bidi.plain(t("loot_card_full") + " ↗", rtl))
        self._full.setToolTip(t("loot_card_full_tip"))
        self._stop.setToolTip(t("loot_card_stop_tip"))
        fold = self.folded()
        self._body.setVisible(not fold)
        self._fold.setText(theme.SYMBOL_ICONS["add" if fold else "minimize"])
        for b, key in ((self._fold, "loot_card_unfold" if fold else "loot_card_fold"), (self._close, "loot_card_close")):
            b.setAccessibleName(t(key))
            b.setToolTip(t(key))
        self.layout().activate()
        self.setFixedHeight(self.layout().sizeHint().height())
        self.update()

    def _restyle(self) -> None:
        """The accent the settings pick, in the window's own style sheet (its glass is painted in the tint by
        _paint_glass); skipped when it is the same — this refreshes every few seconds while a log runs."""
        sheet = qss(self.settings)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)


    def sync(self) -> None:
        """Shown while settings["loot_overlay"] is on; a log that just started turns it on."""
        running = self.scanner.running(self.scanner.cid())
        if running and not self._was_running and not self.settings["loot_overlay"]:
            self.settings["loot_overlay"] = True
        self._was_running = running
        want = bool(self.settings["loot_overlay"])
        if want and not self.isVisible():
            self.show_on_game()
        elif want:
            self.refresh()
        elif self.isVisible():
            self.hide()

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self.refresh()

    # ---------------------------------------------------------------- the buttons

    def _start_clicked(self, _=False) -> None:
        self.scanner.start()

    def _stop_clicked(self, _=False) -> None:
        """The log ends (saved in Recent logs and the database); its totals stay here to read."""
        self.scanner.end()

    def _reset_clicked(self, _=False) -> None:
        """Back to zero: the running log ends (kept, as Stop keeps it) and a new one starts at once."""
        self.scanner.start()

    def _fold_clicked(self, _=False) -> None:
        self.settings["loot_overlay_folded"] = not self.folded()
        self.refresh()

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the bar's button and the Loot log page's switch follow), hidden until switched
        back on. A running log goes on counting."""
        self.settings["loot_overlay"] = False
        self.hide()
        self.closed.emit()
        self.scanner.changed.emit()

    # ---------------------------------------------------------------- show / place

    def show_on_game(self) -> None:
        self.refresh()
        pos = self.settings["loot_overlay_pos"]
        if isinstance(pos, dict) and all(isinstance(pos.get(k), int) for k in ("x", "y")) and self._on_screen(pos):
            self.move(pos["x"], pos["y"])
        else:
            scr = QGuiApplication.primaryScreen().availableGeometry()
            self.move(scr.right() - WIDTH - 40, scr.top() + 160)
        self._placed = True
        self.show()
        set_capturable(self, self.settings)

    def _on_screen(self, pos: dict) -> bool:
        r = QRect(pos["x"], pos["y"], WIDTH, max(40, self.height()))
        return any(s.availableGeometry().intersects(r) for s in QGuiApplication.screens())

    def moveEvent(self, e) -> None:
        if keep_off_minimap(self, self.settings):
            return                  # moved again, off the minimap box: that move's own event follows
        self._geom.start()
        super().moveEvent(e)

    def hideEvent(self, e) -> None:
        if self._placed:             # hidden or the app closing: keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self.settings["loot_overlay_pos"] = {"x": self.x(), "y": self.y()}
