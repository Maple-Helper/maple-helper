"""What every AI provider shares: process flags, finding the CLI, keyring storage, error codes."""
from __future__ import annotations

import glob
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


def scrub(text: str) -> str:
    """CLI output on its way into the log, which ships in "Report a problem": no account email, no one-time link."""
    return re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", re.sub(r"https?://\S+", "<link>", text))


# no console window flashing up on Windows; elsewhere creationflags must stay 0
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# API keys live in Windows Credential Manager / the macOS Keychain, never in plain files.
KEYRING_SERVICE = "MapleHelper"


@dataclass
class RawResult:
    """One CLI run: the raw answer (visible text + @@META@@ JSON), or an error code."""
    text: str = ""
    error: str | None = None
    cost_usd: float | None = None
    limits: dict | None = None     # plan usage, when the CLI reports it with the answer (Claude Code, see usage.py)
    model: str | None = None       # the model that answered, when the CLI says ("claude-sonnet-5")
    # how the answer was reached, where the CLI's stream tells (None: it doesn't): tools run (file reads, greps,
    # shell commands) and model turns. For the speed evals (tools/eval_answers.py); the app shows neither
    tool_calls: int | None = None
    turns: int | None = None


def note_tool_use(ev: dict, ids: set) -> bool:
    """A Claude-Code-format stream line's tool calls, into ids (Claude Code, Grok): the partial stream's
    content_block_start and the whole "assistant" message both carry each tool_use block, so they're counted
    once by id. True when the line carries content blocks at all (the stream tells about tools)."""
    blocks = []
    if ev.get("type") == "stream_event" and (ev.get("event") or {}).get("type") == "content_block_start":
        blocks = [(ev["event"].get("content_block") or {})]
    elif ev.get("type") == "assistant":
        blocks = ((ev.get("message") or {}).get("content")) or []
        blocks = blocks if isinstance(blocks, list) else []
    else:
        return False
    for b in blocks:
        if isinstance(b, dict) and b.get("type") in ("tool_use", "server_tool_use"):
            ids.add(b.get("id") or f"#{len(ids)}")
    return True


# Hedging: a run with no sign of life this long after its question was sent starts a second, identical run, and the
# first to answer wins. The server sometimes sits on a request for minutes (a question with no tool calls took 338 s
# once and 10 s when asked again), and a cap on time would cut off the slow answers that are working. A healthy run
# shows its first sign of life in 1-2 s, ~13 s when the server first caches a long prompt (measured, Sonnet 5).
HEDGE_AFTER_S = 25.0


def _kill(proc) -> None:
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass


class Attempt:
    """One run in a Race. The run reports each sign of life (activity) and its visible text (delta) here, and
    registers its processes (track) so the race can stop them."""

    def __init__(self, race: Race, n: int):
        self.race, self.n = race, n
        self.active = False            # a sign of life from the model: any streamed event, thinking included
        self.procs: list = []
        self.result: RawResult | None = None

    @property
    def lost(self) -> bool:
        """Another run won or the question was cancelled: this one is stopped, and its output goes nowhere."""
        return self in self.race.losers or self.race.cancelled

    def track(self, proc) -> bool:
        """A process of this run. False (and it is stopped right away) when the run is already out."""
        with self.race.cond:
            self.procs.append(proc)
            out = self.lost
        if out:
            _kill(proc)
        return not out

    def activity(self) -> None:
        with self.race.cond:
            if not self.active:
                self.active = True
                self.race.cond.notify_all()

    def delta(self, text: str) -> None:
        """Visible text so far. The first run to stream text wins the question (the other one is stopped), and only
        its text reaches the chat: two answers never interleave."""
        race = self.race
        stop = []
        with race.cond:
            self.active = True
            if race.owner is None and not self.lost:
                race.owner = self
                stop = race._lose_others(self)
                race.cond.notify_all()
            mine = race.owner is self and not race.cancelled
        for p in stop:
            _kill(p)
        if mine and race.on_delta:
            race.on_delta(text)


