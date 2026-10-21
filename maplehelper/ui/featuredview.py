"""A featured article (featured.py) in its own window: Nexon's page as pictures, as wide as the window, its
animations playing where they are on it and its links clickable; "Save as PDF" writes the same pictures to a file.

Only the tiles on screen are decoded (at the size they are drawn), and only the animations on screen play: the
article is ~30 tiles of 1500x2000 pixels, a few hundred megabytes when all decoded at once.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImageReader, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import bidi, featured
from ..osapi import open_url
from .glass import GlassDialog

CACHE = 8               # decoded tiles kept (a screen shows two or three)


class ArticleCanvas(QWidget):
    """The article's tiles one under the other, scaled to the widget's width."""

    def __init__(self, m: dict, lang: str):
        super().__init__()
        self.setMouseTracking(True)
        self.set_article(m, lang)

    def set_article(self, m: dict, lang: str) -> None:
        self.m, self.page = m, m["langs"][lang]
        self._cache: OrderedDict = OrderedDict()
        for lb in getattr(self, "_anims", []):
            lb.movie().stop()
            lb.deleteLater()
        self._anims = []
        for a in self.page.get("anims") or []:
            path = featured.file_path(m, a["file"])
            if not path.is_file():
                continue
            lb = QLabel(self)
            mv = QMovie(str(path), parent=lb)
            mv.setCacheMode(QMovie.CacheNone)
            lb.setMovie(mv)
            lb.setScaledContents(True)
            lb.setAttribute(Qt.WA_TransparentForMouseEvents)
            lb.rect_css = a["rect"]
            self._anims.append(lb)
        self._place()
        self.update()

    def k(self) -> float:
        """Widget pixels per CSS pixel of the article."""
        return max(1, self.width()) / float(self.page["width"])

    def height_for(self, w: int) -> int:
        return round(float(self.page["height"]) * w / float(self.page["width"]))

    def _css_rect(self, r) -> QRect:
        k = self.k()
        return QRect(round(r[0] * k), round(r[1] * k), max(1, round(r[2] * k)), max(1, round(r[3] * k)))

    def _place(self) -> None:
        for lb in self._anims:
            lb.setGeometry(self._css_rect(lb.rect_css))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._cache.clear()
        self._place()

    def play_visible(self, visible: QRect) -> None:
        """The animations on screen play; the others stop (a few decode megabytes a second each)."""
        for lb in self._anims:
            on = lb.geometry().intersects(visible)
            mv = lb.movie()
            if on and mv.state() != QMovie.Running:
                mv.start()
                lb.show()
            elif not on and mv.state() == QMovie.Running:
                mv.setPaused(True)

    def _tile(self, i: int, t: dict, size: QSize) -> QPixmap:
        dpr = self.devicePixelRatioF()
        key = (i, size.width(), size.height(), dpr)
        pm = self._cache.get(key)
        if pm is None:
            reader = QImageReader(str(featured.tile_path(self.m, t)))
            reader.setScaledSize(QSize(max(1, round(size.width() * dpr)), max(1, round(size.height() * dpr))))
            img = reader.read()
            pm = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
            pm.setDevicePixelRatio(dpr)
            self._cache[key] = pm
            while len(self._cache) > CACHE:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        return pm

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        k = self.k()
        for i, t in enumerate(self.page["tiles"]):
            y0, y1 = round(t["y"] * k), round((t["y"] + t["h"]) * k)
            r = QRect(0, y0, self.width(), max(1, y1 - y0))
            if r.intersects(e.rect()):
                pm = self._tile(i, t, r.size())
                if not pm.isNull():
                    p.drawPixmap(r, pm)
        p.end()

    def link_at(self, pos) -> str | None:
        for ln in self.page.get("links") or []:
            if self._css_rect(ln["rect"]).contains(pos):
                return ln["href"]
        return None

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.PointingHandCursor if self.link_at(e.position().toPoint()) else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        url = self.link_at(e.position().toPoint()) if e.button() == Qt.LeftButton else None
        if url:
            open_url(url)
            return
        super().mouseReleaseEvent(e)


class _Scroll(QScrollArea):
    """Keeps the canvas as wide as the viewport and as tall as the article at that width."""

    def __init__(self, canvas: ArticleCanvas):
        super().__init__()
        self.canvas = canvas
        self.setWidget(canvas)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.NoFrame)
        self.verticalScrollBar().setSingleStep(40)
        self.verticalScrollBar().valueChanged.connect(lambda *_: self._visible())

    def fit(self) -> None:
        w = self.viewport().width()
        self.canvas.resize(w, self.canvas.height_for(w))
        self._visible()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        top = self.verticalScrollBar().value() / max(1, self.canvas.height())
        self.fit()
        self.verticalScrollBar().setValue(round(top * self.canvas.height()))

    def _visible(self) -> None:
        vb = self.verticalScrollBar().value()
        self.canvas.play_visible(QRect(0, vb, self.viewport().width(), self.viewport().height()))


class FeaturedDialog(GlassDialog):
    """The article, with "Save as PDF", the other language (Hebrew / the English original) and Nexon's own page."""

    pdf_done = Signal(str, bool)

    def __init__(self, m: dict, app_lang: str, stylesheet: str):
        from ..i18n import I18n
        self.t = t = I18n(app_lang or "he")
        super().__init__(featured.text(m, "title", t.lang), t.rtl)
        self.m = m
        self.lang = featured.language(m, t.lang)
        self.setStyleSheet(stylesheet)
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.pdf_btn = QPushButton(bidi.plain(t("featured_pdf"), t.rtl), objectName="Primary")
        self.pdf_btn.setCursor(Qt.PointingHandCursor)
        self.pdf_btn.clicked.connect(self.save_pdf)
        bar.addWidget(self.pdf_btn)
        self.lang_btn = QPushButton(objectName="Secondary")
        self.lang_btn.setCursor(Qt.PointingHandCursor)
        self.lang_btn.clicked.connect(self.switch_language)
        bar.addWidget(self.lang_btn)
        src = m.get("source_url") or ""
        if src.startswith("https://"):
            site = QPushButton(bidi.plain(t("featured_source", who=m.get("publisher") or "Nexon"), t.rtl),
                               objectName="Link")
            site.setCursor(Qt.PointingHandCursor)
            site.clicked.connect(lambda: open_url(src))
            bar.addWidget(site)
        bar.addStretch(1)
        self.status = QLabel(objectName="RowHint")
        bar.addWidget(self.status)
        outer.addLayout(bar)
        self.canvas = ArticleCanvas(m, self.lang)
        self.scroll = _Scroll(self.canvas)
        outer.addWidget(self.scroll, 1)
        self.pdf_done.connect(self._pdf_finished)
        self._lang_button()
        screen = self.screen().availableGeometry() if self.screen() else QRect(0, 0, 1280, 900)
        self.resize(min(860, max(560, screen.width() // 2)), max(560, round(screen.height() * 0.88)))
        QTimer.singleShot(0, self.scroll.fit)

    def _lang_button(self) -> None:
        other = [x for x in self.m["langs"] if x != self.lang]
        self.lang_btn.setVisible(bool(other))
        if other:
            label = {"he": "featured_in_he", "en": "featured_in_en"}.get(other[0])
            self.lang_btn.setText(bidi.plain(self.t(label) if label else other[0], self.t.rtl))

    def switch_language(self) -> None:
        other = [x for x in self.m["langs"] if x != self.lang]
        if not other:
            return
        self.lang = other[0]
        self.canvas.set_article(self.m, self.lang)
        self.scroll.verticalScrollBar().setValue(0)
        self.scroll.fit()
        self._lang_button()

    def save_pdf(self) -> None:
        name = featured.text(self.m, "pdf_name", self.lang) or "article"
        start = Path.home() / "Downloads"
        start = (start if start.is_dir() else Path.home()) / f"{name}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, self.t("featured_pdf"), str(start), "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self.pdf_btn.setEnabled(False)
        self.status.setText(bidi.plain(self.t("featured_pdf_saving"), self.t.rtl))
        m, lang = self.m, self.lang

        def work():
            try:
                featured.to_pdf(m, lang, Path(path))
                ok = True
            except Exception:       # noqa: BLE001 - a full disk or a locked file: say so, keep the window
                featured.log.exception("featured PDF failed")
                ok = False
            self.pdf_done.emit(path, ok)
        threading.Thread(target=work, daemon=True).start()

    def _pdf_finished(self, path: str, ok: bool) -> None:
        self.pdf_btn.setEnabled(True)
        self.status.setText(bidi.plain(self.t("featured_pdf_saved" if ok else "featured_pdf_failed"), self.t.rtl))
        if ok:          # the file, selected, in Explorer / Finder (as the problem report)
            import subprocess
            import sys
            subprocess.Popen(["explorer", "/select,", str(Path(path))] if sys.platform == "win32"
                             else ["open", "-R", path])
