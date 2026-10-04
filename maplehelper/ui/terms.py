"""The "?" beside game terms: hovering (or clicking) it shows what the term means. From the keyboard, Tab reaches
each "?" and Enter or Space opens it.

The badge is a small orange circle with a "?" (an image, so it's big enough to notice and to hit).
The explanation is our own popup, always on top: Qt's tooltip opens behind the chat and the tools
window, which stay on top of the game."""
from __future__ import annotations

import html
import re

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QKeyEvent, QPainter, QPixmap, QTextDocument
from PySide6.QtWidgets import QLabel

from .. import glossary
from . import theme

LANG = "he"          # the UI language; the chat sets it at start
BADGE_PX = 15


def _badge_uri(color: str | None = None, size_px: int | None = None) -> str | None:
    """Draw the "?" badge once (needs a running Qt app) and hand it to the glossary's links as a data: URI.

    Kept in memory, never in a file: a shared temp file was redrawn or deleted by another process (a test
    run, a second copy) and every "?" in the open app became an empty dot or a broken-page icon."""
    if QGuiApplication.instance() is None:
        return None
    scale = 3                                   # drawn large, shown at BADGE_PX: crisp on any screen
    size = (size_px or BADGE_PX) * scale
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color or theme.ORANGE))
    p.drawEllipse(0, 0, size, size)
    f = QFont("Arial")
    f.setBold(True)
    f.setPixelSize(int(size * 0.72))
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, "?")
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, "PNG")
    return "data:image/png;base64," + bytes(data.toBase64()).decode()


def hint_badge_html(px: int = 13) -> str:
    """A small grey "?" before a line whose tooltip explains it (the scope line under the chat)."""
    uri = _badge_uri("#9A9AA0", px)
    return f"<img src='{uri}' width='{px}' height='{px}' style='vertical-align: middle'>&nbsp;" if uri else ""


def setup():
    """Use the image badge in every annotated text from now on."""
    uri = _badge_uri()
    if uri:
        glossary.MARK = (f"<img src='{uri}' width='{BADGE_PX}' height='{BADGE_PX}' "
                         f"style='vertical-align: middle'>")


class _Popup(QLabel):
    """One explanation card, on top of everything, beside the mouse."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setMaximumWidth(320)
        self._hide = QTimer(self, singleShot=True, interval=7000, timeout=self.hide)

    def show_text(self, body: str, near: QRect | None = None, rtl: bool = False):
        """near: the label (in screen coordinates) a "?" opened from the keyboard is in, the card under it from the
        label's reading start (its right edge in Hebrew); else beside the mouse."""
        c = theme.P()
        bg = "#2C2C2E" if theme.MODE == "dark" else "#FFFFFF"
        self.setStyleSheet(f"QLabel {{ background: {bg}; color: {c['text']}; border: 1px solid {theme.ORANGE};"
                           f" border-radius: 10px; padding: 9px 11px; font-size: 13px; }}")
        self.setText(body)
        self.adjustSize()
        w, h = self.width(), self.height()
        if near is None:
            m = QCursor.pos()
            (x, x_flip), (y, y_flip) = (m.x() + 14, m.x() - w - 14), (m.y() + 16, m.y() - h - 10)
        else:
            # from the keyboard the mouse is anywhere, maybe on another screen: under the label instead (or over it)
            m = near.center()
            (x, x_flip), (y, y_flip) = (near.left(), near.right() - w), (near.bottom() + 6, near.top() - h - 6)
            if rtl:
                x, x_flip = x_flip, x
        screen = (QGuiApplication.screenAt(m) or QGuiApplication.primaryScreen()).availableGeometry()
        x = x if screen.left() <= x and x + w <= screen.right() else x_flip
        y = y if y + h <= screen.bottom() else y_flip
        self.move(x, y)
        self.show()
        self.raise_()
        self._hide.start()


_popup: _Popup | None = None


def tip_html(term: str, lang: str) -> str | None:
    text = glossary.explain(term, lang)
    if not text:
        return None
    d = "rtl" if lang != "en" else "ltr"
    title = glossary.TITLES.get(term, term)
    return (f"<div dir='{d}'><b style='color:{theme.accent_text(deep=True)};'>{html.escape(title)}</b><br>"
            f"<span style='line-height:135%;'>{html.escape(text)}</span></div>")