class Race:
    """Runs one question, hedged (see HEDGE_AFTER_S). run_one(attempt) does one full run and returns its RawResult;
    a second one starts at most once, only while the first has shown no sign of life and no text has streamed.
    The first success wins and the other run is stopped; a run that streams text wins at once."""

    def __init__(self, on_delta=None, label: str = "AI"):
        self.on_delta, self.label = on_delta, label
        self.cond = threading.Condition()
        self.attempts: list[Attempt] = []
        self.losers: set[Attempt] = set()
        self.owner: Attempt | None = None
        self.winner: Attempt | None = None
        self.cancelled = False

    def cancel(self) -> None:
        """Stop every run of the question."""
        with self.cond:
            self.cancelled = True
            procs = [p for a in self.attempts for p in a.procs]
            self.cond.notify_all()
        for p in procs:
            _kill(p)

    def _lose_others(self, keep: Attempt) -> list:
        """(under the lock) every other unfinished run is out; their processes, to stop outside the lock."""
        procs = []
        for a in self.attempts:
            if a is not keep and a.result is None and a not in self.losers:
                self.losers.add(a)
                procs += a.procs
        return procs

    def _start(self, run_one) -> None:
        a = Attempt(self, len(self.attempts))
        self.attempts.append(a)

        def go():
            try:
                r = run_one(a)
            except Exception:      # noqa: BLE001 - a run that breaks must never leave the question waiting
                log.exception("%s run %s failed", self.label, a.n)
                r = RawResult(error="no_result")
            self._finish(a, r)
        threading.Thread(target=go, daemon=True).start()

    def _finish(self, a: Attempt, r: RawResult) -> None:
        stop = []
        with self.cond:
            a.result = r
            if self.winner is None and not a.lost:
                others = [o for o in self.attempts if o is not a and o.result is None and o not in self.losers]
                # a failed run waits for the other one, still going, to answer instead
                if self.owner is a or r.error is None or not others:
                    self.winner = a
                    stop = self._lose_others(a)
                    if len(self.attempts) > 1:
                        log.info("%s: run %s answered first (%s)", self.label, a.n, r.error or "ok")
            self.cond.notify_all()
        for p in stop:
            _kill(p)

    def _outcome(self) -> RawResult | None:
        if self.winner is not None:
            r = self.winner.result
            if r.limits is None:          # the plan usage another run reported still counts
                r.limits = next((o.result.limits for o in self.attempts if o.result and o.result.limits), None)
            return r
        if self.cancelled and all(a.result is not None for a in self.attempts):
            return self.attempts[0].result
        return None

    def run(self, run_one, hedge_after: float | None = HEDGE_AFTER_S) -> RawResult:
        began = time.monotonic()
        with self.cond:
            self._start(run_one)
            while (r := self._outcome()) is None:
                first = self.attempts[0]
                if hedge_after is not None and len(self.attempts) == 1 and not self.cancelled and \
                        self.owner is None and not first.active and first.result is None:
                    wait = began + hedge_after - time.monotonic()
                    if wait <= 0:
                        log.info("%s: no sign of life %g s after the question, asking again in parallel",
                                 self.label, hedge_after)
                        self._start(run_one)
                        continue
                    self.cond.wait(wait)
                else:
                    self.cond.wait()
        return r


EXIT_GRACE_S = 3.0      # a CLI that has exited but whose output pipe stays open: read no longer than this
OUTLIVE_LOG_S = 5.0     # a CLI still running this long after its final event is logged, then stopped


