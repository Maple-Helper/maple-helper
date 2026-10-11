"""The little bar over the game: only what is about where the player is now — the NPCs and the monsters on their
map (each button its window's switch), the loot log's window — and a Tools button to the app's own Tools window,
where everything else lives (one place for each thing: the owner's, 2026-10-11). Like the NPCs window it
floats over the game: frameless, always on top, never taking the keyboard from it (clicks still work), dark
glass the minimap reads never see. Its sides are draggable: the buttons wrap into as many rows as the width
leaves room for, the height following, and the width is kept in game_toolbar_width. The ⚙ beside the ✕ opens
the panel with the colours of every window over the game, the bar's own icons mode and the transparency
(_SettingsPanel below). Where the player left it is kept in game_toolbar_pos; with none saved it opens just
above the drawn minimap box."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget

from .. import bidi
from ..i18n import I18n
from ..store import Settings
from . import mapview, theme
from .controls import FlowLayout
from .glass import SHADOW, EdgeResize
from .npcoverlay import (
    ACCENTS,
    FILL1,
    FILL2,
    GAP,
    GEOM_DEBOUNCE_MS,
    MUTED,
    OPACITY_DEFAULT,
    OPACITY_MAX,
    OPACITY_MIN,
    OPACITY_STEPS,
    PANEL,
    TEXT,
    TINTS,
    _flags,
    _paint_glass,
    _picked,
    accent_of,
    apply_look,
    set_capturable,
)
from .regionpick import from_capture

SIDE, TOP, BOTTOM = SHADOW + 12, SHADOW + 8, SHADOW + 8    # content margins, past the glass's shadow rim
GRIP = "⋮⋮"                                               # the drag handle's dots
FLOW_GAP = 6                                              # between the buttons, and between the rows they wrap into
# the windows over the game the bar shows and hides: each button is its window's setting -> its i18n key
WINDOWS = {"npc_overlay": "tb_npcs_here", "monster_overlay": "tb_monsters", "loot_overlay": "tb_loot"}
# the Tools button's menu: straight to these Tools window tabs (one click from the game, each tool still in one
# place: the owner, 2026-10-11), then the whole window
QUICK = ("prices", "route", "scrolls", "gear", "crafting", "quests", "farm", "loot")
# icons mode: each button's emoji, by its i18n key (Segoe UI Emoji draws them in colour on Windows)
ICONS = {"tb_npcs_here": "\U0001F4AC", "tb_monsters": "\U0001F47E", "tb_loot": "\U0001F4DC",
         "tb_tools": "\U0001F6E0\uFE0F"}


def qss(settings) -> str:
    """The bar's (and its ⚙ panel's) own style: the accent that lights a lit button, from the settings, and in
    icons mode the emoji font its buttons show their icons in (rebuilt whenever either is picked)."""
    accent, on_accent = accent_of(settings)
    icons = bool(_picked(settings, "game_toolbar_icons"))
    emoji = 'font-family: "Segoe UI Emoji"; font-size: 15px;' if icons else ""
    return f"""
#Title {{ color: {TEXT}; font-weight: 600; }}
#Status {{ color: {MUTED}; font-size: 11px; font-weight: 600; padding: 0 2px; }}
#Grip {{ color: {MUTED}; font-size: 13px; font-weight: 600; padding: 0 2px; }}
QPushButton {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px;
              padding: 5px 10px; text-align: center; {emoji} }}
QPushButton:hover {{ background: {FILL2}; }}
QPushButton:checked, QPushButton[on="true"] {{ background: {accent}; color: {on_accent}; }}
QToolButton {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
              padding: 2px 7px; font-size: 13px; }}
QToolButton:hover {{ background: {FILL2}; color: {TEXT}; }}
"""


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _swatch_qss(colour: str, on: bool) -> str:
    """A round colour chip: the colour itself, ringed when it is the chosen one."""
    ring = TEXT if on else "rgba(255,255,255,0.30)"
    return (f"QPushButton {{ background: {colour}; border: 2px solid {ring}; border-radius: 11px; padding: 0; }}"
            f"QPushButton:hover {{ border: 2px solid {TEXT}; }}")


class _Grip(QLabel):
    """The dots at the bar's end: dragging them moves it (the buttons around them still click)."""

    def __init__(self):
        super().__init__(GRIP, objectName="Grip")
        self.setCursor(Qt.OpenHandCursor)
        self._grab = None          # (its window is self.window(), not kept)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._grab = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._grab is not None and e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._grab)

    def mouseReleaseEvent(self, e):
        self._grab = None


