"""Paths, settings, character profiles and conversation history (all local, in the per-user data folder)."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger("maplehelper")


def _app_root() -> Path:
    # PyInstaller unpacks bundled files next to the executable
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


APP_ROOT = _app_root()
ASSETS = APP_ROOT / "assets"
BUNDLED_KB = APP_ROOT / "data" / "kb"


def _data_root() -> Path:
    """%APPDATA% on Windows, ~/Library/Application Support on macOS. $APPDATA wins anywhere (tests set it)."""
    if os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home()


DATA_DIR = _data_root() / "MapleHelper"
DATA_DIR.mkdir(parents=True, exist_ok=True)
USER_KB = DATA_DIR / "kb"            # knowledge base updates downloaded at runtime
HISTORY_DIR = DATA_DIR / "history"
AVATAR_DIR = DATA_DIR / "avatars"
for d in (HISTORY_DIR, AVATAR_DIR):
    d.mkdir(parents=True, exist_ok=True)


def kb_dir() -> Path:
    """The newest knowledge base: a downloaded update wins over the bundled copy."""
    if (USER_KB / "index.json").exists() and _kb_version(USER_KB) >= _kb_version(BUNDLED_KB):
        return USER_KB      # an app update may ship a newer KB than the one downloaded earlier
    return BUNDLED_KB if (BUNDLED_KB / "index.json").exists() or not (USER_KB / "index.json").exists() else USER_KB


def adopt_bundled_kb() -> None:
    """macOS app: copy the bundled KB to the data folder once, while it is the one in use. The grep tables are
    built into the KB's own folder (the AI works there), and the bundled one sits inside the signed .app: a write
    there breaks the code seal (or fails, read-only, and every question goes without tables). Windows installs
    per user into a folder of its own, so it keeps using the bundled copy."""
    if not (getattr(sys, "frozen", False) and sys.platform == "darwin") or kb_dir() != BUNDLED_KB:
        return
    import shutil
    tmp, old = USER_KB.with_name("kb.new"), USER_KB.with_name("kb.old")
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(BUNDLED_KB, tmp)
        shutil.rmtree(old, ignore_errors=True)
        if USER_KB.exists():
            os.replace(USER_KB, old)        # an older download: the bundled KB is newer (kb_dir chose it)
        os.replace(tmp, USER_KB)
        shutil.rmtree(old, ignore_errors=True)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        if old.exists() and not USER_KB.exists():
            try:
                os.replace(old, USER_KB)
            except OSError:
                pass                       # kb_dir falls back to the bundled copy


def _kb_version(root: Path) -> str:
    try:
        return str(json.loads((root / "meta.json").read_text(encoding="utf-8")).get("version", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def _read_json(path: Path, default):
    """The file's JSON when it has the default's type; else its last good copy (.bak); else the default."""
    for p in (path, path.with_suffix(path.suffix + ".bak")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):          # missing, half-written, not UTF-8 (a power cut)
            continue
        if isinstance(data, type(default)):
            return data
    return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=1))
            f.flush()
            os.fsync(f.fileno())                # on disk before the rename: a power cut can't leave it empty
        if path.exists():
            try:
                import shutil
                shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))    # the last good copy
            except OSError:
                pass
        # antivirus / the search indexer holds the file for a moment: a hold of 0.4 s outlasted 5 x 0.1 s
        for attempt in range(10):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.2)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)         # no stray .tmp left behind by a failed write
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- settings

DEFAULT_SETTINGS = {
    "language": None,             # "he" | "en"; None until onboarding
    "appearance": "light",         # dark | light (opaque surfaces)
    "font_size": 14,
    "answer_length": "short",     # short | detailed
    "window": None,               # {"x","y","w","h","screen"} saved on move/resize
    "item_window_pos": None,      # where the player left the item details window ({"x","y"}); None: beside the chat
    "start_with_windows": False,
    "voice_send_immediately": True,
    "microphone": None,           # a name from voice.input_devices(); None = the system's default microphone
    "voice_language": "app",       # app: transcribe in the app's language | auto: the model guesses
    "voice_last_used": None,       # when a voice question was last heard (epoch s): the model preloads only if recent
    "provider": "claude",          # claude | codex | gemini | grok: which AI CLI answers (see providers/)
    "model": "sonnet",             # Claude's model
    "codex_model": None,           # Codex's model; None = the Codex CLI default
    "grok_model": None,            # Grok's model; None = the Grok CLI default
    "gemini_model": None,          # Gemini's model alias (pro, flash); None = the Gemini CLI default
    "last_model": {},              # provider -> the model that actually answered last (shown in Settings)
    # per provider: use an API key (stored in Credential Manager / Keychain) instead of the account login.
    # Older files hold a single bool here, which meant the Anthropic key.
    "api_key_fallback": {},
    "onboarding_done": False,
    "tour_done": False,           # the first-run tour of the chat window was shown (skipped counts too)
    "pins": {},                   # character id -> pinned answers [{q, a, t}]
    "tips_dismissed": {},         # character id -> {tip kind: level it was hidden at}
    "usage": None,                # last known Claude plan usage (see usage.py)
    "saver_mode": False,          # short answers on a lighter model, so the plan lasts longer
    "usage_warned": 0,            # [reset time, level] of the plan-usage warning already shown
    "wishlist": {},               # character id -> item keys the player is hunting for
    "farm_target": {},            # character id -> the item key the Farm tab shows the droppers of (farm.py)
    "seen_version": "",           # the app version whose "what's new" the player has seen
    "news_read": [],              # news ids the player dismissed or read (news.py), so they come up once
    "last_session": None,         # summary of the previous play session, shown when the chat next opens
    "instant_answers": True,      # simple factual questions answered from the KB, without Claude
    "telemetry": False,           # anonymous usage stats, opt-in (see telemetry.py)
    "install_id": "",             # random id for those stats, created on first use
    "grind_auto": True,           # the grind tracker reads the game every minute while a session runs
}


class Settings:
    path = DATA_DIR / "settings.json"

    def __init__(self):
        self.data = {**DEFAULT_SETTINGS, **_read_json(self.path, {})}
        self._sane_types()
        self._stored_language()

    # stored in another shape than the default on purpose: a single bool from older versions, [reset, level]
    _ANY_TYPE = ("api_key_fallback", "usage_warned")

    def _sane_types(self) -> None:
        """A known setting of the wrong type (a hand edit: "font_size": "big") loads as its default: it crashed
        every start in the stylesheet. Settings whose default is None, and unknown ones, are kept as they are."""
        for key, default in DEFAULT_SETTINGS.items():
            v = self.data.get(key)
            if default is None or key in self._ANY_TYPE:
                continue
            if isinstance(default, bool):
                ok = isinstance(v, bool)
            elif isinstance(default, (int, float)):
                ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            else:
                ok = isinstance(v, type(default))
            if not ok:
                self.data[key] = default

    def _stored_language(self) -> None:
        """v0.9.x stored the language only when it was clicked (Hebrew was preselected): a player who set up the app
        and never clicked it has none, and the system language (for brand-new installs only, UX-3) turned the app's
        direction LTR under a Hebrew UI on an English Windows. They keep Hebrew, as before (review3 UX3-a)."""
        if self.data.get("onboarding_done") and not self.data.get("language"):
            self.data["language"] = "he"
            self.save()

    def __getitem__(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def save(self):
        # a write that fails (file held by a scanner, read-only, disk full) keeps the value in memory: raising here
        # left an answer on "thinking…" (the slot died before set_text) or stopped the app at start
        try:
            _write_json(self.path, self.data)
        except OSError as e:
            log.warning(f"settings not saved: {e}")

    def _api_key_flags(self) -> dict:
        v = self["api_key_fallback"]
        return dict(v) if isinstance(v, dict) else {"claude": bool(v)}

    def api_key_mode(self, provider: str) -> bool:
        """True when this provider runs on a stored API key rather than the player's account login."""
        return bool(self._api_key_flags().get(provider))

    def set_api_key_mode(self, provider: str, on: bool) -> None:
        flags = self._api_key_flags()
        flags[provider] = bool(on)
        self["api_key_fallback"] = flags


