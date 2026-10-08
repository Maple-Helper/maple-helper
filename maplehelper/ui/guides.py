"""The guides library: picks for your character, categories and search, and a clean reader with a
Hebrew/English summary on demand and "Ask about this guide" (tags it in the chat)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QFontInfo, QGuiApplication, QPixmap, QTextCharFormat, QTextCursor, QTextFormat, QTextOption, QTextTable
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from .. import bidi, guides
from ..i18n import I18n
from ..osapi import open_url
from .controls import FlowLayout, follow_typing, rtl_buttons
from .glass import GlassDialog
from .patchnotes import gutter
from .widgets import fit_picture


ZOOM = 3            # pictures are pixel art: a whole-number zoom keeps them sharp
ZOOM_MAX_W = 720
TABLE_MIN_PX = 10   # a wide table's text gets this small at the least before it scrolls sideways
TABLE_MIN_PAD = 2   # and then its cells this little padding (the guides' tables have 5)


def fit_tables(browser: QTextBrowser, wait: bool = True) -> None:
    """Tables wider than the view get a smaller font, a pixel at a time, until they fit (down to TABLE_MIN_PX),
    then less padding in their cells (down to TABLE_MIN_PAD). Lines break between words only (WordWrap), so a
    10-column table no longer splits "341,782" into "341,7" / "82"; what is still too wide after this scrolls
    sideways."""
    if not browser.isVisible():
        # filled before its window is on screen (a window opened on this page): the view has no width yet, and
        # every table would come out at the smallest size. Measured once the window is up instead.
        if wait:
            def later():
                try:
                    fit_tables(browser, wait=False)
                except RuntimeError:          # closed meanwhile
                    pass
            QTimer.singleShot(0, later)
        return
    doc = browser.document()
    room = browser.viewport().width() - 2 * doc.documentMargin()
    base = QFontInfo(browser.font()).pixelSize()        # a font set in points has no pixelSize() of its own
    if room <= 0 or base <= TABLE_MIN_PX:
        return
    lay = doc.documentLayout()
    for frame in doc.rootFrame().childFrames():
        if not isinstance(frame, QTextTable):
            continue
        px = base
        while lay.frameBoundingRect(frame).width() > room + 1 and px > TABLE_MIN_PX:
            px -= 1
            cur = QTextCursor(doc)
            cur.setPosition(frame.firstPosition())
            cur.setPosition(frame.lastPosition(), QTextCursor.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setProperty(QTextFormat.FontPixelSize, px)
            cur.mergeCharFormat(fmt)
        # still over at the smallest font: the width left is the cells' padding around their longest words and
        # pictures (the Fighter guide's 8-column weapon table was 15 px too wide at 10 px, 80 px of it padding)
        tf = frame.format()
        while lay.frameBoundingRect(frame).width() > room + 1 and tf.cellPadding() > TABLE_MIN_PAD:
            tf.setCellPadding(tf.cellPadding() - 1)
            frame.setFormat(tf)
    # the sideways bar only for a table still too wide: the page itself measures a pixel over the view (rounding),
    # which put a bar under every guide
    over = doc.size().width() - browser.viewport().width()
    browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if over > 2 else Qt.ScrollBarAlwaysOff)
    if over > 2 and browser.layoutDirection() == Qt.RightToLeft:
        # a right-to-left page that scrolls sideways starts at its right edge, where its lines begin
        bar = browser.horizontalScrollBar()
        bar.setValue(bar.maximum())


def zoomed(pix: QPixmap) -> QPixmap:
    """The picture enlarged for the hover view: up to 3x (whole steps stay pixel-sharp), at most ZOOM_MAX_W wide."""
    if pix.isNull():
        return pix
    k = max(1, min(ZOOM, ZOOM_MAX_W // max(1, pix.width())))
    return pix.scaled(pix.width() * k, pix.height() * k, Qt.KeepAspectRatio, Qt.FastTransformation)


class ImageZoom(QObject):
    """Hovering a picture in the guide shows it enlarged next to the mouse."""

    def __init__(self, browser: QTextBrowser):
        super().__init__(browser)
        self.browser = browser
        self.pop = QLabel(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.pop.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pop.setStyleSheet(_pop_style())
        # the popup is a window of its own (no parent, so it can stand beside the dialog): it goes with the view.
        # The dialogs delete themselves on close, before the poll below could hide it, so it stayed on screen
        # over the game until the app quit, and each Play tools / Guides window left one more behind
        self.destroyed.connect(self.pop.deleteLater)
        self._shown = None
        browser.viewport().setMouseTracking(True)
        browser.viewport().installEventFilter(self)
        # mouse-move events don't always reach a glass dialog's text view (Windows), so also look
        # where the mouse is a few times a second while the guide is on screen
        self._poll = QTimer(self, interval=120)
        self._poll.timeout.connect(self._check_mouse)
        self._poll.start()

    def _check_mouse(self):
        b = self.browser
        if not b.isVisible():
            self.hide()
            # nothing to look at until it shows again (eventFilter): the Tools window kept waking the app 8 times
            # a second for its Build page while another page or the game was in front
            self._poll.stop()
            return
        vp = b.viewport()
        under = QApplication.widgetAt(QCursor.pos())
        pos = vp.mapFromGlobal(QCursor.pos())
        if under is vp or (under is not None and vp.isAncestorOf(under)):
            name = self.image_at(pos)
            self.show(name) if name else self.hide()
        elif self._shown:
            self.hide()

    def image_at(self, pos: QPoint) -> str | None:
        """The file of the picture under this viewport point, if any.

        Asked of the document's own layout (imageAt), which knows each picture's box: working it out from text
        cursors went wrong in Hebrew table cells, where two icons side by side zoomed the wrong one or none."""
        b = self.browser
        at = QPointF(pos.x() + b.horizontalScrollBar().value(), pos.y() + b.verticalScrollBar().value())
        name = b.document().documentLayout().imageAt(at)
        if not name:
            return None
        url = QUrl(name)
        return url.toLocalFile() if url.isLocalFile() else name

    def eventFilter(self, obj, e):
        if e.type() == QEvent.MouseMove:
            name = self.image_at(e.position().toPoint())
            if name:
                self.show(name)
            else:
                self.hide()
        elif e.type() in (QEvent.Leave, QEvent.Wheel, QEvent.MouseButtonPress):
            self.hide()
        elif e.type() == QEvent.Show and not self._poll.isActive():
            self._poll.start()
        elif e.type() == QEvent.Hide and self._shown:
            # the window closed (or another page took its place) with a picture zoomed: Qt sends no Leave to a
            # window that is closing
            self.hide()
        return False

    def show(self, name: str):
        if name != self._shown:
            pix = zoomed(QPixmap(name))
            if pix.isNull():
                return
            self.pop.setPixmap(pix)
            self.pop.adjustSize()
            self._shown = name
        # beside the mouse, kept on the screen
        at = QCursor.pos() + QPoint(18, 18)
        screen = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        w, h = self.pop.width(), self.pop.height()
        x = at.x() if at.x() + w <= screen.right() else QCursor.pos().x() - w - 18
        y = at.y() if at.y() + h <= screen.bottom() else max(screen.top(), screen.bottom() - h)
        self.pop.move(x, y)
        self.pop.show()

    def hide(self):
        self._shown = None
        self.pop.hide()


COVER_W = 480       # a guide's cover picture, shown when hovering its card
# the cover on a guide's card: covers are 1200x630, and in a 44 px square they were a 44x23 smudge (VIS-22)
COVER_THUMB = (88, 46)


def _pop_style() -> str:
    """A picture shown large: on the tooltips' background (white in the light theme), not always black."""
    from . import theme
    c = theme.P()
    bg = "#2C2C2E" if theme.MODE == "dark" else "#FFFFFF"
    return f"background: {bg}; border: 1px solid {c['stroke']}; border-radius: 12px; padding: 8px;"


