"""Claude through the player's own Claude Code install (their Claude account, or an Anthropic API key).

Each question runs `claude -p` in a locked-down mode: no shell, no web, no MCP
servers, read-only file tools confined to the knowledge-base folder. The
screenshot travels inside the message itself (stream-json input), so Claude sees
it without an extra tool round-trip, and the answer streams back token by token.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import usage
from .base import CREATE_NO_WINDOW, HEDGE_AFTER_S, Attempt, Installer, Lines, Provider, Race, RawResult, StreamText, \
    classify_error, cli_outdated, child_env, find_posix, find_windows_exe, http_ok, note_tool_use, open_login, run_installer, scrub

log = logging.getLogger(__name__)

INSTALL_CMD = "irm https://claude.ai/install.ps1 | iex"
INSTALL_CMD_MAC = "curl -fsSL https://claude.ai/install.sh | bash"
# An app opened from Finder gets PATH=/usr/bin:/bin:/usr/sbin:/sbin, so the usual install spots are listed here.
STALL_TIMEOUT_S = 150   # no output from the CLI for this long = stuck (tools and streaming print all along)
WARM_MAX_AGE_S = 15 * 60    # a warm process waiting longer is replaced by a fresh one
RESULT_GRACE_S = 5.0        # the answer's turn ended (with its META block) and no "result" line came: done anyway
META_MARK = "@@META@@"
POSIX_DIRS = ["~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin"]
# flags ClaudeBackend passes that an old Claude Code rejects ("error: unknown option '--restricted'", issue #107)
NEEDED_FLAGS = ("--restricted", "--strict-mcp-config", "--tools", "--no-session-persistence",
                "--include-partial-messages")


def find_claude() -> str | None:
    """Locate the Claude Code executable (native install, npm or Homebrew)."""
    if sys.platform != "win32":
        return find_posix("claude", POSIX_DIRS)
    exe = find_windows_exe("claude", [
        Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude.exe",
        Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
    ])
    if exe:
        return exe
    # an npm install elsewhere (a custom prefix): PATH has its claude.cmd shim, the real .exe sits beside it.
    # Never the .cmd itself: cmd.exe cuts the multi-line --system-prompt at its first newline
    import shutil
    shim = shutil.which("claude")
    if shim:
        real = Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if real.exists():
            return str(real)
    return None


# credentials, providers and endpoints in the player's environment that would override the account (or the stored
# key) Maple Helper chose: Claude Code takes them over the sign-in (a gateway or Foundry of a developer's own
# routed the answers there, or made them fail). Not CLAUDE_CONFIG_DIR: it locates the player's own sign-in
FOREIGN_AUTH = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
                "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL")


def env(api_key: str | None = None) -> dict:
    """The CLI's environment: the player's account sign-in, or the stored API key, never a stray credential."""
    e = child_env(POSIX_DIRS)
    for k in FOREIGN_AUTH:
        e.pop(k, None)
    if api_key:
        e["ANTHROPIC_API_KEY"] = api_key
    return e


class Claude(Provider):
    name = "claude"
    label = "Claude"
    tool = "Claude Code"
    keyring_user = "anthropic_api_key"
    model_setting = "model"
    saver_model = usage.SAVER_MODEL
    reports_usage = True

    def find_exe(self) -> str | None:
        return find_claude()

    def models(self) -> list[tuple[str | None, str]]:
        # Claude Code's aliases always point at the newest model of each family
        return [("sonnet", "Sonnet"), ("opus", "Opus"), ("haiku", "Haiku")]

    def account(self) -> dict:
        exe = find_claude()
        if not exe:
            return {"status": "not_installed", "email": None}
        if cli_outdated(self.name, exe, ("--help",), NEEDED_FLAGS, env()):
            return {"status": "outdated", "email": None}       # signed in or not, no answer can work: update first
        try:
            r = subprocess.run([exe, "auth", "status"], capture_output=True, timeout=20, env=env(),
                               creationflags=CREATE_NO_WINDOW)
            data = json.loads(r.stdout.decode("utf-8", errors="replace") or "{}")
        except OSError:
            # found but Windows won't start it: offer the installer, not a sign-in that can't open
            return {"status": "not_installed", "email": None}
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return {"status": "logged_out", "email": None}
        if not data.get("loggedIn"):
            return {"status": "logged_out", "email": None}
        return {"status": "ok", "email": data.get("email")}

    def logout(self) -> bool:
        """Sign Claude Code out of the current account (the next sign-in can pick another one)."""
        exe = find_claude()
        if not exe:
            return False
        try:
            r = subprocess.run([exe, "auth", "logout"], capture_output=True, timeout=30, env=env(),
                               creationflags=CREATE_NO_WINDOW)
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def login(self) -> subprocess.Popen | None:
        """Official sign-in flow: opens the browser, no window of its own."""
        exe = find_claude()
        return open_login(exe, ["auth", "login"], env()) if exe else None

    def install(self) -> Installer:
        return run_installer(INSTALL_CMD, INSTALL_CMD_MAC)

    def test_api_key(self, key: str) -> bool:
        return http_ok("https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"})

    def backend(self, brain):
        return ClaudeBackend(brain)


def _alive(ev: dict) -> bool:
    """A sign of life from the model: a streamed event (thinking too: "thinking_tokens" while it thinks), a whole
    message, a tool result or the plan usage. Claude Code's own "init" and "status: requesting" lines come the moment
    the question arrives, before the server has said anything, so they don't count."""
    t = ev.get("type")
    return t in ("stream_event", "assistant", "user", "rate_limit_event") or \
        (t == "system" and ev.get("subtype") == "thinking_tokens")