# ---------------------------------------------------------------- profiles

@dataclass
class Character:
    id: str
    name: str
    base_class: str               # Beginner | Warrior | Magician | Bowman | Thief
    job: str                      # current job, e.g. Fighter, Cleric
    level: int
    map: str = ""
    active_quests: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    avatar: str = ""              # file in AVATAR_DIR, cropped from the latest screenshot
    exp_pct: float | None = None  # EXP bar of the current level, read from a screenshot
    stats: dict = field(default_factory=dict)          # from the stat window: acc, dmg_min, dmg_max, hp, mp
    quests_done: list[str] = field(default_factory=list)   # quest keys the player marked done
    town: str = ""                                    # citizenship town (Henesys / Kerning City), "" = not chosen
    job_shown: str = ""     # the job as the game's HUD names it ("Archer" on an Old School server for a Bowman)
    name_seen: bool = False  # the name was read off the HUD once: from then on only that exact name is this character
    crafts: dict = field(default_factory=dict)        # crafting profession -> its level
    cycle_done: dict = field(default_factory=dict)    # a daily / weekly quest marked done -> when (it comes back)
    updated_at: float = field(default_factory=time.time)

    @property
    def job_label(self) -> str:
        """The job as the player sees it in game (the app works with the MapleStory Classic name inside)."""
        return self.job        # the KB's name everywhere; job_shown only tells the HUD's reading apart

    def finish_quest(self, name: str) -> list[str]:
        """The started quests this name finishes, removed from active_quests and returned: compared by
        quest_key, so a quest marked done in Play tools by its KB name leaves the one the AI started under it."""
        gone = [a for a in self.active_quests if quest_key(a) and quest_key(a) == quest_key(name)]
        for a in gone:
            self.active_quests.remove(a)
        return gone

    def summary(self) -> str:
        parts = [f"Name: {self.name}", f"Class: {self.base_class}", f"Job: {self.job}", f"Level: {self.level}"]
        if self.map:
            parts.append(f"Last known map: {self.map}")
        if self.active_quests:
            parts.append("Active quests: " + ", ".join(self.active_quests[-QUESTS_IN_PROMPT:]))
        st = self.stats or {}
        if st:
            bits = [f"ACC {st['acc']}" if st.get("acc") else "",
                    f"damage {st['dmg_min']}-{st.get('dmg_max', st['dmg_min'])}" if st.get("dmg_min") else "",
                    f"max HP {st['hp']}" if st.get("hp") else "", f"max MP {st['mp']}" if st.get("mp") else ""]
            parts.append("Stats (stat window): " + ", ".join(b for b in bits if b))
        if self.notes:
            parts.append("Notes: " + "; ".join(self.notes[-10:]))
        return "\n".join(parts)