def set_title(lb: QLabel, text: str, rtl: bool) -> None:
    """A word-wrapped guide title. In Hebrew as right-to-left rich text: as plain text a nearly full line kept its
    trailing space and lost the edge of its last word ("…עליית רמות ל-Assassin … רמה 30-70" showed "רמ" at the
    card's edge, the review UI-2; the sign-in hints had the same, DLG-8)."""
    if rtl:
        lb.setTextFormat(Qt.RichText)
        lb.setText(bidi.to_html(text, "rtl"))
    else:
        lb.setTextFormat(Qt.PlainText)
        lb.setText(bidi.plain(text, False))


class CoverPic(QLabel):
    """The small cover on a guide's card; hovering it shows the cover large."""

    def __init__(self, path: str | None):
        super().__init__()
        self.full = QPixmap(path) if path else QPixmap()
        self.pop = QLabel(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.pop.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pop.setStyleSheet(_pop_style())
        self.destroyed.connect(self.pop.deleteLater)

    def enterEvent(self, e):
        if not self.full.isNull():
            big = self.full.scaledToWidth(min(COVER_W, self.full.width()), Qt.SmoothTransformation)
            self.pop.setPixmap(big)
            self.pop.adjustSize()
            at = QCursor.pos() + QPoint(18, 18)
            screen = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
            x = at.x() if at.x() + self.pop.width() <= screen.right() else QCursor.pos().x() - self.pop.width() - 18
            y = at.y() if at.y() + self.pop.height() <= screen.bottom() else max(screen.top(), screen.bottom() - self.pop.height())
            self.pop.move(x, y)
            self.pop.show()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.pop.hide()
        super().leaveEvent(e)

    def hideEvent(self, e):
        self.pop.hide()
        super().hideEvent(e)


class GuideRow(QFrame):
    clicked = Signal(str)

    def __init__(self, kb, g: dict, t, rtl: bool):
        super().__init__(objectName="Card")
        self.key = g["key"]
        self.setCursor(Qt.PointingHandCursor)
        # a row opens from the keyboard too (Tab to it, Enter or Space): with the mouse only, no guide could be
        # opened without one. Its title is what a screen reader says.
        self.setFocusPolicy(Qt.TabFocus)          # a click opens it without leaving a focus ring behind
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        img = kb.picture(self.key)
        pic = CoverPic(str(img) if img else None)
        pic.setFixedSize(*COVER_THUMB)
        pic.setAlignment(Qt.AlignCenter)
        pm = QPixmap(str(img)) if img else QPixmap()
        if not pm.isNull():
            pic.setPixmap(fit_picture(pm, *COVER_THUMB, pic))
        row.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        shown_title = guides.title(g["key"], g["title"], t.lang)
        self.setAccessibleName(shown_title)
        title = QLabel(objectName="CardName")
        title.setWordWrap(True)
        set_title(title, shown_title, rtl)
        title.setAlignment(align)
        col.addWidget(title)
        meta = t(f"gcat_{g['category']}") + (f" · {t('g_minutes', n=g['minutes'])}" if g.get("minutes") else "")
        sub = QLabel(bidi.plain(meta, rtl), objectName="CardSub")
        sub.setAlignment(align)
        col.addWidget(sub)
        row.addLayout(col, 1)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.hasFocus():
            from .pinsview import draw_focus
            draw_focus(self)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.key)
            e.accept()
            return
        super().keyPressEvent(e)


