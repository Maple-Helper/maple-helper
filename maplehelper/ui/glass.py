"""A liquid-glass backdrop that works even with the system's transparency effects off.

The overlay excludes itself from screen capture, samples what is behind it
(the game), and repaints that as a frosted, color-saturated material a few
times a second. Sampling happens at quarter resolution, so it stays cheap.
"""
from __future__ import annotations

from PIL import ImageEnhance, ImageFilter
from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap

from .. import osapi
from ..i18n import I18n

SCALE = 0.25          # sample at quarter resolution
BLUR = 7              # at quarter scale ≈ 28px of real blur
SATURATION = 1.7      # iOS-style vibrancy: blurred colors get richer, not muddier
BRIGHTNESS = 0.78
INTERVAL_MS = 120


class GlassBackdrop(QObject):
    updated = Signal()

    def __init__(self, widget):
        super().__init__(widget)
        self.pixmap: QPixmap | None = None
        self.timer = QTimer(self, interval=INTERVAL_MS, timeout=self.refresh)

    def start(self):
        self.refresh()
        self.timer.start()

    def stop(self):
        self.timer.stop()

    @property
    def widget(self):
        # the window it's the backdrop of, asked from Qt, not kept: a reference here and the window's to this made a
        # cycle, so a closed dialog was freed only when Python's garbage collector next ran, on whichever thread
        # that was, deleting its widgets under whatever used them then (seen as "Internal C++ object already
        # deleted" in a test)
        return self.parent()

    def refresh(self):
        w = self.widget
        if not w.isVisible():
            return
        dpr = w.devicePixelRatioF() if osapi.SCREEN_COORDS_ARE_PHYSICAL else 1.0
        g = w.geometry()
        try:
            img = osapi.grab_screen(round(g.x() * dpr), round(g.y() * dpr), round(g.width() * dpr),
                                     round(g.height() * dpr))
        except Exception:
            return
        small = img.resize((max(1, int(img.width * SCALE)), max(1, int(img.height * SCALE))))
        small = small.filter(ImageFilter.GaussianBlur(BLUR))
        small = ImageEnhance.Color(small).enhance(SATURATION)
        small = ImageEnhance.Brightness(small).enhance(BRIGHTNESS)
        data = small.tobytes("raw", "RGB")
        qimg = QImage(data, small.width, small.height, small.width * 3, QImage.Format_RGB888).copy()
        self.pixmap = QPixmap.fromImage(qimg)
        self.updated.emit()
        w.update()


# ---------------------------------------------------------------- shared painting

from PySide6.QtCore import QMetaMethod, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QApplication, QDialog, QHBoxLayout,  # noqa: E402
                               QLabel, QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget)

from . import theme  # noqa: E402

SHADOW = 12


def glass_path(widget, radius: float = None) -> QPainterPath:
    r = theme.RADIUS if radius is None else radius
    path = QPainterPath()
    path.addRoundedRect(QRectF(widget.rect()).adjusted(SHADOW + 0.5, SHADOW + 0.5, -SHADOW - 0.5, -SHADOW - 0.5), r, r)
    return path


