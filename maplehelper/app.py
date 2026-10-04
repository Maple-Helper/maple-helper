"""Maple Helper entry point: tray icon, global hotkeys, overlay, voice, onboarding."""
from __future__ import annotations

import os
import sys
import threading
import webbrowser

from PySide6.QtCore import QLockFile, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import APP_NAME, __version__, news, osapi, providers, report, telemetry, updater, whatsnew, wishlist
from .brain import Brain
from .i18n import I18n
from .kb import KnowledgeBase
from .store import ASSETS, DATA_DIR, History, Profiles, Settings
from .ui import theme
from .ui.dialogs import Onboarding, SettingsDialog
from .ui.overlay import Overlay
from .ui.patchnotes import PatchNotesDialog, WhatsNewDialog, update_notice
from .ui.toast import notify
from .voice import VoiceController

HOTKEY_TOGGLE = 1
HOTKEY_VOICE = 2
INSTANCE_SERVER = "MapleHelper-" + (os.environ.get("USERNAME") or os.environ.get("USER") or "app")
BACKGROUND_ARG = "--background"   # start in the tray only (autostart at login, silent updates)
UPDATED_ARG = "--updated"         # the installer reopens the app with it after "Update now": show what's new
# the .ico carries every Windows size; macOS draws the menu bar and Dock from a PNG
APP_ICON = "app.ico" if sys.platform == "win32" else "icon-256.png"


def _remove_stray_screenshots() -> None:
    """Screenshots handed to ChatGPT live in %TEMP% only for one answer; a quit mid-answer left them there
    (Gemini's are in Maple Helper's Antigravity folder, removed the same way)."""
    import glob
    import tempfile
    import time
    for f in glob.glob(os.path.join(tempfile.gettempdir(), "maplehelper-shot-*.jpg")):
        try:
            if time.time() - os.path.getmtime(f) > 3600:
                os.remove(f)
        except OSError:
            pass
    import shutil

    from .providers import gemini, grok
    for d in glob.glob(str(gemini.shots_dir() / "run-*")) + glob.glob(str(grok.shots_dir() / "run-*")):
        try:
            if time.time() - os.path.getmtime(d) > 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def load_kb() -> KnowledgeBase:
    """The newest KB; the bundled one when the downloaded copy can't be read (one bad release mustn't stop
    every start)."""
    from .store import BUNDLED_KB
    try:
        return KnowledgeBase()
    except Exception:      # noqa: BLE001
        report.log.exception("knowledge base unreadable, using the bundled one")
        return KnowledgeBase(BUNDLED_KB)


class _MainThread(QObject):
    """Background checks emit here; Qt delivers the call on the GUI thread (queued connection).

    QTimer.singleShot(0, fn) from a plain Python thread never fires: that thread has no Qt event loop.
    """
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(lambda fn: fn())


