"""Live content: what the owner changes for every player with no app release (docs/LIVE.md).

Some of the app's own files are content, not code: the owner's announcements, feature switches and text fixes
(assets/live/config.json), the official game facts, the AI's instructions (assets/ai), the curated Hebrew of items,
skills, news and guides, and the featured articles (a new one too, see NEW_FILES). assets/live.json lists every one
of them as the repository's main branch has it now (tools/live_manifest.py writes it; a test keeps it current).
Every 15 minutes, with the KB check, the app reads that list from GitHub and downloads each file that differs from
the copy it shipped with into DATA_DIR/live/assets, where asset() finds it first. A merge to main reaches every open
app within about 20 minutes.

What is trusted, and its limit: files come over HTTPS from this repository only, each must match the checksum the
list gives (a half-served or mixed-up file is skipped until the next check), parse as JSON / UTF-8 text (or be a
picture of its type), and be no bigger than MAX_BYTES. Nothing downloaded here is ever run: these are texts, tables
and pictures, read the way the shipped copies are, and each reader falls back to the shipped copy when a live one
doesn't fit (see bundled()).

Format changes: each kind of file carries a contract number (CONTRACTS). A release that reads a kind in a new way
bumps its number here; the list then says the new number, and an older app keeps its shipped copy of that kind
instead of reading a file it doesn't understand. A new app version drops every live file it downloaded before (its
own shipped files are at least as new).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .store import ASSETS, DATA_DIR

log = logging.getLogger("maplehelper")

GITHUB_REPO = "Maple-Helper/maple-helper"
MANIFEST = "live.json"               # in assets/: {"schema": 1, "files": {path: {"sha256", "contract"}}}
LIVE_DIR = DATA_DIR / "live"
FILES_DIR = LIVE_DIR / "assets"
STATE_FILE = LIVE_DIR / "state.json"  # {"app": version, "files": {path: sha256}}: what FILES_DIR holds
MAX_BYTES = 8 * 1024 * 1024          # the biggest live file (a featured article's animation) is ~4 MB
CONFIG = "live/config.json"

# the format version this app reads each kind of live file in (see the module docstring); a kind not listed here
# never comes live
CONTRACTS = {"config": 1, "facts": 1, "ai": 1, "translations": 1, "guides": 1, "featured": 1}
_TRANSLATION_DIRS = ("items", "news", "cashshop", "pet_skills", "quest_tasks", "skill_changes", "skills")


def kind(rel: str) -> str | None:
    """Which kind of live file `rel` (a path under assets/) is, or None when it never comes live."""
    if rel == CONFIG:
        return "config"
    if rel == "official/facts.json":
        return "facts"
    if re.fullmatch(r"ai/[a-z_]+\.(md|json)", rel):
        return "ai"
    if any(rel == f"{d}/he.json" for d in _TRANSLATION_DIRS):
        return "translations"
    if re.fullmatch(r"guides/(he|en)/[A-Za-z0-9_.-]+\.json", rel):
        return "guides"
    if re.fullmatch(r"featured/[a-z0-9][a-z0-9-]*/(?:[a-z0-9_-]+/)?[A-Za-z0-9_.-]+\.(?:json|webp|png|jpg)", rel):
        return "featured"
    return None


# kinds whose new files may come live too (not only changed copies of shipped ones): a featured article is a folder
# of pictures and its manifest, drawn by code every copy has (featured.py)
NEW_FILES = {"featured"}
_MAGIC = {".webp": (b"RIFF", 8, b"WEBP"), ".png": (b"\x89PNG", 0, b""), ".jpg": (b"\xff\xd8\xff", 0, b"")}


def text_sha(data: bytes) -> str:
    """The checksum of a text file whatever its line ends: git may check a file out with CRLF on one PC and LF on
    another, and GitHub serves it as stored."""
    return hashlib.sha256(data.replace(b"\r", b"")).hexdigest()


def branch() -> str:
    """main; $MAPLEHELPER_LIVE_BRANCH points a test copy at a branch (to see an announcement before it is merged)."""
    b = os.environ.get("MAPLEHELPER_LIVE_BRANCH", "").strip()
    return b if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,100}", b) and ".." not in b else "main"


def raw_url(rel: str) -> str:
    return (f"https://raw.githubusercontent.com/{GITHUB_REPO}/{urllib.parse.quote(branch())}/assets/"
            + urllib.parse.quote(rel))


# ---------------------------------------------------------------- what this copy reads

_lock = threading.Lock()
_files: dict[str, str] = {}       # path -> sha256 of the live copies in use
_gen = 0                          # moves on whenever the live files change: readers' caches compare it
_bundled_shas: dict[str, str] = {}


def generation() -> int:
    return _gen


def bundled(rel: str) -> Path:
    """The copy the app shipped with: a reader's fallback when the live one doesn't fit."""
    return ASSETS / rel


def asset(rel: str) -> Path:
    """The file to read for `rel` (a path under assets/): the live copy when one is in use, else the shipped one."""
    if rel in _files:
        p = FILES_DIR / rel
        if p.is_file():
            return p
    return ASSETS / rel


def files(prefix: str) -> list[str]:
    """The live files in use under a folder ("featured/"): new ones the shipped folder doesn't have, among them."""
    with _lock:
        return sorted(r for r in _files if r.startswith(prefix))