STAT_KEYS = ("acc", "dmg_min", "dmg_max", "hp", "mp")
# active quests only ever come from what the player mentions, and a finished one is only removed when they say so:
# the list is capped (newest kept) and only the newest few go into every prompt (it reached 200 in testing)
MAX_ACTIVE_QUESTS = 15
QUESTS_IN_PROMPT = 8
MAX_NOTES = 30          # summary() uses the last 10


def quest_key(name: str) -> str:
    """A quest name compared without letter case, spacing or punctuation: "Maya's medicine" finishes the started
    "Maya's Medicine"."""
    return re.sub(r"[\W_]+", "", name.casefold())


def _consistent_job(update: dict, c: "Character") -> dict:
    """Class and job as the app names them, and never a job of another class (the HUD of an Old School server
    says "Archer": that once left a Bowman with the job Assassin)."""
    from .jobs import canonical_class, canonical_job, class_of, first_job
    update = dict(update)
    raw = " ".join(update["job"].split()) if isinstance(update.get("job"), str) else ""
    job = canonical_job(raw) if raw else None
    if job:
        # keep the HUD's own word for it when it differs ("Archer"), shown on the card
        update["job_shown"] = raw.title() if raw.lower() != job.lower() else ""
    cls = canonical_class(update["base_class"]) if isinstance(update.get("base_class"), str) else None
    update.pop("job", None)
    update.pop("base_class", None)
    if job and class_of(job):
        cls = class_of(job)            # the job says which class it is
    if cls:
        update["base_class"] = cls
    if job:
        update["job"] = job
    new_cls = cls or c.base_class
    level = update.get("level") if isinstance(update.get("level"), int) else c.level
    current = job or c.job
    if new_cls in ("Warrior", "Magician", "Bowman", "Thief") and class_of(current) not in (None, new_cls):
        update["job"] = first_job(new_cls, level)
    return update


def same_character(saved: str, hud: str, seen: bool = False, others: tuple[str, ...] = ()) -> bool:
    """Is the name on the HUD the saved character's? Only the same name (letter case aside). A longer name that
    starts with it is asked about, never taken: "Ayash" (Lv. 131 Night Lord) was silently renamed to the alt
    "Ayashii" and overwritten with the alt's class and level (a player's report). The chat offers "this is the same
    character (update the name)" for "Kalimero" typed at setup and "KalimeroZz" in game.
    (seen / others are kept for the callers; an exact match never depends on them.)"""
    return saved.strip().lower() == hud.strip().lower()


