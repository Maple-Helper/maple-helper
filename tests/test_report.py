"""The problem report carries the log and diagnostics, never private data."""
import json
import logging
import zipfile

from maplehelper import report


def test_report_has_log_and_info_but_no_private_settings(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "maplehelper.log").write_text("2026-10-01 INFO started\n", encoding="utf-8")
    monkeypatch.setattr(report, "LOG_DIR", logs)
    info = report.system_info("0.4.0", "2026.10.01.0100", "ok")
    path = report.build_report(tmp_path / "out", info, {"language": "he", "window": {"x": 1}, "bubble_pos": {}})
    with zipfile.ZipFile(path) as z:
        assert set(z.namelist()) == {"info.json", "settings.json", "logs/maplehelper.log"}
        assert json.loads(z.read("info.json"))["app_version"] == "0.4.0"
        assert "os" in json.loads(z.read("info.json")) and "windows" not in info    # a Mac report isn't "windows"
        assert json.loads(z.read("settings.json")) == {"language": "he"}


def test_logging_writes_to_the_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "LOG_DIR", tmp_path)
    monkeypatch.setattr(report, "LOG_FILE", tmp_path / "maplehelper.log")
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        for h in [h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]:
            root.removeHandler(h)
        report.setup_logging()
        logging.getLogger("maplehelper.test").info("hello log")
        for h in root.handlers:
            h.flush()
        assert "hello log" in (tmp_path / "maplehelper.log").read_text(encoding="utf-8")
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_report_leaves_out_the_stats_id_and_every_unlisted_setting(tmp_path, monkeypatch):
    from maplehelper.store import DEFAULT_SETTINGS
    monkeypatch.setattr(report, "LOG_DIR", tmp_path / "logs")
    settings = {**DEFAULT_SETTINGS, "install_id": "26583820c5e5", "language": "en", "some_new_secret": "x"}
    path = report.build_report(tmp_path / "out", {}, settings)
    with zipfile.ZipFile(path) as z:
        saved = json.loads(z.read("settings.json"))
    assert "install_id" not in saved and "some_new_secret" not in saved and saved["language"] == "en"
    # every setting is either reported or private on purpose: a new one makes this test ask which
    private = {"window", "map_window_pos", "item_window_pos", "pins", "last_session", "wishlist", "farm_target", "microphone",
               "tips_dismissed", "usage_warned", "install_id", "news_read"}
    assert set(DEFAULT_SETTINGS) - set(report.REPORT_SETTINGS) == private


def test_report_names_the_folder_it_really_went_to(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(report, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(report, "DATA_DIR", tmp_path / "data")
    desktop = tmp_path / "desk"
    monkeypatch.setattr(sys, "platform", "win32")
    path, key = report.save_report(desktop, {}, {})
    assert path.parent == desktop and key == "report_saved"

    real = report.build_report

    def blocked(out_dir, info, settings):
        if out_dir == desktop:
            raise PermissionError("Controlled Folder Access")
        return real(out_dir, info, settings)
    monkeypatch.setattr(report, "build_report", blocked)
    path, key = report.save_report(desktop, {}, {})
    assert path.parent == tmp_path / "data" and key == "report_saved_data"


def test_mac_report_never_touches_the_protected_desktop(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(report, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(report, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(sys, "platform", "darwin")
    path, key = report.save_report(tmp_path / "desk", {}, {})
    assert path.parent == tmp_path / "data" and key == "report_saved_data" and not (tmp_path / "desk").exists()


def test_the_report_logs_name_no_user_folder(tmp_path, monkeypatch):
    """The app and installer logs name files under the user's home, and so carried the Windows user name, while
    the report promises no personal details (audit SEC-8)."""
    from pathlib import Path
    home = tmp_path / "עמית כהן"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(report, "LOG_DIR", logs)
    h = str(home)
    (logs / "maplehelper.log").write_text(f"read {h}\\AppData\\x.json\nJSON {json.dumps(h)}\nslash "
                                          f"{h.replace(chr(92), '/')}/a\nother {h}2\\x\n", encoding="utf-8")
    # an installer log in the PC's code page: a Hebrew Windows (the CI runners' code page can't hold the name)
    import locale
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a: "cp1255")
    (logs / "update-1.log").write_bytes(f"Dest filename: {h}\\AppData\\Local\\a.exe\n".encode("cp1255"))
    path = report.build_report(tmp_path / "out", {}, {})
    with zipfile.ZipFile(path) as z:
        app, upd = z.read("logs/maplehelper.log").decode("utf-8"), z.read("logs/update-1.log").decode("utf-8")
    assert "עמית" not in app.replace(f"{h}2", "") and "%USERPROFILE%\\AppData\\x.json" in app
    assert app.count("%USERPROFILE%") == 3 and f"{h}2" in app          # another folder that only starts the same
    assert "עמית" not in upd and "%USERPROFILE%\\AppData\\Local\\a.exe" in upd


def test_libraries_dont_log_every_request(tmp_path, monkeypatch):
    """httpx logged each model download's URL (a signed CDN link among them) at INFO (audit SEC-9)."""
    monkeypatch.setattr(report, "LOG_DIR", tmp_path)
    monkeypatch.setattr(report, "LOG_FILE", tmp_path / "maplehelper.log")
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        report.setup_logging()
        for name in ("httpx", "huggingface_hub", "urllib3"):
            assert not logging.getLogger(name).isEnabledFor(logging.INFO)
        assert logging.getLogger("maplehelper.x").isEnabledFor(logging.INFO)
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
