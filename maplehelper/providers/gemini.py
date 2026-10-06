"""Gemini through Google's Antigravity CLI (`agy`): the player's Google account (Gemini plan), or a Gemini API key.

Since June 2026 Google serves personal accounts (free, AI Pro, AI Ultra) only through Antigravity,
not Gemini CLI. Each question runs `agy` headless, locked down:
  * a custom agent of ours (excludeDefaultComponents) holds our instructions and only four read
    tools (view_file, grep_search, list_dir, find_by_name): no shell, writing, web or browser;
  * file reads are allowed only in the knowledge base and the screenshot folder (permissions.allow);
    headless runs auto-deny anything that would need approval. agy also reads its own temp folder
    freely, so that is a private one in its home, not the player's %TEMP%;
  * the CLI gets a home of its own in Maple Helper's data folder (HOME/USERPROFILE), so the player's
    own Antigravity setup (rules, skills, plugins, MCP servers) never reaches the answers. The Google
    sign-in itself lives in the system's credential store, shared with the player's own Antigravity.
The screenshot is a file the agent opens with view_file (agy takes only text in a message), the
question comes on stdin and the answer streams back.

Sign-in: agy prints a Google link and opens it in the browser itself; after signing in, the page shows a
code the player pastes back (it waits 60 seconds). login() starts it hidden; submit_login_code() types the
code into its console. Anything else run with -p while signed out would start that same sign-in, so the
usage read checks first and an answer stops at "Authentication required".
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import base
from .base import CREATE_NO_WINDOW, Installer, Provider, RawResult, classify_error, child_env, find_posix, http_ok, \
    http_status, run_installer

log = logging.getLogger(__name__)
STALL_TIMEOUT_S = 150    # no output for this long = stuck (tool steps and streaming print all along)
CHECK_TIMEOUT_S = 30

INSTALL_CMD = "irm https://antigravity.google/cli/install.ps1 | iex"
INSTALL_CMD_MAC = "curl -fsSL https://antigravity.google/cli/install.sh | bash"
POSIX_DIRS = ["~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"]

# the sign-in lives in the system credential store (go-keyring: service "gemini", user "antigravity")
CREDENTIAL = ("gemini", "antigravity")

AGENT = "maplehelper"
SUMMARY_AGENT = "maplehelper-summary"     # each kind of call has its own agent file: they can run together
QUICK_AGENT = "maplehelper-quick"
SHOT_AGENT = "maplehelper-shot"          # a quick screenshot read (the ⟳ sync): view_file only
TOOLS = ["view_file", "grep_search", "list_dir", "find_by_name"]
SHOT_TOOLS = ["view_file"]
RETRY_NOTE = ("\n\n(Your last attempt stopped at a blocked file. Read only inside the knowledge-base folder "
              "and the screenshot, then answer.)")
SIGNED_IN_AS = re.compile(r"authenticated successfully as (\S+@\S+)")
AUTH_NEEDED = b"Authentication required"     # agy, signed out, about to start a sign-in


def home() -> Path:
    """Maple Helper's own Antigravity home: settings, our agents, the conversations it keeps."""
    from ..store import DATA_DIR
    return DATA_DIR / "antigravity"


def shots_dir() -> Path:
    return home() / "shots"


def tmp_dir() -> Path:
    return home() / "tmp"


def find_windows() -> str | None:
    p = shutil.which("agy")
    if p and p.lower().endswith(".exe"):
        return p
    c = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    return str(c) if c.exists() else None


def find_agy() -> str | None:
    """The Antigravity CLI from Google's installer (Windows: %LOCALAPPDATA%\\agy\\bin, macOS: ~/.local/bin)."""
    return find_windows() if sys.platform == "win32" else find_posix("agy", POSIX_DIRS)


def env(api_key: str | None = None, tmp: Path | None = None) -> dict:
    """tmp: this run's own temp folder (default: the shared one in our home)."""
    e = child_env(POSIX_DIRS)
    h = str(home())
    e["HOME"] = h
    if sys.platform == "win32":
        e["USERPROFILE"] = h
    # agy's tools may always read the process's temp folder (a scratch area), whatever permissions.allow says:
    # with the player's own %TEMP% that opened other apps' files and Codex's screenshots to view_file, list_dir
    # and grep_search. Pointed at a folder of ours, the allow list holds.
    t = tmp or tmp_dir()
    t.mkdir(parents=True, exist_ok=True)
    e["TEMP"] = e["TMP"] = e["TMPDIR"] = str(t)
    e.pop("GEMINI_API_KEY", None)
    if api_key:
        e["GEMINI_API_KEY"] = api_key
    return e