class MapleHelperApp:
    def __init__(self, qapp: QApplication):
        self.qapp = qapp
        self.settings = Settings()
        self.profiles = Profiles()
        self.kb = load_kb()
        self.font_family = theme.load_fonts()
        theme.FONT_FAMILY = self.font_family
        qapp.setWindowIcon(QIcon(str(ASSETS / "brand" / APP_ICON)))
        qapp.setQuitOnLastWindowClosed(False)
        self.main_thread = _MainThread()
        self._look = (self.settings["language"], self.settings["appearance"], self.settings["font_size"])

    # ------------------------------------------------------------------ startup

    def style(self, opacity: float | None = None) -> str:
        theme.set_mode(self.settings["appearance"])
        self.qapp.setLayoutDirection(Qt.RightToLeft if I18n(self.settings["language"]).rtl else Qt.LeftToRight)
        css = theme.stylesheet(self.font_family, self.settings["font_size"])
        # restyling the app re-polishes every open widget (the chat with its answers too): only when it changed,
        # not each time a window opens, which held "Play tools" back for a second
        if css != self.qapp.styleSheet():
            self.qapp.setStyleSheet(css)
        return css

    @staticmethod
    def bring_dialogs_forward():
        # a macOS menu bar app is never frontmost by itself, so its dialogs would open behind the game
        if osapi.IS_MAC:
            osapi.activate_self(0)

    def run_onboarding(self) -> bool:
        first = True
        while True:
            dlg = Onboarding(self.settings, self.profiles, self.kb, self.style)
            dlg.report_requested.connect(self.make_report)
            if not first:
                dlg.restart_on_language()
            self.bring_dialogs_forward()
            r = dlg.exec()
            if r == Onboarding.RESTART:
                first = False
                continue
            return bool(r)

    def start(self) -> bool:
        # listening from the start: a second launch while onboarding (or the character setup) is still open
        # brings that window forward instead of timing out against a server that wasn't up yet
        self._listen_for_second_launch()
        fresh_install = not self.settings["onboarding_done"]
        if not self.settings["onboarding_done"]:
            if not self.run_onboarding():
                return False
        elif not self.profiles.active:
            # set up already (language, AI): only a character is missing
            self.style()
            Onboarding(self.settings, self.profiles, self.kb, self.style, only_character=True).exec()
        telemetry.init(self.settings, __version__)
        telemetry.track("app_started", fresh_install=fresh_install, background=BACKGROUND_ARG in sys.argv[1:],
                        provider=self.settings["provider"], language=self.settings["language"])
        self.brain = Brain(self.kb, provider=self.settings["provider"], length=self.settings["answer_length"])
        self.apply_ai_settings()
        threading.Thread(target=self.brain.prewarm, daemon=True).start()   # first answer without startup delay
        self.overlay = Overlay(self.settings, self.profiles, self.kb, self.brain)
        self.overlay.setStyleSheet(self.style())
        self.overlay.setWindowOpacity(1.0)
        self.overlay.shot_provider = self.capture
        self.overlay.settings_requested.connect(self.open_settings)
        self.overlay.saver_requested.connect(self.turn_on_saver)
        self.overlay.show_saver_badge(self.settings["saver_mode"])
        self.overlay.wishlist_requested.connect(self.show_wishlist)
        self.overlay.history_requested.connect(self.show_history)
        self.overlay.guides_requested.connect(lambda: self.show_guides())
        self.overlay.guide_requested.connect(lambda key: self.show_guides(key))
        self.overlay.closed.connect(self.maybe_summarize_later)
        self.overlay.update_requested.connect(self.update_now)
        self.overlay.profile_requested.connect(self.open_settings)
        self.overlay.add_character_requested.connect(self.add_character)
        self.overlay.edit_character_requested.connect(self.edit_character)
        self.overlay.delete_character_requested.connect(self.delete_character)
        # play tools: the EXP meter lives as long as the app (the window may close in between)
        self.exp_meter: dict = {}
        self.overlay.tools_requested.connect(lambda: self.show_tools())
        self.overlay.news_requested.connect(lambda: self.show_patch_notes(tab="news"))
        self.overlay.profile_changed.connect(self.on_profile_changed)
        self.overlay.sync_finished.connect(lambda ok: self._tools_call("sync_done", ok))

        self.hotkeys = osapi.Hotkeys()
        self.hotkeys.pressed.connect(self.on_hotkey)
        self.register_hotkeys()

        self.voice = VoiceController(self.settings["hotkey_voice"])
        self.register_voice_hotkey()
        self.voice.started.connect(self.on_voice_start)
        self.voice.state.connect(lambda s: self.overlay.voice_state(s))
        self.voice.text.connect(self.on_voice_text)
        self.voice.failed.connect(self.on_voice_failed)
        self.voice.text.connect(lambda _: telemetry.track("voice_used"))
        self.overlay.mic_clicked.connect(self.voice.toggle)

        self.make_tray()
        self.apply_autostart()
        self.pending_installer = None
        self._reopen_after_update = False
        QTimer.singleShot(4000, self.check_kb_update_silently)
        QTimer.singleShot(6000, self.voice.preload)    # voice answers right away after a start or an update
        QTimer.singleShot(8000, updater.remove_old_installers)
        QTimer.singleShot(9000, _remove_stray_screenshots)
        from . import inventory     # the icon index for "check the inventory", built before it's needed
        QTimer.singleShot(10000, lambda: threading.Thread(target=inventory.warm, args=(self.kb,), daemon=True).start())
        # a session can run for hours: look again every 3 hours
        self._update_timer = QTimer(interval=3 * 60 * 60 * 1000, timeout=self.check_kb_update_silently)
        self._update_timer.start()
        if BACKGROUND_ARG in sys.argv[1:]:
            # started with Windows or by a silent update: stay in the tray until the player asks for the chat
            t = I18n(self.settings["language"])
            self.toast(t("app_tagline"), t("ob_done_hint").replace("F9", self.settings["hotkey_toggle"]))
        else:
            # the first-run tour starts with the chat itself (Overlay.open_overlay), whenever it first opens
            QTimer.singleShot(0, lambda: self.overlay.toggle(self.capture))
        QTimer.singleShot(1500, self.check_permissions)
        self.announce_whats_new(fresh_install)
        self.qapp.aboutToQuit.connect(self.shutdown)
        return True

    def replay_tour(self, settings_dialog) -> None:
        """Settings → "Take the app tour": the settings window steps away and the chat shows the tour."""
        settings_dialog.close()
        if not self.overlay.isVisible():
            self.overlay.toggle(self.capture)
        QTimer.singleShot(300, self.overlay.start_tour)

    def _listen_for_second_launch(self):
        """The app runs in the tray (autostart): opening it again from the desktop or Start menu shows the chat."""
        QLocalServer.removeServer(INSTANCE_SERVER)        # a stale socket after a crash (macOS)
        self._instance_server = QLocalServer(self.qapp)
        self._instance_server.newConnection.connect(self._on_second_launch)
        self._instance_server.listen(INSTANCE_SERVER)

    def _on_second_launch(self):
        while self._instance_server.hasPendingConnections():
            self._instance_server.nextPendingConnection().deleteLater()
        if getattr(self, "overlay", None) is None:
            # still in onboarding or the character setup: no chat yet, bring that window forward
            win = QApplication.activeModalWidget()
            if win is not None:
                self.bring_dialogs_forward()
                win.raise_()
                win.activateWindow()
            return
        self.show_chat()

    def show_chat(self):
        """Open the chat, or bring it forward when it is open already (never closes it: the tray's "Open chat"
        closed an open chat, a toggle under an "open" label)."""
        if not self.overlay.isVisible() or self.overlay.windowOpacity() <= 0.5:
            self.overlay.toggle(self.capture)
        else:
            self.overlay.raise_()
            self.overlay.activateWindow()

    def announce_whats_new(self, fresh_install: bool):
        """First start after an app update: a note in the chat with a "What's new?" button."""
        seen = self.settings["seen_version"] or ("" if fresh_install else whatsnew.FIRST_TRACKED)
        self.settings["seen_version"] = __version__
        if fresh_install:
            return            # a new player gets the welcome screen, not a changelog
        if seen != __version__:
            telemetry.track("app_updated", from_version=seen)
        notes = whatsnew.since(seen, __version__)
        if notes:
            self.overlay.add_notice(lambda t: t("whats_new_notice", version=__version__), lambda t: t("whats_new_show"),
                                    lambda: self.show_whats_new(notes))
            if UPDATED_ARG in sys.argv:
                # back from "Update now": the chat is open, show what changed right away; after the first-run tour
                # when that runs too (both at once: the changelog took the focus and the tour's keys went dead)
                if self.settings["tour_done"]:
                    QTimer.singleShot(900, lambda: self.show_whats_new(notes))
                else:
                    self.overlay.tour_ended.connect(lambda: self.show_whats_new(notes), Qt.SingleShotConnection)

    def open_window(self, kind: str, make, on_close=None):
        """Settings, guides, history…: a window NEXT TO the chat, which stays usable (not modal).
        One window of each kind; asking again brings the open one forward."""
        windows = self.__dict__.setdefault("_windows", {})
        dlg = windows.get(kind)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return dlg
        dlg = make()
        windows[kind] = dlg
        dlg.setWindowModality(Qt.NonModal)
        dlg.setAttribute(Qt.WA_DeleteOnClose)                  # closed windows don't pile up in memory
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)      # over the game, like the chat
        dlg.finished.connect(lambda *_: windows.pop(kind, None) if windows.get(kind) is dlg else None)
        if on_close:
            dlg.finished.connect(lambda *_: on_close())
        self.bring_dialogs_forward()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def show_whats_new(self, notes: list[dict] | None = None):
        self.open_window("whats_new", lambda: WhatsNewDialog(
            notes if notes is not None else whatsnew.load()[:6], self.settings["language"], self.style()))

    def register_hotkeys(self):
        key = self.settings["hotkey_toggle"]
        if not self.hotkeys.register(HOTKEY_TOGGLE, key):
            t = I18n(self.settings["language"])
            self.toast(t("settings"), t("hotkey_taken", key=key), timeout_ms=9000)

    def register_voice_hotkey(self):
        self.hotkeys.unregister(HOTKEY_VOICE)
        key = self.settings["hotkey_voice"]
        if key != self.settings["hotkey_toggle"] and not self.hotkeys.register(HOTKEY_VOICE, key):
            t = I18n(self.settings["language"])
            self.toast(t("settings"), t("hotkey_taken", key=key), timeout_ms=9000)

    def check_permissions(self):
        """macOS: ask once for Screen Recording (the screenshot), and say how to grant it when missing."""
        if osapi.missing_permissions(request=True):
            t = I18n(self.settings["language"])
            self.toast(t("perm_title"), t("perm_screen_body"), timeout_ms=20000)

    def toast(self, title: str, message: str = "", timeout_ms: int = 5000):
        notify(title, message, rtl=I18n(self.settings["language"]).rtl, font_family=self.font_family,
               timeout_ms=timeout_ms, screen=self._toast_screen())

    def _toast_screen(self):
        """Where the player is looking: the chat's monitor when it is open, else the game's (a toast on the
        primary monitor was missed with the game on the other one), else the primary one (None)."""
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QGuiApplication
        overlay = getattr(self, "overlay", None)
        if overlay is not None and overlay.isVisible():
            return overlay.screen()
        try:
            hwnd = osapi.find_game_window()
            rect = osapi.window_rect(hwnd) if hwnd else None
        except Exception:      # noqa: BLE001 - a toast never fails over where to go
            rect = None
        if rect:
            return QGuiApplication.screenAt(QPoint(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2))
        return None

    # ------------------------------------------------------------------ events

    def capture(self, hwnd):
        return osapi.capture_game(hwnd)

    def on_hotkey(self, hotkey_id: int):
        if hotkey_id == HOTKEY_TOGGLE:
            if self.overlay.isVisible():
                self.overlay.close_overlay()
            else:
                self.overlay.toggle(self.capture)   # also restores from the minimized bubble
        elif hotkey_id == HOTKEY_VOICE:
            self.voice.toggle()

    def on_voice_start(self):
        # the talk key in game opens the chat (with a fresh screenshot)
        if not self.overlay.isVisible():
            self.overlay.toggle(self.capture)

    def on_voice_failed(self, error: str):
        """No microphone, a blocked one, or the speech model failed to download/load: say so, don't go silent."""
        report.log.warning("voice failed: %s", error)
        t = I18n(self.settings["language"])
        if not self.overlay.isVisible():
            self.overlay.toggle(self.capture)
        key = ("voice_mic_failed" if error.startswith("mic:") else
               "voice_download_failed" if error.startswith("download:") else "voice_failed")
        self.overlay.add_system(t(key))

    def on_voice_text(self, text: str):
        if not text.strip():          # silence (or only noise): say so, instead of nothing happening
            self.overlay.add_system(I18n(self.settings["language"])("voice_nothing")
                                    .replace("F10", self.settings["hotkey_voice"]))
            return
        fixed = self.kb.resolve_names(text)
        self.overlay.voice_text(fixed, send=self.settings["voice_send_immediately"])

    def maybe_summarize_later(self):
        """After 30 minutes without the chat, the session is summarized for long-term context."""
        if not hasattr(self, "_idle_timer"):
            self._idle_timer = QTimer(singleShot=True, interval=30 * 60 * 1000, timeout=self.summarize_session)
        self._idle_timer.start()

    def summarize_session(self):
        if self.overlay.isVisible():
            return
        # one summary per character the player talked as (switching mid-session left the others unsummarized)
        transcripts = self.overlay.end_session()
        if not transcripts:
            return

        def work():
            for cid, transcript in transcripts.items():
                s = self.brain.summarize(transcript)
                if s:
                    History(cid).add_summary(s)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ tray & settings

    def make_tray(self):
        t = I18n(self.settings["language"])
        self.tray = QSystemTrayIcon(QIcon(str(ASSETS / "brand" / APP_ICON)))
        self.tray.setToolTip(f"{APP_NAME} · {t('app_tagline')}")
        menu = QMenu()
        # rounded, app-styled menu (the app-wide stylesheet paints it; the window must be see-through at the corners)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        menu.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        header = QAction(APP_NAME, menu)
        header.setEnabled(False)
        menu.addAction(header)
        menu.addSeparator()
        key = self.settings["hotkey_toggle"]
        # the key in the label itself: the menu's shortcut column glued it to the text in Hebrew ("הצ'אטF9", seen live)
        from . import bidi
        a_show = QAction(bidi.plain(f"{t('tray_open')}  ·  {key}", t.rtl), menu, triggered=self.show_chat)
        a_set = QAction(t("tray_settings"), menu, triggered=self.open_settings)
        a_quit = QAction(t("tray_quit"), menu, triggered=self.qapp.quit)
        menu.addAction(a_show)
        menu.addAction(a_set)
        if getattr(self, "pending_installer", None):
            a_upd = QAction(t("update_now_tray", version=updater.installer_version(self.pending_installer)), menu,
                            triggered=self.update_now)
            menu.addAction(a_upd)
        menu.addSeparator()
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.overlay.toggle(self.capture)
                                    if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        self._tray_menu = menu
        self._add_announced_item()

    def open_settings(self):
        def make():
            dlg = SettingsDialog(self.settings, self.profiles, self.kb, self.style)
            dlg.changed.connect(self.on_settings_changed)
            dlg.update_kb_requested.connect(self.update_kb_interactive)
            dlg.history_cleared.connect(self.on_history_cleared)
            dlg.report_requested.connect(self.make_report)
            dlg.account_changed.connect(self.on_account_changed)
            dlg.patch_notes_requested.connect(lambda: self.show_patch_notes())
            dlg.whats_new_requested.connect(lambda: self.show_whats_new())
            dlg.tour_requested.connect(lambda: self.replay_tour(dlg))
            return dlg
        self.open_window("settings", make, on_close=self.overlay.refresh_profile_chip)

    def _reopen_windows_in_new_look(self):
        """A language or appearance change: the other open windows (tools, guides, history...) were built in the
        old one (the tools window stayed Hebrew after a switch to English, seen live). Reopen them."""
        look = (self.settings["language"], self.settings["appearance"], self.settings["font_size"])
        if getattr(self, "_look", look) == look:
            self._look = look
            return
        self._look = look
        for kind, dlg in list(self.__dict__.get("_windows", {}).items()):
            if kind == "settings" or dlg is None:
                continue
            again = self._reopen_call(kind, dlg)        # read before close: where the player was in it
            dlg.close()
            if again:
                QTimer.singleShot(0, again)

    def _reopen_call(self, kind: str, dlg):
        """How to open `dlg` again as it is now: the same page, guide, notes and character (reopened with no
        arguments, Tools came back on its first page, an open guide on the list, a KB update's patch notes as
        the generic changelog, and another character's History as the active one's)."""
        if kind == "tools":
            from .ui.tools import PAGES
            stack = getattr(dlg, "stack", None)
            i = stack.currentIndex() if stack is not None else 0
            page = PAGES[i] if 0 <= i < len(PAGES) else "train"
            return lambda: self.show_tools(page)
        if kind == "guides":
            key = getattr(dlg, "_reading", None)
            return lambda: self.show_guides(key)
        if kind == "patch_notes":
            entries, tab = getattr(dlg, "entries", None), getattr(dlg, "tab", "changes")
            return lambda: self.show_patch_notes(entries, tab)
        if kind == "whats_new":
            notes = getattr(dlg, "notes", None)
            return lambda: self.show_whats_new(notes)
        cid = kind.split(":", 1)[1] if ":" in kind else None
        if self._character(cid) is None:
            return None                       # that character is gone
        if kind.startswith("history:"):
            return lambda: self.show_history(cid)
        if kind.startswith("wishlist:"):
            return lambda: self.show_wishlist(cid)
        return None

    def add_character(self):
        before = self.profiles.active_id
        if Onboarding(self.settings, self.profiles, self.kb, self.style, only_character=True).exec():
            self.overlay.refresh_profile_chip()
            c = self.profiles.active
            if c and c.id != before:
                self.overlay.add_system(I18n(self.settings["language"])("switched_character", name=c.name))

    def edit_character(self, cid: str):
        if Onboarding(self.settings, self.profiles, self.kb, self.style, edit_id=cid).exec():
            self.overlay.refresh_profile_chip()
            self.overlay.refresh_plan()
            self.on_profile_changed()

    def delete_character(self, cid: str):
        from .ui.dialogs import ConfirmDialog
        c = next((c for c in self.profiles.characters if c.id == cid), None)
        if not c:
            return
        t = I18n(self.settings["language"])
        if not ConfirmDialog(t("delete_character"), t("delete_character_confirm", name=c.name), t("delete"),
                             t("cancel"), t.rtl, self.style()).exec():
            return
        self.profiles.remove(cid)
        # nothing of the deleted character stays behind: its pinned answers, tracked items and hidden tips
        for key in ("pins", "wishlist", "tips_dismissed"):
            data = dict(self.settings[key] or {})
            if data.pop(cid, None) is not None:
                self.settings[key] = data
        if not self.profiles.characters:
            # advice needs a character: offer to create one right away
            Onboarding(self.settings, self.profiles, self.kb, self.style, only_character=True).exec()
        self.overlay.clear_feed()
        self.overlay.refresh_profile_chip()
        self.overlay.refresh_plan()
        now = self.profiles.active
        if now:
            self.overlay.add_system(t("switched_character", name=now.name))
        self.on_profile_changed()

    def apply_ai_settings(self):
        """Point the brain at the chosen provider, with its model, saver mode and (when used) its stored API key."""
        self.brain.provider = self.settings["provider"]
        ai = providers.get(self.settings["provider"])
        self.brain.api_key = ai.load_api_key() if self.settings.api_key_mode(ai.name) else None
        self.brain.ui_lang = self.settings["language"]
        self.apply_saver_mode()

    def on_account_changed(self):
        # another provider, account or model: a warm process started under the old setup is replaced. An answer
        # in progress goes on (changing the model mid-answer killed it); switching provider ends the old one anyway
        self.apply_ai_settings()
        self.brain.drop_warm()
        threading.Thread(target=self.brain.prewarm, daemon=True).start()

    def make_report(self):
        """Zip the log and diagnostics onto the desktop and show the file, ready to send."""
        from pathlib import Path
        from PySide6.QtCore import QStandardPaths
        t = I18n(self.settings["language"])
        ai = providers.get(self.settings["provider"])
        desktop = Path(QStandardPaths.writableLocation(QStandardPaths.DesktopLocation) or Path.home())
        self.toast(t("report_preparing"))

        def work():
            try:
                status = ai.status()
            except Exception as e:      # noqa: BLE001 - the report is most needed when things are broken
                status = f"error: {e!r}"
            self.main_thread.call.emit(lambda: self._write_report(desktop, f"{ai.label}: {status}"))
        threading.Thread(target=work, daemon=True).start()

    def _write_report(self, desktop, ai_status: str):
        import subprocess
        t = I18n(self.settings["language"])
        info = report.system_info(__version__, updater.local_version(), ai_status)
        path, saved = report.save_report(desktop, info, dict(self.settings.data))
        report.log.info("problem report written: %s", path.name)
        # show the file, selected, in Explorer / Finder
        subprocess.Popen(["explorer", "/select,", str(path)] if sys.platform == "win32" else ["open", "-R", str(path)])
        self.toast(t(saved), t("report_saved_body", name=path.name), timeout_ms=12000)

    def on_history_cleared(self):
        self.overlay.clear_feed()
        self.toast(I18n(self.settings["language"])("history_cleared"))

    def apply_saver_mode(self):
        """Saver mode: short answers, on the provider's lighter model when it has one.

        The warm process is respawned on the next prewarm."""
        ai = providers.get(self.settings["provider"])
        saver = self.settings["saver_mode"]
        self.brain.model = (saver and ai.saver_model) or self.settings[ai.model_setting]
        self.brain.length = "short" if saver else self.settings["answer_length"]

    def turn_on_saver(self):
        self.settings["saver_mode"] = True
        settings_win = self.__dict__.get("_windows", {}).get("settings")
        if settings_win is not None and hasattr(settings_win, "saver"):
            settings_win.saver.setChecked(True)     # its Save must not switch it off again
        self.apply_saver_mode()
        self.overlay.show_saver_badge(True)
        self.overlay.add_system(I18n(self.settings["language"])("saver_turned_on"))
        threading.Thread(target=self.brain.prewarm, daemon=True).start()

    def on_settings_changed(self):
        telemetry.set_enabled(self.settings["telemetry"])
        # the new theme first: apply_language rebuilds text with the theme's colors written in (the retake link
        # kept the dark theme's faint orange on white)
        self.overlay.setStyleSheet(self.style())
        self.overlay.apply_language()
        self._reopen_windows_in_new_look()
        from .ui import terms
        from .ui.toast import Toast
        for toast in list(Toast._live):
            toast.restyle()              # a toast up during the switch: old text colors on the new glass
        terms.hide()
        self.overlay.apply_capture_mode()
        self.apply_saver_mode()
        self.overlay.show_saver_badge(self.settings["saver_mode"])
        threading.Thread(target=self.brain.prewarm, daemon=True).start()
        self.voice.set_key(self.settings["hotkey_voice"])
        self.hotkeys.unregister(HOTKEY_TOGGLE)      # free both first: a swap would otherwise collide
        self.hotkeys.unregister(HOTKEY_VOICE)
        self.register_hotkeys()
        self.register_voice_hotkey()
        self.apply_autostart()
        self.tray.hide()
        self.make_tray()

    def apply_autostart(self):
        # the setting means "start at login" on macOS (named before macOS support)
        if osapi.set_autostart(self.settings["start_with_windows"], [BACKGROUND_ARG]) is False:
            t = I18n(self.settings["language"])     # macOS, run from the disk image: the login item would break
            self.toast(t("start_at_login"), t("start_at_login_move"), timeout_ms=15000)

    # ------------------------------------------------------------------ knowledge base updates

    def check_kb_update_silently(self):
        if getattr(sys, "frozen", False) and (osapi.IS_MAC or not updater.installed_copy()):
            # no silent self-update on macOS (the installer is a Windows .exe) or for a portable copy: point at
            # the new release instead
            def mac_update():
                rel = updater.newer_release(__version__)
                if rel:
                    self.main_thread.call.emit(lambda: self.announce_update(*rel))
            threading.Thread(target=mac_update, daemon=True).start()
        elif getattr(sys, "frozen", False) and not self.pending_installer and not getattr(self, "_downloading", False):
            def app_update():
                rel = updater.newer_release(__version__)
                if not rel:
                    return
                self.main_thread.call.emit(lambda: self.update_found(rel[0]))
            threading.Thread(target=app_update, daemon=True).start()

        self._update_kb_in_background(interactive=False)

    def _stop_ai_for_kb_swap(self) -> bool:
        """Right before the KB folders swap: the warm AI process runs inside the KB, so stop it, unless the
        player is waiting on an answer (then the 3-hourly timer tries again later)."""
        if self.overlay.busy or getattr(self.overlay, "_syncing", False):
            return False
        self.brain.drop_warm()      # not shutdown(): that also cancels, and a question may start right now
        return True

    def _update_kb_in_background(self, interactive: bool):
        if getattr(self, "_kb_updating", False):
            return
        self._kb_updating = True

        def work():
            before = updater.local_version()
            try:
                status = updater.fetch_kb(self._stop_ai_for_kb_swap)
            except Exception:              # noqa: BLE001 - disk full etc.: never leave the flag stuck
                report.log.exception("knowledge base update failed")
                status = "failed"
            self._kb_updating = False
            if status == "updated":
                report.log.info("knowledge base updated to %s", updater.local_version())
            self.main_thread.call.emit(lambda: self._kb_update_done(status, before, interactive))
        threading.Thread(target=work, daemon=True).start()

    def _kb_update_done(self, status: str, before: str, interactive: bool):
        t = I18n(self.settings["language"])
        self.overlay.show_scope()           # the check itself moves "verified on" on, even with nothing new
        if status == "updated":
            self.reload_kb()
            self.kb_updated(before, interactive=interactive)
        else:
            from .store import kb_dir
            if status == "failed" and kb_dir() != self.kb.root:
                self.reload_kb()        # the swap failed halfway and the folder changed: use what exists now
            if interactive:
                self.toast(t({"uptodate": "kb_uptodate", "postponed": "kb_update_postponed"}.get(status,
                                                                                               "kb_update_failed")))
        threading.Thread(target=self.brain.prewarm, daemon=True).start()     # whatever happened, warm again

    def announce_update(self, version: str, url: str):
        if getattr(self, "_mac_announced", None) == version:
            return                       # the 3-hourly check found the same version again
        self._mac_announced, self._announced_url = version, url
        t = I18n(self.settings["language"])
        self.toast(t("update_available", version=version), t("update_available_mac" if osapi.IS_MAC else "update_available_win"),
                   timeout_ms=20000)
        self._add_announced_item()

    def _add_announced_item(self):
        """The "update available" tray item (macOS / portable); make_tray adds it again after a rebuild."""
        version = getattr(self, "_mac_announced", None)
        if not version or not getattr(self, "_tray_menu", None):
            return
        t = I18n(self.settings["language"])
        url = self._announced_url
        a = QAction(t("update_available", version=version), self._tray_menu, triggered=lambda: webbrowser.open(url))
        self._tray_menu.insertAction(self._tray_menu.actions()[2], a)   # right under the header

    def update_found(self, version: str):
        """A newer version exists: say so at once, and download it in the background (with progress)."""
        if self.pending_installer or getattr(self, "_downloading", False):
            return
        self._update_version = version
        self.overlay.show_update(version, "available")
        self._start_download()

    def _start_download(self):
        self._downloading = True
        version = getattr(self, "_update_version", "")

        def progress(done, total):
            if total and getattr(self, "_update_clicked", False):
                pct = done * 100 / total
                if pct - getattr(self, "_last_pct", -1) >= 1 or done == total:
                    self._last_pct = pct
                    self.main_thread.call.emit(lambda p=pct: self.overlay.show_update(version, "downloading", p))

        def work():
            try:
                path = updater.download_app_update(__version__, progress)
            except Exception:              # noqa: BLE001 - disk full etc.: never leave "downloading" stuck
                report.log.exception("app update download failed")
                path = None
            self._downloading = False
            self.main_thread.call.emit(lambda: self.app_update_ready(path) if path else self._download_failed())
        threading.Thread(target=work, daemon=True).start()

    def _download_failed(self):
        if getattr(self, "_update_clicked", False):
            self.overlay.show_update(getattr(self, "_update_version", ""), "failed")
        self._update_clicked = False

    def app_update_ready(self, path: str):
        """A newer version is downloaded and verified: offer it at the top of the chat and in the tray."""
        self.pending_installer = path
        version = updater.installer_version(path)
        if getattr(self, "_update_clicked", False):
            self._install_now()          # the player is waiting on the progress bar: go on
            return
        t = I18n(self.settings["language"])
        self.overlay.show_update(version, "ready")
        self.toast(t("update_bar", version=version), t("update_ready"))
        self.tray.hide()
        self.make_tray()   # adds "Update to X" to the tray menu

    def update_now(self):
        """The player pressed "Update now": show the download, then install and reopen with what's new."""
        self._update_clicked = True
        if not self.overlay.isVisible():
            self.overlay.toggle(self.capture)
        if self.pending_installer:
            self._install_now()
        elif getattr(self, "_downloading", False):
            self._last_pct = -1
            self.overlay.show_update(getattr(self, "_update_version", ""), "downloading", 0)
        else:                                   # a failed download: try again
            self.overlay.show_update(getattr(self, "_update_version", ""), "downloading", 0)
            self._start_download()

    def _install_now(self):
        """Say what happens next, then close: the installer shows its progress and opens the new version."""
        version = updater.installer_version(self.pending_installer)
        self.overlay.show_update(version, "installing")
        self._reopen_after_update = True
        QTimer.singleShot(1800, self.qapp.quit)

    def update_kb_interactive(self):
        self._update_kb_in_background(interactive=True)     # off the GUI thread: the zip is ~20 MB

    def kb_updated(self, before: str, interactive: bool = False):
        """Tell the player exactly what the update changed (patch notes), not just that it happened."""
        t = I18n(self.settings["language"])
        entries = updater.changes_since(before)
        if not entries:
            self.toast(t("kb_updated"))
            return
        if interactive:
            self.show_patch_notes(entries)
            return
        # in the chat, where the player looks next; a dialog over the game would interrupt play. What touches the
        # active character (gear for them, monsters in their training range, wished items) is said first, by name
        # an update that brought only news opens on the News tab
        only_news = all(not any((e.get("counts") or {}).get(k) for k in ("added", "changed", "updated", "removed"))
                        for e in entries)
        self.overlay.add_notice(lambda t: update_notice(t, entries, self.kb, self.profiles.active,
                                                        wishlist.items(self.settings, self.profiles.active_id)),
                                lambda t: t("patch_notes_show"),
                                lambda: self.show_patch_notes(entries, "news" if only_news else "changes"))
        if not self.overlay.isVisible():
            self.toast(t("kb_updated"), t("kb_updated_open"))

    def show_tools(self, page: str = "train"):
        from .ui.tools import ToolsDialog

        def make():
            dlg = ToolsDialog(self.kb, self.profiles, self.settings, self.settings["language"], self.style(),
                              self.exp_meter, page)
            dlg.sync_requested.connect(self.overlay.sync_profile)
            dlg.ask_requested.connect(self.ask_from_tools)
            dlg.detail_ask_requested.connect(lambda q, shown: self.ask_from_tools(q, True, detail=True, shown=shown))
            dlg.tag_requested.connect(self.ask_about_guide)
            dlg.guide_requested.connect(self.show_guides)
            return dlg
        self.open_window("tools", make)

    def ask_from_tools(self, question: str, with_screenshot: bool, detail: bool = False, shown: str | None = None):
        if not self.overlay.isVisible():
            self.overlay.toggle(self.capture)
        if self.overlay._is_busy():      # an answer is on its way: say so, don't drop the question silently
            self.overlay._say_busy()
            return
        if with_screenshot:
            self.overlay.ask_with_screenshot(question, detail=detail, shown=shown)
        else:
            self.overlay.ask(question, shown=shown)

    def _tools_call(self, method: str, *args):
        tools = self.__dict__.get("_windows", {}).get("tools")
        if tools is not None:
            getattr(tools, method)(*args)

    def on_profile_changed(self):
        self._tools_call("profile_changed")

    def show_guides(self, open_key: str | None = None):
        from .ui.guides import GuidesDialog
        def make():
            dlg = GuidesDialog(self.kb, self.profiles.active, self.settings["language"], self.style())
            dlg.ask_requested.connect(self.ask_about_guide)
            return dlg
        dlg = self.open_window("guides", make)
        if open_key and self.kb.get(open_key):
            dlg.open_guide(open_key)

    def ask_about_guide(self, key: str):
        """Tag the guide in the chat, so the next question is about it (Claude reads the page)."""
        if not self.overlay.isVisible():
            self.overlay.toggle(self.capture)
        self.overlay.set_tags([key])
        self.overlay.input.setFocus()

    def _character(self, cid: str | None):
        """That character (the active one when cid is None), or None."""
        if cid is None:
            return self.profiles.active
        return next((c for c in self.profiles.characters if c.id == cid), None)

    def show_history(self, cid: str | None = None):
        from . import pins
        from .ui.pinsview import HistoryDialog
        c = self._character(cid)
        if not c:
            return
        pairs = pins.conversations(History(c.id).recent(100000))
        def make():
            dlg = HistoryDialog(pairs, c.name, self.settings["language"], self.style(), self.kb)
            dlg.pin_requested.connect(lambda q, a, cid=c.id: self.overlay.pin_answer(q, a, cid))
            # the window stays open across a character switch: its exchange belongs to its own character
            dlg.continue_requested.connect(lambda q, a, k, cid=c.id: self.continue_conversation(q, a, k, cid))
            return dlg
        self.open_window(f"history:{c.id}", make)

    def continue_conversation(self, question: str, answer: str, keys: list, cid: str | None = None):
        """From the history: the exchange back in the chat, and the next question follows on from it. cid: whose
        history it came from; the chat switches to that character first (A's exchange went into B's chat)."""
        if not self.overlay.isVisible():
            self.overlay.toggle(self.capture)
        c = self.profiles.active
        if cid is not None and (c is None or c.id != cid):
            if self.overlay._is_busy():        # mid-answer the reply still belongs to the current character
                self.overlay._say_busy()
                return
            self.overlay.switch_character(cid)
            c = self.profiles.active
            if c is None or c.id != cid:
                return                         # that character is gone
        history = self.__dict__.get("_windows", {}).get(f"history:{c.id}") if c else None
        if history is not None:
            history.close()           # the conversation goes on in the chat, not behind the history window
        self.overlay.continue_from(question, answer, keys)

    def show_wishlist(self, cid: str | None = None):
        c = self._character(cid)
        cid = c.id if c else self.profiles.active_id
        keys = wishlist.items(self.settings, cid)
        old = self.__dict__.get("_windows", {}).get(f"wishlist:{cid}")
        if old is not None:
            old.close()             # a star added meanwhile: show the list as it is now, not the open copy
        self.open_window(f"wishlist:{cid}", lambda: self._wishlist_dialog(keys))

    def _wishlist_dialog(self, keys):
        from .ui.wishlist import WishlistDialog
        dlg = WishlistDialog(keys, self.kb, self.settings["language"], self.style())
        dlg.ask_requested.connect(lambda q: self.ask_from_tools(q, False))
        return dlg

    def show_patch_notes(self, entries: list[dict] | None = None, tab: str = "changes"):
        """tab: "changes" (what a KB update changed) or "news" (MapleStory Classic news, news.py)."""
        if entries is None:
            entries = updater.changelog()[:5]
        old = self.__dict__.get("_windows", {}).get("patch_notes")
        if old is not None and old.isVisible() and getattr(old, "tab", tab) != tab:
            old.tabs.group.buttons()[1 if tab == "news" else 0].click()     # open already: show the asked tab

        def make():
            unread = [i["id"] for i in news.unread(self.kb, self.settings[news.SETTING])]
            dlg = PatchNotesDialog(entries, self.settings["language"], self.style(), self.kb, self.profiles.active,
                                   wishlist.items(self.settings, self.profiles.active_id), tab, unread)
            dlg.news_seen.connect(self._news_seen)
            return dlg
        self.open_window("patch_notes", make)

    def _news_seen(self, ids: list) -> None:
        news.mark_read(self.settings, ids)
        self.overlay.show_news()          # the chat's news strip goes once its news was read

    def reload_kb(self):
        self.kb = load_kb()
        from . import inventory
        threading.Thread(target=inventory.warm, args=(self.kb,), daemon=True).start()   # the new KB's icons
        self.brain.kb = self.kb
        self.overlay.kb = self.kb
        self.overlay.show_scope()           # the new KB's "verified on" date
        self.overlay.show_news()            # and its news

    def shutdown(self):
        telemetry.flush()
        try:
            self.overlay.save_session_summary()   # quitting ends the session: show it next time
        except Exception:
            pass
        try:
            self.brain.cancel()                  # an answer in progress ends now...
            for th in (getattr(self.overlay, "_thread", None), getattr(self.overlay, "_sync_thread", None)):
                if th is not None and th.isRunning():
                    th.quit()
                    th.wait(2000)               # ...and its thread with it (a running QThread at exit crashes)
        except Exception:
            pass
        try:
            self.brain.shutdown()
        except Exception:
            pass
        try:
            from .providers.base import stop_login
            stop_login()                         # a sign-in still waiting (Codex's holds a port the next one needs)
        except Exception:
            pass
        try:
            self.hotkeys.close()
        except Exception:
            pass
        if getattr(self, "pending_installer", None):
            from .setupwait import setup_running
            if setup_running():
                return                  # an installer is closing us right now (it is the update)
            if updater.windows_shutting_down():
                # Qt quits on shutdown/sign-out too: the next start downloads nothing and offers it again
                report.log.info("update postponed: Windows is shutting down")
                return
            updater.run_installer_silently(self.pending_installer, reopen=getattr(self, "_reopen_after_update", False),
                                           lang=self.settings["language"] or "he")



