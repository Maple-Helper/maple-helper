"""The box that holds the bar's show/hide key (game_toolbar_hotkey), in Settings > Minimap and in the bar's ⚙ panel.

It shows the key ("Shift+F11"; "None" in grey when there is none). A click listens for the next key with its
modifiers: Esc, a second click or a click elsewhere keeps the key it had, and a key the bar can't use says why in
the box while it goes on listening (keys.usable: an F-key needs a modifier, a letter Ctrl or Alt). The small ✕
beside it takes the key away.

While a box listens, CAPTURE says so: the app lets go of the key it holds meanwhile, or pressing that same key
again would show or hide the bar instead of reaching the box (a hotkey reaches no window)."""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QToolButton, QWidget

from .. import bidi, keys
from ..i18n import I18n
from . import theme
from .npcoverlay import FILL1, FILL2, MUTED, TEXT

HINT_MS = 1600                  # a refused key's reason stays in the box this long, then it asks again


class _Capture(QObject):
    listening = Signal(bool)    # a box started (True) or stopped (False) listening for a key


CAPTURE = _Capture()

_MODIFIER_KEYS = {Qt.Key_Shift: "Shift", Qt.Key_Control: "Ctrl", Qt.Key_Alt: "Alt", Qt.Key_Meta: "Meta",
                  Qt.Key_AltGr: "Alt"}
_MODIFIERS = ((Qt.ControlModifier, "Ctrl"), (Qt.AltModifier, "Alt"), (Qt.ShiftModifier, "Shift"),
              (Qt.MetaModifier, "Meta"))
_NAMED = {Qt.Key_Insert: "Ins", Qt.Key_Delete: "Del", Qt.Key_Home: "Home", Qt.Key_End: "End",
          Qt.Key_PageUp: "PgUp", Qt.Key_PageDown: "PgDown"}


def pressed(e) -> tuple[frozenset[str], str | None]:
    """The modifiers a key press holds and its key as keys.py names it: None while only modifiers are down, "" for
    a key no hotkey can be. By Qt's key, else by the key's native code (Shift+1 is "!" to Qt)."""
    mods = frozenset(name for flag, name in _MODIFIERS if e.modifiers() & flag)
    k = e.key()
    if k in _MODIFIER_KEYS:
        return mods, None
    if Qt.Key_F1 <= k <= Qt.Key_F24:
        return mods, f"F{k - Qt.Key_F1 + 1}"
    if Qt.Key_A <= k <= Qt.Key_Z or Qt.Key_0 <= k <= Qt.Key_9:
        return mods, chr(k)
    return mods, _NAMED.get(k) or keys.from_native(e.nativeVirtualKey()) or ""


def _held(mods) -> str:
    """The modifiers held so far, before the key: "Ctrl+Shift+…" (on a Mac "⌃⇧…")."""
    if sys.platform == "darwin":
        return "".join(sym for m, sym in keys.MAC_SYMBOLS.items() if m in mods) + "…"
    return "+".join(["Win" if m == "Meta" else m for m in keys.MODIFIERS if m in mods] + ["…"])


def _qss(glass: bool, accent: str) -> str:
    """The box in the app's own look (Settings), or in the dark glass over the game (the ⚙ panel, its accent)."""
    if glass:
        return f"""
QPushButton#HotkeyBox {{ background: {FILL1}; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px;
                         min-height: 28px; max-height: 28px; padding: 0 12px; color: {TEXT}; font-size: 12px;
                         font-weight: 600; }}
QPushButton#HotkeyBox:hover {{ background: {FILL2}; }}
QPushButton#HotkeyBox[empty="true"] {{ color: {MUTED}; font-weight: 500; }}
QPushButton#HotkeyBox[listening="true"] {{ border: 1px solid {accent}; color: {TEXT}; font-weight: 500; }}
QToolButton#HotkeyClear {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
                           padding: 2px 6px; font-size: 11px; }}
QToolButton#HotkeyClear:hover {{ background: {FILL2}; color: {TEXT}; }}
"""
    c = theme.P()
    return f"""
QPushButton#HotkeyBox {{ background: {c['fill2']}; border: 1px solid {c['stroke']}; border-radius: 8px;
                         min-height: 28px; max-height: 28px; padding: 0 12px; color: {c['text']}; font-weight: 600; }}
QPushButton#HotkeyBox:hover {{ background: {c['fill3']}; }}
QPushButton#HotkeyBox[empty="true"] {{ color: {c['muted']}; font-weight: 500; }}
QPushButton#HotkeyBox[listening="true"] {{ border: 1px solid {accent}; color: {c['muted']}; font-weight: 500; }}
"""