def settings_path() -> Path:
    return home() / ".gemini" / "antigravity-cli" / "settings.json"


def write_settings(kb_root, api_key: bool) -> None:
    """Reads only in the knowledge base and the screenshot folder; nothing outside asks (headless = denied)."""
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings = {
        "allowNonWorkspaceAccess": False,
        "enableTelemetry": False,
        "permissions": {"allow": [f"read_file({Path(kb_root).resolve()})", f"read_file({shots_dir().resolve()})"]},
    }
    if api_key:
        settings["modelProvider"] = "gemini"      # the Gemini API with GEMINI_API_KEY instead of the sign-in
    _write(settings_path(), json.dumps(settings, indent=2))


def sign_in_mode() -> None:
    """The account checks, the sign-in and the usage read run on the Google sign-in: an API-key question's
    "modelProvider" left in the settings made agy refuse them all ("GEMINI_API_KEY is not set")."""
    try:
        settings = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(settings, dict) and settings.pop("modelProvider", None):
        _write(settings_path(), json.dumps(settings, indent=2))


def agent_text(name: str, instructions: str, tools: list[str]) -> str:
    tool_lines = "".join(f"  - {t}\n" for t in tools) if tools else ""
    return (f"---\nname: {name}\ndescription: Maple Helper, the in-game assistant for MapleStory Classic.\n"
            f"tools:{'' if tools else ' []'}\n{tool_lines}excludeDefaultComponents: true\n---\n{instructions}\n")


def write_agent(name: str, instructions: str, tools: list[str]) -> None:
    d = home() / ".gemini" / "config" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / f"{name}.md", agent_text(name, instructions, tools))


def _write(path: Path, text: str) -> None:
    """Only when it changed, through a temp file of its own (an answer and a summary can write together)."""
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        log.warning("couldn't write %s", path.name, exc_info=True)   # the other writer's copy is the same
        try:
            os.remove(tmp)
        except OSError:
            pass


def tools_note(kb_root) -> str:
    """Without the default prompt the agent doesn't know where it is: the folder goes in by full path."""
    return (f"\n\nTools: the knowledge base is the folder {Path(kb_root).resolve()} - read it with view_file, "
            "grep_search, list_dir and find_by_name, always with absolute paths. Nothing outside that folder "
            "(and the screenshot) is open to you: never list or open other folders, they are blocked. You cannot "
            "write files, run commands or use the network.")


SHOT_NOTE = ("\n\nThis is a quick screenshot read. The knowledge base is not open to you this time: do not search, "
             "list or open any folder. Your only tool is view_file, for the player's game screenshot (its path comes "
             "with the question). Open it first, then answer from it and the player's profile.")


def shots_line(shots: list[Path]) -> str:
    """The screenshot's path, with the question: each run has a folder of its own, so in the agent's instructions it
    rewrote the agent file for every question."""
    return ("\n\nThe player's game screenshot is attached as " + ", ".join(str(s) for s in shots) +
            ": open it with view_file first, before answering.") if shots else ""


def agy_command(exe: str, agent: str, model: str | None = None) -> list[str]:
    """The question comes on stdin (agy reads it there when there's no -p)."""
    cmd = [exe, "--agent", agent, "--output-format", "stream-json", "--disable-slash-commands"]
    if model:
        cmd += ["--model", model]
    return cmd


def classify(text: str) -> str | None:
    t = text.lower()
    # whole words and a bare status code only: "design in", a request id or "1429 ms" in stderr aren't a sign-out
    if ("authentication" in t or "not logged in" in t or "not authenticated" in t or re.search(r"\bsign in\b", t)
            or "api key not valid" in t):
        return "not_logged_in"
    if "quota" in t or "limit remaining" in t or "resource_exhausted" in t or "rate limit" in t or http_status(t, 429):
        return "usage_limit"
    return classify_error(text)