class GuidesDialog(GlassDialog):
    ask_requested = Signal(str)          # guide key: tag it in the chat and focus the question box

    def __init__(self, kb, character, lang: str, stylesheet: str, open_key: str | None = None):
        self.t = t = I18n(lang or "he")
        super().__init__(t("guides"), t.rtl)
        self.kb, self.c = kb, character
        self.setStyleSheet(stylesheet)
        self.fit_screen(560, 760)          # never taller than a small screen; the library and reader scroll
        self.all = guides.all_guides(kb)
        self.picks = guides.for_you(kb, character)
        self._reading: str | None = None
        # the guides left through a link inside a guide, with where each was scrolled to: Back / Esc return there,
        # not to the list (which lost the place in the first guide)
        self._trail: list[tuple[str, int]] = []

        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self._library())
        self.stack.addWidget(self._reader())
        rtl_buttons(self, t.rtl)
        self.initial_focus = self.search      # (an opened guide: its first control, "Back")
        if open_key and kb.get(open_key):
            self.open_guide(open_key)

    def keyPressEvent(self, e):
        # Esc in an open guide goes back (like "Back": to the guide a link came from, else the list); in the list
        # it closes the window
        if e.key() == Qt.Key_Escape and self.stack.currentIndex() == 1:
            self._back()
            e.accept()
            return
        super().keyPressEvent(e)

    def _back(self):
        if self._trail:
            key, at = self._trail.pop()
            self._show(key)
            bar = self.browser.verticalScrollBar()
            bar.setValue(at)
            QTimer.singleShot(0, lambda: bar.setValue(at))      # again once the document has its full height
            return
        self.stack.setCurrentIndex(0)

    # library ----------------------------------------------------------------

    def _library(self) -> QWidget:
        t, rtl = self.t, self.t.rtl
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText(bidi.plain(t("g_search"), rtl))
        self.search.setAccessibleName(t("g_search"))          # a placeholder isn't read as the field's name
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self._fill())
        self.search.returnPressed.connect(self._open_first)          # Enter opens the top result
        follow_typing(self.search, rtl)
        lay.addWidget(self.search)
        chips = FlowLayout(spacing=6)        # wraps onto a second row: one row of five was 521 px wide
        self.cats = QButtonGroup(self)
        for cat in guides.CATEGORIES:
            b = QPushButton(bidi.plain(t(f"gcat_{cat}"), rtl), objectName="Chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("cat", cat)
            self.cats.addButton(b)
            chips.addWidget(b)
        self.cats.buttons()[0].setChecked(True)
        self._cat = guides.CATEGORIES[0]          # the category to go back to when the search is emptied
        self.cats.buttonClicked.connect(self._pick_cat)
        lay.addLayout(chips)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(*gutter(rtl))    # the room before the scrollbar, on its side (left in Hebrew)
        self.rows.setSpacing(8)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        self._fill()
        return w

    def _fill(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().hide()   # gone now, not at the next event loop
                item.widget().deleteLater()
        q = self.search.text().strip().lower()
        # a search looks in every guide: no category chip stays lit as if it filtered (TOOL-11); emptied, the
        # category picked before comes back
        self._light_cat(None if q else self._cat)
        cat = self._cat
        if q:
            texts = self.__dict__.setdefault("_texts", {})
            for g in self.all:
                if g["key"] not in texts:
                    b = guides.book(g["key"], self.t.lang)
                    texts[g["key"]] = (guides.book_text(b) if b else
                                       guides.search_text(g["key"], self.kb.page(g["key"])) + " "
                                       + guides.text_of(g["key"], self.t.lang)).lower()
            wants = self._queries(q)
            shown = [g for g in self.all if any(w in g["title"].lower() or w in texts[g["key"]] for w in wants)]
        elif cat == "for_you":
            by_key = {g["key"]: g for g in self.all}
            shown = [by_key[k] for k in self.picks if k in by_key]
        else:
            shown = [g for g in self.all if g["category"] == cat]
        if q and shown:
            self.rows.addWidget(QLabel(bidi.plain(self.t("g_found", n=len(shown)), self.t.rtl), objectName="RowHint"))
        for g in shown:
            row = GuideRow(self.kb, g, self.t, self.t.rtl)
            row.clicked.connect(self.open_guide)
            self.rows.addWidget(row)
        if not shown:
            self.rows.addWidget(QLabel(bidi.plain(self.t("g_none"), self.t.rtl), objectName="RowHint"))
        self.rows.addStretch(1)

    def _pick_cat(self, b) -> None:
        """A category chip: that category's guides, the search emptied (it looks in every guide)."""
        self._cat = b.property("cat")
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        self._fill()

    def _light_cat(self, cat: str | None) -> None:
        self.cats.setExclusive(False)         # (an exclusive group never lets its checked chip go)
        for b in self.cats.buttons():
            b.setChecked(b.property("cat") == cat)
        self.cats.setExclusive(True)

    def _queries(self, q: str) -> list[str]:
        """The search as typed, and in Hebrew also with the KB's Hebrew names turned into the English ones the
        guides use ("קרנינג" -> "kerning city"): even the Hebrew guides keep game names in English, so a Hebrew
        name found nothing."""
        wants = [q]
        resolve = getattr(self.kb, "resolve_names", None)
        if resolve and bidi._RTL.search(q):
            en = resolve(q).lower().strip()
            if en and en != q:
                wants.append(en)
        return wants

    def _open_first(self):
        row = next((self.rows.itemAt(i).widget() for i in range(self.rows.count())
                    if isinstance(self.rows.itemAt(i).widget(), GuideRow)), None)
        if row is not None:
            self.open_guide(row.key)

    # reader -----------------------------------------------------------------

    def _reader(self) -> QWidget:
        t, rtl = self.t, self.t.rtl
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.back = back = QPushButton(bidi.plain(t("g_back"), rtl), objectName="Link")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self._back)
        lay.addWidget(back, 0, (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute)   # the reading start
        self.r_title = QLabel(objectName="PageTitle")
        self.r_title.setWordWrap(True)
        self.r_title.setLayoutDirection(Qt.LeftToRight)
        lay.addWidget(self.r_title)
        self.r_meta = QLabel(objectName="CardSub")
        lay.addWidget(self.r_meta)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        ask = QPushButton(bidi.plain(t("g_ask"), rtl), objectName="Primary")
        ask.setCursor(Qt.PointingHandCursor)
        ask.clicked.connect(lambda: (self.ask_requested.emit(self._reading), self.accept()))
        actions.addWidget(ask)
        web = QPushButton(bidi.plain(t("g_web"), rtl), objectName="Link")
        web.setCursor(Qt.PointingHandCursor)
        web.clicked.connect(lambda: open_url((self.kb.get(self._reading) or {}).get("url", "")))
        actions.addWidget(web)
        actions.addStretch(1)
        lay.addLayout(actions)
        self.stale = QLabel(objectName="RowHint")
        self.stale.setWordWrap(True)
        lay.addWidget(self.stale)
        self.browser = QTextBrowser(objectName="GuideText")
        self.browser.setOpenLinks(False)                      # guide: links open here, web links in the browser
        self.browser.anchorClicked.connect(self._on_link)
        from . import terms
        self.browser.highlighted.connect(lambda url: terms._hovered(url.toString(), self.t.lang))   # hover shows it too
        # lines break between words only: Qt's default also breaks inside a word when a table cell is narrow, so
        # a 10-column table read "341,7" / "82" for one DPM value and "Savag" / "e Blow". A wide table gets a
        # smaller font instead, and one still too wide scrolls sideways (fit_tables; the text around it still
        # wraps at the window's width)
        self.browser.setWordWrapMode(QTextOption.WordWrap)
        from . import theme
        # the app's style draws only the vertical bar: the sideways one in the same thin look
        self.browser.horizontalScrollBar().setStyleSheet(
            f"QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 1px 4px; }}"
            f"QScrollBar::handle:horizontal {{ background: {theme.P()['scroll']}; border-radius: 3px; min-width: 28px; }}"
            "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page"
            " { width: 0; background: none; }")
        self.zoom = ImageZoom(self.browser)
        self.browser.setLayoutDirection(Qt.LeftToRight)      # the guides are written in English
        lay.addWidget(self.browser, 1)
        return w

    def _on_link(self, url):
        link = url.toString()
        from . import terms
        if terms.show(link, self.t.lang):
            return
        if link.startswith("guide:"):
            key = "guide/" + link[6:]
            if self.kb.get(key) or guides.book(key, "en"):
                if self._reading and self._reading != key:
                    self._trail.append((self._reading, self.browser.verticalScrollBar().value()))
                self._show(key)
        elif link.startswith("http"):
            open_url(link)

    def open_guide(self, key: str):
        """A guide opened from the list (or by the app): Back from it goes to the list."""
        self._trail.clear()
        self._show(key)

    def _show(self, key: str):
        t = self.t
        self._reading = key
        # Back says where it goes: the guide a link came from, else the list (not the guide's name: the titles are
        # long, "MapleStory Classic Thief Build and Leveling Guide: Lv 1-30" ran out of the window)
        self.back.setText(bidi.plain(t("g_back_prev") if self._trail else t("g_back"), t.rtl))
        b = guides.book(key, t.lang)
        if b:
            self._open_book(key, b)
            return
        page = self.kb.page(key)
        g, translated, stale = guides.localized(key, page, t.lang)
        rtl = translated and t.rtl
        self.r_title.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        set_title(self.r_title, g.title, rtl)
        meta = t(f"gcat_{guides.category(key)}") + (f" · {t('g_minutes', n=g.minutes)}" if g.minutes else "")
        self.r_meta.setText(bidi.plain(meta, t.rtl))
        self.stale.setVisible(translated and stale)
        self.stale.setText(bidi.plain(t("g_stale"), t.rtl))
        labels = {"pros": t("g_pros") if rtl else "Pros", "cons": t("g_cons") if rtl else "Cons"}
        self.browser.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        # table cells take their direction from the document, not from the cell's dir attribute
        opt = self.browser.document().defaultTextOption()
        opt.setTextDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        self.browser.document().setDefaultTextOption(opt)
        self.browser.setHtml(guides.to_html(g, labels, rtl))
        self.stack.setCurrentIndex(1)
        fit_tables(self.browser)

    def _open_book(self, key: str, b: dict):
        """A full guide (pictures, tables, notes) from assets/guides."""
        from . import theme
        t = self.t
        rtl = b["lang"] != "en" and t.rtl
        self.r_title.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        set_title(self.r_title, b.get("title") or key, rtl)
        meta = t(f"gcat_{guides.category(key)}") + (f" · {t('g_minutes', n=b['minutes'])}" if b.get("minutes") else "")
        self.r_meta.setText(bidi.plain(meta, t.rtl))
        self.stale.setVisible(b["lang"] != "en" and b.get("stale", False))
        self.stale.setText(bidi.plain(t("g_stale"), t.rtl))
        self.browser.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        opt = self.browser.document().defaultTextOption()
        opt.setTextDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        self.browser.document().setDefaultTextOption(opt)
        from .. import glossary
        self.browser.setHtml(glossary.annotate(guides.book_html(b, theme.MODE, t.rtl), t.lang, limit=30))
        self.browser.verticalScrollBar().setValue(0)
        self.stack.setCurrentIndex(1)
        fit_tables(self.browser)