class HotkeyBox(QWidget):
    """The key in its box, and the ✕ that takes it away. changed(name) when the player picks a key, "" when they
    clear it; set_key() puts one in without it (the setting changed elsewhere)."""

    changed = Signal(str)

    def __init__(self, key: str, t: I18n, glass: bool = False):
        super().__init__()
        self.t = t
        self._glass = glass
        self._key = key or ""
        self._accent = theme.ORANGE
        self._listening = False
        self._mods: frozenset[str] = frozenset()      # held while listening, shown before the key comes
        self._refused = ""                              # the i18n key of why the last key can't be used
        self._label = ""
        self._hint = QTimer(self, singleShot=True, interval=HINT_MS, timeout=self._hint_done)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.box = QPushButton(objectName="HotkeyBox")
        self.box.setCursor(Qt.PointingHandCursor)
        self.box.setFocusPolicy(Qt.ClickFocus)
        self.box.clicked.connect(self._clicked)
        self.box.installEventFilter(self)
        if glass:
            self.clear = QToolButton(objectName="HotkeyClear", text=theme.SYMBOL_ICONS["close"])
            self.clear.setCursor(Qt.PointingHandCursor)
        else:
            self.clear = theme.dismiss_button()
        self.clear.setFocusPolicy(Qt.NoFocus)
        keep = self.clear.sizePolicy()
        keep.setRetainSizeWhenHidden(True)          # the box doesn't jump when the ✕ goes
        self.clear.setSizePolicy(keep)
        self.clear.clicked.connect(self._cleared)
        lay.addWidget(self.box)
        lay.addWidget(self.clear)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setStyleSheet(_qss(glass, self._accent))
        self.retext(t)

    # ------------------------------------------------------------ the key

    def key(self) -> str:
        return self._key

    def set_key(self, key: str) -> None:
        """The key the setting holds now (changed elsewhere: Settings saved, the ⚙ panel), without changed()."""
        key = key or ""
        if key != self._key:
            self._key = key
            self._show()

    def listening(self) -> bool:
        return self._listening

    def set_accent(self, colour: str) -> None:
        """The ring while it listens in the windows' accent (the ⚙ panel's pick); restyled only on a change."""
        if colour != self._accent:
            self._accent = colour
            self.setStyleSheet(_qss(self._glass, colour))

    def set_label(self, label: str) -> None:
        """What the box is for, read with the key it holds (Section.add_row passes the row's label)."""
        self._label = label
        self._name()

    def retext(self, t: I18n) -> None:
        self.t = t
        self.box.setToolTip(t("tb_hotkey_box_tip"))
        self.clear.setToolTip(t("tb_hotkey_clear"))
        self.clear.setAccessibleName(t("tb_hotkey_clear"))
        self._show()

    # ------------------------------------------------------------ listening

    def _clicked(self, _=False) -> None:
        if self._listening:
            self._stop()                            # a second click: the key stays as it was
            return
        self._listening = True
        self._mods, self._refused = frozenset(), ""
        self.window().activateWindow()              # the panel over the game takes the keyboard only once clicked
        self.box.setFocus(Qt.MouseFocusReason)
        CAPTURE.listening.emit(True)
        self._show()

    def _stop(self) -> None:
        if not self._listening:
            return
        self._listening = False
        self._hint.stop()
        self._mods, self._refused = frozenset(), ""
        CAPTURE.listening.emit(False)
        self._show()

    def _cleared(self, _=False) -> None:
        self._stop()
        if self._key:
            self._key = ""
            self._show()
            self.changed.emit("")

    def eventFilter(self, obj, e) -> bool:
        if obj is self.box and self._listening:
            kind = e.type()
            if kind == QEvent.ShortcutOverride:
                e.accept()                          # no shortcut of the window takes the keys meanwhile
                return True
            if kind == QEvent.KeyPress:
                self._key_pressed(e)
                return True
            if kind == QEvent.KeyRelease:
                if not e.isAutoRepeat() and e.key() in _MODIFIER_KEYS:
                    self._mods = self._mods - {_MODIFIER_KEYS[e.key()]}
                    self._show()
                return True
            if kind == QEvent.FocusOut:
                self._stop()                        # clicked elsewhere: the key stays as it was
        return super().eventFilter(obj, e)

    def _key_pressed(self, e) -> None:
        mods, key = pressed(e)
        if key is None:                             # a modifier alone, so far: the box shows what is held
            self._mods, self._refused = mods, ""
            self._hint.stop()
            self._show()
            return
        if e.key() == Qt.Key_Escape and not mods:   # Esc: the key stays as it was
            self._stop()
            return
        name = keys.join(mods, key) if key else ""
        if not keys.usable(name):
            reserved = key == keys.RESERVED["mac" if sys.platform == "darwin" else "win"]
            self._refused = "tb_hotkey_other" if not key or reserved else "tb_hotkey_add_mod"
            self._mods = frozenset()
            self._hint.start()
            self._show()
            return
        if name != self._key:
            # said before listening ends: the ⚙ panel's key is bound first, so the app doesn't put the old one back
            # for a moment (and toast it, were it the very key the player is moving away from)
            self._key = name
            self.changed.emit(name)
        self._stop()

    def _hint_done(self) -> None:
        self._refused = ""
        self._show()

    def hideEvent(self, e) -> None:
        self._stop()            # its window closed while it listened: the app takes its key back
        super().hideEvent(e)

    # ------------------------------------------------------------ what it shows

    def _text(self) -> str:
        t, rtl = self.t, self.t.rtl
        if self._listening:
            if self._refused:
                return bidi.plain(t(self._refused), rtl)
            return _held(self._mods) if self._mods else bidi.plain(t("tb_hotkey_press"), rtl)
        return keys.label(self._key) if self._key else bidi.plain(t("tb_hotkey_none"), rtl)

    def _show(self) -> None:
        text = self._text()
        self.box.setText(text)
        # as wide as its widest text, so the row doesn't jump while it listens
        self.box.ensurePolished()               # measured in the style's font, not the default one
        fm = self.box.fontMetrics()
        widest = max(fm.horizontalAdvance(s) for s in (text, keys.label(self._key), self.t("tb_hotkey_press"),
                                                       self.t("tb_hotkey_none")))
        self.box.setMinimumWidth(widest + 30)
        for prop, on in (("listening", self._listening), ("empty", not self._key and not self._listening)):
            if self.box.property(prop) != on:
                self.box.setProperty(prop, on)
                self.box.style().unpolish(self.box)
                self.box.style().polish(self.box)
        self.clear.setVisible(bool(self._key) and not self._listening)
        self._name()

    def _name(self) -> None:
        shown = self.box.text()
        self.box.setAccessibleName(f"{self._label}: {shown}" if self._label else shown)