def start(version: str) -> None:
    """At start-up: use the live files downloaded before, unless this is a new app version (its shipped files are
    newer than what was downloaded for the old one)."""
    global _gen
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    files = state.get("files") if isinstance(state, dict) else None
    if not isinstance(files, dict) or state.get("app") != version:
        if files:
            log.info("live content from %s dropped: this is %s", state.get("app"), version)
        shutil.rmtree(FILES_DIR, ignore_errors=True)
        files = {}
        _save(version, files)
    with _lock:
        _files.clear()
        _files.update({r: s for r, s in files.items() if kind(r) and (FILES_DIR / r).is_file()})
        _gen += 1
    _reset_caches()


def _save(version: str, files: dict) -> None:
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"app": version, "files": files}, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as e:
        log.warning("live content state not saved: %s", e)


# ---------------------------------------------------------------- the check

def _get(url: str, timeout: int = 30) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MapleHelper", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(MAX_BYTES + 1)
            return data if len(data) <= MAX_BYTES else None
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError):
        return None


def _bundled_sha(rel: str) -> str:
    if rel not in _bundled_shas:
        try:
            _bundled_shas[rel] = text_sha((ASSETS / rel).read_bytes())
        except OSError:
            _bundled_shas[rel] = ""
    return _bundled_shas[rel]


def _usable(rel: str, data: bytes) -> bool:
    """It reads the way its kind is read: JSON with an object at the top, UTF-8 text, or a picture of its type."""
    ext = Path(rel).suffix
    if ext in _MAGIC:
        head, at, tag = _MAGIC[ext]
        return data.startswith(head) and data[at:at + len(tag)] == tag
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if rel.endswith(".json"):
        try:
            return isinstance(json.loads(text), dict)
        except ValueError:
            return False
    return bool(text.strip())


_syncing = threading.Lock()


def sync(version: str, get=_get) -> set[str] | None:
    """Brings the live files in line with the repository's list. Returns the paths whose content changed (an empty
    set: nothing new), or None when the list couldn't be read (the files in use stay)."""
    global _gen
    if not _syncing.acquire(blocking=False):
        return set()                      # a check is running already
    try:
        raw = get(raw_url(MANIFEST), 20)
        try:
            manifest = json.loads(raw) if raw else None
        except ValueError:
            manifest = None
        listed = manifest.get("files") if isinstance(manifest, dict) and manifest.get("schema") == 1 else None
        if not isinstance(listed, dict):
            log.info("live content: no list (%s)", "unreadable" if raw else "didn't download")
            return None
        want: dict[str, str] = {}
        for rel, entry in listed.items():
            # only a file the app ships is replaced: a new one would need code (or pictures) that this copy lacks;
            # but a new featured article (NEW_FILES)
            k = kind(rel) if isinstance(rel, str) and "\\" not in rel and ".." not in rel else None
            k = k if k and (k in NEW_FILES or (ASSETS / rel).is_file()) else None
            sha = entry.get("sha256") if isinstance(entry, dict) else None
            if (k and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)
                    and entry.get("contract") == CONTRACTS[k] and sha != _bundled_sha(rel)):
                want[rel] = sha
        with _lock:
            have = dict(_files)
        files: dict[str, str] = {}
        for rel, sha in want.items():
            if have.get(rel) == sha and (FILES_DIR / rel).is_file():
                files[rel] = sha
                continue
            data = get(raw_url(rel), 60)
            if not data or text_sha(data) != sha or not _usable(rel, data):
                # GitHub's cache can serve the new list before the new file: the next check gets it
                log.info("live content: %s skipped (%s)", rel, "no download" if not data else "doesn't match")
                if rel in have and (FILES_DIR / rel).is_file():
                    files[rel] = have[rel]
                continue
            dest = FILES_DIR / rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".part")
                tmp.write_bytes(data)
                tmp.replace(dest)
            except OSError as e:
                log.warning("live content: %s not saved (%s)", rel, e)
                continue
            files[rel] = sha
        for rel in set(have) - set(files):
            try:
                (FILES_DIR / rel).unlink()
            except OSError:
                pass
        changed = {r for r in set(have) | set(files) if have.get(r) != files.get(r)}
        if changed:
            log.info("live content changed: %s", ", ".join(sorted(changed)))
            _save(version, files)
            with _lock:
                _files.clear()
                _files.update(files)
                _gen += 1
            _reset_caches()
        return changed
    finally:
        _syncing.release()


