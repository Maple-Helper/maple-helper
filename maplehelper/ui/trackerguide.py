"""The grind tracker's guide: a walk through every part of it, a page each, with a picture of that part as the app
draws it (assets/tracker_guide/<lang>/, made by tools/tracker_guide_shots.py from a made-up session) and a few plain
lines on what it shows and how to use it. It opens by itself once after the update that brought the tracker (or the
first time a new player opens the tracker), and again from the tracker's "Guide" button. Back / Next and the dots
move between the pages; the last page's button, Skip and the ✕ close it, and it counts as seen."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .. import bidi
from ..i18n import I18n
from ..store import ASSETS
from .controls import rtl_buttons
from .glass import GlassDialog

SEEN = "tracker_guide_seen"     # the setting: the guide was shown (closed, skipped or finished)
DIR = ASSETS / "tracker_guide"
# (page, its picture or ""): the tracker from the bar to the maps compared, then the tips
PAGES = (("intro", "bar"), ("card", "card"), ("top", "top"), ("summary", "summary"), ("kills", "kills"),
         ("items", "items"), ("potions", "potions"), ("history", "history"), ("compare", "compare"), ("tips", ""))
PIC_W, PIC_H = 620, 330       # the picture's room on a page (scaled down to fit, never up)


class TrackerGuide(GlassDialog):
    def __init__(self, settings, lang: str, stylesheet: str):
        self.t = t = I18n(lang or "he")
        super().__init__(t("tg_title"), t.rtl)
        self.settings = settings
        self.setStyleSheet(stylesheet + """
#TgPic { background: transparent; }
#TgFrame { background: rgba(127,127,127,0.08); border: 1px solid rgba(127,127,127,0.18); border-radius: 16px; }
#TgStep { color: #F07A12; font-weight: 700; }
#TgHead { font-size: 21px; font-weight: 700; }
#TgDot { background: rgba(127,127,127,0.30); border-radius: 4px; }
#TgDotOn { background: #F07A12; border-radius: 4px; }
""")
        self.fit_screen(700, 760)
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(4, 0, 4, 0)
        outer.setSpacing(12)
        self.stack = QStackedWidget()
        for i, (key, pic) in enumerate(PAGES):
            self.stack.addWidget(self._page(i, key, pic))
        outer.addWidget(self.stack, 1)
        dots = QHBoxLayout()
        dots.setSpacing(6)
        dots.addStretch(1)
        self.dots = []
        for i in range(len(PAGES)):
            d = QPushButton(objectName="TgDot")
            d.setFixedSize(8, 8)
            d.setCursor(Qt.PointingHandCursor)
            d.setFocusPolicy(Qt.NoFocus)
            d.setAccessibleName(t("tg_step", n=i + 1, of=len(PAGES)))
            d.clicked.connect(lambda *_, i=i: self.go(i))
            dots.addWidget(d)
            self.dots.append(d)
        dots.addStretch(1)
        outer.addLayout(dots)
        row = QHBoxLayout()
        self.skip = QPushButton(bidi.plain(t("tg_skip"), t.rtl), objectName="Link")
        self.skip.setCursor(Qt.PointingHandCursor)
        self.skip.clicked.connect(self.accept)
        self.back = QPushButton(bidi.plain(t("tg_back"), t.rtl), objectName="Secondary")
        self.next = QPushButton(bidi.plain(t("tg_next"), t.rtl), objectName="Primary")
        for b in (self.back, self.next):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(120)
        self.back.clicked.connect(lambda *_: self.go(self.stack.currentIndex() - 1))
        self.next.clicked.connect(self._next)
        row.addWidget(self.skip)
        row.addStretch(1)
        row.addWidget(self.back)
        row.addWidget(self.next)
        outer.addLayout(row)
        rtl_buttons(self, t.rtl)
        self.finished.connect(lambda *_: self._seen())
        self.go(0)

    def _page(self, i: int, key: str, pic: str) -> QWidget:
        t = self.t
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        step = QLabel(bidi.plain(t("tg_step", n=i + 1, of=len(PAGES)), t.rtl), objectName="TgStep")
        head = QLabel(bidi.plain(t(f"tg_{key}_title"), t.rtl), objectName="TgHead")
        head.setWordWrap(True)
        lay.addWidget(step)
        lay.addWidget(head)
        path = DIR / t.lang / f"{pic}.png"
        if not path.exists():
            path = DIR / "en" / f"{pic}.png"
        if pic and path.exists():
            frame = QWidget(objectName="TgFrame")
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(12, 12, 12, 12)
            img = QLabel(objectName="TgPic")
            img.setAlignment(Qt.AlignCenter)
            pm = QPixmap(str(path))
            dpr = self.devicePixelRatioF() or 1.0
            pm = pm.scaled(round(PIC_W * dpr), round(PIC_H * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation) \
                if pm.width() > PIC_W * dpr or pm.height() > PIC_H * dpr else pm
            pm.setDevicePixelRatio(dpr)
            img.setPixmap(pm)
            fl.addWidget(img)
            lay.addWidget(frame)
        body = QLabel(objectName="DialogBody")
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        d = "rtl" if t.rtl else "ltr"
        body.setText("".join(bidi.paragraph_html(line, d) for line in t(f"tg_{key}_body").split("\n") if line.strip()))
        lay.addWidget(body)
        lay.addStretch(1)
        return w

    def go(self, i: int) -> None:
        i = max(0, min(len(PAGES) - 1, i))
        self.stack.setCurrentIndex(i)
        for j, d in enumerate(self.dots):
            d.setObjectName("TgDotOn" if j == i else "TgDot")
            d.style().unpolish(d)
            d.style().polish(d)
        self.back.setVisible(i > 0)
        last = i == len(PAGES) - 1
        self.next.setText(bidi.plain(self.t("tg_done" if last else "tg_next"), self.t.rtl))
        self.skip.setVisible(not last)

    def _next(self) -> None:
        if self.stack.currentIndex() >= len(PAGES) - 1:
            self.accept()
        else:
            self.go(self.stack.currentIndex() + 1)

    def _seen(self) -> None:
        try:
            self.settings[SEEN] = True
        except Exception:  # noqa: BLE001 - a stub settings in a test
            pass