class Lines:
    """A CLI's output lines, read on a thread of their own, so that reading never outlasts the answer:
      * the caller stops at its final event ("result") and calls finish(): the process is left to end in the
        background (stopped after OUTLIVE_LOG_S, with what it printed meanwhile in the log);
      * no meaningful output for stall_s (the caller says what counts, with touch()): the process is stopped and
        `stalled` is set; status or keep-alive chatter can't keep a dead run going;
      * the process has exited but its output stays open (a child process of its own holds the pipe: an answer
        once sat 501 s after it was complete): reading ends EXIT_GRACE_S later.
    Iterating yields each line, and None about every half second while nothing comes (for the caller's own
    checks)."""

    def __init__(self, proc, stall_s: float | None, label: str = "CLI", deadline_s: float | None = None):
        self.proc, self.stall_s, self.label = proc, stall_s, label
        self.q: queue.Queue = queue.Queue()
        self.began = self.last = time.monotonic()
        self.deadline = self.began + deadline_s if deadline_s else None
        self.stalled = self.orphaned = False
        self.done = False

        def pump():
            try:
                for line in proc.stdout:
                    self.q.put(line)
            except (OSError, ValueError):
                pass
            self.q.put(b"")              # end of output
        threading.Thread(target=pump, daemon=True).start()

    def touch(self) -> None:
        """A meaningful event: the stall clock starts again."""
        self.last = time.monotonic()

    def __iter__(self):
        exited = None
        while True:
            try:
                line = self.q.get(timeout=0.5)
            except queue.Empty:
                line = None
            now = time.monotonic()
            # (checked whatever comes: a stream of status lines must not hold a stalled run open)
            if (self.stall_s and now - self.last > self.stall_s) or (self.deadline and now > self.deadline):
                self.stalled = True
                _kill(self.proc)
                return
            if line is None:
                if self.proc.poll() is not None:
                    exited = exited or now
                    if now - exited > EXIT_GRACE_S:
                        self.orphaned = True
                        log.info("%s exited but its output stayed open: stopped reading", self.label)
                        return
                yield None
                continue
            if line == b"":
                self.done = True
                return
            yield line

    def finish(self) -> None:
        """The answer is complete: the process ends in the background. Still running OUTLIVE_LOG_S later, it is
        logged with the kinds of lines it printed meanwhile, and stopped."""
        if self.done:
            return
        proc, label, q = self.proc, self.label, self.q

        def reap():
            end = time.monotonic() + OUTLIVE_LOG_S
            kinds: list[str] = []
            while time.monotonic() < end:
                try:
                    line = q.get(timeout=max(0.05, end - time.monotonic()))
                except queue.Empty:
                    continue
                if line == b"":
                    break
                kinds.append(line_kind(line))
            if proc.poll() is None:
                log.info("%s still running %.0f s after its answer (printed since: %s): stopped", label,
                         OUTLIVE_LOG_S, ", ".join(kinds[-8:]) or "nothing")
                _kill(proc)
            elif kinds:
                log.info("%s printed after its answer: %s", label, ", ".join(kinds[-8:]))
        threading.Thread(target=reap, daemon=True).start()


def line_kind(line: bytes) -> str:
    """"stream_event/message_stop", "system/status": what an output line was, for the log (never its text)."""
    import json
    try:
        ev = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return "text"
    if not isinstance(ev, dict):
        return "json"
    inner = ev.get("event")
    t = str(ev.get("type") or (inner if isinstance(inner, str) else "?"))
    sub = ev.get("subtype") or (inner.get("type") if isinstance(inner, dict) else None)
    return f"{t}/{sub}" if sub else t


class StreamText:
    """The answer in a Claude-format stream (Claude Code, Grok): the text of the last message after its last tool
    call. Text before a tool call, in an earlier message or earlier in the same one, was a lead-in."""

    def __init__(self):
        self.text = ""

    def feed(self, se: dict) -> bool:
        """One stream event (the "event" of a stream_event line); True when the visible text grew."""
        t = se.get("type")
        if t == "message_start":
            self.text = ""
        elif t == "content_block_start" and (se.get("content_block") or {}).get("type") not in (None, "text"):
            self.text = ""         # a tool call (or thinking) after text: that text led in to it
        elif t == "content_block_delta" and (se.get("delta") or {}).get("type") == "text_delta":
            self.text += str(se["delta"].get("text", ""))
            return True
        return False


def model_name(model_id: str) -> str:
    """A readable name: "claude-sonnet-5-20260101" -> "Sonnet 5", "claude-opus-4-5" -> "Opus 4.5",
    "gpt-6.1-sol" -> "GPT-6.1-Sol"."""
    import re
    m = re.fullmatch(r"claude-([a-z]+)-(\d+(?:-\d+)?)(?:-\d{8})?(?:\[.*\])?", model_id or "")
    if m:
        return f"{m.group(1).capitalize()} {m.group(2).replace('-', '.')}"
    if (model_id or "").startswith("gpt-"):
        return "GPT-" + "-".join(w.capitalize() for w in model_id[4:].split("-"))
    if (model_id or "").startswith("gemini-"):      # "gemini-3.8-flash-lite" -> "Gemini 3.8 Flash Lite"
        return " ".join(w if w[:1].isdigit() else w.capitalize() for w in model_id.split("-"))
    return model_id or ""