class _SettingsPanel(QWidget):
    """The bar's ⚙ panel: the colours of every window over the game (the glass tint, the lit buttons' accent),
    the bar's own icons mode and the transparency — in the same dark glass, over the game like the bar. Each
    choice is written, applies at once to every window and stays; ✕, the ⚙ again or Esc closes it (Esc once
    the panel has been clicked into: it takes the keyboard only then, never on opening)."""

    changed = Signal()          # a choice applied: every window over the game follows (app.py wires it)

    def __init__(self, bar):
        # not the bar's own flags: it takes the keyboard once clicked, for Esc
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._bar = bar
        self._settings = bar._settings
        self.t = bar.t
        self.setLayoutDirection(Qt.RightToLeft if self.t.rtl else Qt.LeftToRight)
        self._opacity = OPACITY_DEFAULT
        self.setMinimumWidth(232)
        self.setStyleSheet(qss(self._settings))
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._title = QLabel(objectName="Title")
        self._close = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.clicked.connect(self._close_clicked)
        head.addWidget(self._title, 1)
        head.addWidget(self._close, 0, Qt.AlignTop)
        col.addLayout(head)
        col.addSpacing(4)
        # the glass tint, then the lit buttons' accent: a chip per preset, the chosen one ringed
        self._tint_label = QLabel(objectName="Status")
        col.addWidget(self._tint_label)
        self._tints = self._swatches(TINTS, lambda name: (name, _hex(TINTS[name][1])), self._pick_tint)
        col.addWidget(self._tints)
        self._accent_label = QLabel(objectName="Status")
        col.addWidget(self._accent_label)
        self._accents = self._swatches(ACCENTS, lambda name: (name, ACCENTS[name][1]), self._pick_accent)
        col.addWidget(self._accents)
        col.addSpacing(4)
        # icons instead of words: the one toggle, lit while on (the bar follows at once)
        self._icons = QPushButton(checkable=True)
        self._icons.setCursor(Qt.PointingHandCursor)
        self._icons.clicked.connect(self._pick_icons)
        col.addWidget(self._icons)
        col.addSpacing(4)
        # the transparency: the same steps Settings > Minimap offers, one chip each
        self._opacity_label = QLabel(objectName="Status")
        col.addWidget(self._opacity_label)
        steps = QWidget()
        chips = FlowLayout(steps, spacing=FLOW_GAP)
        chips.setContentsMargins(0, 0, 0, 0)
        self._steps: dict[str, QPushButton] = {}
        for v in OPACITY_STEPS:
            b = QPushButton(f"{round(v * 100)}%", checkable=True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=v: self._pick_opacity(v))
            self._steps[f"{v:g}"] = b
            chips.addWidget(b)
        col.addWidget(steps)
        self.refresh()

    # ------------------------------------------------------------ the panel

    def _swatches(self, presets: dict, colour_of, picked) -> QWidget:
        """A row of round chips, one per preset (a tint or an accent): the colour itself, the chosen one
        ringed; clicking picks it."""
        w = QWidget()
        row = FlowLayout(w, spacing=FLOW_GAP)
        row.setContentsMargins(0, 0, 0, 0)
        for key in presets:
            _, colour = colour_of(key)
            b = QPushButton(checkable=True)
            b.setFixedSize(22, 22)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: picked(k))
            b.setProperty("key", key)        # _style_swatches rings the chosen preset's chip
            b.setProperty("colour", colour)
            row.addWidget(b)
        return w

    def refresh(self) -> None:
        """Every choice back in step with the settings (the Settings dialog may have changed them), and the
        panel's own glass at the transparency they give."""
        t, rtl = self.t, self.t.rtl
        self._title.setText(bidi.plain(t("tbs_title"), rtl))
        self._close.setAccessibleName(t("tbs_close"))
        self._close.setToolTip(t("tbs_close"))
        for lb, key in ((self._tint_label, "tbs_tint"), (self._accent_label, "tbs_accent"),
                        (self._opacity_label, "npc_overlay_opacity")):
            lb.setText(bidi.plain(t(key), rtl))
        self._icons.setText(bidi.plain(t("tbs_icons"), rtl))
        self._icons.setToolTip(t("tbs_icons"))
        self._icons.setAccessibleName(t("tbs_icons"))
        self._icons.setChecked(bool(self._settings["game_toolbar_icons"]))
        sheet = qss(self._settings)
        if sheet != self.styleSheet():       # a freshly picked accent lights this panel's own chips too
            self.setStyleSheet(sheet)
        self._style_swatches()
        try:
            op = float(self._settings["npc_overlay_opacity"])
        except (TypeError, ValueError):
            op = OPACITY_DEFAULT
        self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, op))
        for k, b in self._steps.items():
            b.setChecked(f"{self._opacity:g}" == k)
        self.adjustSize()
        self.update()

    def _style_swatches(self) -> None:
        """Each chip in its own colour, the chosen one ringed."""
        tint = self._settings["overlay_tint"]
        accent = self._settings["overlay_accent"]
        for row, chosen in ((self._tints, tint), (self._accents, accent)):
            for b in row.findChildren(QPushButton):
                on = b.property("key") == chosen
                b.setChecked(on)
                b.setStyleSheet(_swatch_qss(b.property("colour"), on))

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    def place_under(self, btn) -> None:
        """Under the ⚙ that opened it, centred on it, kept on its screen."""
        g = btn.mapToGlobal(QPoint(0, btn.height()))
        screen = QGuiApplication.screenAt(g) or QGuiApplication.primaryScreen()
        room = screen.availableGeometry()
        x = min(max(room.left(), g.x() + btn.width() // 2 - self.width() // 2),
                max(room.left(), room.right() - self.width() + 1))
        y = min(max(room.top(), g.y() + 4), max(room.top(), room.bottom() - self.height() + 1))
        self.move(x, y)

    # ------------------------------------------------------------ the choices

    def _pick_tint(self, key: str) -> None:
        self._settings["overlay_tint"] = key
        self._applied()

    def _pick_accent(self, key: str) -> None:
        self._settings["overlay_accent"] = key
        self._applied()

    def _pick_icons(self, _=False) -> None:
        self._settings["game_toolbar_icons"] = self._icons.isChecked()
        self._applied()

    def _pick_opacity(self, value: float) -> None:
        self._settings["npc_overlay_opacity"] = value
        self._applied()

    def _applied(self) -> None:
        """One choice, on everything at once: written (it stays), the glass tint every _paint_glass paints,
        this panel's and the bar's own style and texts, then every other window over the game follows."""
        apply_look(self._settings)
        self.refresh()
        self._bar.look_followed()
        self.changed.emit()

    def _close_clicked(self, _=False) -> None:
        self.hide()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self.refresh()


class GameToolbar(EdgeResize, QWidget):
    """The bar itself. Each WINDOWS button flips its window's setting (window_toggled: the window follows), Tools
    asks for the app's Tools window (tools_requested), and the ✕ turns the bar itself off. Its sides are dragged
    narrower and wider, the buttons wrapping into rows; the ⚙ opens the panel with the colours, the icons mode
    and the transparency. Clicks work but it never takes the keyboard from the game, and the minimap reads never
    see it. Its place is kept in game_toolbar_pos, its width in game_toolbar_width."""

    window_toggled = Signal(str)       # a WINDOWS setting flipped ("npc_overlay" | "monster_overlay" | "loot_overlay")
    tools_requested = Signal(str)      # the Tools menu: the app's Tools window opens on that tab ("": its own)
    look_changed = Signal()            # a ⚙ panel choice: every window over the game re-styles and repaints

    def __init__(self, settings: Settings, t: I18n):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)          # hovering a side shows the resize cursor
        self._settings = settings
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._opacity = OPACITY_DEFAULT
        self._placed = False                 # geometry applied at least once: there is a place to remember
        self._panel: _SettingsPanel | None = None   # the ⚙ panel, made on the first ask
        self._fitting = False                # our own width/height fit, not the player's drag to remember
        self._width_set = -1                 # the width the settings last gave: a width past it is a drag
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._widths = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember_width)
        self.setStyleSheet(qss(settings))
        self._build()

    # ------------------------------------------------------------ the bar

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(0)
        self._flow = FlowLayout(spacing=FLOW_GAP)
        col.addLayout(self._flow)
        self._grip = _Grip()
        self._flow.addWidget(self._grip)
        self._windows: dict[str, QPushButton] = {}
        for key in WINDOWS:
            b = QPushButton(checkable=True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self._window_clicked(k))
            self._windows[key] = b
            self._flow.addWidget(b)
        self._tools = QPushButton()
        self._tools.setCursor(Qt.PointingHandCursor)
        self._tools.setFocusPolicy(Qt.NoFocus)
        self._tools.clicked.connect(self._tools_clicked)
        self._menu = QMenu(self)
        self._flow.addWidget(self._tools)
        # the ⚙: the panel with the colours, the icons mode and the transparency; then the ✕
        self._gear = QToolButton(objectName="Gear", text=theme.SYMBOL_ICONS["settings"])
        self._gear.setCursor(Qt.PointingHandCursor)
        self._gear.setFocusPolicy(Qt.NoFocus)
        self._gear.clicked.connect(self._gear_clicked)
        self._flow.addWidget(self._gear)
        self._close = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setFocusPolicy(Qt.NoFocus)
        self._close.clicked.connect(self._close_clicked)
        self._flow.addWidget(self._close)
        self._retext()

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    # ------------------------------------------------------------ text (all of it, for apply_language)

    def _tools_clicked(self, _=False) -> None:
        """The Tools button: its menu of tabs, under it (popup, not exec: nothing waits on it, the game goes on)."""
        t, rtl, m = self.t, self.t.rtl, self._menu
        m.clear()
        m.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        m.setStyleSheet(f"QMenu {{ background: rgb{PANEL}; color: {TEXT}; border: 1px solid {FILL2}; border-radius: 8px;"
                        f" padding: 4px; }} QMenu::item {{ padding: 5px 14px; border-radius: 6px; }}"
                        f" QMenu::item:selected {{ background: {FILL2}; }} QMenu::separator {{ height: 1px;"
                        f" background: {FILL1}; margin: 4px 6px; }}")
        for page in QUICK:
            m.addAction(bidi.plain(t("tool_" + page), rtl), lambda p=page: self.tools_requested.emit(p))
        m.addSeparator()
        m.addAction(bidi.plain(t("tb_tools_all"), rtl), lambda: self.tools_requested.emit(""))
        m.popup(self._tools.mapToGlobal(QPoint(0, self._tools.height())))

    def _buttons(self):
        """Every button on the bar with its i18n key: the texts, tooltips and icons follow the language."""
        for key, text in WINDOWS.items():
            yield self._windows[key], text
        yield self._tools, "tb_tools"

    def _retext(self) -> None:
        t, rtl = self.t, self.t.rtl
        icons = bool(self._settings["game_toolbar_icons"])
        self._grip.setAccessibleName(t("tb_move"))
        self._grip.setToolTip(t("tb_move"))
        for b, key in self._buttons():
            name = t(key)
            if icons:
                b.setText(ICONS[key])                    # the name stays in the tooltip, for what the icon is
                b.setToolTip(name)
            else:
                b.setText(bidi.plain(name, rtl).replace("&", "&&"))   # "&" alone: Qt's shortcut mark
                b.setToolTip(t(key + "_tip"))
            b.setAccessibleName(name)
        self._gear.setAccessibleName(t("tbs_title"))
        self._gear.setToolTip(t("tbs_title"))
        self._close.setAccessibleName(t("tb_close"))
        self._close.setToolTip(t("tb_close"))
        self._apply_width()

    # ------------------------------------------------------------ width (the bar wraps)

    def _width_limits(self) -> tuple[int, int]:
        """The narrowest the bar can be — its widest single button — and its natural one-row width."""
        widest, total, n = 0, 0, 0
        for i in range(self._flow.count()):
            item = self._flow.itemAt(i)
            if item.isEmpty():
                continue
            w = FlowLayout._width(item)
            widest, total, n = max(widest, w), total + w, n + 1
        return widest + 2 * SIDE, total + FLOW_GAP * max(0, n - 1) + 2 * SIDE

    def _apply_width(self) -> None:
        """The width the settings give — None: the natural one-row width — clamped to the widest single button
        and to the screen; the height follows the rows that width wraps the buttons into."""
        min_w, natural = self._width_limits()
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        room = screen.availableGeometry().width()
        try:
            want = int(self._settings["game_toolbar_width"])
        except (TypeError, ValueError):
            want = None
        w = min(max(natural if want is None else want, min_w), room)
        self._width_set = w
        self.setMinimumWidth(min_w)
        self.setMaximumWidth(room)
        self._fitting = True
        try:
            self.resize(w, self.layout().totalHeightForWidth(w))
        finally:
            self._fitting = False

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._fitting:
            return
        self._widths.start()               # the player's drag: remembered once it settles
        h = self.layout().totalHeightForWidth(self.width())
        if h > 0 and h != self.height():
            self._fitting = True
            try:
                self.resize(self.width(), h)   # only as tall as the rows this width wraps the buttons into
            finally:
                self._fitting = False

    def _edges_at(self, pos) -> Qt.Edge:
        """Only the bar's sides: its height follows its rows, never dragged."""
        return Qt.Edge(super()._edges_at(pos) & (Qt.LeftEdge | Qt.RightEdge))

    def _remember_width(self) -> None:
        """The dragged width kept in game_toolbar_width (the natural one-row width kept as None, so a bigger
        font still fits); the settings' own width, applied by _apply_width, is not the player's to keep."""
        if not self._placed or self.width() == self._width_set:
            return
        _, natural = self._width_limits()
        self._settings["game_toolbar_width"] = self.width() if self.width() != natural else None

    # ------------------------------------------------------------ the buttons

    def _window_clicked(self, key: str) -> None:
        """A window over the game on or off: the checkable button is its setting (the window follows)."""
        self._settings[key] = self._windows[key].isChecked()
        self.window_toggled.emit(key)

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the Settings switch follows), the bar hidden until it's switched back on."""
        self._settings["game_toolbar"] = False
        self.hide()

    # ------------------------------------------------------------ the ⚙ panel

    def _gear_clicked(self, _=False) -> None:
        """The ⚙: the panel over the game under it, or gone when it is up (its own ✕ and Esc close it too)."""
        if self._panel is None:
            self._panel = _SettingsPanel(self)
            self._panel.changed.connect(self.look_changed.emit)
        if self._panel.isVisible():
            self._panel.hide()
            return
        self._panel.refresh()
        self._panel.place_under(self._gear)
        self._panel.show()
        set_capturable(self._panel, self._settings)

    def look_followed(self) -> None:
        """A choice in the ⚙ panel: the bar's own style, texts (icons mode) and glass follow at once."""
        self._restyle()
        self._retext()
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT
        self.update()

    def _restyle(self) -> None:
        """The accent (and the icons mode's emoji font) the settings pick; skipped when it is the same."""
        sheet = qss(self._settings)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)

    # ------------------------------------------------------------ place and move

    def _place(self) -> None:
        """Where the bar opens: where the player left it, when that is still on a screen (a monitor since
        unplugged: above the minimap box again)."""
        screens = [s.geometry() for s in QGuiApplication.screens()]
        spot = mapview.saved_spot(self._settings["game_toolbar_pos"], self.size(), screens)
        self.setGeometry(QRect(spot if spot is not None else self._default_spot(), self.size()))
        self._placed = True

    def _default_spot(self) -> QPoint:
        """Just above the drawn minimap box (the bar is about the map in it), left edges lined up; else the
        primary screen's top centre."""
        screens = [(s.geometry(), s.devicePixelRatio()) for s in QGuiApplication.screens()]
        region = self._settings["minimap_region"]
        primary = QGuiApplication.primaryScreen().availableGeometry()
        if isinstance(region, dict):
            try:
                box, _ = from_capture(region, screens, scale=sys.platform != "darwin")
            except (KeyError, TypeError, ValueError):
                box = None
            if box is not None:
                screen = next((g for g, _ in screens if g.intersects(box)), primary)
                x = min(max(box.left(), screen.left()), screen.right() - self.width() + 1)
                y = min(max(box.top() - self.height() - GAP, screen.top()),
                        max(screen.top(), screen.bottom() - self.height() + 1))
                return QPoint(x, y)
        return QPoint(primary.center().x() - self.width() // 2, primary.top() + SHADOW + 4)

    def moveEvent(self, e) -> None:
        self._geom.start()
        super().moveEvent(e)

    def hideEvent(self, e) -> None:
        if self._placed:             # turned off or the app closing: keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
            self._widths.stop()
            self._remember_width()
        if self._panel is not None:
            self._panel.hide()
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self._settings["game_toolbar_pos"] = {"x": self.x(), "y": self.y()}

    # ------------------------------------------------------------ the rest of the bar's life

    def refresh(self) -> None:
        """Show or hide from the setting, each window's button back in step with the window's own (its ✕ turned
        it off; the Settings switch may have turned it on), the width the settings give (its buttons wrapping
        into rows), and the glass at the opacity the player picked."""
        self._restyle()
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT
        for key, b in self._windows.items():
            b.setChecked(bool(self._settings[key]))
        if not self._settings["game_toolbar"]:
            if self.isVisible():
                self.hide()
            return
        self._retext()                 # icons or words, as Settings may have just switched (it fits the width too)
        if not self.isVisible():
            self._place()
            self.show()
            set_capturable(self, self._settings)
        if self._panel is not None and self._panel.isVisible():
            self._panel.refresh()
        self.update()

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        if self._panel is not None:
            self._panel.apply_language(t)
        self._retext()
