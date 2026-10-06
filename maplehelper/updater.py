"""Knowledge-base updates from GitHub Releases (players never hit meowdb.com directly).

A release carries kb-manifest.json: {"version": "2026.10.02", "url": ".../kb.zip", "sha256": "..."}.
The zip is unpacked into %APPDATA%/MapleHelper/kb, which then wins over the bundled copy.

What is trusted, and its limit: every file must come over HTTPS from this repository's own release downloads,
for the release's own tag, with the size GitHub lists and the SHA-256 the release publishes (SHA256SUMS.txt /
kb-manifest.json), and never be an older version. That catches corruption, a truncated download and a file from
anywhere else, but the checksum comes from the same release as the file: whoever can publish a release can
publish both. The real fix is a signature from outside the release: Authenticode-sign the installer (and check it
here with WinVerifyTrust and the expected signer before running it), or sign SHA256SUMS.txt / kb-manifest.json with
a key whose public half ships inside the app. Updates are unsigned for now (owner's decision).
"""
from __future__ import annotations

import hashlib
import http.client
import io
import logging
import re
import sys
import json
import shutil
import time
import urllib.error
import urllib.request
import zipfile

from .store import DATA_DIR, USER_KB, kb_dir

log = logging.getLogger("maplehelper")

# Set when the GitHub repository exists (see README, "Publishing").
GITHUB_REPO = "Maple-Helper/maple-helper"
MANIFEST_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/kb-manifest.json" if GITHUB_REPO else ""
# the same repository under its owner before it moved to the Maple-Helper organization: GitHub redirects its release
# links here, and a manifest carried forward from back then still names it (0.8.3 refused it: no KB updates)
FORMER_REPOS = ("Amitaflalo1995/maple-helper",)


_TAG = re.compile(r"v?\d{1,4}(\.\d{1,4}){1,2}")        # "v0.9.2": also safe in a file name
_SHA = re.compile(r"[0-9a-f]{64}")


def release_url_ok(url, tag: str | None = None, name: str | None = None) -> bool:
    """A download from this repository's releases over HTTPS (for that tag and file name, when given)."""
    if not isinstance(url, str) or not GITHUB_REPO:
        return False
    repos = "|".join(re.escape(r) for r in (GITHUB_REPO, *FORMER_REPOS))
    m = re.fullmatch(rf"https://github\.com/(?:{repos})/releases/(?:download/([^/]+)|latest/download)/([^/?#]+)",
                     url)
    if not m:
        return False
    return (tag is None or m.group(1) == tag) and (name is None or m.group(2) == name)


def local_version() -> str:
    try:
        return json.loads((kb_dir() / "meta.json").read_text(encoding="utf-8")).get("version", "")
    except (OSError, json.JSONDecodeError):
        return ""