def show(link: str, lang: str, near: QRect | None = None) -> bool:
    """Show the explanation for a "g:<term>" link; False when the link isn't a term."""
    global _popup
    term = glossary.term_of(link or "")
    if not term:
        return False
    body = tip_html(term, lang)
    if body:
        if _popup is None:
            _popup = _Popup()
        _popup.show_text(body, near, rtl=lang != "en")
    return True


def show_html(body: str, rtl: bool = False) -> None:
    """Any explanation in the same card beside the mouse (a skill's changes on the build table's chip)."""
    global _popup
    if _popup is None:
        _popup = _Popup()
    _popup.show_text(f"<div dir='{'rtl' if rtl else 'ltr'}'>{body}</div>", rtl=rtl)


def hide():
    if _popup is not None:
        _popup.hide()


def _hovered(link: str, lang: str):
    if not show(link, lang):
        hide()


_LINK = re.compile(r"<a\b[^>]*href=['\"]g:([^'\"]+)['\"][^>]*>.*?</a>", re.S)


class _Keys(QObject):
    """The keyboard side of a watched label. Qt's own link navigation does the moving (Tab goes from one "?" to
    the next, then on out of the label) and Enter opens; this adds Space, puts the label in the Tab order only
    while it has a "?" (a label without one was a stop where nothing happens), skips the stop on the label
    itself before its first "?", and names the label for screen readers (the "?" is a picture: read as nothing)."""

    def __init__(self, label: QLabel):
        super().__init__(label)
        self._text = None
        self.keyboard = False           # the link being opened was opened from the keyboard
        label.installEventFilter(self)
        self.sync(label)

    def sync(self, label: QLabel) -> None:
        # the text can change any time (setText from anywhere): checked again whenever the label shows or paints
        text = label.text()
        if text == self._text:
            return
        self._text = text
        names = [html.unescape(m) for m in _LINK.findall(text)]
        label.setFocusPolicy(Qt.StrongFocus if names else Qt.NoFocus)
        if not names:
            label.setAccessibleName("")
            label.setAccessibleDescription("")
            return
        doc = QTextDocument()
        doc.setHtml(_LINK.sub("", text))
        from ..i18n import I18n
        label.setAccessibleName(doc.toPlainText().replace("\ufffc", "").strip())
        label.setAccessibleDescription(I18n(LANG)("term_links_a11y", terms=", ".join(dict.fromkeys(names))))

    def eventFilter(self, obj, e) -> bool:
        kind = e.type()
        if kind in (QEvent.Show, QEvent.Paint):
            self.sync(obj)
        elif kind == QEvent.FocusIn and e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason):
            # Qt stops on the label first with no "?" chosen, where Enter does nothing: on to its first (or, going
            # back, its last) "?" right away
            forward = e.reason() == Qt.TabFocusReason
            QTimer.singleShot(0, lambda: self._to_link(forward))
        elif kind == QEvent.KeyPress and not e.modifiers():
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.keyboard = True        # Qt opens the link while handling this key, right after the filter
                QTimer.singleShot(0, lambda: setattr(self, "keyboard", False))
            elif e.key() == Qt.Key_Space:
                # Space opens a "?" like Enter does (Qt's links take only Enter)
                QGuiApplication.sendEvent(obj, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "\r"))
                return True
        return False

    def _to_link(self, forward: bool) -> None:
        label = self.parent()
        if label.hasFocus() and not label.hasSelectedText():
            label.focusNextPrevChild(forward)        # a label made in Python may call it: moves to the next "?"

    def activated(self, link: str) -> None:
        near = None
        if self.keyboard:
            label = self.parent()
            near = QRect(label.mapToGlobal(QPoint(0, 0)), label.size())
        show(link, LANG, near)


def watch(label: QLabel, lang: str | None = None) -> QLabel:
    """A rich-text label whose "?" links explain their term on hover and on click, and from the keyboard (Tab to
    a "?", Enter or Space to open it).

    The explanation is in the UI language at the moment it pops up (LANG), so a label made before a
    language switch explains in the new one; `lang` is accepted for older callers and not used."""
    label.setTextFormat(Qt.RichText)
    label.setOpenExternalLinks(False)
    label.setMouseTracking(True)
    label.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
    keys = _Keys(label)
    label.linkHovered.connect(lambda link: _hovered(link, LANG))
    label.linkActivated.connect(keys.activated)
    return label


def label(text_html: str, lang: str, obj: str = "RowLabel") -> QLabel:
    lb = QLabel(glossary.annotate(text_html, lang), objectName=obj)
    lb.setWordWrap(True)
    return watch(lb, lang)