# where Node version managers put npm's global installs (npm i -g @anthropic-ai/claude-code, @openai/codex) and
# node itself: an app opened from Finder gets launchd's PATH (/usr/bin:/bin:/usr/sbin:/sbin), none of these
NODE_DIRS = ["~/.volta/bin", "~/.bun/bin", "~/.nvm/versions/node/*/bin", "~/.local/state/fnm_multishells/*/bin",
             "~/Library/Application Support/fnm/node-versions/*/installation/bin"]


def _version_key(path: str) -> list:
    return [(0, int(x), "") if x.isdigit() else (1, 0, x) for x in re.split(r"(\d+)", path)]


def posix_dirs(dirs: list[str]) -> list[str]:
    """The install folders plus the Node managers' ones, globs expanded (the newest Node version first)."""
    out = []
    for d in dirs + NODE_DIRS:
        d = str(Path(d).expanduser())
        if "*" in d:
            out += sorted(glob.glob(d), key=_version_key, reverse=True)
        else:
            out.append(d)
    return out


def find_posix(name: str, dirs: list[str]) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    for d in posix_dirs(dirs):
        c = Path(d) / name
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def find_windows_exe(name: str, candidates: list[Path]) -> str | None:
    """A real .exe on PATH, else a known install spot."""
    p = shutil.which(name)
    if p and p.lower().endswith(".exe"):
        # which() takes the extension from PATHEXT (".EXE"). Claude Code started as claude.EXE hangs when it
        # re-runs itself as its built-in rg, so the path always ends in a lowercase ".exe"
        return p[:-4] + ".exe"
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def child_env(dirs: list[str], env: dict | None = None) -> dict:
    """Environment for a CLI: on macOS the install folders join PATH (an npm install needs node)."""
    env = dict(os.environ if env is None else env)
    if sys.platform != "win32":
        extra = posix_dirs(dirs)
        env["PATH"] = os.pathsep.join([env.get("PATH") or "/usr/bin:/bin"] + extra)
    return env


_login: subprocess.Popen | None = None


def open_login(exe: str, args: list[str], env: dict | None = None, cwd: str | None = None,
               keep_stdin: bool = False, on_line=None) -> subprocess.Popen | None:
    """The official sign-in, with no console window: the CLI opens the browser itself and waits for it there.
    Its output goes to the log (it says why, when a sign-in fails). None when it couldn't start.
    keep_stdin: the CLI waits for a code the player pastes (send_login_input). on_line(text): each output
    line, before it's logged (Gemini's sign-in link is opened from there)."""
    global _login
    stop_login()   # one left waiting still holds its local port (Codex: 1455), so a new one would fail
    try:
        p = subprocess.Popen([exe, *args], stdin=subprocess.PIPE if keep_stdin else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=cwd,
                             creationflags=CREATE_NO_WINDOW)
    except OSError:
        log.warning("sign-in can't start: %s", exe, exc_info=True)
        return None

    def drain():
        for line in p.stdout:
            text = line.decode("utf-8", errors="replace").strip()
            if text and on_line:
                try:
                    on_line(text)
                except Exception:      # noqa: BLE001 - the sign-in goes on; the log says why it didn't open
                    log.warning("sign-in line handler failed", exc_info=True)
            if text:   # the one-time sign-in links stay out of the log
                log.info("sign-in: %s", scrub(text))   # no email in reports
        if p.wait():
            log.warning("sign-in ended with code %s", p.returncode)
    threading.Thread(target=drain, daemon=True).start()
    _login = p
    return p


def send_login_input(text: str) -> bool:
    """Type into the sign-in that's waiting (a code pasted from the browser). False when none is waiting.
    On Windows it's typed into the CLI's hidden console: Antigravity reads the code from there, not from stdin
    (a code written to its stdin was never seen, and the sign-in timed out)."""
    p = _login
    if not p or p.poll() is not None:
        return False
    if sys.platform == "win32":
        return type_into_console(p.pid, text.replace("\r\n", "\n").replace("\n", "\r"))
    if not p.stdin:
        return False
    try:
        p.stdin.write(text.encode("utf-8"))
        p.stdin.flush()
        return True
    except OSError:
        return False


_console_lock = threading.Lock()