# ---------------------------------------------------------------- the owner's config (assets/live/config.json)

_config: tuple[int, dict] | None = None


def _reset_caches() -> None:
    global _config
    _config = None


def config() -> dict:
    """{"announcements": [...], "flags": {...}, "min_app": "0.19.0", "strings": {...}}; {} when unreadable."""
    global _config
    hit = _config
    if hit and hit[0] == _gen:
        return hit[1]
    data: dict = {}
    for p in (asset(CONFIG), bundled(CONFIG)):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                break
        except (OSError, ValueError):
            pass
        data = {}
    _config = (_gen, data)
    return data


# every switch the code reads (enabled(name)); a config name not listed here switches nothing (tools/live_edit.py
# refuses it, a test checks the config)
FLAGS = ("mesowatch", "osms_shop", "server_status", "game_toolbar", "quickslot", "provider_claude", "provider_codex",
         "provider_gemini", "provider_grok", "kb_update", "app_update", "telemetry")


def enabled(flag: str) -> bool:
    """A feature switch: on unless the config turns it off ("flags": {"mesowatch": false})."""
    flags = config().get("flags")
    return not (isinstance(flags, dict) and flags.get(flag) is False)


def _version_tuple(v) -> tuple[int, ...]:
    parts = [int(x) for x in re.findall(r"\d+", str(v or ""))[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


def outdated(version: str) -> bool:
    """This version is older than the lowest the owner still supports ("min_app")."""
    low = config().get("min_app")
    return bool(isinstance(low, str) and re.fullmatch(r"\d+\.\d+(\.\d+)?", low)
                and _version_tuple(version) < _version_tuple(low))


def string(key: str, lang: str) -> str | None:
    """The owner's fix for a UI text ("strings": {"key": {"he": "...", "en": "..."}}), or None."""
    strings = config().get("strings")
    row = strings.get(key) if isinstance(strings, dict) else None
    s = row.get(lang) if isinstance(row, dict) else None
    return s if isinstance(s, str) and s.strip() else None


def _when(s, end: bool) -> datetime | None:
    """"2026-10-25" (that whole day, local time) or "2026-10-25 18:00" / "2026-10-25T18:00"."""
    if not isinstance(s, str):
        return None
    s = s.strip().replace("T", " ")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            d = datetime.strptime(s, "%Y-%m-%d")
            return d.replace(hour=23, minute=59, second=59) if end else d
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _pick(a: dict, field: str, lang: str) -> str:
    """An announcement's text in `lang` ("he" / "en"; a title: "title_he"), else in the other language."""
    other = "en" if lang == "he" else "he"
    for k in ((lang, other) if field == "text" else (f"{field}_{lang}", f"{field}_{other}")):
        v = a.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


SAFE_LINK = re.compile(r"https://[^\s\"'<>]{3,500}")


def announcements(version: str, lang: str, dismissed=(), now: datetime | None = None) -> list[dict]:
    """The owner's announcements this player should see now, in order: {"id", "text", "title", "link",
    "link_text", "level", "toast"} in the player's language (the other one when it has none)."""
    now = now or datetime.now()
    out = []
    items = config().get("announcements")
    for a in items if isinstance(items, list) else []:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"].strip():
            continue
        if a["id"] in dismissed:
            continue
        text = _pick(a, "text", lang)
        if not text:
            continue
        start, end = _when(a.get("from"), False), _when(a.get("until"), True)
        if (a.get("from") and not start) or (a.get("until") and not end):
            continue                      # a date that doesn't read: never shown, rather than shown forever
        if (start and now < start) or (end and now > end):
            continue
        if a.get("min_app") and _version_tuple(version) < _version_tuple(a["min_app"]):
            continue
        if a.get("max_app") and _version_tuple(version) > _version_tuple(a["max_app"]):
            continue
        if a.get("lang") in ("he", "en") and a["lang"] != lang:
            continue
        link = a.get("link") if isinstance(a.get("link"), str) and SAFE_LINK.fullmatch(a["link"]) else ""
        out.append({"id": a["id"], "text": text, "title": _pick(a, "title", lang), "link": link,
                    "link_text": _pick(a, "link_text", lang) if link else "",
                    "level": "warning" if a.get("level") == "warning" else "info", "toast": a.get("toast") is True})
    return out
