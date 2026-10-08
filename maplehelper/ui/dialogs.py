"""Onboarding (mandatory, no skipping), character editor and settings."""
from __future__ import annotations

import logging
import math
import re
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from .. import bidi, osapi, providers
from ..providers.base import login_failed, login_waiting, stop_login
from .controls import AdaptiveRow, FlowLayout, Section, Segmented, Select, Stepper, Switch, rtl_buttons
from .glass import GlassDialog, no_default_buttons
from .patchnotes import gutter
from ..i18n import I18n, system_language
from ..jobs import JOBS, job_label, open_jobs        # the job tree: base class -> [(job, min level)], checked against the KB
from ..kb import KnowledgeBase
from ..store import ASSETS, History, Profiles, Settings
from . import theme

log = logging.getLogger(__name__)

def set_hint(lb: QLabel, text: str, rtl: bool) -> None:
    """A word-wrapped hint under a row. In Hebrew as right-to-left rich text: as plain text a nearly full line kept
    its trailing space and lost the edge of its last word (Gemini's "...יש לזה דקה אחת" was cut at the card's edge)."""
    if rtl:
        lb.setTextFormat(Qt.RichText)
        lb.setText(bidi.to_html(text, "rtl"))
    else:
        lb.setText(bidi.plain(text, False))


# the account checks and sign-outs run on threads: an unexpected error there emitted nothing, and the window said
# "Checking…" forever with Next or "Switch account" disabled. Logged, and the window settles on "offline"
def _safe_status(ai) -> str:
    try:
        return ai.status()
    except Exception:  # noqa: BLE001
        log.exception("%s: the account check failed", ai.name)
        return "offline"


def _safe_account(ai) -> dict:
    try:
        return {**ai.account(), "provider": ai.name}
    except Exception:  # noqa: BLE001
        log.exception("%s: the account check failed", ai.name)
        return {"status": "offline", "email": None, "provider": ai.name}


def _safe_logout(ai) -> None:
    try:
        ai.logout()
    except Exception:  # noqa: BLE001
        log.exception("%s: signing out failed", ai.name)


CLASS_HE = {"Beginner": "ביגינר", "Warrior": "לוחם", "Magician": "קוסם", "Bowman": "קשת", "Thief": "גנב"}
# Only the level field's bound, not the game's level cap: the KB says no launch cap is published (testers reached
# at least 100). It matches the bound the saved profile keeps (store._repair), so nothing typed here is
# changed on the next load.
LEVEL_FIELD_MAX = 250


def jobs_for(base_class: str, level: int, kb=None) -> list[str]:
    """The jobs a character of this class can have at this level, only those in the game (no 3rd job until
    the KB confirms it)."""
    return [j for j, lv in open_jobs(base_class, kb) if lv <= level]


def _title(text: str) -> QLabel:
    lb = QLabel(bidi.plain(text), objectName="PageTitle")
    lb.setWordWrap(True)
    return lb


def _body(text: str) -> QLabel:
    lb = QLabel(bidi.plain(text), objectName="PageBody")
    lb.setWordWrap(True)
    return lb


# where players report problems (the owner's choice, UX-8); the report toast names it too (report_saved_body)
ISSUES_URL = "https://github.com/Maple-Helper/maple-helper/issues"


def _field(text: str) -> QLabel:
    return QLabel(text, objectName="FieldLabel")


def _while_open(slot):
    """A slot fed from a background check: the answer can arrive after the dialog closed and Qt deleted its
    widgets ("Internal C++ object already deleted"), which then crashed the app. Too late is simply dropped."""
    import functools

    @functools.wraps(slot)
    def run(self, *args):
        try:
            return slot(self, *args)
        except RuntimeError as e:
            if "already deleted" not in str(e):
                raise
    return run


RUNNING_INSTALLS: dict = {}     # provider name -> its Installer, while it runs (shared by every dialog)


def install_for(ai):
    """The installer already running for this AI, or a new one."""
    inst = RUNNING_INSTALLS.get(ai.name)
    if inst is None or inst.done.is_set():
        inst = RUNNING_INSTALLS[ai.name] = ai.install()
    return inst


def running_install(name: str):
    inst = RUNNING_INSTALLS.get(name)
    return inst if inst is not None and not inst.done.is_set() else None


class InstallPanel(QWidget):
    """The official installer running in the background, shown inside the app (no console window): what it's
    installing, a moving bar, the installer's latest line, and why it failed when it did."""
    ended = Signal(bool)          # the installer finished: True when it reported success

    def __init__(self, t: I18n, parent=None):
        super().__init__(parent)
        self.t, self.inst, self.name = t, None, ""
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 8)
        v.setSpacing(6)
        self.title = QLabel(objectName="RowLabel")
        self.title.setWordWrap(True)
        self.bar = QProgressBar(objectName="ExpBar")
        self.bar.setRange(0, 0)                      # busy: the installers don't report a percentage
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.detail = QLabel(objectName="RowHint")   # the installer's own words, English: left to right
        self.detail.setLayoutDirection(Qt.LeftToRight)
        self.detail.setAlignment((Qt.AlignRight if t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute)
        self.error = QLabel(objectName="RowHint")
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for w in (self.title, self.bar, self.detail, self.error):
            v.addWidget(w)
        self._timer = QTimer(self, interval=400)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self, inst, name: str):
        self.inst, self.name = inst, name
        self.title.setText(bidi.plain(self.t("install_running", name=name), self.t.rtl))
        self.detail.setText(self.t("install_starting"))
        self.bar.show()
        self.detail.show()
        self.error.hide()
        self.show()
        self._timer.start()

    def _tick(self):
        line = self.inst.status()
        if line:
            self.detail.setText(line if len(line) <= 70 else line[:67] + "…")
        if not self.inst.done.is_set():
            return
        self._timer.stop()
        # the exit code alone doesn't decide (Antigravity's installer reported -1 after installing fine):
        # the dialog checks whether the CLI is there and calls fail() only when it isn't
        self.ended.emit(self.inst.code == 0)

    def fail(self):
        """The CLI isn't there after the installer ended: say so, with the installer's own reason."""
        t, inst = self.t, self.inst
        not_found = bool(inst) and inst.code == 0          # it said it worked: then it's simply missing
        self._timer.stop()
        self.bar.hide()
        self.detail.hide()
        self.title.setText(bidi.plain(t("install_failed", name=self.name), t.rtl))
        if not_found:
            why = t("install_not_found", name=self.name)
        elif inst and inst.error():
            why = t("install_failed_reason") + "\n" + inst.error()
        else:
            why = t("install_failed_none", code=getattr(inst, "code", "?"))
        self.error.setText(why)
        self.error.show()
        self.show()

    def stop(self):
        """Hide it (the installer itself goes on; its result no longer matters here)."""
        self._timer.stop()
        self.hide()


def _code_row(t: I18n, on_send) -> tuple[QWidget, QLineEdit]:
    """Gemini's sign-in ends with a code the browser shows: a field to paste it, shown while that sign-in waits."""
    edit = QLineEdit()
    edit.setPlaceholderText(t("ob_code_hint"))
    edit.setAccessibleName(t("ob_code_hint"))          # a placeholder isn't read as the field's name
    edit.setLayoutDirection(Qt.LeftToRight)                       # the code itself is Latin
    edit.returnPressed.connect(on_send)
    btn = QPushButton(t("ob_code_send"), objectName="Secondary")
    btn.setCursor(Qt.PointingHandCursor)
    btn.clicked.connect(on_send)
    row = AdaptiveRow(edit, btn, main_min=200)
    row.setContentsMargins(0, 6, 0, 8)
    row.hide()
    return row, edit


class _Bridge(QObject):
    status = Signal(str, str)      # provider, status
    account = Signal(object)
    logged_out = Signal()
    key_checked = Signal(str, str, bool)   # provider, API key, it works