def type_into_console(pid: int, text: str) -> bool:
    """Windows: key presses into another process's console (one made with CREATE_NO_WINDOW has a hidden one),
    as if the player typed them there. "\\r" is Enter."""
    import ctypes
    from ctypes import wintypes

    class KEY_EVENT_RECORD(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                    ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                    ("uChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]

    class EVENT(ctypes.Union):
        _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("pad", ctypes.c_byte * 16)]

    class INPUT_RECORD(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", EVENT)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    records = []
    for ch in text:
        for down in (True, False):
            r = INPUT_RECORD(EventType=1)              # KEY_EVENT
            r.Event.KeyEvent.bKeyDown = down
            r.Event.KeyEvent.wRepeatCount = 1
            r.Event.KeyEvent.uChar = ch
            r.Event.KeyEvent.wVirtualKeyCode = 0x0D if ch == "\r" else 0
            records.append(r)
    with _console_lock:                                # attaching is process-wide: one at a time
        had_console = bool(k32.GetConsoleWindow())     # a run from a terminal (development) has its own
        k32.FreeConsole()
        try:
            if not k32.AttachConsole(pid):
                log.warning("can't reach the sign-in's console: %s", ctypes.get_last_error())
                return False
            handle = k32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)   # read|write, shared, existing
            if not handle or handle == wintypes.HANDLE(-1).value:
                return False
            try:
                arr = (INPUT_RECORD * len(records))(*records)
                written = wintypes.DWORD()
                return bool(k32.WriteConsoleInputW(handle, arr, len(records), ctypes.byref(written)))
            finally:
                k32.CloseHandle(handle)
        finally:
            k32.FreeConsole()
            if had_console:
                k32.AttachConsole(-1)                  # ATTACH_PARENT_PROCESS: back to the terminal it ran in


def login_waiting() -> bool:
    """A sign-in started and still waiting for the browser (a second click must not start another)."""
    return _login is not None and _login.poll() is None


def stop_login() -> None:
    """End a sign-in that's still waiting for the browser (the player gave up or closed the window)."""
    global _login
    if _login and _login.poll() is None:
        _login.kill()
    _login = None


def login_failed(p: subprocess.Popen | None) -> bool:
    """The sign-in process ended without success (a successful one exits 0)."""
    return p is not None and p.poll() is not None and p.returncode != 0


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def installer_command(win_cmd: str, mac_cmd: str, platform: str = sys.platform) -> list[str]:
    """The official installer, run with no window. On Windows: UTF-8 output (not the console's code page), no
    progress bars or prompts (-NonInteractive: a question fails instead of waiting forever), and a failure,
    whether the script throws or a program in it fails, ends with a non-zero exit code."""
    if platform == "darwin":
        return ["/bin/bash", "-c", f"set -o pipefail; {mac_cmd}"]
    # errors come out as one plain line each ("ERROR: <message>"), not PowerShell's multi-line error records;
    # the installer may leave strict mode on, so the exit code is read without touching an unset variable
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; "
              f"& {{ try {{ {win_cmd} }} catch {{ 'ERROR: ' + $_.Exception.Message; exit 1 }} }} 2>&1 | "
              "ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) "
              "{ 'ERROR: ' + $_.Exception.Message } else { \"$_\" } }; "
              "$c = Get-Variable LASTEXITCODE -ValueOnly -ErrorAction SilentlyContinue; if ($c) { exit $c }; exit 0")
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]


