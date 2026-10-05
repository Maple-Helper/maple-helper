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
from .base import CREATE_NO_WINDOW, Installer, Provider, RawResult, classify_error, child_env, find_posix, \
    find_windows_exe, http_ok, open_login, run_installer

log = logging.getLogger(__name__)

INSTALL_CMD = "irm https://claude.ai/install.ps1 | iex"
INSTALL_CMD_MAC = "curl -fsSL https://claude.ai/install.sh | bash"
# An app opened from Finder gets PATH=/usr/bin:/bin:/usr/sbin:/sbin, so the usual install spots are listed here.
STALL_TIMEOUT_S = 150   # no output from the CLI for this long = stuck (tools and streaming print all along)
POSIX_DIRS = ["~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin"]


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


# credentials in the player's environment that would override the account (or the stored key) Maple Helper
# chose: Claude Code takes them over the sign-in
FOREIGN_AUTH = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_OAUTH_TOKEN")


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


class ClaudeBackend:
    """Runs questions for a Brain through Claude Code, keeping the next process warm."""

    _cancels = 0      # cancel() calls so far: a question being written when one lands isn't re-sent

    def __init__(self, brain):
        self.brain = brain
        self.exe = find_claude()
        self._proc: subprocess.Popen | None = None
        self._warm: subprocess.Popen | None = None
        self._warm_config: tuple | None = None
        self._warm_lock = threading.Lock()

    # ------------------------------------------------------------ warm process
    # Claude Code needs ~3s to start. A process started ahead of time sits waiting for its first
    # stdin message, so a question skips that startup entirely. One fresh process per question
    # keeps every answer's context clean.

    def _config(self) -> tuple:
        b = self.brain
        return (self.exe, b.model, b.length, b.api_key, str(b.kb.root))

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
        """Start the next question's process now (no-op if one is ready)."""
        if not self.exe:
            return
        with self._warm_lock:
            if self._warm and self._warm.poll() is None and self._warm_config == self._config():
                return
            self._discard_warm()
            cfg = self._config()          # before spawning: settings can change while it starts
            try:
                self._warm, self._warm_config = self._spawn(), cfg
            except OSError:
                self._warm = None

    def _take_warm(self) -> subprocess.Popen | None:
        with self._warm_lock:
            p, cfg = self._warm, self._warm_config
            self._warm = None
        if p and p.poll() is None and cfg == self._config():
            return p
        if p and p.poll() is None:
            p.kill()
        return None

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
        self._cancels += 1            # a question being written when this lands is not sent again
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

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
        cancels = self._cancels
        data = (json.dumps(msg) + "\n").encode("utf-8")
        try:
            self._proc = (self._take_warm() if own else None) or self._spawn(model, tools)
        except OSError as e:
            log.error("could not start Claude Code: %s", e)
            return RawResult(error=f"launch_failed: {e}")
        if not self._send(self._proc, data):
            if self._cancels != cancels:
                return RawResult(error="no_result")      # stopped (sync timeout, quit): not sent again
            # the warm process had died meanwhile: start fresh once
            try:
                self._proc = self._spawn(model, tools)
            except OSError as e:
                return RawResult(error=f"launch_failed: {e}")
            if not self._send(self._proc, data):
                return RawResult(error="no_result")
        # get the next one ready while the player reads this answer
        if own:
            threading.Thread(target=self.prewarm, daemon=True).start()

        current = ""       # text of the assistant message being streamed
        result = None
        limits = None
        model = None
        proc = self._proc
        # stderr is drained alongside: a CLI that writes a lot there would otherwise block both sides
        err_chunks: list[bytes] = []
        err_reader = threading.Thread(target=lambda: err_chunks.extend(iter(lambda: proc.stderr.read(4096), b"")),
                                      daemon=True)
        err_reader.start()
        # a stalled CLI (network retries, a hung login) must not leave the chat on "thinking" forever
        last = [time.monotonic()]
        stalled = threading.Event()

        def watchdog():
            while proc.poll() is None:
                if time.monotonic() - last[0] > STALL_TIMEOUT_S:
                    stalled.set()
                    proc.kill()
                    return
                time.sleep(2)
        threading.Thread(target=watchdog, daemon=True).start()
        for line in proc.stdout:
            last[0] = time.monotonic()
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "stream_event":
                se = ev.get("event", {})
                if se.get("type") == "message_start":
                    current = ""
                    model = (se.get("message") or {}).get("model") or model
                elif se.get("type") == "content_block_delta" and se.get("delta", {}).get("type") == "text_delta":
                    current += se["delta"]["text"]
                    if on_raw_delta:
                        on_raw_delta(current)
            elif t == "result":
                result = ev
            elif t == "system" and ev.get("subtype") == "init":
                model = ev.get("model") or model
            elif t == "rate_limit_event":
                limits = usage.parse(ev.get("rate_limit_info"))
        proc.wait()
        err_reader.join(timeout=2)
        stderr = b"".join(err_chunks).decode("utf-8", errors="replace")
        if stalled.is_set():
            log.warning("Claude Code stalled for %ss, stopped: %s", STALL_TIMEOUT_S, stderr[-1000:])
            return RawResult(error="timeout", limits=limits)
        if not result:
            if not limits:
                log.warning("no result from Claude Code (exit %s): %s", proc.returncode, stderr[-1500:])
            return RawResult(error=classify_error(stderr) or "no_result", limits=limits)
        if result.get("is_error"):
            log.warning("Claude Code error: %s | %s", str(result.get("result", ""))[:500], stderr[-1000:])
            # with the plan usage: hitting the limit is exactly when the meter and its warning matter
            return RawResult(error=classify_error(str(result.get("result", "")) + stderr) or "api_error",
                             limits=limits)
        return RawResult(text=result.get("result") or current, cost_usd=result.get("total_cost_usd"), limits=limits,
                         model=model)

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