_RUNNING = None


def _hold_running_mutex():
    """Windows: wait while an update installs, then hold a named mutex the installer waits on.
    (The launchers already waited before importing anything heavy; this covers other entry points.)"""
    global _RUNNING
    if sys.platform != "win32":
        return
    import ctypes

    from .setupwait import wait_for_setup
    wait_for_setup()
    k32 = ctypes.windll.kernel32
    _RUNNING = k32.CreateMutexW(None, False, "MapleHelperRunning")


def main():
    if any(a.startswith("--selftest") for a in sys.argv[1:]):
        from . import selftest   # `Maple Helper.exe --selftest <report file>`, see selftest.py
        return selftest.main(sys.argv[1:])
    osapi.prepare_process()
    report.setup_logging()
    report.log.info("Maple Helper %s starting on %s (%s)", __version__, sys.platform, " ".join(sys.argv[1:]) or "no args")
    qapp = QApplication(sys.argv)
    # Fusion: the native Windows 11 style ignores rounded corners on buttons. AppStyle adds the hand cursor and
    # the focus ring as Qt styles each widget (no app-wide event filter)
    qapp.setStyle(theme.AppStyle("Fusion"))
    qapp.setApplicationName(APP_NAME)
    qapp.setApplicationDisplayName(APP_NAME)
    lock = QLockFile(str(DATA_DIR / "app.lock"))
    if not lock.tryLock(100):
        # already running (often in the tray): ask it to show the chat, unless this start is itself a background one
        if BACKGROUND_ARG not in sys.argv[1:]:
            sock = QLocalSocket()
            sock.connectToServer(INSTANCE_SERVER)
            sock.waitForConnected(1000)
            sock.disconnectFromServer()
        return 0
    _hold_running_mutex()
    app = MapleHelperApp(qapp)
    if not app.start():
        return 0
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