class ClaudeBackend:
    """Runs questions for a Brain through Claude Code, keeping the next process warm."""

    def __init__(self, brain):
        self.brain = brain
        self.exe = find_claude()
        self._race: Race | None = None          # the question running now: cancel() stops all of its runs
        self._warm: subprocess.Popen | None = None
        self._warm_config: tuple | None = None
        self._warm_born = 0.0                    # when the warm process started (time.monotonic)
        self._warm_lock = threading.Lock()

    # ------------------------------------------------------------ warm process
    # Claude Code needs ~3s to start. A process started ahead of time sits waiting for its first
    # stdin message, so a question skips that startup entirely. One fresh process per question
    # keeps every answer's context clean. One that waited longer than WARM_MAX_AGE_S is replaced:
    # a question once sat ~100 s in a process started long before it (see _take_warm).

    def _config(self) -> tuple:
        b = self.brain
        # the instructions too: they name the model only after the first answer, and carry the official facts
        return (self.exe, b.model, b.length, b.api_key, str(b.kb.root), hash(b.system_prompt()))

    def _spawn(self, model: str | None = None, tools: bool = True) -> subprocess.Popen:
        """model / tools: a one-off call's own (the ⟳ sync: Haiku, no file tools); the warm process uses the
        player's model with the knowledge-base tools."""
        b = self.brain
        cmd = [self.exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--restricted", "--strict-mcp-config",
               "--tools", "Read,Grep,Glob" if tools else "",
               "--model", model or b.model or "sonnet", "--no-session-persistence", "--system-prompt", b.system_prompt()]
        return subprocess.Popen(cmd, cwd=str(b.kb.root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env(b.api_key), creationflags=CREATE_NO_WINDOW)

    def prewarm(self) -> None:
        """Start the next question's process now (no-op if a fresh one is ready)."""
        if not self.exe:
            return
        with self._warm_lock:
            if self._warm and self._warm.poll() is None and self._warm_config == self._config() and \
                    time.monotonic() - self._warm_born < WARM_MAX_AGE_S:
                return
            self._discard_warm()
            cfg = self._config()          # before spawning: settings can change while it starts
            try:
                self._warm, self._warm_config, self._warm_born = self._spawn(), cfg, time.monotonic()
            except OSError:
                self._warm = None
                return
            # replaced when it gets old, so the next question still finds a fresh one waiting
            timer = threading.Timer(WARM_MAX_AGE_S + 1, self._refresh, args=(self._warm,))
            timer.daemon = True
            timer.start()

    def _refresh(self, proc: subprocess.Popen) -> None:
        if self._warm is proc and proc.poll() is None:
            wants = getattr(self.brain, "wants_warm", None)
            if wants is not None and not wants():
                # the chat closed, or no question, for long: let it go; the next opening, typing or question warms one
                # again (audit PRF-1)
                log.info("chat not in use for a while: the warm Claude Code process is stopped, not renewed")
                self.drop_warm()
                return
            log.info("warm Claude Code process is %d min old: starting a fresh one", WARM_MAX_AGE_S // 60)
            self.prewarm()

    def _take_warm(self) -> tuple[subprocess.Popen | None, float]:
        """The waiting process and its age in seconds, when it fits this question. One older than WARM_MAX_AGE_S
        is not used: a question once waited ~100 s for a process started long before it to take it."""
        with self._warm_lock:
            p, cfg, born = self._warm, self._warm_config, self._warm_born
            self._warm = None
        age = time.monotonic() - born
        if p and p.poll() is None and cfg == self._config() and age < WARM_MAX_AGE_S:
            return p, age
        if p and p.poll() is None:
            p.kill()
        return None, 0.0

    def _discard_warm(self) -> None:
        if self._warm and self._warm.poll() is None:
            self._warm.kill()
            try:
                self._warm.wait(timeout=5)      # gone for real: on Windows it holds the KB folder until it exits
            except subprocess.TimeoutExpired:
                pass
        self._warm = None

    def drop_warm(self) -> None:
        """Stop only the process waiting for the next question (an answer in progress goes on)."""
        with self._warm_lock:
            self._discard_warm()

    def shutdown(self) -> None:
        with self._warm_lock:
            self._discard_warm()
        self.cancel()

    def cancel(self) -> None:
        if self._race:
            self._race.cancel()       # both runs of a hedged question; a question being written isn't sent again

    @staticmethod
    def _send(proc: subprocess.Popen, data: bytes) -> bool:
        """Write the question (a screenshot makes it far bigger than the pipe). A fresh process reads it only
        once it has started: a CLI stuck before that is stopped after STALL_TIMEOUT_S, and the write fails."""
        def stall():
            if proc.poll() is None:
                log.warning("Claude Code didn't take the question for %ss, stopped", STALL_TIMEOUT_S)
                proc.kill()
        timer = threading.Timer(STALL_TIMEOUT_S, stall)
        timer.daemon = True
        timer.start()
        try:
            proc.stdin.write(data)
            proc.stdin.close()
            return True
        except OSError:
            return False
        finally:
            timer.cancel()

    @staticmethod
    def _died(proc: subprocess.Popen) -> RawResult:
        """A CLI that ended before it took the question, twice: its stderr says why. A Claude Code too old for a
        flag passed here ("error: unknown option '--restricted'") failed every answer with "Something went wrong"
        and nothing in the log (issue #107)."""
        try:
            proc.wait(timeout=5)
            stderr = proc.stderr.read().decode("utf-8", errors="replace")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            stderr = ""
        log.warning("Claude Code ended before taking the question (exit %s): %s", proc.poll(), scrub(stderr[-1500:]))
        return RawResult(error=classify_error(stderr) or "no_result")

    def run(self, prompt: str, screenshot_jpeg: bytes | None, on_raw_delta=None, model: str | None = None,
            tools: bool = True) -> RawResult:
        """model / tools: see _spawn. The warm process serves only a call with the player's own setup."""
        content = []
        # one screenshot, or the screenshot and its full-resolution detail tiles
        for jpeg in (screenshot_jpeg if isinstance(screenshot_jpeg, list) else [screenshot_jpeg]):
            if jpeg:
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                             "data": base64.b64encode(jpeg).decode()}})
        content.append({"type": "text", "text": prompt})
        msg = {"type": "user", "message": {"role": "user", "content": content}}

        own = (model in (None, self.brain.model)) and tools        # the player's setup: the warm process fits
        data = (json.dumps(msg) + "\n").encode("utf-8")
        # hedged: with no sign of life from the server for HEDGE_AFTER_S, the same question goes out a second
        # time (on the warm process when it's ready) and the first answer wins
        race = self._race = Race(on_raw_delta, "Claude")
        return race.run(lambda a: self._attempt(a, data, own, model, tools), HEDGE_AFTER_S)

    def _attempt(self, a: Attempt, data: bytes, own: bool, model: str | None, tools: bool) -> RawResult:
        """One run of the question: a process (the warm one when it fits), the question written to it, and its
        stream read to the end. Progress and text go through `a`; the timings go to the log."""
        began = time.monotonic()
        proc, age = self._take_warm() if own else (None, 0.0)
        try:
            proc = proc or self._spawn(model, tools)
        except OSError as e:
            log.error("could not start Claude Code: %s", e)
            return RawResult(error=f"launch_failed: {e}")
        how = f"warm process ({age:.0f} s old)" if age else "new process"
        if not a.track(proc):
            return RawResult(error="no_result")
        if not self._send(proc, data):
            if a.lost:
                return RawResult(error="no_result")      # stopped (sync timeout, quit, the other run won): not re-sent
            # the warm process had died meanwhile: start fresh once
            try:
                proc = self._spawn(model, tools)
            except OSError as e:
                return RawResult(error=f"launch_failed: {e}")
            how = "new process (the warm one had ended)"
            if not a.track(proc):
                return RawResult(error="no_result")
            if not self._send(proc, data):
                return RawResult(error="no_result") if a.lost else self._died(proc)
        sent = time.monotonic()
        # get the next one ready while the player reads this answer
        if own:
            threading.Thread(target=self.prewarm, daemon=True).start()

        text = StreamText()     # the answer: the last message's text after its last tool call
        result = None
        limits = None
        used = None
        first_event = first_life = None
        calls: set = set()      # tool calls (evals): the ids of the tool_use blocks
        blocks = False
        # stderr is drained alongside: a CLI that writes a lot there would otherwise block both sides
        err_chunks: list[bytes] = []
        err_reader = threading.Thread(target=lambda: err_chunks.extend(iter(lambda: proc.stderr.read(4096), b"")),
                                      daemon=True)
        err_reader.start()
        # read until the "result" line, never until the process exits: an answer once stayed "answering" 501 s after
        # it was complete. A run with no sign of life for STALL_TIMEOUT_S (network retries, a hung login; status
        # lines don't count) is stopped: the chat must not stay on "thinking" forever
        lines = Lines(proc, STALL_TIMEOUT_S, f"Claude Code (run {a.n})")
        ended = None            # when the last message ended its turn (end_turn): the answer if "result" never comes
        for line in lines:
            now = time.monotonic()
            if ended and now - ended > RESULT_GRACE_S and META_MARK in text.text:
                log.info("Claude Code sent no result %.0f s after its answer ended: taken as is", RESULT_GRACE_S)
                break
            if line is None:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            first_event = first_event or now
            if _alive(ev):
                first_life = first_life or now
                lines.touch()
                a.activity()
            t = ev.get("type")
            n_calls = len(calls)
            blocks = note_tool_use(ev, calls) or blocks
            if len(calls) > n_calls:
                a.stage("tools")
            if t == "stream_event":
                se = ev.get("event") or {}
                kind = se.get("type")
                if kind == "message_start":
                    used = (se.get("message") or {}).get("model") or used
                    ended = None
                elif kind == "message_delta":
                    stop_reason = (se.get("delta") or {}).get("stop_reason")
                    ended = now if stop_reason == "end_turn" else None
                if text.feed(se):
                    a.delta(text.text)
            elif t == "result":
                result = ev
                break                   # the answer is complete: the process ends in the background
            elif t == "system" and ev.get("subtype") == "init":
                used = ev.get("model") or used
            elif t == "rate_limit_event":
                limits = usage.parse(ev.get("rate_limit_info"))
        lines.finish()
        if not result or result.get("is_error"):
            err_reader.join(timeout=2)        # its words say what went wrong (an answer doesn't wait for them)
        stalled = lines.stalled

        def since(t):
            return f"{t - sent:.1f} s" if t else "none"
        # for "why was it slow?" from the log: startup (a warm process takes the question at once), the server's
        # first sign of life, and the whole answer
        log.info("Claude run %s: %s, question sent in %.1f s, first event %s, first sign of life %s, ended after "
                 "%.1f s%s", a.n, how, sent - began, since(first_event), since(first_life), time.monotonic() - sent,
                 " (stopped: the other run answered)" if a.lost else "")
        stderr = b"".join(err_chunks).decode("utf-8", errors="replace")
        if not result and ended and META_MARK in text.text and not stalled:
            result = {"result": text.text}      # the answer ended its turn; Claude Code never said "result"
        if stalled:
            log.warning("Claude Code stalled for %ss, stopped: %s", STALL_TIMEOUT_S, scrub(stderr[-1000:]))
            return RawResult(error="timeout", limits=limits)
        if not result:
            if not limits and not a.lost:
                log.warning("no result from Claude Code (exit %s): %s", proc.poll(), scrub(stderr[-1500:]))
            return RawResult(error=classify_error(stderr) or "no_result", limits=limits)
        if result.get("is_error"):
            log.warning("Claude Code error: %s | %s", scrub(str(result.get("result", ""))[:500]), scrub(stderr[-1000:]))
            # with the plan usage: hitting the limit is exactly when the meter and its warning matter
            return RawResult(error=classify_error(str(result.get("result", "")) + stderr) or "api_error",
                             limits=limits)
        # the streamed final message first: the result's own text has carried a lead-in before
        turns = result.get("num_turns")
        return RawResult(text=text.text or result.get("result") or "", cost_usd=result.get("total_cost_usd"),
                         limits=limits, model=used, tool_calls=len(calls) if blocks else None,
                         turns=turns if isinstance(turns, int) else None)

    def summarize(self, instructions: str, text: str, timeout: int = 90) -> str | None:
        """One short call on Haiku, no tools: session summaries and guide summaries."""
        if not self.exe:
            return None
        cmd = [self.exe, "-p", "--restricted", "--strict-mcp-config", "--tools", "", "--model", "haiku",
               "--no-session-persistence", "--system-prompt", instructions]
        e = env(self.brain.api_key)            # the same account choice as the answers (see _spawn)
        try:
            r = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=timeout,
                               env=e, creationflags=CREATE_NO_WINDOW)
            out = r.stdout.decode("utf-8", errors="replace").strip()
            return out if r.returncode == 0 and out else None      # an error message is no summary
        except (OSError, subprocess.TimeoutExpired):
            return None