class CharacterForm(QWidget):
    """Name, class (cards), level, job. Used by onboarding and 'add character'."""

    changed = Signal()

    def __init__(self, t: I18n, kb: KnowledgeBase):
        super().__init__()
        self.t, self.kb = t, kb
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(_field(t("ob_char_name")))
        self.name = QLineEdit()
        self.name.setAccessibleName(t("ob_char_name"))     # its label above is a separate QLabel
        self.name.setMaxLength(24)
        self.name.textChanged.connect(lambda *_: (self._check_name(), self.changed.emit()))
        lay.addWidget(self.name)
        # the player's other characters' names (case-folded): a second "Amit" made two identical chips
        self.taken: set[str] = set()
        self.name_hint = QLabel(bidi.plain(t("ob_name_taken"), t.rtl), objectName="JobHint")
        self.name_hint.setWordWrap(True)
        self.name_hint.hide()
        lay.addWidget(self.name_hint)

        lay.addWidget(_field(t("ob_class")))
        grid = QGridLayout()
        self.class_group = QButtonGroup(self)
        self.class_group.setExclusive(True)
        for i, cls in enumerate(JOBS):
            # the picture above the name: beside it, a tile at 470 px had no room left ("Warrior" was cut, seen live)
            b = QToolButton(objectName="ClassTile")
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # three per row share the width
            b.setMinimumHeight(72)
            b.setCursor(Qt.PointingHandCursor)
            img = kb.image_path(f"class/{cls.lower()}")
            label = cls if t.lang == "en" else f"{CLASS_HE[cls]}\n{cls}"
            b.setText(label)
            if img:
                from PySide6.QtGui import QIcon
                b.setIcon(QIcon(str(img)))
                b.setIconSize(QPixmap(str(img)).size().scaled(36, 36, Qt.KeepAspectRatio))
            b.setProperty("cls", cls)
            self.class_group.addButton(b)
            grid.addWidget(b, i // 3, i % 3)
        self.class_group.buttonToggled.connect(lambda *_: (setattr(self, "_job_picked", False), self._refresh_jobs()))
        lay.addLayout(grid)

        row = QHBoxLayout()
        col1 = QVBoxLayout()
        col1.addWidget(_field(t("ob_level")))
        self.level = Stepper(1, LEVEL_FIELD_MAX, 1)
        self.level.set_label(t("ob_level"), t("step_less"), t("step_more"))
        self.level.valueChanged.connect(lambda *_: self._refresh_jobs())
        col1.addWidget(self.level)
        row.addLayout(col1)
        col2 = QVBoxLayout()
        self.job_label = _field(t("ob_job"))
        col2.addWidget(self.job_label)
        self.job = Select()
        self.job.set_label(t("ob_job"))
        self.job.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self._job_picked = False       # the user chose a job by hand: keep it while it stays available
        self.job.picked.connect(lambda *_: setattr(self, "_job_picked", True))
        col2.addWidget(self.job)
        self.job_fixed = QLabel("Beginner", objectName="JobFixed")
        self.job_fixed.setAccessibleDescription(t("ob_job"))
        # an English word in a Hebrew form still starts on the right, like the other fields
        self.job_fixed.setAlignment((Qt.AlignRight if t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter)
        self.job_fixed.hide()
        col2.addWidget(self.job_fixed)
        row.addLayout(col2, 1)
        lay.addLayout(row)
        self.job_hint = QLabel(objectName="JobHint")
        self.job_hint.setWordWrap(True)
        lay.addWidget(self.job_hint)
        # the profile keeps itself current from screenshots: say so, so nobody feels they must maintain it
        note = QFrame(objectName="InfoNote")
        note.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        nl = QHBoxLayout(note)
        nl.setContentsMargins(12, 10, 12, 10)
        nl.setSpacing(10)
        icon = QLabel(theme.ICON["info"], objectName="InfoIcon")
        nl.addWidget(icon, 0, Qt.AlignTop)
        text = QLabel(bidi.plain(t("auto_profile_note"), t.rtl), objectName="InfoText")
        text.setWordWrap(True)
        nl.addWidget(text, 1)
        lay.addSpacing(6)
        lay.addWidget(note)
        lay.addStretch(1)
        # no class yet: no job field at all (it showed as an empty dropdown with only its arrows, VIS-20)
        self._refresh_jobs()

    def base_class(self) -> str | None:
        b = self.class_group.checkedButton()
        return b.property("cls") if b else None

    def _refresh_jobs(self):
        cls = self.base_class()
        previous = self.current_job()
        hint = ""
        jobs = ["Beginner"] if cls == "Beginner" else []
        if cls and cls != "Beginner":
            # a class is chosen at its 1st job, so its level starts there (10 for all four: assets/official)
            first_level = next(lv for j, lv in JOBS[cls] if j != "Beginner")
            self.level.setMinimum(first_level)
            jobs = [j for j in jobs_for(cls, self.level.value(), self.kb) if j != "Beginner"]
            upcoming = [(j, lv) for j, lv in open_jobs(cls, self.kb) if lv > self.level.value()]
            if upcoming:
                lv = upcoming[0][1]
                names = [job_label(job, self.t.lang) for job, need in upcoming if need == lv]
                hint = (self.t("job_hint_next", job=names[0], level=lv) if len(names) == 1 else
                        self.t("job_hint_next_many", jobs=", ".join(names), level=lv))
        else:
            self.level.setMinimum(1)
        # one possible job → a fixed field; a real choice → a dropdown
        single = len(jobs) <= 1
        self.job.setVisible(bool(cls) and not single)
        self.job_fixed.setVisible(bool(cls) and single)
        self.job_label.setVisible(bool(cls))
        # shown in the player's language (Hebrew beside the game's English name); the values stay English
        self._fixed_job = jobs[0] if jobs else ""
        self.job_fixed.setText(job_label(self._fixed_job, self.t.lang) if self._fixed_job else "")
        self._job_values = [] if single else jobs
        self.job.clear()
        if not single:
            self.job.addItems([job_label(j, self.t.lang) for j in jobs])
            if self._job_picked and previous in jobs:
                self.job.setCurrentIndex(jobs.index(previous))
            else:
                # a real choice (Fighter, Page or Spearman…): the player picks; a guessed job was usually wrong
                self.job.show_none(bidi.plain(self.t("ob_pick_job"), self.t.rtl))
        self.job_hint.setText(bidi.plain(hint, self.t.rtl) if hint else "")
        self.job_hint.setVisible(bool(hint))
        self.changed.emit()

    def load(self, c) -> None:
        """Pre-fill for editing an existing character."""
        self.name.setText(c.name)
        for b in self.class_group.buttons():
            if b.property("cls") == c.base_class:
                b.setChecked(True)
        self.level.setValue(c.level)
        self._refresh_jobs()
        if c.job in self._job_values:
            self.job.setCurrentIndex(self._job_values.index(c.job))
        self._job_picked = True        # the saved job is the player's choice

    def current_job(self) -> str:
        """The job's English name (what the profile keeps), whatever language the list shows."""
        if self.job_fixed.isVisibleTo(self):
            return getattr(self, "_fixed_job", "")
        i = self.job.currentIndex()
        values = getattr(self, "_job_values", [])
        return values[i] if 0 <= i < len(values) else ""

    def _name_taken(self) -> bool:
        return self.name.text().strip().casefold() in self.taken

    def _check_name(self) -> None:
        self.name_hint.setVisible(self._name_taken())

    def valid(self) -> bool:
        return (bool(self.name.text().strip()) and not self._name_taken() and bool(self.base_class())
                and bool(self.current_job()))

    def values(self) -> tuple[str, str, str, int]:
        return self.name.text().strip(), self.base_class(), self.current_job(), self.level.value()


class Onboarding(GlassDialog):
    """Language → AI connection (Claude or Codex) → character. Every step is required."""

    report_requested = Signal()     # "Report a problem" on the connect page, before Settings exist

    def __init__(self, settings: Settings, profiles: Profiles, kb: KnowledgeBase, stylesheet_fn, only_character=False,
                 edit_id: str | None = None):
        # never chosen yet: the system's language (UX-3); the player can still pick the other on the first page
        self.t = I18n(settings["language"] or system_language())
        self.edit_id = edit_id
        only_character = only_character or edit_id is not None
        # the window title is what the taskbar, Alt+Tab and screen readers show
        title = (self.t("edit_character") if edit_id else
                 self.t("add_character") if only_character else "Maple Helper")
        super().__init__(title, self.t.rtl)
        self.settings, self.profiles, self.kb = settings, profiles, kb
        self.stylesheet_fn = stylesheet_fn
        self.only_character = only_character
        # Esc must not quit the first-run setup (closing it quits the app); adding a character can be cancelled
        self.esc_closes = only_character
        self.fit_screen(600, 680)
        self._bridge = _Bridge()
        self._bridge.status.connect(self._on_status)
        self._bridge.key_checked.connect(self._on_key_checked)
        self.provider = providers.get(settings["provider"]).name
        self._ai_ok = False
        self._signing_in = False   # a sign-in/install is under way: keep the hint, re-check quietly
        self._closed = False
        # the sign-in is one for the whole app: only the window that can start one ends it (not "Add character")
        if not only_character:
            self.finished.connect(lambda *_: stop_login())
        self.finished.connect(self._on_closed)
        self._build()

    def _build(self):
        self.title_label.hide()
        self.setStyleSheet(self.stylesheet_fn(1.0))
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(10, 4, 10, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        nav = QHBoxLayout()
        self.back = QPushButton(self.t("ob_back"), objectName="Secondary")
        self.next = QPushButton(self.t("ob_next"), objectName="Primary")
        self.back.clicked.connect(self._go_back)
        self.next.clicked.connect(self._go_next)
        nav.addWidget(self.back)
        nav.addStretch(1)
        nav.addWidget(self.next)
        outer.addLayout(nav)

        self.pages = []
        if not self.only_character:
            self.pages.append(self._page_language())
            self.pages.append(self._page_ai())
        self.pages.append(self._page_character())
        if not self.only_character:
            self.pages.append(self._page_done())
        for p in self.pages:
            # each step scrolls on its own, so on a short screen Next stays visible under it
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(p)
            self.stack.addWidget(scroll)
        rtl_buttons(self, self.t.rtl)
        no_default_buttons(self)
        self.enter_button = self.next
        self._update_nav()

    # pages ---------------------------------------------------------------

    def _page_language(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        logo = QLabel()
        wm = ASSETS / "brand" / "wordmark.png"
        if wm.exists():
            logo.setPixmap(QPixmap(str(wm)).scaled(260, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)
        # both languages: the player hasn't picked one yet
        for lang in ("he", "en"):
            lb = _body(I18n(lang)("app_tagline"))
            lb.setAlignment(Qt.AlignHCenter)
            lay.addWidget(lb)
        lay.addSpacing(16)
        row = QHBoxLayout()
        self.lang_group = QButtonGroup(self)
        for code, label in (("he", "עברית"), ("en", "English")):
            b = QPushButton(label, objectName="Quick")
            b.setCheckable(True)
            b.setMinimumHeight(56)
            b.setProperty("lang", code)
            if self.t.lang == code:
                b.setChecked(True)
            self.lang_group.addButton(b)
            row.addWidget(b)
        self.lang_group.buttonClicked.connect(self._on_language)
        lay.addLayout(row)
        lay.addStretch(1)
        return w

    def _page_ai(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        lay.addWidget(_title(self.t("ob_connect")))
        rtl = self.t.rtl
        self.provider_pick = Segmented([(p.label, p.name) for p in providers.PROVIDERS.values()], self.provider, rtl)
        self.provider_pick.set_label(self.t("ai_provider"))
        self.provider_pick.changed.connect(self._on_provider)
        for b in self.provider_pick.group.buttons():       # each AI's cost, on hover too (UX-10)
            b.setToolTip(bidi.plain(self.t.p("ob_need_plan", b.property("value")), rtl))
        prow = QHBoxLayout()
        prow.addWidget(self.provider_pick)
        prow.addStretch(1)
        lay.addLayout(prow)
        # which AI a player without a paid plan can start with, before they click through all four
        overview = QLabel(bidi.plain(self.t("ob_plans_overview"), rtl), objectName="RowHint")
        overview.setWordWrap(True)
        lay.addWidget(overview)
        self.ai_body = _body("")
        lay.addWidget(self.ai_body)
        lay.addSpacing(6)
        sec = self.account_sec = Section(self.t("sec_account"), rtl)
        # the status on its own line, its actions on a row under it (three links beside it squeezed the status
        # into a column one word wide and pushed the window past 470 px); the row wraps when it must
        status_row = QWidget()
        scol = QVBoxLayout(status_row)
        scol.setContentsMargins(0, 8, 0, 4)
        scol.setSpacing(2)
        self.status_label = QLabel(bidi.plain(self.t("ob_checking"), rtl), objectName="RowLabel")
        self.status_label.setWordWrap(True)
        scol.addWidget(self.status_label)
        srow = FlowLayout(spacing=18, line_spacing=0)
        scol.addLayout(srow)
        self.install_btn = QPushButton(objectName="Link")
        self.login_btn = QPushButton(objectName="Link")
        self.check_btn = QPushButton(self.t("ob_check"), objectName="Link")
        self.install_btn.clicked.connect(self._start_install)
        self.login_btn.clicked.connect(self._start_login)
        self.check_btn.clicked.connect(self._check_status)
        for b in (self.install_btn, self.login_btn, self.check_btn):
            b.setCursor(Qt.PointingHandCursor)
            srow.addWidget(b)
        self.install_btn.hide()
        self.login_btn.hide()
        sec.add_widget(status_row)
        self.login_hint = QLabel(objectName="RowHint")
        self.login_hint.setWordWrap(True)
        self.login_hint.hide()
        sec.add_widget(self.login_hint)
        self.code_row, self.code_edit = _code_row(self.t, self._send_code)
        sec.add_widget(self.code_row)
        self.install_panel = InstallPanel(self.t)
        self.install_panel.ended.connect(self._install_ended)
        self._install_for, self._install_check = None, False
        sec.add_widget(self.install_panel)
        lay.addWidget(sec)
        lay.addSpacing(8)
        sec = Section(self.t("ob_use_api_key"), rtl)
        self.key_edit = QLineEdit()
        self.key_edit.setAccessibleName(self.t("ob_use_api_key"))     # the section header names it on screen
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setLayoutDirection(Qt.LeftToRight)
        self.key_edit.returnPressed.connect(self._check_key)      # Enter checks the pasted key
        self.key_edit.textChanged.connect(self._key_direction)
        self.key_btn = QPushButton(self.t("ob_check_key"), objectName="Secondary")
        self.key_btn.setCursor(Qt.PointingHandCursor)
        self.key_btn.clicked.connect(self._check_key)
        # the field takes the whole width; its button beside it only when there is room, else under it
        kbox = AdaptiveRow(self.key_edit, self.key_btn, main_min=260)
        kbox.setContentsMargins(0, 10, 0, 10)
        sec.add_widget(kbox)
        self.key_hint = QLabel(objectName="RowHint")
        self.key_hint.setWordWrap(True)
        self.key_hint.hide()
        sec.add_widget(self.key_hint)
        lay.addWidget(sec)
        # not connected: why Next waits, and a way on without any AI (the Play tools need none, UX-10)
        self.no_ai_note = QLabel(bidi.plain(self.t("ob_no_ai_note"), rtl), objectName="RowHint")
        self.no_ai_note.setWordWrap(True)
        lay.addWidget(self.no_ai_note)
        self.skip_ai_btn = QPushButton(self.t("ob_skip_ai"), objectName="Link")
        self.skip_ai_btn.setCursor(Qt.PointingHandCursor)
        self.skip_ai_btn.clicked.connect(self._skip_ai)
        lay.addWidget(self.skip_ai_btn, 0, Qt.AlignLeading)
        lay.addStretch(1)
        report_btn = QPushButton(self.t("report_problem"), objectName="Link")
        report_btn.setCursor(Qt.PointingHandCursor)
        report_btn.clicked.connect(self.report_requested.emit)
        lay.addWidget(report_btn, 0, Qt.AlignHCenter)
        self._label_ai_page()
        return w

    def _ai(self):
        return providers.get(self.provider)

    def _key_direction(self, *_):
        """The key itself is English (left to right); the empty field shows the hint in the UI's direction."""
        rtl = self.t.rtl and not self.key_edit.text()
        self.key_edit.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        self.key_edit.setAlignment((Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter)

    def _label_ai_page(self):
        """Texts of the connect page for the chosen provider."""
        t, p = self.t, self.provider
        self.ai_body.setText(bidi.plain(t.p("ob_connect_body", p) + " " + t.p("ob_need_plan", p), t.rtl))
        self.account_sec.set_header(t.p("sec_account", p))
        self.install_btn.setText(t.p("ob_install", p))
        self.login_btn.setText(t.p("ob_login", p))
        self.key_edit.clear()
        # a Hebrew hint reads right to left while the field is empty (_key_direction), and the key's prefix
        # ("sk-ant-", "AIza") stays one block in it, not "ב--sk-ant" (ltr_block inside the Hebrew sentence)
        hint = t.p("ob_api_key_hint", p)
        if t.rtl:
            prefix = next((x for x in ("sk-ant-", "sk-", "AIza", "xai-") if x in hint), "")   # (Grok's read "-xai")
            if prefix:
                hint = hint.replace(prefix, bidi.ltr_block(prefix, True))
            hint = bidi.plain(hint, True)
        self.key_edit.setPlaceholderText(hint)
        self._key_direction()
        self.key_hint.hide()
        if getattr(self, "privacy_label", None):          # the done page is built after this one
            self.privacy_label.setText(bidi.plain(t.p("ob_privacy", p), t.rtl))

    def _on_provider(self, name: str):
        self.provider = self.settings["provider"] = name
        self._ai_ok = False
        stop_login()                       # a sign-in still waiting belongs to the AI the player left
        self._end_sign_in()
        self.install_panel.stop()
        self._install_check = False
        if running_install(name):          # back to an AI whose installer still runs: show it again
            self._install_for = name
            self.install_panel.start(running_install(name), self._ai().label)
        self.install_btn.hide()
        self.login_btn.hide()
        self._label_ai_page()
        self._check_status()
        self._update_nav()

    def _page_character(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        heading = (self.t("edit_character") if self.edit_id else
                   self.t("add_character") if self.only_character else self.t("ob_welcome"))
        lay.addWidget(_title(heading))
        self.form = CharacterForm(self.t, self.kb)
        self.form.taken = {c.name.strip().casefold() for c in self.profiles.characters if c.id != self.edit_id}
        if self.edit_id:
            c = next((c for c in self.profiles.characters if c.id == self.edit_id), None)
            if c:
                self.form.load(c)
        self.form.changed.connect(self._update_nav)
        self.form.name.returnPressed.connect(self._go_next)       # Enter after the name moves on (when ready)
        lay.addWidget(self.form, 1)
        return w

    def _page_done(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(14)
        mascot = QLabel()
        m = ASSETS / "brand" / "mascot.png"
        if m.exists():
            mascot.setPixmap(QPixmap(str(m)).scaled(220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        mascot.setAlignment(Qt.AlignCenter)
        lay.addWidget(mascot)
        title = _title(self.t("ob_done_hint"))
        title.setAlignment(Qt.AlignHCenter)
        lay.addWidget(title)
        lay.addSpacing(4)
        sec = Section("", self.t.rtl)
        sec.add_row(self.t("ob_borderless"))
        self.privacy_label = sec.add_row(self.t.p("ob_privacy", self.provider)).findChild(QLabel, "RowLabel")
        sec.add_row(self.t("disclaimer"))
        lay.addWidget(sec)
        note = QLabel(bidi.plain(self.t("unofficial"), self.t.rtl), objectName="RowHint")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignHCenter)
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    # logic ---------------------------------------------------------------

    RESTART = 2
    _carried: dict | None = None     # what the player typed, kept across a language restart (restart_on_language)

    def _on_language(self, btn):
        lang = btn.property("lang")
        if lang != self.t.lang:
            # reopen in the chosen language (the app loops on RESTART). The new dialog is built from scratch:
            # carry over what was typed, or a character filled in before going back here was lost
            f = self.form
            Onboarding._carried = {"name": f.name.text(), "base_class": f.base_class(), "level": f.level.value(),
                                   "job": f.current_job() if f._job_picked else "", "provider": self.provider,
                                   "key": self.key_edit.text()}
            self.settings["language"] = lang
            self.done(self.RESTART)
            return
        self.settings["language"] = lang
        self._update_nav()

    def _start_login(self):
        if login_waiting():
            return                 # one is waiting for the browser already: a second opened another tab
        self._begin_sign_in()
        self._login_proc = self._ai().login()
        if self._login_proc is None:
            # the sign-in couldn't even start: say so, and offer the official installer instead
            self._end_sign_in()
            set_hint(self.login_hint, self.t.p("ob_login_failed", self.provider), self.t.rtl)
            self.login_hint.show()
            self.install_btn.show()
            return
        set_hint(self.login_hint, self.t.p("ob_login_wait", self.provider), self.t.rtl)
        self.login_hint.show()
        self.install_btn.show()     # the way out when no sign-in window shows up
        if self._ai().login_code:
            self.code_edit.clear()
            self.code_row.show()
        self._poll_status(180)

    def _send_code(self):
        code = self.code_edit.text().strip()
        if not code:
            return
        if self._ai().submit_login_code(code):
            self.code_row.hide()
            set_hint(self.login_hint, self.t("ob_code_sent"), self.t.rtl)
        else:                       # the sign-in already gave up (it waits one minute)
            self.code_row.hide()
            set_hint(self.login_hint, self.t.p("ob_login_failed", self.provider), self.t.rtl)

    def _on_closed(self, *_):
        """Closed (or restarted in another language): its timers stop, so it never shows itself again."""
        self._closed = True
        if hasattr(self, "_poll_timer"):
            self._poll_timer.stop()

    def _start_install(self):
        """The official installer, in the background: its progress and errors show here, no console."""
        ai = self._ai()
        stop_login()                       # a sign-in still waiting holds the CLI's file (agy.exe is locked)
        self._end_sign_in()
        for w in (self.install_btn, self.login_btn, self.login_hint, self.code_row):
            w.hide()
        self._install_for = ai.name
        self._install_check = False
        self.install_panel.start(install_for(ai), ai.label)

    def _install_ended(self, ok: bool):
        if self._install_for != self.provider:
            return                         # the player picked another AI meanwhile
        self._install_check = True         # the status check says whether it's there, and what's next (sign in)
        self._check_status()

    def _begin_sign_in(self):
        """The sign-in console, the installer and the browser open as normal windows: stop staying on top
        so they don't hide behind this one (where a click on "Sign in" looked like it did nothing)."""
        self._signing_in = True
        if self.windowFlags() & Qt.WindowStaysOnTopHint:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
            self.show()                                          # (changing a flag hides the window)

    def _end_sign_in(self):
        self._signing_in = False
        if hasattr(self, "code_row"):
            self.code_row.hide()
        if hasattr(self, "_poll_timer"):
            self._poll_timer.stop()
        if not self.windowFlags() & Qt.WindowStaysOnTopHint and not self._closed:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)   # (changing a flag hides the window)
            self.show()
            self.raise_()
            self.activateWindow()

    def _check_status(self):
        if not self._signing_in:
            self.status_label.setText(bidi.plain(self.t("ob_checking"), self.t.rtl))
        ai = self._ai()
        threading.Thread(target=lambda: self._bridge.status.emit(ai.name, _safe_status(ai)), daemon=True).start()

    def _poll_status(self, seconds: int):
        """One timer for the dialog: a second sign-in click used to start another, and the first kept running."""
        self._poll_left = seconds // 3
        if not hasattr(self, "_poll_timer"):
            self._poll_timer = QTimer(self, interval=3000)
            self._poll_timer.timeout.connect(self._poll_tick)
        self._poll_timer.start()

    def _poll_tick(self):
        self._poll_left -= 1
        if self._ai_ok:
            self._poll_timer.stop()
            self._signing_in = False
            return
        if self._poll_left <= 0:
            # gave up waiting: back on top, and no more "this updates by itself" for a check that stopped. The
            # sign-in still waiting ends too, or the "click the sign-in button again" this says did nothing
            stop_login()
            self._login_proc = None
            self._end_sign_in()
            set_hint(self.login_hint, self.t("sign_in_timeout"), self.t.rtl)
            self.login_hint.show()
            self._check_status()
            return
        if login_failed(getattr(self, "_login_proc", None)):
            self._login_proc = None
            self._end_sign_in()
            set_hint(self.login_hint, self.t.p("ob_login_failed", self.provider), self.t.rtl)
            self.install_btn.setVisible(not self._ai().login_code)    # Gemini: sign in again, see _login_failed
            return
        self._check_status()

    @_while_open
    def _on_status(self, provider: str, st: str):
        if provider != self.provider:
            return            # a check that started before the player switched provider
        t = self.t
        signed_in = st == "ok"
        # a key that checked out counts as connected, whatever the account check says (it reads the sign-in);
        # but only with the CLI there: a key alone answered every question with "not installed"
        self._ai_ok = signed_in or (self.settings.api_key_mode(provider) and st != "not_installed")
        if self._ai_ok:
            st = "ok"
        text = {"ok": t("ob_connected"), "logged_out": t.p("ob_not_logged", provider),
                "not_installed": t.p("ob_not_installed", provider),
                "offline": t("ob_offline", name=providers.get(provider).label)}[st]
        self.status_label.setText(bidi.plain(text, t.rtl))
        self.login_btn.setVisible(st == "logged_out")
        if self._install_check:
            self._install_check = False
            if st == "not_installed":      # the CLI isn't there: why, and "Install" again
                self.install_panel.fail()
                self.install_btn.show()
                return
            self.install_panel.stop()
        if self._ai_ok:
            if self._signing_in:
                self._end_sign_in()      # back on top, showing "Connected"
            self.login_hint.hide()
            self.install_btn.hide()
            if signed_in:
                # the account itself works: answers go through it, not the key. Only then: a stored key is what
                # made a signed-out check "connected", and dropping its mode here left the player with no AI
                self.settings.set_api_key_mode(provider, False)
        elif not self._signing_in:
            self.install_btn.setVisible(st == "not_installed")
        self._update_nav()

    def _check_key(self):
        key = self.key_edit.text().strip()
        if not key or not self.key_btn.isEnabled():
            return                      # nothing pasted, or a check is already running
        if not key.isascii():
            # a key is plain Latin letters and digits; anything else can't even be sent (it raised before)
            self._key_message(self.t("ob_key_bad_chars"))
            return
        ai = self._ai()
        self.key_btn.setEnabled(False)
        self._key_message(self.t("ob_checking"))

        def work():
            try:
                ok = ai.test_api_key(key)
            except Exception:
                ok = False
            self._bridge.key_checked.emit(ai.name, key, ok)
        # the check can take up to 15 seconds: off the GUI thread, so the window doesn't freeze
        threading.Thread(target=work, daemon=True).start()

    @_while_open
    def _on_key_checked(self, provider: str, key: str, ok: bool):
        self.key_btn.setEnabled(True)
        if provider != self.provider:
            self.key_hint.hide()
            return            # the player switched provider while the key was checked
        if ok:
            ai = providers.get(provider)
            try:
                ai.save_api_key(key)
            except Exception:  # noqa: BLE001 - a locked or refusing keychain: say so, the key isn't kept
                log.exception("saving the API key failed")
                self._key_message(self.t("ob_key_not_saved"))
                self._update_nav()
                return
            self.settings.set_api_key_mode(provider, True)
            self.key_hint.hide()
            if ai.find_exe():
                self._ai_ok = True
                self.status_label.setText(bidi.plain(self.t("ob_connected"), self.t.rtl))
            else:
                # the key runs through the AI's CLI: without it every answer failed "not installed"
                self.status_label.setText(bidi.plain(self.t.p("ob_not_installed", provider), self.t.rtl))
                self._key_message(self.t.p("ob_key_saved_install", provider))
                if not self._signing_in:
                    self.install_btn.show()
        else:
            self._key_message(self.t("ob_key_failed"))
        self._update_nav()

    def _key_message(self, text: str):
        set_hint(self.key_hint, text, self.t.rtl)
        self.key_hint.show()

    def showEvent(self, e):
        super().showEvent(e)
        if not self.only_character:
            self._check_status()

    def _current_ok(self) -> bool:
        i = self.stack.currentIndex()
        if not 0 <= i < len(self.pages):
            return False
        page = self.pages[i]                # (the stack holds each page inside its scroll area)
        if not self.only_character and page is self.pages[0]:
            return self.lang_group.checkedButton() is not None
        if not self.only_character and page is self.pages[1]:
            return self._ai_ok
        if page.findChild(CharacterForm):
            return self.form.valid()
        return True

    def _update_nav(self):
        i = self.stack.currentIndex()
        self.back.setVisible(i > 0)
        last = i == self.stack.count() - 1
        # adding a character from the app (not the first run) says so, not "Done, let's play!" (UX-23)
        finish = self.t("save_changes") if self.edit_id else self.t("add_character" if self.only_character else "ob_finish")
        self.next.setText(bidi.plain(finish if last else self.t("ob_next"), self.t.rtl))
        self.next.setEnabled(self._current_ok())
        if hasattr(self, "skip_ai_btn"):
            for w in (self.no_ai_note, self.skip_ai_btn):
                w.setVisible(not self._ai_ok)

    def _skip_ai(self):
        """On to the character without an AI: the Play tools work without one, and Settings connects one later."""
        if not self.only_character and self.stack.currentIndex() == 1:
            self.stack.setCurrentIndex(2)
            self._update_nav()

    def _go_back(self):
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._update_nav()

    def _go_next(self):
        if not self._current_ok():
            return
        if self.stack.currentIndex() == self.stack.count() - 1:
            if self.edit_id:
                self.profiles.edit(self.edit_id, *self.form.values())
            else:
                self.profiles.add(*self.form.values())
            if not self.only_character:
                self.settings["onboarding_done"] = True
            self.accept()
            return
        if not self.only_character and self.stack.currentIndex() == 0:
            # the pre-selected language, kept without a click: it was stored only on a click, so the AI got None
            # and answered a Hebrew player's "Mano" in English
            self.settings["language"] = self.lang_group.checkedButton().property("lang")
        self.stack.setCurrentIndex(self.stack.currentIndex() + 1)
        self._update_nav()

    def restart_on_language(self):
        """After a language restart, open straight on the AI step, with what was typed before it."""
        carried, Onboarding._carried = Onboarding._carried, None
        if self.only_character:
            return
        if carried:
            self._restore(carried)
        if self.stack.count() > 1:
            self.stack.setCurrentIndex(1)
            self._update_nav()

    def _restore(self, c: dict):
        from types import SimpleNamespace
        if c.get("base_class") or c.get("name"):
            self.form.load(SimpleNamespace(name=c["name"], base_class=c["base_class"], level=c["level"], job=c["job"]))
            self.form._job_picked = bool(c["job"])     # a job the player hadn't picked yet stays unpicked
        if c.get("key") and c.get("provider") == self.provider:
            self.key_edit.setText(c["key"])


class ConfirmDialog(GlassDialog):
    """Glass confirmation for destructive actions only (Apple: use sparingly)."""

    def __init__(self, title: str, body: str, confirm: str, cancel: str, rtl: bool, stylesheet: str, danger=True):
        super().__init__(title, rtl)
        self.setStyleSheet(stylesheet)
        self.resize(420, 230)
        lay = QVBoxLayout(self.content)
        msg = QLabel(bidi.plain(body, rtl), objectName="DialogBody")
        msg.setWordWrap(True)
        lay.addWidget(msg)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        no = QPushButton(cancel, objectName="Secondary")
        yes = QPushButton(confirm, objectName="Danger" if danger else "Primary")
        for b in (no, yes):
            b.setCursor(Qt.PointingHandCursor)
        self.choice = None          # "yes" / "no" (a button), None (closed with X or Esc: neither)
        no.clicked.connect(lambda: (setattr(self, "choice", "no"), self.reject()))
        yes.clicked.connect(lambda: (setattr(self, "choice", "yes"), self.accept()))
        row.addWidget(no)
        row.addWidget(yes)
        lay.addLayout(row)
        rtl_buttons(self, rtl)
        self.initial_focus = no      # the safe answer has the focus (Enter / Space never confirms by accident)
        no.setFocus()


class SettingsDialog(GlassDialog):
    changed = Signal()
    update_kb_requested = Signal()
    history_cleared = Signal()
    report_requested = Signal()
    account_changed = Signal()
    patch_notes_requested = Signal()
    whats_new_requested = Signal()
    tour_requested = Signal()
    _mic_heard = Signal(float)       # the microphone test's loudness (RMS), -1 when it couldn't record

    def __init__(self, settings: Settings, profiles: Profiles, kb: KnowledgeBase, stylesheet_fn):
        self.t = t = I18n(settings["language"] or "he")
        super().__init__(t("settings"), t.rtl)
        self.settings, self.profiles, self.kb = settings, profiles, kb
        self.stylesheet_fn = stylesheet_fn
        self.setStyleSheet(stylesheet_fn(1.0))
        self.fit_screen(500, 720)
        # Esc would close without saving, like a stray key press in the game; the close button stays the way out
        self.esc_closes = False
        rtl = t.rtl

        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(*gutter(rtl))          # the room before the scrollbar, on its side (left in Hebrew)
        lay.setSpacing(18)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # appearance
        sec = Section(t("sec_appearance"), rtl)
        self.appearance = Segmented([(t("appearance_dark_short"), "dark"), (t("appearance_light_short"), "light")],
                                    settings["appearance"], rtl)
        sec.add_row(t("appearance"), self.appearance)
        self.font = Segmented([("A", 13), ("A", 14), ("A", 16)], settings["font_size"], rtl)
        # small / medium / large "A" (the stylesheet wins over setFont, so size it there)
        for i, b in enumerate(self.font.group.buttons()):
            b.setStyleSheet(f"font-size: {11 + i * 4}px; font-weight: 600;")
            b.setAccessibleName(t(("font_small", "font_medium", "font_large")[i]))   # three "A"s, read aloud
        sec.add_row(t("font_size"), self.font)
        self.lang = Segmented([("עברית", "he"), ("English", "en")], settings["language"] or "he", rtl)
        sec.add_row(t("language"), self.lang)
        lay.addWidget(sec)

        # audio
        sec = Section(t("sec_audio"), rtl)
        # the microphone: the system default was a far webcam/USB mic on a PC whose player talks into a headset,
        # and the model then "heard" sentences nobody said
        from .. import voice
        self.voice_send = Switch(settings["voice_send_immediately"])
        sec.add_row(t("voice_send"), self.voice_send)
        self.mics = voice.input_devices()
        self.mic = Select()
        self.mic.text_width = 190         # "Microphone (Logitech PRO X Wireless Gaming Headset)" widened the window
        # "Microphone (Logitech PRO X …)" → "Logitech PRO X …": every Windows name starts the same way
        self.mic.addItems([t("mic_default")] + [re.sub(r"^Microphone \((.+)\)$", r"\1", m) for m in self.mics])
        if settings["microphone"] in self.mics:
            self.mic.setCurrentIndex(self.mics.index(settings["microphone"]) + 1)
        sec.add_row(t("microphone"), self.mic)
        self.mic_test = QPushButton(t("mic_test"), objectName="Link")
        self.mic_test.clicked.connect(self._test_mic)
        self.mic_result = QLabel("", objectName="RowHint")
        self.mic_result.setWordWrap(True)
        sec.add_row(t("mic_test_label"), self.mic_test)
        sec.add_widget(self.mic_result)
        self.mic_result.hide()
        self._mic_heard.connect(self._mic_tested)
        self.voice_lang = Segmented([(t("voice_lang_app"), "app"), (t("voice_lang_auto"), "auto")],
                                    settings["voice_language"], rtl)
        sec.add_row(t("voice_lang"), self.voice_lang, hint=t("voice_lang_hint"), hint_below=True)
        lay.addWidget(sec)

        # answers
        sec = Section(t("sec_answers"), rtl)
        self.length = Segmented([(t("short"), "short"), (t("detailed"), "detailed")], settings["answer_length"], rtl)
        sec.add_row(t("answer_length"), self.length)
        self.instant = Switch(settings["instant_answers"])
        sec.add_row(t("instant_answers"), self.instant, hint=t.p("instant_answers_hint", settings["provider"]))
        # how often the minimap box is read (where the player is, under the level on the character card)
        self.scan = QDoubleSpinBox()
        self.scan.setRange(0.2, 60.0)
        self.scan.setSingleStep(0.5)
        self.scan.setDecimals(1)
        try:
            scan_value = float(settings["minimap_scan_interval"])
        except (TypeError, ValueError):
            scan_value = 1.0
        if not math.isfinite(scan_value):
            scan_value = 1.0
        self.scan.setValue(min(60.0, max(0.2, scan_value)))
        sec.add_row(t("minimap_scan_interval"), self.scan, hint=t("minimap_scan_hint"), hint_below=True)
        lay.addWidget(sec)

        # AI account: the provider and the model wait for Save like every other setting ("Don't save" kept a
        # provider picked only to read about it, UX-6); a sign-in, an install or a sign-out act right away
        self._provider = providers.get(settings["provider"]).name
        self._pending_models: dict[str, str | None] = {}      # model setting -> the model picked, until Save
        sec = Section(t("sec_ai"), rtl)
        self.provider_pick = Segmented([(p.label, p.name) for p in providers.PROVIDERS.values()],
                                       self._provider, rtl)
        self.provider_pick.changed.connect(self._on_provider)
        sec.add_row(t("ai_provider"), self.provider_pick)
        # the model; under it, which model answered last
        self.model_pick = Select()
        # the hint under the whole row: beside the dropdown it was squeezed into a narrow column
        self.model_hint = sec.add_row(t("ai_model"), self.model_pick, hint=" ", hint_below=True).findChild(QLabel, "RowHint")
        self.model_hint.setWordWrap(True)
        self.model_pick.picked.connect(self._on_model)
        self._model_values: list = []
        self._models_bridge = _Bridge()
        # a bound method, not a lambda: the lambda held the dialog from inside the bridge's C++ connection, where
        # Python's collector can't see it, so every Settings window opened stayed in memory with its bridges
        self._models_bridge.account.connect(self._on_models)
        self._fill_models()
        self.account_label = QLabel(bidi.plain(t("ob_checking"), rtl), objectName="RowLabel")
        self.account_label.setWordWrap(True)
        self.account_label.setContentsMargins(0, 10, 0, 10)
        sec.add_widget(self.account_label)
        self.account_hint = QLabel(objectName="RowHint")      # "didn't finish signing in? try again"
        self.account_hint.setWordWrap(True)
        self.account_hint.setContentsMargins(0, 0, 0, 8)
        self.account_hint.hide()
        sec.add_widget(self.account_hint)
        self.code_row, self.code_edit = _code_row(t, self._send_code)
        sec.add_widget(self.code_row)
        self.install_panel = InstallPanel(t)
        self.install_panel.ended.connect(self._install_ended)
        self._install_for, self._install_check = None, False
        sec.add_widget(self.install_panel)
        # not installed: the same official installer onboarding offers
        self.install_btn = QPushButton(t.p("ob_install", settings["provider"]), objectName="Link")
        self.install_btn.clicked.connect(self._start_install)
        sec.add_widget(self.install_btn)
        self.switch_btn = QPushButton(t("account_switch"), objectName="Link")
        self.switch_btn.clicked.connect(self._switch_account)
        sec.add_widget(self.switch_btn)
        self.logout_btn = QPushButton(t("account_logout"), objectName="LinkDanger")
        self.logout_btn.clicked.connect(self._logout)
        sec.add_widget(self.logout_btn)
        for b in (self.install_btn, self.switch_btn, self.logout_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.hide()
        lay.addWidget(sec)
        self._login_broken = False
        self._account_bridge = _Bridge()
        self._account_bridge.account.connect(self._on_account)
        self._account_bridge.logged_out.connect(self._after_logout)
        self._logout_for = None
        self._account_status = None
        self._login_proc = None
        self.finished.connect(lambda *_: stop_login())
        self._login_timer = QTimer(self, interval=3000)
        self._login_timer.timeout.connect(self._login_tick)
        # closed while a sign-in waited, the timer went on: its timeout put the closed window back on screen
        # (_set_on_top shows it), and its checks ran on for 3 minutes (it also crashed a later test, the review UI-6)
        self.finished.connect(lambda *_: self._login_timer.stop())
        self._refresh_account()

        # usage of the plan above (Claude reports it with each answer, ChatGPT when asked) and saver mode
        sec = self.usage_sec = Section(t("sec_usage"), rtl)
        self.usage_meter = QLabel(objectName="RowLabel")
        self.usage_meter.setWordWrap(True)
        self.usage_meter.setContentsMargins(0, 10, 0, 2)
        sec.add_widget(self.usage_meter)
        self.usage_note = QLabel(objectName="RowHint")
        self.usage_note.setContentsMargins(0, 0, 0, 8)
        sec.add_widget(self.usage_note)
        self.saver = Switch(settings["saver_mode"])
        self.saver_hint = sec.add_row(t("saver_mode"), self.saver, hint=t("saver_hint")).findChild(QLabel, "RowHint")
        lay.addWidget(sec)
        self._limits_bridge = _Bridge()
        self._limits_bridge.account.connect(self._on_limits)
        self._label_usage()

        # privacy & system
        sec = Section(t("sec_system"), rtl)
        self.autostart = Switch(settings["start_with_windows"])
        sec.add_row(t("start_at_login" if sys.platform == "darwin" else "start_with_windows"), self.autostart)
        self.telemetry = Switch(settings["telemetry"])
        sec.add_row(t("telemetry"), self.telemetry, hint=t("telemetry_hint"))
        lay.addWidget(sec)

        # data
        sec = Section(t("sec_data"), rtl)
        upd = QPushButton(t("update_kb"), objectName="Link")
        upd.setCursor(Qt.PointingHandCursor)
        upd.clicked.connect(self.update_kb_requested.emit)
        sec.add_widget(upd)
        notes = QPushButton(t("patch_notes"), objectName="Link")
        notes.setCursor(Qt.PointingHandCursor)
        notes.clicked.connect(self.patch_notes_requested.emit)
        sec.add_widget(notes)
        news = QPushButton(t("whats_new"), objectName="Link")
        news.setCursor(Qt.PointingHandCursor)
        news.clicked.connect(self.whats_new_requested.emit)
        sec.add_widget(news)
        tour = QPushButton(t("tour_replay"), objectName="Link")
        tour.clicked.connect(self.tour_requested.emit)
        sec.add_widget(tour)
        report_btn = QPushButton(t("report_problem"), objectName="Link")
        report_btn.setCursor(Qt.PointingHandCursor)
        report_btn.clicked.connect(self.report_requested.emit)
        sec.add_widget(report_btn)
        # where a report goes: the project's GitHub Issues (UX-8)
        issues = QPushButton(t("report_github"), objectName="Link")
        issues.setCursor(Qt.PointingHandCursor)
        issues.setToolTip(ISSUES_URL)
        issues.clicked.connect(lambda: osapi.open_url(ISSUES_URL))
        sec.add_widget(issues)
        clear = QPushButton(t("clear_history"), objectName="LinkDanger")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self._clear_history)
        sec.add_widget(clear)
        lay.addWidget(sec)

        credit = QLabel("\n".join(bidi.plain(t(k), rtl) for k in ("credits", "unofficial", "disclaimer")),
                        objectName="RowHint")
        credit.setWordWrap(True)
        credit.setAlignment(Qt.AlignHCenter)
        lay.addWidget(credit)
        lay.addStretch(1)

        brow = QHBoxLayout()
        brow.setContentsMargins(0, 10, 0, 0)
        brow.addStretch(1)
        self.save_btn = save = QPushButton(t("save"), objectName="Primary")
        save.setCursor(Qt.PointingHandCursor)
        save.setMinimumWidth(180)
        save.clicked.connect(self._save)
        brow.addWidget(save)
        brow.addStretch(1)
        outer.addLayout(brow)
        rtl_buttons(self, rtl)
        no_default_buttons(self)
        self._initial = self._values()          # the X asks before dropping changes from here on
        self.close_btn.clicked.disconnect()
        self.close_btn.clicked.connect(self._close_clicked)

    # AI account ----------------------------------------------------------

    def _ai(self):
        return providers.get(self._provider)       # the one shown (saved only with Save)

    def _label_usage(self):
        """The meter shows the plan of the AI that answers now (Claude's or ChatGPT's); saver mode is there for both."""
        t, ai = self.t, self._ai()
        self.usage_sec.set_header(t.p("sec_usage", ai.name) if ai.reports_usage else t("sec_saver"))
        self._show_usage(ai.name)
        self.usage_meter.setVisible(ai.reports_usage)
        self.usage_note.setText(bidi.plain(t.p("usage_note", ai.name), t.rtl))
        self.usage_note.setVisible(ai.reports_usage)
        self.saver_hint.setText(bidi.plain(t.p("saver_hint", ai.name), t.rtl))
        if ai.reports_usage and not self.settings.api_key_mode(ai.name):
            threading.Thread(target=lambda: self._limits_bridge.account.emit(
                {"provider": ai.name, "limits": ai.read_limits()}), daemon=True).start()

    def _show_usage(self, provider: str):
        from .. import usage
        self.usage_meter.setText(bidi.plain("\n".join(usage.lines(self.settings, self.t, provider=provider)), self.t.rtl))

    @_while_open
    def _on_limits(self, r: dict):
        """Fresh usage read in the background (ChatGPT); ignored if the player switched AI meanwhile."""
        from .. import usage
        if r["provider"] != self._ai().name or not r["limits"]:
            return
        usage.record(self.settings, r["limits"], provider=r["provider"])
        self._show_usage(r["provider"])

    # model ---------------------------------------------------------------

    def _fill_models(self):
        """Claude's list is fixed; ChatGPT's and Gemini's come from OpenAI and Google, so they fill in a moment later."""
        ai = self._ai()
        if ai.name != "claude":
            self._show_models(ai.name, [(None, "")])
            threading.Thread(target=lambda: self._models_bridge.account.emit(
                {"provider": ai.name, "models": ai.models()}), daemon=True).start()
        else:
            self._show_models(ai.name, ai.models())

    def _on_models(self, r: dict):
        self._show_models(r["provider"], r["models"])

    @_while_open
    def _show_models(self, name: str, models: list):
        from ..providers.base import model_name
        ai = self._ai()
        if name != ai.name:
            return              # a list that arrived after the player switched AI
        t = self.t
        cur = self._model_of(ai)
        labels, values = [], []
        for value, shown in models:
            if value is None:
                labels.append(t("model_default", name=shown) if shown else t.p("model_default_unknown", name))
            elif name == "claude" and value == "sonnet":
                labels.append(t("model_recommended", name=shown))
            else:
                labels.append(shown)
            values.append(value)
        if cur not in values:            # a model the list doesn't offer (any more): keep showing it
            labels.append(model_name(cur) if cur else t.p("model_default_unknown", name))
            values.append(cur)
        self._model_values = values
        self.model_pick.clear()
        self.model_pick.addItems(labels)
        self.model_pick.setCurrentIndex(values.index(cur))
        self._model_note()

    def _model_note(self):
        from ..providers.base import model_name
        t, ai = self.t, self._ai()
        lines = [t.p("model_hint", ai.name)]
        last = (self.settings["last_model"] or {}).get(ai.name)
        if last:
            lines.append(t("model_last", name=model_name(last)))
        if self.settings["saver_mode"] and ai.saver_model:
            saver = model_name(ai.saver_model)            # "haiku" -> "Haiku", "gemini-3.8-flash-low" -> "Gemini 3.8 Flash Low"
            lines.append(t("model_saver_note", name=saver[:1].upper() + saver[1:]))
        self.model_hint.setText(bidi.plain(" · ".join(lines[:1]) + ("\n" + " · ".join(lines[1:]) if lines[1:] else ""),
                                           t.rtl))

    def _on_model(self, i: int):
        ai = self._ai()
        if 0 <= i < len(self._model_values):
            self._pending_models[ai.model_setting] = self._model_values[i]     # stored by Save

    def _model_of(self, ai) -> str | None:
        return self._pending_models.get(ai.model_setting, self.settings[ai.model_setting])

    def _on_provider(self, name: str):
        self._provider = name
        self._fill_models()
        self._label_usage()
        self._login_timer.stop()
        stop_login()                       # a sign-in still waiting belongs to the AI the player left
        self._set_on_top(True)             # it had stepped back for that sign-in's browser
        self.code_row.hide()
        self.install_panel.stop()
        self._install_check = self._login_broken = False
        self._account_status = None
        self.install_btn.setText(self.t.p("ob_install", name))
        for w in (self.install_btn, self.switch_btn, self.logout_btn, self.account_hint):
            w.hide()
        if running_install(name):          # back to an AI whose installer still runs: show it again
            self._install_for = name
            self.install_panel.start(running_install(name), self._ai().label)
        self._set_account_text(self.t("ob_checking"))
        self._refresh_account()

    def _refresh_account(self):
        ai = self._ai()
        threading.Thread(target=lambda: self._account_bridge.account.emit(_safe_account(ai)), daemon=True).start()

    def _set_account_text(self, text: str):
        self.account_label.setText(bidi.plain(text, self.t.rtl))

    @_while_open
    def _on_account(self, acc: dict):
        t, p = self.t, acc.get("provider")
        if p != self._ai().name:
            return            # a check that started before the player switched provider
        st = acc["status"]
        was, self._account_status = self._account_status, st
        api_key = self.settings.api_key_mode(p) or acc.get("method") == "api_key"
        if self._install_check:
            self._install_check = False
            if st == "not_installed":      # the CLI isn't there: why ("Install" shows again below)
                self.install_panel.fail()
            else:
                self.install_panel.stop()
        key_saved = api_key
        if st == "not_installed":
            # the key runs through the AI's CLI: "connected" with no CLI hid the installer it needs
            api_key = False
        if api_key:
            self._set_account_text(t("account_api_key"))
        elif st == "ok":
            self._set_account_text(t("account_signed_in", email=acc["email"]) if acc.get("email")
                                   else t("account_signed_in_no_email", name=self._ai().label))
        elif st == "offline":     # the sign-in may be fine: no "Sign in" (it would fail offline too)
            self._set_account_text(t("ob_offline", name=self._ai().label))
        elif not self._login_timer.isActive():
            self._set_account_text(t.p("ob_not_logged", p) if st == "logged_out" else t.p("ob_not_installed", p))
        connected = api_key or st == "ok"
        self.switch_btn.setText(t("account_switch") if connected else t.p("ob_login", p))
        self.switch_btn.setVisible(st not in ("not_installed", "offline"))
        self.logout_btn.setVisible(connected or key_saved)      # (a saved key can be dropped, CLI or not)
        # not installed, or a sign-in that broke ("reinstalling should fix this"): offer the installer
        self.install_btn.setVisible(not connected and (st == "not_installed" or self._login_broken))
        if connected:
            self.account_hint.hide()
            self.code_row.hide()
        if self._login_timer.isActive() and st == "ok":
            self._login_timer.stop()
            self._set_on_top(True)
        if st == "ok" and was not in (None, "ok"):
            # just installed or signed in: the model list and plan usage read before that came back empty
            self._fill_models()
            self._label_usage()
        if was is not None and was != st and not key_saved:
            self.account_changed.emit()

    def _drop_api_key(self) -> bool:
        """Forget this provider's stored API key; True if it was in use."""
        ai = self._ai()
        if not self.settings.api_key_mode(ai.name):
            return False
        ai.delete_api_key()
        self.settings.set_api_key_mode(ai.name, False)
        self.account_changed.emit()
        return True

    def _switch_account(self):
        """Sign out, then run the official sign-in so another account can be chosen in the browser.
        Signed in, it asks first like "Sign out" (both end the current sign-in); signed out, the same button is
        "Sign in" and just goes."""
        t, ai = self.t, self._ai()
        if login_waiting():
            return                 # a sign-in is waiting for the browser already: a second opened another tab
        connected = self._account_status == "ok" or self.settings.api_key_mode(ai.name)
        if not connected:
            # signed out: nothing to sign out of first (that took seconds, and a second click meanwhile started
            # a second sign-in, each opening its own browser tab)
            self._start_login()
            return
        dlg = ConfirmDialog(t("account_switch"), t.p("account_switch_confirm", ai.name), t("account_switch"),
                            t("cancel"), t.rtl, self.stylesheet_fn(1.0), danger=False)
        if not dlg.exec():
            return
        self.switch_btn.setEnabled(False)
        self.logout_btn.hide()
        self._set_account_text(self.t("account_signing_out"))
        self._drop_api_key()

        self._logout_for = ai.name

        def work():
            _safe_logout(ai)
            self._account_bridge.logged_out.emit()
        threading.Thread(target=work, daemon=True).start()

    def _after_logout(self):
        """Signed out for "Switch account": the sign-in follows, unless the player picked another AI meanwhile
        (Codex's sign-out takes up to 30 s, and a Google sign-in opening unasked is wrong)."""
        if self._logout_for != self._ai().name:
            self.switch_btn.setEnabled(True)
            return
        self._start_login()

    def _start_login(self):
        self.switch_btn.setEnabled(True)
        self.account_hint.hide()
        self.install_panel.stop()
        self._account_status = "logged_out"
        self.account_changed.emit()
        self._login_proc = self._ai().login()
        if self._login_proc is None:
            self._login_failed()
            return
        self._login_broken = False
        self.install_btn.hide()
        self._set_on_top(False)   # the sign-in window and the browser must not open behind this one
        self._set_account_text(self.t.p("account_browser", self._ai().name))
        if self._ai().login_code:
            self.code_edit.clear()
            self.code_row.show()
        self._login_left = 60   # 3 minutes
        self._login_timer.start()

    def _send_code(self):
        code = self.code_edit.text().strip()
        if not code:
            return
        self.code_row.hide()
        if self._ai().submit_login_code(code):
            self._set_account_text(self.t("ob_code_sent"))
        else:                       # the sign-in already gave up (it waits one minute)
            self._login_failed()

    def _login_failed(self):
        self.code_row.hide()
        self._set_account_text(self.t.p("ob_login_failed", self._ai().name))
        if not self._ai().login_code:
            # a sign-in that can't even start or ends at once: reinstalling fixes it. Gemini's ends when the
            # minute for the code runs out: its text says to sign in again, the installer wouldn't help
            self._login_broken = True
            self.install_btn.show()

    def _start_install(self):
        """The official installer, in the background as in onboarding: progress and errors show here."""
        ai = self._ai()
        self._login_broken = False
        self._login_proc = None
        stop_login()                       # a sign-in still waiting holds the CLI's file (agy.exe is locked)
        self._login_timer.stop()
        for w in (self.account_hint, self.install_btn, self.switch_btn, self.code_row):
            w.hide()
        self._install_for = ai.name
        self._install_check = False
        self.install_panel.start(install_for(ai), ai.label)

    def _install_ended(self, ok: bool):
        if self._install_for != self._ai().name:
            return                         # the player picked another AI meanwhile
        self._install_check = True         # the account check says whether it's there, and what's next (sign in)
        self._refresh_account()

    def _set_on_top(self, on: bool):
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) != on:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
            self.show()                                          # (changing a flag hides the window)
            if on:
                self.raise_()

    def _login_tick(self):
        self._login_left -= 1
        if login_failed(self._login_proc):
            self._login_proc = None
            self._login_timer.stop()
            self._set_on_top(True)
            self._login_failed()
            return
        if self._login_left <= 0:
            # gave up waiting: say so (the text said "finish signing in…" forever) and show the real status.
            # The sign-in still waiting for the browser ends too: the hint says to click the button again, and
            # that click did nothing while the old one lived (login_waiting)
            self._login_timer.stop()
            stop_login()
            self._login_proc = None
            self._set_on_top(True)
            set_hint(self.account_hint, self.t("sign_in_timeout"), self.t.rtl)
            self.account_hint.show()
        self._refresh_account()

    def _logout(self):
        t = self.t
        ai = self._ai()
        dlg = ConfirmDialog(t("account_logout"), t.p("account_logout_confirm", ai.name), t("account_logout"),
                            t("cancel"), t.rtl, self.stylesheet_fn(1.0))
        if not dlg.exec():
            return
        self.logout_btn.hide()
        self._set_account_text(t("account_signing_out"))
        if self._drop_api_key():
            self._refresh_account()
            return

        def work():
            _safe_logout(ai)
            self._account_bridge.account.emit(_safe_account(ai))
        threading.Thread(target=work, daemon=True).start()

    def _clear_history(self):
        c = self.profiles.active
        if not c:
            return
        t = self.t
        dlg = ConfirmDialog(t("clear_history"), t("clear_history_confirm", name=c.name), t("clear"), t("cancel"),
                            t.rtl, self.stylesheet_fn(1.0))
        if dlg.exec():
            History(c.id).clear()
            self.history_cleared.emit()

    def _mic_value(self):
        i = self.mic.currentIndex()
        return self.mics[i - 1] if i > 0 else None

    def _test_mic(self):
        """Record 2 s from the chosen microphone (what "Listening for 2 seconds" says) and say whether it hears the player."""
        from .. import voice
        self.mic_test.setEnabled(False)
        self.mic_result.setText(bidi.plain(self.t("mic_testing"), self.t.rtl))
        self.mic_result.show()
        device = voice.device_index(self._mic_value())

        def run():
            try:
                import numpy as np
                import sounddevice as sd
                audio = sd.rec(int(2 * voice.SAMPLE_RATE), samplerate=voice.SAMPLE_RATE, channels=1,
                               dtype="float32", device=device)
                sd.wait()
                level = float(np.sqrt(np.mean(np.square(audio))))
            except Exception:      # noqa: BLE001
                level = -1.0
            try:
                self._mic_heard.emit(level)
            except RuntimeError:
                pass                # Settings closed meanwhile (it was logged as a crash)
        threading.Thread(target=run, daemon=True).start()

    def _mic_tested(self, level: float):
        # speech into a headset measured RMS ~0.09; the same words on a far USB mic ~0.016 (heard as nothing)
        key = "mic_test_failed" if level < 0 else "mic_test_quiet" if level < 0.03 else "mic_test_ok"
        self.mic_result.setText(bidi.plain(self.t(key), self.t.rtl))
        self.mic_test.setEnabled(True)

    def _values(self) -> dict:
        """What Save would store (a sign-in or sign-out acts at once, it's not in here)."""
        return {
            "provider": self._provider,
            **{p.model_setting: self._model_of(p) for p in providers.PROVIDERS.values()},
            "language": self.lang.value(),
            "appearance": self.appearance.value(),
            "font_size": self.font.value(),
            "voice_send_immediately": self.voice_send.isChecked(),
            "microphone": self._mic_value(),
            "voice_language": self.voice_lang.value(),
            "instant_answers": self.instant.isChecked(),
            "minimap_scan_interval": self.scan.value(),
            "saver_mode": self.saver.isChecked(),
            "answer_length": self.length.value(),
            "start_with_windows": self.autostart.isChecked(),
            "telemetry": self.telemetry.isChecked(),
        }

    def unsaved(self) -> bool:
        return self._values() != getattr(self, "_initial", self._values())

    def _close_clicked(self):
        """The X with unsaved changes asks: save them, or not (closing the question keeps Settings open).
        (Esc does nothing here, esc_closes; the app closing the window itself never asks.)"""
        if self.unsaved():
            t = self.t
            dlg = ConfirmDialog(t("settings_unsaved"), t("settings_unsaved_body"), t("save"), t("discard"), t.rtl,
                                self.stylesheet_fn(1.0), danger=False)
            dlg.exec()
            if dlg.choice == "yes":
                self._save()
                return
            if dlg.choice is None:
                return                  # closed the question: back to Settings
        self.reject()

    def _save(self):
        s = self.settings
        ai_keys = ["provider", *(p.model_setting for p in providers.PROVIDERS.values())]
        ai_before = [s[k] for k in ai_keys]
        s.data.update(self._values())
        s.save()
        if [s[k] for k in ai_keys] != ai_before:
            self.account_changed.emit()     # the app moves its AI over to the saved provider and model
        self.changed.emit()
        self.accept()