def changelog() -> list[dict]:
    """Patch notes of recent KB updates, newest first (written by tools/kb_release.py)."""
    try:
        log = json.loads((kb_dir() / "changelog.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [e for e in log if isinstance(e, dict) and e.get("version")] if isinstance(log, list) else []


def changes_since(version: str) -> list[dict]:
    """The updates a player hasn't seen yet: every entry newer than the KB they had."""
    return [e for e in changelog() if str(e["version"]) > version]


def _get(url: str, timeout: int = 30) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MapleHelper"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError):
        return None


def _rename(src, dst, tries: int = 10) -> None:
    """A rename that waits out an antivirus scanning the freshly unpacked files."""
    import time
    for attempt in range(tries):
        try:
            src.rename(dst)
            return
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(0.3)


def update_kb(before_swap=None) -> bool:
    """Download a newer knowledge base if one is published. Returns True when updated."""
    return fetch_kb(before_swap) == "updated"


CHECKED_FILE = DATA_DIR / "kb_checked.txt"


def _remember_checked(day) -> None:
    """The night the KB was last checked against NiaMeowDB (the manifest's "checked", moved on every night even
    when nothing changed): the chat shows it as "knowledge base verified on"."""
    if isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        try:
            CHECKED_FILE.write_text(day, encoding="utf-8")
        except OSError:
            pass


def kb_checked() -> str:
    """"2026-10-03" when known: the manifest's last check, else the installed KB's own date."""
    try:
        day = CHECKED_FILE.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return max(day, _kb_date())
    except OSError:
        pass
    return _kb_date()


def _kb_date() -> str:
    """The installed KB's date from its version ("2026.09.30.2111" -> "2026-09-30")."""
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", local_version())
    return "-".join(m.groups()) if m else ""


def fetch_kb(before_swap=None) -> str:
    """"updated", "uptodate", "postponed" or "failed". before_swap() runs right before the folders are swapped (the app
    stops the AI process working inside the KB there); returning False postpones the update."""
    if not MANIFEST_URL:
        return "uptodate"
    raw = _get(MANIFEST_URL, timeout=15)
    if not raw:
        return _failed("the manifest didn't download")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError:
        return _failed("the manifest isn't JSON")
    if not (isinstance(manifest, dict) and release_url_ok(manifest.get("url"))
            and _SHA.fullmatch(str(manifest.get("sha256", "")).lower())
            and re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(\.\d{1,6})?", str(manifest.get("version", "")))):
        return _failed("the manifest isn't this repository's")    # a real checksum and version too
    _remember_checked(manifest.get("checked"))
    if str(manifest.get("version", "")) <= local_version():
        return "uptodate"
    # the same KB was "updated" again on consecutive starts once (0.9.2): say what was compared, to explain it
    log.info("knowledge base %s found: installed %s in %s", manifest["version"], local_version() or "none", kb_dir())
    data = _get(manifest["url"], timeout=300)
    if not data:
        return _failed("kb.zip didn't download")
    if hashlib.sha256(data).hexdigest() != manifest["sha256"].lower():
        return _failed("kb.zip doesn't match its checksum")
    tmp = USER_KB.with_name("kb.new")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
    except zipfile.BadZipFile:
        shutil.rmtree(tmp, ignore_errors=True)
        return _failed("kb.zip isn't a zip")
    try:
        # it must load, not just exist: a bad release would otherwise stop every start
        index = json.loads((tmp / "index.json").read_text(encoding="utf-8"))
        if not (isinstance(index, list) and index and all(isinstance(e, dict) and e.get("key") and e.get("category")
                                                         for e in index)):
            raise ValueError("index.json has no usable entries")
    except (OSError, ValueError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return _failed(f"the new KB doesn't load ({e})")
    meta_path = tmp / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        if not isinstance(meta, dict):
            raise ValueError("not an object")
        meta["version"] = manifest["version"]
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    except (OSError, ValueError, TypeError) as e:
        shutil.rmtree(tmp, ignore_errors=True)      # not ~46 MB of kb.new left behind
        return _failed(f"the new KB's meta.json is unusable ({e})")
    # swap by renames: on Windows a folder another process works in (the AI runs inside the KB)
    # can't be removed or renamed; then keep the current KB intact and try again next time
    if before_swap is not None and before_swap() is False:
        shutil.rmtree(tmp, ignore_errors=True)
        log.info("knowledge base %s waits: an answer is running", manifest["version"])
        return "postponed"            # an answer is running: the app tries again in a few minutes
    old = USER_KB.with_name("kb.old")
    shutil.rmtree(old, ignore_errors=True)
    try:
        if USER_KB.exists():
            _rename(USER_KB, old)
        _rename(tmp, USER_KB)
    except OSError as e:
        if old.exists() and not USER_KB.exists():
            try:
                _rename(old, USER_KB)
            except OSError:
                pass        # the app falls back to the bundled KB (kb_dir) on its next reload
        shutil.rmtree(tmp, ignore_errors=True)
        return _failed(f"the KB folder can't be swapped ({e})")    # mostly a process still working inside it
    shutil.rmtree(old, ignore_errors=True)
    return "updated"


def _failed(why: str) -> str:
    """Every failed check says why in the log ("Report a problem" sends it): it used to fail without a word."""
    log.warning("knowledge base update failed: %s", why)
    return "failed"


# ---------------------------------------------------------------- app updates

SETUP_ASSET = "MapleHelper-Setup.exe"
SUMS_ASSET = "SHA256SUMS.txt"     # "<sha256>  <file name>" lines, published with every release


def _version_tuple(v: str) -> tuple[int, ...]:
    """'1.0' and '1.0.0' compare equal (padded to three parts)."""
    parts = [int(x) for x in re.findall(r"\d+", v)[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


def installed_copy() -> bool:
    """Run from an installed copy (its uninstaller next to it), not the portable zip: only that one self-updates.
    A portable copy updating itself installed a second copy elsewhere and kept re-downloading (found in testing)."""
    if not getattr(sys, "frozen", False):
        return False
    from pathlib import Path
    return (Path(sys.executable).parent / "unins000.exe").exists()


def _asset(rel: dict, name: str) -> dict | None:
    return next((a for a in rel.get("assets", []) or [] if isinstance(a, dict) and a.get("name") == name), None)


def _published_sha256(rel: dict, name: str) -> str | None:
    """The release's own checksum for `name`, from its SHA256SUMS.txt."""
    sums = _asset(rel, SUMS_ASSET)
    if not sums or not release_url_ok(sums.get("browser_download_url"), rel.get("tag_name"), SUMS_ASSET):
        return None
    raw = _get(sums["browser_download_url"], timeout=30)
    if not raw:
        return None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def _latest_release() -> dict | None:
    raw = _get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=15) if GITHUB_REPO else None
    try:
        rel = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None
    if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
        return None
    if not isinstance(rel.get("tag_name"), str) or not _TAG.fullmatch(rel["tag_name"]):
        return None           # the tag names the downloaded file: nothing but a plain version
    return rel


def newer_release(current: str) -> tuple[str, str] | None:
    """(version, release page URL) when GitHub has a newer release: macOS shows a notice instead of self-updating."""
    rel = _latest_release()
    if not rel or _version_tuple(rel.get("tag_name", "")) <= _version_tuple(current):
        return None
    return rel["tag_name"].lstrip("v"), rel.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases/latest"


def _download(url: str, progress=None, timeout: int = 600) -> bytes | None:
    """The whole file, reporting progress(done_bytes, total_bytes) as it arrives."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MapleHelper"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            total = int(r.headers.get("Content-Length") or 0)
            chunks, done = [], 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
            return b"".join(chunks)
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError):
        return None


def download_app_update(current: str, progress=None) -> str | None:
    """If GitHub has a newer release, download its installer. Returns the installer path.
    progress(done_bytes, total_bytes) is called while it downloads.

    The installer is only kept when it comes from this repository's release of that tag over HTTPS, has the size
    GitHub lists and the SHA-256 of the release's SHA256SUMS.txt, and is newer than this version (never a
    downgrade): it is executed on the player's PC, so a truncated, corrupted or foreign file must never run.
    It is not signed yet (see the module docstring: Authenticode + WinVerifyTrust is the real fix).
    """
    rel = _latest_release()
    if not rel or _version_tuple(rel["tag_name"]) <= _version_tuple(current):
        return None
    asset = _asset(rel, SETUP_ASSET)
    if not (asset and release_url_ok(asset.get("browser_download_url"), rel["tag_name"], SETUP_ASSET)
            and asset.get("state", "uploaded") == "uploaded"):
        return None
    size = asset.get("size")
    want = _published_sha256(rel, SETUP_ASSET)
    if not want or not isinstance(size, int) or size <= 0:
        return None
    path = USER_KB.parent / "updates" / f"MapleHelper-Setup-{rel['tag_name']}.exe"
    try:
        if path.exists() and path.stat().st_size == size and hashlib.sha256(path.read_bytes()).hexdigest() == want:
            return str(path)       # downloaded before (an update skipped at shutdown): no second download
    except OSError:
        pass
    url = asset["browser_download_url"]
    data = _download(url, progress) if progress else _get(url, timeout=600)
    if not data or len(data) != size or hashlib.sha256(data).hexdigest() != want:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    try:
        part.write_bytes(data)
    except OSError:
        part.unlink(missing_ok=True)    # a cut write (disk full) mustn't leave ~100 MB behind
        raise
    part.replace(path)          # whole or not at all: a cut write never looks like a ready installer
    return str(path)


def remove_old_installers() -> None:
    """Downloaded installers for this version or older are no longer needed (~100 MB each)."""
    from . import __version__
    folder = USER_KB.parent / "updates"
    for f in folder.glob("MapleHelper-Setup-*.exe") if folder.exists() else []:
        if _version_tuple(installer_version(str(f))) <= _version_tuple(__version__):
            try:
                f.unlink()
            except OSError:
                pass
    # a half-written download (MapleHelper-Setup-v0.9.6.part) is never used: the next download writes it again
    for f in folder.glob("MapleHelper-Setup-*.part") if folder.exists() else []:
        try:
            f.unlink()
        except OSError:
            pass


def installer_version(path: str) -> str:
    """'0.4.0' from '.../MapleHelper-Setup-v0.4.0.exe'."""
    m = re.search(r"Setup-v?([\d.]+)\.exe$", str(path))
    return m.group(1) if m else ""


def installer_args(path: str, reopen: bool, lang: str = "he") -> list[str]:
    # the installer starts the app again when done: in the tray after a quiet update on quit,
    # with the chat open when the player pressed "Update now" (see [Run] in packaging/installer.iss)
    # why an update failed, if it ever does: one log per run (Inno overwrites its log), the last few kept
    logs = USER_KB.parent / "logs"
    for old in sorted(logs.glob("update-*.log"))[:-2] if logs.exists() else []:
        try:
            old.unlink()
        except OSError:
            pass
    log = logs / f"update-{time.strftime('%Y%m%d-%H%M%S')}.log"
    # "Update now": /SILENT shows the installer's own progress window while the app is closed, so the
    # player sees the update happen; an update on quit stays fully quiet (/VERYSILENT)
    # "Update now" keeps Inno's message boxes: a file held by an antivirus scan then offers Retry, where a
    # suppressed box answers Abort and leaves a half-replaced install
    args = [path, "/SILENT" if reopen else "/VERYSILENT", *([] if reopen else ["/SUPPRESSMSGBOXES"]),
            "/NORESTART", f"/LOG={log}",
            # the installer's window in the app's language, not Windows' ([Languages] in installer.iss)
            "/LANG=" + ("english" if lang == "en" else "hebrew")]
    if reopen:
        args.append("/LAUNCHARGS=--updated")
    return args


def windows_shutting_down() -> bool:
    """Windows is shutting down or signing out: an installer started now would be killed halfway."""
    if sys.platform != "win32":
        return False
    import ctypes
    SM_SHUTTINGDOWN = 0x2000
    return bool(ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN))


def run_installer_silently(path: str, reopen: bool = False, lang: str = "he") -> None:
    """Runs after the app exits; the installer restarts the app when done."""
    import subprocess
    subprocess.Popen(installer_args(path, reopen, lang), close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
