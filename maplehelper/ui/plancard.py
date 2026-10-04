"""The player's plan inside the chat: an EXP bar on the character card, one tip at a time, and a
"My plan" panel (where to train, the next job, stats, the job's guide, "What now?").

Everything comes from plan.py (the KB's guides), so it shows up instantly and costs no Claude usage;
tapping a line asks the chat for the details.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QToolButton, QVBoxLayout

from .. import bidi, plan


class ExpBar(QFrame):
    """A slim EXP bar with one line under it: '67% · 51,328 EXP left · ~734 Jr. Wraith'."""

    def __init__(self):
        super().__init__(objectName="ExpRow")
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(3)
        self.bar = QProgressBar(objectName="ExpBar")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        col.addWidget(self.bar)
        self.text = QLabel(objectName="ExpText")
        self.text.setWordWrap(True)     # one long line here used to set the chat's narrowest width
        col.addWidget(self.text)

    def show_progress(self, p: dict | None, t, rtl: bool):
        self.setVisible(p is not None)
        if not p:
            return
        self.bar.setValue(round(p["pct"] * 10))
        # one line, the kills and of what (it wrapped onto a second: the owner); the EXP left on hover
        if p.get("kills"):
            text = t("exp_line_kills", n=f"{p['kills']:,}", pct=f"{p['pct']:g}", mob=p["mob"])
            self.text.setToolTip(t("exp_kills_tip", left=f"{p['left']:,}"))
        else:
            text = t("exp_line", pct=f"{p['pct']:g}", left=f"{p['left']:,}")
            self.text.setToolTip("")
        from .. import glossary
        from . import terms
        if not getattr(self, "_terms", False):
            terms.watch(self.text, t.lang)
            self._terms = True
        d = "rtl" if rtl else "ltr"
        self.text.setText(glossary.annotate(bidi.paragraph_html(text, d).replace("margin:0 0 4px 0;", "margin:0;"),
                                            t.lang, limit=1))
        self.text.setAlignment((Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute)


class TipStrip(QFrame):
    """One tip under the character card; tap to ask about it, ✕ to hide it until the next level."""

    asked = Signal(str)
    dismissed = Signal(str)

    def __init__(self):
        super().__init__(objectName="InfoNote")
        from . import theme
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 6, 6)
        row.setSpacing(8)
        self.icon = QLabel(theme.ICON["info"], objectName="InfoIcon")
        row.addWidget(self.icon, 0, Qt.AlignVCenter)
        self.text = QLabel(objectName="InfoText")
        self.text.setWordWrap(True)
        row.addWidget(self.text, 1)
        self.close_btn = QToolButton(objectName="Icon", text=theme.ICON["close"])
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(lambda: self.dismissed.emit(self._tip.kind) if self._tip else None)
        row.addWidget(self.close_btn, 0, Qt.AlignTop)
        self.setCursor(Qt.PointingHandCursor)
        # a tap asks about the tip: from the keyboard too, Tab to it and Enter or Space (with the focus ring)
        self.setFocusPolicy(Qt.TabFocus)
        self.setProperty("focus_ring", True)
        self._tip = None
        self.hide()

    def show_tip(self, tip: plan.Tip | None, t, rtl: bool):
        self._tip = tip
        self.setVisible(tip is not None)
        if tip:
            self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
            self.text.setText(bidi.plain(t(tip.key, **tip.args), rtl))
            self.close_btn.setToolTip(t("tip_hide"))
            self.close_btn.setAccessibleName(t("tip_hide"))           # an ✕ glyph, read as nothing
            self.setAccessibleName(t(tip.key, **tip.args))
            self.setAccessibleDescription(t("tip_ask_a11y"))

    def keyPressEvent(self, e):
        if self._tip and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not e.modifiers():
            self.asked.emit(self._tip.question)
            return
        super().keyPressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._tip and e.button() == Qt.LeftButton:
            self.asked.emit(self._tip.question)
