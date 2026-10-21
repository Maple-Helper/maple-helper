"""The owner's announcement at the top of the chat (live.py: assets/live/config.json, changed with no app release):
"Maple Helper" and the title, the text, a link when it has one, ✕ to dismiss it for good. One at a time, in the
config's order; the next comes up when it is dismissed."""
from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from .. import bidi
from ..osapi import open_url
from . import theme
from .newsview import glyph
from .widgets import ElideLink


def _rich(text: str, rtl: bool) -> str:
    """A Hebrew text as right-to-left rich text, its English runs kept whole (newsview.set_title's way); line breaks
    kept."""
    lines = []
    for ln in text.split("\n"):
        parts = re.split(r"(?<=[.!?])\s+", ln.strip())
        lines.append(" ".join(html.escape(bidi.isolate_ltr_runs(x) if rtl else x, quote=False) for x in parts if x))
    body = "<br>".join(lines)
    return (f'<p dir="rtl" align="right" style="margin:0;">{body}</p>' if rtl
            else f'<p style="margin:0;">{body}</p>')


class AnnouncementStrip(QFrame):
    """One announcement; dismissed(id) on ✕, link_opened(id) when its link is followed."""

    dismissed = Signal(str)
    link_opened = Signal(str)

    def __init__(self):
        super().__init__(objectName="InfoNote")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 6, 8)
        row.setSpacing(8)
        self.icon = QLabel(glyph(), objectName="InfoIcon")
        row.addWidget(self.icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.head = QLabel(objectName="InfoText")
        self.head.setStyleSheet("font-weight: 600;")
        self.head.setWordWrap(True)
        col.addWidget(self.head)
        self.body = QLabel(objectName="InfoText")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(self.body)
        self.link = ElideLink(objectName="Link")
        self.link.setCursor(Qt.PointingHandCursor)
        self.link.clicked.connect(self._open)
        col.addWidget(self.link, 0, Qt.AlignLeft)
        row.addLayout(col, 1)
        self.close_btn = theme.dismiss_button()
        self.close_btn.clicked.connect(lambda: self.dismissed.emit(self._item["id"]) if self._item else None)
        row.addWidget(self.close_btn, 0, Qt.AlignTop)
        self._item: dict | None = None
        self.hide()

    def item(self) -> dict | None:
        return self._item

    def show_items(self, items: list[dict], t) -> None:
        self._item = items[0] if items else None
        self.setVisible(self._item is not None)
        if not self._item:
            return
        a, rtl = self._item, t.rtl
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        head = a["title"] or "Maple Helper"
        if len(items) > 1:
            head += " · " + t("live_more", n=len(items) - 1)
        self.head.setText(bidi.plain(head, rtl))
        self.icon.setText(theme.ICON.get("warn", glyph()) if a["level"] == "warning" else glyph())
        self.body.setText(_rich(a["text"], rtl))
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        for lb in (self.head, self.body):
            lb.setAlignment(align)
        self.link.setVisible(bool(a["link"]))
        if a["link"]:
            self.link.setText(bidi.plain(a["link_text"] or t("live_link"), rtl))
            self.link.setToolTip(a["link"])
            self.layout().itemAt(1).layout().setAlignment(self.link, align)
        self.close_btn.setToolTip(t("live_dismiss"))
        self.close_btn.setAccessibleName(t("live_dismiss"))
        self.setAccessibleName(f"{head}: {a['text']}")

    def _open(self) -> None:
        if self._item and self._item["link"]:
            open_url(self._item["link"])
            self.link_opened.emit(self._item["id"])
