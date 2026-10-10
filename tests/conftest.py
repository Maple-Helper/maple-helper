"""Shared test setup.

maplehelper.store creates folders under %APPDATA% at import time, so APPDATA is
pointed at a throwaway folder here, before any test imports the app. Tests never
touch a real player's settings, profiles or history.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="maplehelper-tests-")
# the other per-user folders too: the AI CLIs are looked up there (%LOCALAPPDATA%\Programs, ~/.local/bin, ~/.grok)
_HOME = tempfile.mkdtemp(prefix="maplehelper-tests-home-")
for _var in ("LOCALAPPDATA", "USERPROFILE", "HOME"):
    os.environ[_var] = _HOME
os.environ.pop("GROK_BIN_DIR", None)
# every window a test makes stays off the screen (one test file without this flashed a real Settings window)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# offscreen Qt has no fonts unless pointed at them: text would be laid out in a fake fixed-width font, and
# results would change once a test registers real fonts. Every test measures the fonts players have
_FONTS = {"win32": r"C:\Windows\Fonts", "darwin": "/System/Library/Fonts"}.get(sys.platform, "")
if _FONTS and os.path.isdir(_FONTS):
    os.environ.setdefault("QT_QPA_FONTDIR", _FONTS)

# telemetry.py ships a real PostHog key: no test may send real usage stats
# (test_telemetry.py lifts this for itself and mocks the network instead)
os.environ["MAPLEHELPER_NO_TELEMETRY"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import pytest  # noqa: E402

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"

# CI sets this after unpacking a real kb.zip into data/kb: the game-value tests skip without it locally,
# but there a missing KB must fail the run, not leave it green with those tests silently skipped
REQUIRE_REAL_KB = bool(os.environ.get("MAPLEHELPER_REQUIRE_REAL_KB"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    if REQUIRE_REAL_KB and rep.skipped and "knowledge base" in str(rep.longrepr):
        rep.outcome = "failed"
        rep.longrepr = f"MAPLEHELPER_REQUIRE_REAL_KB is set, but this test was skipped: {rep.longrepr}"
    return rep


def _is_address(name: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(name.split("%")[0])
        return True
    except ValueError:
        return False


AI_CLIS = {"codex", "claude", "agy", "grok"}
LOOPBACK = ("127.", "::1", "localhost")


@pytest.fixture(scope="session", autouse=True)
def no_real_world():
    """Tests never start the AI CLIs installed on this machine or reach the internet.

    Windows and dialogs read the account, models and plan usage on background threads, and the prices page
    asks the free market site; on a developer's PC that ran the real Codex app-server and reached meowdb.com.
    Session-wide, so a thread that outlives its test is still covered. A test that needs a CLI or a reply
    patches it itself (FakePopen, find_exe, urlopen) on top of this."""
    import shutil
    import socket

    from maplehelper import market, mesowatch, serverstatus
    from maplehelper.providers import codex

    mp = pytest.MonkeyPatch()
    which = shutil.which
    mp.setattr(shutil, "which", lambda name, *a, **k: None if Path(str(name)).stem.lower() in AI_CLIS
               else which(name, *a, **k))
    mp.setattr(codex, "store_apps", lambda: [])           # the Microsoft Store copy, found through the registry
    mp.setattr(market, "free_market", lambda name, timeout=10: None)
    mp.setattr(market, "item_market", lambda item_id, timeout=8: None)

    # MesoWatch's market file: "can't reach it" (a test that needs it patches _request and resets the module's state)
    def offline(*a, **k):
        raise OSError("tests must not reach meso.watch")
    mp.setattr(mesowatch, "_request", offline)
    # the chat's server-status dot (live from MeowDB): "can't reach it", without asking
    mp.setattr(serverstatus, "fetch", lambda timeout=8: None)

    connect = socket.socket.connect

    def local_only(sock, address):
        host = str(address[0]) if isinstance(address, tuple) else str(address)
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not host.startswith(LOOPBACK):
            raise OSError(f"tests must not reach the network ({host})")
        return connect(sock, address)

    mp.setattr(socket.socket, "connect", local_only)
    # name lookups too: a test that reached for meowdb.com still sent the DNS query before connect refused it
    getaddrinfo = socket.getaddrinfo

    def local_names(host, *a, **k):
        name = host.decode() if isinstance(host, bytes) else str(host or "")
        if name and not name.startswith(LOOPBACK) and not _is_address(name):     # an address is no lookup
            raise socket.gaierror(f"tests must not reach the network ({name})")
        return getaddrinfo(host, *a, **k)

    mp.setattr(socket, "getaddrinfo", local_names)
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def no_high_contrast(monkeypatch):
    """theme.set_mode() follows Windows high contrast on every call: on a PC with a Contrast theme on, "light" came
    out dark and the light-theme tests failed. Off for every test; the ones about it patch it themselves."""
    from maplehelper.ui import theme
    monkeypatch.setattr(theme, "high_contrast", lambda: None)


@pytest.fixture(autouse=True)
def hebrew_system(monkeypatch):
    """A first run picks the system's language (UX-3): the tests run as on the owner's Hebrew Windows, whatever
    the CI runner's locale (an English runner opened every first-run dialog in English). test_ux_setup sets it."""
    from maplehelper import app
    from maplehelper.ui import dialogs
    monkeypatch.setattr(dialogs, "system_language", lambda: "he")
    monkeypatch.setattr(app, "system_language", lambda: "he")


@pytest.fixture
def kb_copy(tmp_path) -> Path:
    """A writable copy of the fixture knowledge base."""
    dst = tmp_path / "kb"
    shutil.copytree(FIXTURE_KB, dst)
    return dst


@pytest.fixture
def kb():
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(FIXTURE_KB)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Settings/Profiles/History (and grind sessions) write into tmp_path instead of the shared test APPDATA."""
    from maplehelper import store
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store, "HISTORY_DIR", tmp_path / "history")
    from maplehelper import grind
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    (tmp_path / "history").mkdir()
    return store