def parse_events(lines, on_delta=None, stats: dict | None = None) -> tuple[str, dict | None, list[str], str | None]:
    """(answer text, the result, errors, conversation id) from agy's stream-json output. The answer is the
    text after the last tool step: earlier text is a lead-in ("I'll check the database"). stats: filled with
    "tool_calls", the tool steps it ran (the evals)."""
    current, result, errors, conv = "", None, [], None
    tools, active = 0, False      # a tool step reports ACTIVE, then DONE: counted when it turns active
    seen: set = set()             # the tool steps' ids counted
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("event")
        if kind == "init":
            conv = ev.get("conversation_id") or conv
        elif kind == "step_update":
            s = ev.get("step_update") or {}
            conv = s.get("conversation_id") or conv
            if s.get("step_type") == "tool":
                # by the step's id when it has one: a second tool going ACTIVE before the first was DONE wasn't
                # counted; with none, a run of ACTIVE updates is one step
                sid = next((s[k] for k in ("step_id", "step_index", "tool_call_id", "id") if s.get(k) is not None),
                           None)
                if sid is not None:
                    if s.get("state") == "ACTIVE" and sid not in seen:
                        tools += 1
                        seen.add(sid)
                else:
                    tools += s.get("state") == "ACTIVE" and not active
                active = s.get("state") == "ACTIVE"
            if s.get("step_type") == "tool" and s.get("state") == "ACTIVE":
                current = ""
            elif s.get("step_type") == "agent_response" and s.get("text_delta"):
                current += str(s["text_delta"])
                if on_delta:
                    on_delta(current)
            if (s.get("tool_info") or {}).get("error"):
                log.info("Gemini tool %s refused: %s", s.get("tool_name"),
                         str(s["tool_info"]["error"].get("message", ""))[:200])
        elif kind == "result":
            result = ev.get("result") or {}
            conv = result.get("conversation_id") or conv
            break                   # the answer is complete: whatever the process does next doesn't matter
    if stats is not None:
        stats["tool_calls"] = tools
    return current, result, errors, conv


def to_result(text: str, result: dict | None, stderr: str, model: str | None) -> RawResult:
    if not result or result.get("status") != "SUCCESS":
        detail = str((result or {}).get("error", "")) + "\n" + stderr
        log.warning("Gemini gave no answer: %s", base.scrub(detail.strip()[-1500:]))   # the cause, for "Report a problem"
        return RawResult(error=classify(detail) or ("api_error" if result else "no_result"))
    answer = text or str(result.get("response") or "")
    if not answer.strip():
        log.warning("Gemini answered nothing: %s | %s", result.get("denied_actions"), base.scrub(stderr[-500:]))
        # it reached for something blocked and stopped there (agy doesn't work around a refusal)
        return RawResult(error="denied" if result.get("denied_actions") else "no_result")
    return RawResult(text=answer, model=model)


# what agy keeps across runs besides each conversation: summaries of past questions (titles, previews), state
# snapshots and per-run locks. All of it is about questions Maple Helper asked; none of it is needed again.
RUN_LEFTOVERS = ("conversation_summaries.db", "conversation_summaries.db-shm", "conversation_summaries.db-wal",
                 "jetbox_summaries_proto.pb")


def forget(conv: str | None) -> None:
    """agy keeps every run (conversation, notes, annotations, summaries); Maple Helper keeps nothing. A file
    another run still has open stays (Windows won't delete it) and goes with a later run."""
    d = home() / ".gemini" / "antigravity-cli"
    gone = []
    if conv and re.fullmatch(r"[\w-]+", conv):
        gone += list((d / "conversations").glob(conv + ".db*")) + [d / "annotations" / f"{conv}.pbtxt"]
        shutil.rmtree(d / "brain" / conv, ignore_errors=True)
    gone += [d / name for name in RUN_LEFTOVERS]
    for sub in ("presence", "implicit"):
        gone += list((d / sub).glob("*"))
    logs = sorted((d / "log").glob("cli-*.log"))
    gone += logs[:-3]                 # its own logs (they name the questions): keep the last few for problems
    for f in gone:
        try:
            f.unlink()
        except OSError:
            pass


def parse_models(output: str) -> list[tuple[str, str]]:
    """`agy models` prints "<id>\\t<name>" lines; the Gemini ones are this provider's."""
    out = []
    for line in output.splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 2 and parts[0].startswith("gemini-"):
            out.append((parts[0], parts[1]))
    return out