def hud_name(update) -> str | None:
    """The character name a screenshot read, when it is a valid in-game name."""
    name = update.get("name") if isinstance(update, dict) else None
    return name.strip() if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9]{2,16}", name.strip()) else None


def _str_list(v) -> list[str]:
    """Quest names from the AI: a list of strings (a bare string is one quest, not its letters)."""
    if isinstance(v, str):
        v = [v]
    return [q.strip() for q in v if isinstance(q, str) and q.strip()] if isinstance(v, list) else []


_TYPES = {f.name: f.type for f in fields(Character)}


def _sane(key: str, v) -> bool:
    """A saved value of the right type: a bad one (once written from a malformed AI reply) is dropped on load."""
    t = str(_TYPES.get(key, ""))
    if t == "str":
        return isinstance(v, str)
    if t == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if t.startswith("list"):
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    if t == "dict":
        return isinstance(v, dict)
    if t == "bool":
        return isinstance(v, bool)
    if t.startswith("float"):         # exp_pct: a number or None
        return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
    return True


def _repair(c: dict) -> dict | None:
    """A saved character with a damaged required field is repaired, not dropped: dropping it lost the character
    for good on the next save (found in testing)."""
    from .jobs import canonical_class, canonical_job, first_job
    if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"]:
        return None
    if not isinstance(c.get("name"), str) or not c["name"].strip():
        return None          # no name at all: not a character the player made
    c = dict(c)
    try:
        c["level"] = max(1, min(250, int(float(c.get("level")))))
    except (TypeError, ValueError, OverflowError):     # NaN, Infinity and 1e400 parse as valid JSON
        c["level"] = 1
    if not isinstance(c.get("base_class"), str) or not canonical_class(c["base_class"]):
        c["base_class"] = "Beginner"
    if not isinstance(c.get("job"), str) or not canonical_job(c["job"]):
        c["job"] = first_job(c["base_class"], c["level"])
    if isinstance(c.get("stats"), dict):      # numbers only: a string here broke the play tools
        c["stats"] = {k: int(v) for k, v in c["stats"].items()
                      if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    for key, cap in (("active_quests", MAX_ACTIVE_QUESTS), ("notes", MAX_NOTES)):
        if isinstance(c.get(key), list):
            c[key] = c[key][-cap:]
    return c


class Profiles:
    path = DATA_DIR / "profiles.json"

    def __init__(self):
        raw = _read_json(self.path, {"active": None, "characters": []})
        known = {f.name for f in fields(Character)}
        # a newer version may have saved fields this one doesn't know (after a downgrade, or a preview build):
        # this one doesn't use them, but keeps them as they are and writes them back, so the newer version finds them
        self._extra = {k: v for k, v in raw.items() if k not in ("active", "characters")}
        self._char_extra: dict[str, dict] = {}
        self.characters = []
        for c in raw.get("characters", []) if isinstance(raw.get("characters"), list) else []:
            try:
                c = _repair(c)
                if c is None:
                    continue
                self.characters.append(Character(**{k: v for k, v in c.items() if k in known and _sane(k, v)}))
            except (TypeError, AttributeError, ValueError, OverflowError):    # still unusable: the others still load
                continue
            extra = {k: v for k, v in c.items() if k not in known}
            if extra:
                self._char_extra[c["id"]] = extra
        self.active_id = raw.get("active")

    @property
    def active(self) -> Character | None:
        return next((c for c in self.characters if c.id == self.active_id), None)

    def add(self, name: str, base_class: str, job: str, level: int) -> Character:
        c = Character(id=uuid.uuid4().hex[:8], name=name, base_class=base_class, job=job, level=level)
        self.characters.append(c)
        self.active_id = c.id
        self.save()
        return c

    def edit(self, cid: str, name: str, base_class: str, job: str, level: int) -> None:
        c = next((c for c in self.characters if c.id == cid), None)
        if c:
            if job != c.job:
                c.job_shown = ""          # picked by hand: the app's own name
            if name != c.name:
                c.name_seen = False       # renamed by hand: the HUD may confirm it again
            c.name, c.base_class, c.job, c.level = name, base_class, job, level
            c.updated_at = time.time()
            self.save()

    def remove(self, cid: str) -> None:
        c = next((c for c in self.characters if c.id == cid), None)
        if not c:
            return
        if c.avatar:
            (AVATAR_DIR / c.avatar).unlink(missing_ok=True)
        History(cid).clear()
        from .grind import Store as GrindStore
        GrindStore().forget(cid)            # its grind sessions go with it
        self.characters.remove(c)
        if self.active_id == cid:
            self.active_id = self.characters[0].id if self.characters else None
        self.save()

    def other_names(self, c: "Character") -> tuple[str, ...]:
        return tuple(o.name for o in self.characters if o is not c)

    def find_by_name(self, name: str) -> "Character | None":
        n = name.strip().lower()
        return next((c for c in self.characters if c.name.strip().lower() == n), None)

    def confirm_hud_name(self, name: str) -> list[tuple[str, object]]:
        """The player said the HUD's other name is the active character's ("this is the same character"): it takes
        that name, confirmed (name_seen), so the next read matches it exactly."""
        c = self.active
        if not c or not hud_name({"name": name}):
            return []
        changed = [("name", name)] if c.name != name else []
        c.name, c.name_seen = name, True
        c.updated_at = time.time()
        self.save()
        return changed

    def set_active(self, cid: str) -> None:
        self.active_id = cid
        self.save()

    def apply_update(self, update: dict) -> list[tuple[str, object]]:
        """Apply a profile update from the assistant. Returns the changed (field, value) pairs."""
        c = self.active
        if not c or not isinstance(update, dict) or not update:
            return []
        update = _consistent_job(update, c)
        changed = []
        relabelled = "job_shown" in update and update["job_shown"] != c.job_shown
        if relabelled:
            c.job_shown = update["job_shown"]
        name = hud_name(update)
        # the name on the HUD (a screenshot read): "Kalimero" typed at setup becomes the real "KalimeroZz".
        # Another name altogether is another character: the overlay asks first and never lands here with it
        if name and same_character(c.name, name, c.name_seen, self.other_names(c)):
            if name != c.name:
                c.name = name
                changed.append(("name", c.name))
            if not c.name_seen:
                c.name_seen, relabelled = True, True
        for key in ("level", "job", "base_class", "map"):
            val = update.get(key)
            if val in (None, "", 0):
                continue
            if key != "level" and not isinstance(val, str):
                continue           # the AI wrote {"name": ...} or a list: never store it (it broke every start)
            if key == "level":
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    continue
                if not 1 <= val <= 250:
                    continue
            if getattr(c, key) != val:
                setattr(c, key, val)
                changed.append((key, val))
        for q in _str_list(update.get("quests_started")):
            if quest_key(q) and all(quest_key(a) != quest_key(q) for a in c.active_quests):
                c.active_quests.append(q)
                changed.append(("quest+", q))
        del c.active_quests[:-MAX_ACTIVE_QUESTS]
        for q in _str_list(update.get("quests_completed")):
            changed += [("quest-", a) for a in c.finish_quest(q)]
        stats = update.get("stats")
        if isinstance(stats, dict):
            clean = {k: int(v) for k, v in stats.items()
                     if k in STAT_KEYS and isinstance(v, (int, float)) and 0 < v < 1_000_000}
            if clean.get("dmg_min", 0) > clean.get("dmg_max", 10**9):
                clean["dmg_min"], clean["dmg_max"] = clean["dmg_max"], clean["dmg_min"]
            new = {**c.stats, **clean}
            if new != c.stats:
                c.stats = new
                changed.append(("stats", ", ".join(f"{k} {v}" for k, v in clean.items())))
        pct = update.get("exp_percent")
        if isinstance(pct, (int, float)) and 0 <= pct <= 100 and pct != c.exp_pct:
            c.exp_pct = round(float(pct), 2)
            changed.append(("exp", c.exp_pct))
        note = update.get("note")
        if isinstance(note, str) and note.strip() and note not in c.notes:
            c.notes.append(note)
            del c.notes[:-MAX_NOTES]
            changed.append(("note", note))
        if changed or relabelled:
            c.updated_at = time.time()
            self.save()
        return changed

    def set_avatar(self, png_bytes: bytes) -> None:
        c = self.active
        if not c:
            return
        name = f"{c.id}-{int(time.time())}.png"
        (AVATAR_DIR / name).write_bytes(png_bytes)
        if c.avatar and c.avatar != name:
            (AVATAR_DIR / c.avatar).unlink(missing_ok=True)
        c.avatar = name
        self.save()

    def avatar_path(self, c: "Character | None" = None) -> Path | None:
        c = c or self.active
        if c and c.avatar and (AVATAR_DIR / c.avatar).exists():
            return AVATAR_DIR / c.avatar
        return None

    def save(self):
        chars = [{**self._char_extra.get(c.id, {}), **asdict(c)} for c in self.characters]
        _write_json(self.path, {**self._extra, "active": self.active_id, "characters": chars})


# ---------------------------------------------------------------- history

class History:
    """Per-character conversation log (jsonl) plus rolling session summaries."""

    RECENT = 20

    def __init__(self, character_id: str):
        self.log = HISTORY_DIR / f"{character_id}.jsonl"
        self.summaries_path = HISTORY_DIR / f"{character_id}.summaries.json"

    def append(self, role: str, text: str, entities: list[str] | None = None) -> None:
        rec = {"t": time.time(), "role": role, "text": text, "entities": entities or []}
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self.log.open("ab+") as f:
                # a crash mid-write leaves a torn last line: without a newline first, this record would be glued to
                # it and lost too
                if f.seek(0, os.SEEK_END) > 0:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        line = b"\n" + line
                f.write(line)
        except OSError as e:        # history is a convenience: a failed write must not stop the question
            log.warning(f"history not saved: {e}")
            return
        self._trim()

    def drop_last_if_user(self, text: str) -> bool:
        """Take back the last record when it is this question with no answer (the answer failed): the History
        window showed questions without answers, and a retry put the question in the conversation twice."""
        try:
            data = self.log.read_bytes()
            body = data.rstrip(b"\n")
            start = body.rfind(b"\n") + 1
            rec = json.loads(body[start:].decode("utf-8"))
            if not (isinstance(rec, dict) and rec.get("role") == "user" and rec.get("text") == text):
                return False
            tmp = self.log.with_suffix(".tmp")
            tmp.write_bytes(data[:start])
            tmp.replace(self.log)
            return True
        except (OSError, ValueError):
            return False

    MAX_BYTES = 4_000_000       # ~8,000 questions: every question reads the file, it mustn't grow forever
    # a trim cuts well under the cap, by size: a fixed line count left long Hebrew answers over it, and then every
    # question rewrote the whole 8 MB file twice
    KEEP_BYTES = 3_000_000

    def _trim(self) -> None:
        try:
            if self.log.stat().st_size <= self.MAX_BYTES:
                return
            data = self.log.read_bytes()
            cut = data.find(b"\n", len(data) - self.KEEP_BYTES)      # the tail, from the start of a line
            tmp = self.log.with_suffix(".tmp")
            tmp.write_bytes(data[cut + 1:] if cut >= 0 else b"")
            tmp.replace(self.log)
        except OSError:
            pass

    def _tail(self, n: int) -> list[str]:
        """The last n lines, read back from the end of the file (not the whole log on every question)."""
        with self.log.open("rb") as f:
            pos = f.seek(0, os.SEEK_END)
            chunk = b""
            while pos > 0 and chunk.count(b"\n") <= n:
                step = min(65536, pos)
                pos -= step
                f.seek(pos)
                chunk = f.read(step) + chunk
        # errors="replace": a line cut off mid-character by a crash must not break every question
        return chunk.decode("utf-8", errors="replace").splitlines()[-n:]

    def recent(self, n: int = RECENT) -> list[dict]:
        if not self.log.exists():
            return []
        lines = self._tail(n)
        out = []
        for ln in lines:
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            # a valid line of the wrong shape must not break every question either
            if isinstance(rec, dict) and isinstance(rec.get("text"), str) and rec.get("role") in ("user", "assistant"):
                out.append(rec)
        return out

    def summaries(self) -> list[str]:
        return _read_json(self.summaries_path, [])

    def add_summary(self, text: str) -> None:
        s = self.summaries()
        s.append(text)
        try:
            _write_json(self.summaries_path, s[-10:])
        except OSError as e:
            log.warning(f"session summary not saved: {e}")

    def clear(self) -> None:
        for p in (self.log, self.summaries_path, self.summaries_path.with_suffix(".json.bak")):
            p.unlink(missing_ok=True)       # the backup copy too, or cleared summaries would come back