class Installer:
    """An official installer running in the background: the app shows its progress and, when it fails, why
    (no console window). done is set when it ends; code is its exit code (0 = worked)."""

    def __init__(self, win_cmd: str, mac_cmd: str, env: dict | None = None):
        self.lines: list[str] = []
        self.code: int | None = None
        self.done = threading.Event()
        try:
            self.proc = subprocess.Popen(installer_command(win_cmd, mac_cmd), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                         creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            log.warning("installer can't start", exc_info=True)
            self.proc, self.lines, self.code = None, [str(e)], -1
            self.done.set()
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            text = ANSI.sub("", raw.decode("utf-8", errors="replace")).strip()
            if text:
                self.lines.append(text)
                log.info("installer: %s", scrub(text))
        self.code = self.proc.wait()
        log.info("installer ended with code %s", self.code)
        self.done.set()

    def status(self) -> str:
        """The installer's latest line ("Downloading Claude Code..."), for the progress line."""
        return self.lines[-1][:110] if self.lines else ""

    def error(self, n: int = 4) -> str:
        """What the installer said last: the reason, when it failed."""
        return "\n".join(line[:160] for line in self.lines[-n:])

    def cancel(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


def run_installer(win_cmd: str, mac_cmd: str, env: dict | None = None) -> Installer:
    return Installer(win_cmd, mac_cmd, env)


def http_ok(url: str, headers: dict) -> bool:
    import http.client
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
            return r.status == 200
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
        return False


# A sign-in that ran out, in each CLI's own words: Codex "Your access token could not be refreshed ... Please log
# out and sign in again." (and "...Please sign in again."), Grok "Your auth token is invalid or expired. Run
# `grok login` to re-authenticate." Without them the player got "Something went wrong. Try again." forever.
SIGNED_OUT = ("not logged in", "please run /login", "invalid api key", "invalid_api_key", "401 unauthorized",
              "authentication", "sign in again", "log out and sign in", "access token could not be refreshed",
              "re-authenticate", "token is invalid or expired")
# No connection: Node's words (Claude Code) and the Go (Antigravity: "dial tcp: lookup ...: no such host",
# "proxyconnect tcp", "connectex") and Rust (Codex: "Connection failed: error sending request ... dns error")
# ones. Checked after sign-in and limits: those messages can carry a URL or a "request" too.
OFFLINE = ("enotfound", "econnrefused", "network", "fetch failed", "no such host", "dial tcp", "connectex",
           "proxyconnect", "dns error", "error sending request", "connection failed", "getaddrinfo",
           "workspace routing discovery failed")


def classify_error(text: str) -> str | None:
    t = text.lower()
    if "credit balance" in t or "insufficient_quota" in t or "billing" in t:
        return "no_credit"          # an API key with no money on it (its check passed: the key itself is valid)
    if any(s in t for s in SIGNED_OUT):
        return "not_logged_in"
    if "usage limit" in t or "rate limit" in t or "limit reached" in t or "resets" in t:
        return "usage_limit"
    if any(s in t for s in OFFLINE):
        return "offline"
    return None


class Provider:
    """One AI CLI the player signs in to. Subclasses fill in the specifics."""
    name = ""
    label = ""
    keyring_user = ""
    model_setting = ""       # settings key holding this provider's model (None = the CLI's default)
    saver_model = None       # lighter model for saver mode; None = keep the model, answers just get shorter
    reports_usage = False    # the CLI reports the player's plan usage (drives the usage meter)
    login_code = False       # the sign-in ends with a code the player pastes back (submit_login_code)

    def submit_login_code(self, code: str) -> bool:
        return False

    def find_exe(self) -> str | None:
        raise NotImplementedError

    def models(self) -> list[tuple[str | None, str]]:
        """The models a player can pick: [(value for the CLI, name to show)]; None = the CLI's default."""
        return []

    def default_model(self) -> str | None:
        """The name of the model the CLI uses when the player picked none, where the CLI says (it may run a CLI:
        call it off the UI thread). None: unknown until an answer reports its model."""
        return None

    def read_limits(self) -> dict | None:
        """The plan usage read on demand (usage.parse shape); None when the CLI only reports it with answers."""
        return None

    def account(self) -> dict:
        """{'status': 'not_installed' | 'logged_out' | 'offline' | 'ok', 'email': str | None, ...}
        offline: the CLI couldn't reach its service, so whether it is signed in is unknown (Gemini)."""
        raise NotImplementedError

    def status(self) -> str:
        return self.account()["status"]

    def logout(self) -> bool:
        raise NotImplementedError

    def login(self) -> subprocess.Popen | None:
        raise NotImplementedError

    def install(self) -> Installer:
        raise NotImplementedError

    def test_api_key(self, key: str) -> bool:
        raise NotImplementedError

    def backend(self, brain):
        """The object that runs questions for `brain` (see claude.ClaudeBackend)."""
        raise NotImplementedError

    # keys ------------------------------------------------------------------
    def save_api_key(self, key: str) -> None:
        import keyring
        keyring.set_password(KEYRING_SERVICE, self.keyring_user, key)

    def delete_api_key(self) -> None:
        try:
            import keyring
            keyring.delete_password(KEYRING_SERVICE, self.keyring_user)
        except Exception:
            pass

    def load_api_key(self) -> str | None:
        try:
            import keyring
            return keyring.get_password(KEYRING_SERVICE, self.keyring_user)
        except Exception:
            return None