def paint_glass(widget, backdrop: "GlassBackdrop | None", strength: float = 0.6, radius: float = None) -> None:
    """The one material every window uses: soft shadow, blurred backdrop, neutral tint, sheen, rim.
    strength (0.4–1.0) scales the tint: higher = more opaque, easier to read over busy scenes."""
    c = theme.P()
    r = theme.RADIUS if radius is None else radius
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    for i in range(SHADOW, 0, -2):
        sh = QPainterPath()
        sh.addRoundedRect(QRectF(widget.rect()).adjusted(SHADOW - i, SHADOW - i + 3, -(SHADOW - i), -(SHADOW - i) + 3),
                          r + i, r + i)
        p.fillPath(sh, QColor(0, 0, 0, int(26 * (1 - i / SHADOW)) + 2))
    path = glass_path(widget, r)
    p.save()
    p.setClipPath(path)
    live = backdrop is not None and backdrop.pixmap is not None
    if live:
        p.drawPixmap(widget.rect(), backdrop.pixmap)
    base = c["glass_alpha"] if live else c["solid_alpha"]
    tint = QColor(*c["glass"])
    if live:
        # strength 0.6 = the designed glass; toward 1.0 more solid (readability), toward 0.4 clearer
        s = max(0.4, min(1.0, strength))
        base = base + (1 - base) * (s - 0.6) / 0.4 if s >= 0.6 else base * s / 0.6
    tint.setAlphaF(base)
    p.fillPath(path, tint)
    sheen = QLinearGradient(0, SHADOW, 0, SHADOW + min(170, widget.height()))
    sheen.setColorAt(0.0, QColor(255, 255, 255, c["sheen"]))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillPath(path, sheen)
    p.restore()
    rim = QLinearGradient(0, SHADOW, 0, widget.height() - SHADOW)
    rim.setColorAt(0.0, QColor(255, 255, 255, c["rim_top"]))
    rim.setColorAt(0.4, QColor(255, 255, 255, c["rim"]))
    rim.setColorAt(1.0, QColor(255, 255, 255, c["rim"] // 2))
    p.setPen(QPen(rim, 1))
    p.drawPath(path)
    p.end()


class _DragBar(QWidget):
    def __init__(self):
        super().__init__()
        self._grab = None          # (its window is self.window(), not kept: see GlassBackdrop.widget)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._grab = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._grab is not None and e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._grab)

    def mouseReleaseEvent(self, e):
        self._grab = None


SCREEN_MARGIN = 48        # room kept free above and below a window that would not fit the screen


def no_default_buttons(root) -> None:
    """Qt makes every push button in a dialog an "auto default": Enter anywhere then clicks the first one
    (a tab chip, "Back"). Turn that off; a window names its own Enter button (GlassDialog.enter_button)."""
    for b in root.findChildren(QPushButton):
        b.setAutoDefault(False)
        b.setDefault(False)


def first_control(root):
    """The first widget inside `root` that Tab would reach (visible, enabled), in focus-chain order."""
    w = root.nextInFocusChain()
    for _ in range(2000):
        if w is None or w is root:
            return None
        # (a scroll area takes Tab focus too, but it's no control)
        if (root.isAncestorOf(w) and w.isVisible() and w.isEnabled() and not isinstance(w, QAbstractScrollArea)
                and w.focusPolicy().value & Qt.TabFocus.value):
            return w
        w = w.nextInFocusChain()
    return None


def _handles_enter(w) -> bool:
    """A text field that does something of its own on Enter (search, next step, check the key)."""
    return isinstance(w, QLineEdit) and w.isSignalConnected(QMetaMethod.fromSignal(w.returnPressed))


class _TabKeys(QObject):
    """When the player last pressed Tab (app-wide): the focus moving by a real Tab, not by a deleted control."""
    _at = 0.0
    _me = None
    _focused = None            # the control the player's last Tab put the focus on (by_keyboard)

    @classmethod
    def watch(cls) -> None:
        app = QApplication.instance()
        if cls._me is None and app is not None:
            cls._me = cls(app)
            app.installEventFilter(cls._me)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyPress and e.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            import time
            _TabKeys._at = time.monotonic()
        elif e.type() == QEvent.FocusIn and isinstance(obj, QWidget):     # (the style sees a copy of it too)
            # a Tab the player pressed, not a focus handed on by a deleted control or set by the window itself
            by_tab = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason) and _TabKeys.pressed_just_now()
            _TabKeys._focused = obj if by_tab else None
        return False

    @classmethod
    def by_keyboard(cls, w) -> bool:
        """The player Tabbed to this control (Enter then clicks it, not the window's own Enter button)."""
        return w is not None and w is cls._focused and w.hasFocus()

    @classmethod
    def pressed_just_now(cls) -> bool:
        import time
        return time.monotonic() - cls._at < 0.5


class GlassDialog(QDialog):
    """Frameless glass window with the app's own title bar (title + close). Put content in self.content."""

    esc_closes = True          # False: Esc does nothing (it must not quit onboarding or drop unsaved settings)
    enter_button = None        # the button Enter clicks when no text field handles it (None: Enter does nothing)

    def __init__(self, title: str, rtl: bool, show_in_captures: bool = False, closable: bool = True,
                 strength: float = 0.6):
        # on top like the chat and Settings, or a confirmation opened from Settings hides behind it
        super().__init__(None, Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_AlwaysShowToolTips)      # tooltips while the game is the active window too
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(title)
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        self._show_in_captures = show_in_captures
        self._strength = strength
        self.backdrop = GlassBackdrop(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(SHADOW + 18, SHADOW + 10, SHADOW + 18, SHADOW + 16)
        root.setSpacing(8)
        bar = _DragBar()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 4)
        self.title_label = QLabel(title, objectName="Title")
        bl.addWidget(self.title_label)
        bl.addStretch(1)
        self.close_btn = QToolButton(objectName="IconClose", text=theme.ICON["close"])
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setAccessibleName(I18n("he" if rtl else "en")("close"))     # an ✕ glyph, read as nothing
        self.close_btn.clicked.connect(self.reject)
        bl.addWidget(self.close_btn)
        self.close_btn.installEventFilter(self)
        _TabKeys.watch()
        # only once it's in the bar: shown while it had no parent, it flashed as a tiny window of its own
        self.close_btn.setVisible(closable)
        root.addWidget(bar)
        self.content = QWidget(objectName="Feed")
        self.content.setFocusPolicy(Qt.ClickFocus)       # holds the focus when the focused control goes away
        root.addWidget(self.content, 1)

    initial_focus = None       # the control a window opens focused on (a search field); else its first control

    def showEvent(self, e):
        super().showEvent(e)
        # Qt focuses the first widget in the chain, the title bar's X: Space or Enter then closed the window
        QTimer.singleShot(0, self._focus_first)

    def _focus_first(self) -> None:
        w = self.initial_focus
        if w is None or not w.isVisible() or not w.isEnabled():
            w = first_control(self.content)
        if w is not None and (self.focusWidget() in (None, self.close_btn) or not self.focusWidget().isVisible()):
            w.setFocus(Qt.OtherFocusReason)

    def eventFilter(self, obj, e):
        # the X takes the focus by Tab or a click only: when a page rebuilt its chips or cards the focused one went
        # away and Qt handed the focus to the X, which then lit up (the owner)
        # A focused control deleted hands the focus on as a "Tab" too: only a Tab key the player pressed counts
        if obj is self.__dict__.get("close_btn") and e.type() == QEvent.FocusIn and e.reason() not in (
                Qt.MouseFocusReason, Qt.ShortcutFocusReason) and not _TabKeys.pressed_just_now():
            QTimer.singleShot(0, lambda: self.close_btn.hasFocus() and self.content.setFocus(Qt.OtherFocusReason))
        return super().eventFilter(obj, e)

    def fit_screen(self, width: int, height: int) -> None:
        """Open at (width, height), but never taller than the screen: on a small or scaled display the
        bottom buttons (Save, Next) must stay visible; the content scrolls instead."""
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            height = min(height, max(320, screen.availableGeometry().height() - SCREEN_MARGIN))
        self.resize(width, height)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and not self.esc_closes:
            e.accept()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & ~Qt.KeypadModifier):
            # Enter does what this window says (or nothing), never "click the first button Qt found". A button the
            # player Tabbed to is theirs: Enter on a focused Back went forward, on Save did nothing (UX-14). Not
            # the first control a window focuses by itself (a chip), and a ConfirmDialog's safe answer stays safe
            b, w = self.enter_button, self.focusWidget()
            if isinstance(w, QAbstractButton) and w is not b and _TabKeys.by_keyboard(w):
                if w.isEnabled():
                    w.click()
            elif not _handles_enter(w) and b is not None and b.isVisible() and b.isEnabled():
                b.click()
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):
        paint_glass(self, None)