def parse_usage(data) -> dict | None:
    """`agy -p /usage --output-format json`: the Gemini group's 5-hour and weekly windows, in usage.parse's shape."""
    from datetime import datetime
    try:
        groups = data["command"]["data"]["groups"]
    except (KeyError, TypeError):
        return None
    out = {}
    for g in groups if isinstance(groups, list) else []:
        if not isinstance(g, dict) or not str(g.get("name", "")).lower().startswith("gemini"):
            continue
        for b in g.get("buckets") or []:
            name = {"5h": "five_hour", "weekly": "seven_day"}.get(b.get("window")) if isinstance(b, dict) else None
            left = b.get("remaining_fraction") if name else None
            if not isinstance(left, (int, float)):
                continue
            try:
                resets = datetime.fromisoformat(str(b.get("reset_time")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                resets = None
            out[name] = {"used": max(0.0, min(1.0, 1 - float(left))), "resets": resets}
    return out or None


def signed_in_email() -> str | None:
    """agy has no command that names the account, but its log says "authenticated successfully as <email>"
    each time it starts signed in (the log is in Maple Helper's own Antigravity home)."""
    try:
        with open(home() / ".gemini" / "antigravity-cli" / "cli.log", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    found = SIGNED_IN_AS.findall(text)
    return found[-1].rstrip(".,;") if found else None


_models_cache: list[tuple[str, str]] = []
_models_at = 0.0
_check_lock = threading.Lock()
SAVER_ALIAS = "gemini-flash-low"   # saver mode before the model list was read: the newest Flash, low effort


def lightest(models: list[tuple[str, str]]) -> str | None:
    """Saver mode: the newest Flash at low effort ("gemini-3.8-flash-low"; agy lists the newest first)."""
    return next((m for m, _ in models if "flash" in m and m.endswith("-low") and "lite" not in m), None)


class CheckFailed(Exception):
    """agy was found but didn't answer (timed out): neither "not installed" nor "signed out"."""


class Offline(CheckFailed):
    """agy couldn't reach Google ("Eligibility check failed: ... dial tcp ... no such host"): the sign-in may be
    fine, so not "signed out"."""


def _run(args: list[str], timeout: float = CHECK_TIMEOUT_S) -> subprocess.CompletedProcess | None:
    """A quick agy command on the sign-in. One at a time: each start rewrites cli.log, which names the account.
    None when agy is missing or won't start; CheckFailed when it hangs."""
    exe = find_agy()
    if not exe:
        return None
    with _check_lock:
        home().mkdir(parents=True, exist_ok=True)
        sign_in_mode()
        try:
            return subprocess.run([exe, *args], capture_output=True, timeout=timeout, env=env(), cwd=str(home()),
                                  stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as e:
            log.warning("agy %s timed out", args[:1])
            raise CheckFailed() from e
        except OSError:
            log.warning("agy %s can't start", args[:1], exc_info=True)
            return None
        finally:
            forget(None)


def read_models(max_age: float = 10.0) -> list[tuple[str, str]] | None:
    """`agy models`: the Gemini list when signed in, [] when signed out, None when agy is missing or won't start.
    The account check and the model list both need it: one run serves both for a few seconds."""
    global _models_cache, _models_at
    if _models_cache and time.monotonic() - _models_at < max_age:
        return _models_cache
    r = _run(["models"])
    if r is None:
        return None
    if r.returncode != 0:
        # offline, agy ends 1 with only the network error on stderr and no list: that is no sign of being signed
        # out (it said "not signed in" to a signed-in player)
        detail = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        if classify(detail) == "offline":
            log.warning("agy models: no connection: %s", base.scrub(detail.strip()[-300:]))
            raise Offline()
    found = parse_models(r.stdout.decode("utf-8", errors="replace"))
    if found:
        _models_cache, _models_at = found, time.monotonic()
    return found


def resolve_model(model: str | None) -> str | None:
    """SAVER_ALIAS becomes the newest light Flash this account offers (read now when the list isn't in yet)."""
    if model != SAVER_ALIAS:
        return model
    try:
        return lightest(_models_cache or read_models() or [])
    except CheckFailed:
        return None


class Gemini(Provider):
    name = "gemini"
    label = "Gemini"
    keyring_user = "gemini_api_key"
    model_setting = "gemini_model"
    reports_usage = True       # read on demand (agy -p /usage)
    login_code = True          # the sign-in ends with a code the player pastes back

    @property
    def saver_model(self) -> str | None:
        """The list is read on demand: right after a start it isn't in yet, and saver mode then asks by alias
        (the backend picks the model when it runs)."""
        return lightest(_models_cache) or SAVER_ALIAS

    def find_exe(self) -> str | None:
        return find_agy()

    def models(self) -> list[tuple[str | None, str]]:
        """Google's current Gemini list for this account (agy models); just the default when it can't be read."""
        try:
            return [(None, "")] + (read_models() or [])
        except CheckFailed:
            return [(None, "")]

    def read_limits(self, timeout: float = CHECK_TIMEOUT_S) -> dict | None:
        try:
            # signed out, agy answers /usage by starting a sign-in, and opens Google in the browser by itself
            # (opening Settings did that): read it only once `agy models` (which never does) says signed in
            if not read_models():
                return None
            r = _run(["-p", "/usage", "--output-format", "json"], timeout)
        except CheckFailed:
            return None
        if not r or r.returncode != 0:
            return None
        try:
            return parse_usage(json.loads(r.stdout.decode("utf-8", errors="replace").strip().splitlines()[-1]))
        except (ValueError, IndexError):
            return None

    def account(self) -> dict:
        """agy models answers in ~2 s: the list when signed in, "Please sign in" when not."""
        if not find_agy():
            return {"status": "not_installed", "email": None}
        try:
            found = read_models(max_age=0)
        except Offline:
            return {"status": "offline", "email": None}         # say so, and no sign-in to offer: it would fail too
        except CheckFailed:
            # it hangs (offline, a stuck update): not "not installed" (a reinstall won't help); a sign-in might
            return {"status": "logged_out", "email": None}
        if found is None:
            return {"status": "not_installed", "email": None}    # found but won't start: offer the installer
        if found:
            return {"status": "ok", "email": signed_in_email()}
        return {"status": "logged_out", "email": None}

    def logout(self) -> bool:
        """Forget the Google sign-in (agy has no sign-out command of its own outside its window)."""
        try:
            if sys.platform == "win32":
                from win32ctypes.pywin32 import win32cred
                win32cred.CredDelete(":".join(CREDENTIAL), win32cred.CRED_TYPE_GENERIC)
            else:
                import keyring
                keyring.delete_password(*CREDENTIAL)
            return True
        except Exception as e:      # noqa: BLE001 - not signed in at all is fine too
            log.info("Gemini sign-out: %s", e)
            return "not found" in str(e).lower() or "1168" in str(e)

    def login(self) -> subprocess.Popen | None:
        """Hidden: agy prints the Google link (opened here) and waits 60 s for the code (submit_login_code)."""
        exe = find_agy()
        if not exe:
            return None
        home().mkdir(parents=True, exist_ok=True)
        sign_in_mode()
        # agy opens the Google page in the browser by itself (a second tab from here was one too many)
        return base.open_login(exe, ["-p", "Reply with just: OK", "--output-format", "json"], env(),
                               cwd=str(home()), keep_stdin=True)

    def submit_login_code(self, code: str) -> bool:
        return base.send_login_input(code.strip() + "\n")

    def install(self) -> Installer:
        return run_installer(INSTALL_CMD, INSTALL_CMD_MAC)

    def test_api_key(self, key: str) -> bool:
        return http_ok("https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key": key})

    def backend(self, brain):
        return GeminiBackend(brain)


class GeminiBackend:
    """Runs questions for a Brain through agy, one fresh process per question."""

    def __init__(self, brain):
        self.brain = brain
        self.exe = find_agy()
        self._proc: subprocess.Popen | None = None
        self._running: set[subprocess.Popen] = set()     # every run, summaries too: a quit stops them all

    def prewarm(self) -> None:
        pass

    def shutdown(self) -> None:
        self.cancel()
        for p in list(self._running):
            if p.poll() is None:
                base.kill(p)

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            base.kill(self._proc)

    def _exec(self, agent: str, instructions: str, tools: list[str], stdin_text: str, model: str | None,
              on_delta=None, answer: bool = True, timeout: float | None = None) -> RawResult:
        b = self.brain
        write_settings(b.kb.root, bool(b.api_key))
        write_agent(agent, instructions, tools)
        r = self._once(agent, stdin_text, model, on_delta, answer, timeout)
        if r.error == "denied":
            log.info("Gemini stopped at a blocked read: asking once more")
            r = self._once(agent, stdin_text + RETRY_NOTE, model, on_delta, answer, timeout)
            if r.error == "denied":
                r = RawResult(error="no_result")
        if model and r.error == "bad_model":
            # a model Google no longer offers (or one of the sign-in's, on an API key): the default instead
            log.warning("Gemini model %s unknown, using the default", model)
            r = self._once(agent, stdin_text, None, on_delta, answer, timeout)
        return RawResult(error="api_error") if r.error == "bad_model" else r

    def _once(self, agent, stdin_text, model, on_delta, answer, timeout) -> RawResult:
        tmp_dir().mkdir(parents=True, exist_ok=True)
        # a temp folder per run, gone with it: an answer and a summary can run together
        tmp = Path(tempfile.mkdtemp(prefix="run-", dir=tmp_dir()))
        try:
            return self._run_in(agent, stdin_text, model, on_delta, answer, timeout, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _run_in(self, agent, stdin_text, model, on_delta, answer, timeout, tmp: Path) -> RawResult:
        try:
            p = subprocess.Popen(agy_command(self.exe, agent, model), cwd=str(self.brain.kb.root),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env(self.brain.api_key, tmp), creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            return RawResult(error=f"launch_failed: {e}")
        if answer:
            self._proc = p
        self._running.add(p)
        err: list[bytes] = []
        signed_out = threading.Event()

        def drain_stderr():
            for chunk in iter(lambda: p.stderr.read(4096), b""):
                err.append(chunk)
                if AUTH_NEEDED in b"".join(err[-2:]):
                    stop_signed_out()
        reader = threading.Thread(target=drain_stderr, daemon=True)

        def stop_signed_out():
            # signed out: agy would open a Google sign-in in the browser and wait a minute for a code
            signed_out.set()
            p.kill()
        reader.start()

        def feed():
            # a question is bigger than the pipe: written from here, so a CLI stuck before reading it is still
            # stopped by the watchdog below
            try:
                p.stdin.write(stdin_text.encode("utf-8"))
                p.stdin.close()
            except OSError:
                pass
        threading.Thread(target=feed, daemon=True).start()
        # read until the result line, not until the process exits (see base.Lines)
        out = base.Lines(p, STALL_TIMEOUT_S, "Gemini", deadline_s=timeout)

        def lines():
            for line in out:
                if line is None:
                    continue
                out.touch()
                if AUTH_NEEDED in line:
                    stop_signed_out()
                    break
                yield line
        conv = None
        stats: dict = {}
        try:
            text, result, _errors, conv = parse_events(lines(), on_delta, stats)
            out.finish()
            if not result or result.get("status") != "SUCCESS":
                reader.join(timeout=5)       # its words say what went wrong (an answer doesn't wait for them)
            stderr = b"".join(err).decode("utf-8", errors="replace")
        finally:
            self._running.discard(p)
            forget(conv)
        if signed_out.is_set():
            log.warning("Gemini is signed out: the question stopped before agy opened a sign-in")
            return RawResult(error="not_logged_in")
        if out.stalled:
            log.warning("Gemini stalled, stopped: %s", base.scrub(stderr[-1000:]))
            return RawResult(error="timeout")
        if "invalid model selection" in str((result or {}).get("error", "")):
            return RawResult(error="bad_model")
        r = to_result(text, result, stderr, model)
        r.tool_calls = stats.get("tool_calls")
        return r

    def run(self, prompt: str, screenshot_jpeg: bytes | None, on_raw_delta=None, model: str | None = None,
            tools: bool = True) -> RawResult:
        """model: this call's own (None: the player's). tools=False: no knowledge-base tools (a quick call)."""
        b = self.brain
        shots_dir().mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="run-", dir=shots_dir()))
        try:
            shots = []
            for i, jpeg in enumerate(screenshot_jpeg if isinstance(screenshot_jpeg, list) else [screenshot_jpeg]):
                if jpeg:
                    shots.append(folder / f"screenshot-{i}.jpg")
                    shots[-1].write_bytes(jpeg)
            if tools:
                agent, allowed, note = AGENT, TOOLS, tools_note(b.kb.root)
            elif shots:
                # the ⟳ sync: the screenshot only (opened with view_file). With the knowledge-base tools too, the
                # agent grepped the knowledge base for over two minutes and the sync gave up at 60 s
                agent, allowed, note = SHOT_AGENT, SHOT_TOOLS, SHOT_NOTE
            else:
                agent, allowed, note = QUICK_AGENT, [], ""
            # the agent file stays the same from question to question (written only when it changes): the
            # screenshot's per-run path goes with the question
            return self._exec(agent, b.system_prompt() + note, allowed, prompt + shots_line(shots),
                              resolve_model(model or b.model), on_raw_delta)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def summarize(self, instructions: str, text: str, timeout: int = 90) -> str | None:
        """One short call, no tools, on the lightest Flash: session summaries and guide summaries."""
        if not self.exe:
            return None
        r = self._exec(SUMMARY_AGENT, instructions, [], text, resolve_model(SAVER_ALIAS), answer=False,
                       timeout=timeout)
        return r.text.strip() or None
