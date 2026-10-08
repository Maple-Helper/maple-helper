"""AI providers (Claude Code, Codex CLI): commands, output parsing, discovery and keys (no real CLI calls)."""
import io
import json
import logging
import threading
import time
import tomllib
from pathlib import Path

import pytest

from maplehelper import providers
from maplehelper.providers import base, claude, codex


class TestRegistry:
    def test_known_providers(self):
        assert providers.get("claude").name == "claude"
        assert providers.get("codex").name == "codex"
        assert providers.get("codex").label == "ChatGPT"

    def test_unknown_or_missing_falls_back_to_claude(self):
        assert providers.get(None).name == "claude"
        assert providers.get("mistral").name == "claude"


@pytest.mark.parametrize("text,kind", [
    ("Error: Not logged in · Please run /login", "not_logged_in"),
    ("Invalid API key", "not_logged_in"),
    ("unexpected status 401 Unauthorized: auth error code: invalid_api_key", "not_logged_in"),
    ("Claude usage limit reached. Your limit resets at 5pm", "usage_limit"),
    ("You've hit your usage limit. Upgrade to Pro or try again later.", "usage_limit"),
    ("getaddrinfo ENOTFOUND api.anthropic.com", "offline"),
    ("something unexpected", None),
    # a sign-in that ran out, word for word from codex.exe and grok.exe
    ("Your access token could not be refreshed because your refresh token has expired. Please log out and sign in "
     "again.", "not_logged_in"),
    ("Your access token could not be refreshed because your refresh token was already used. Please log out and sign "
     "in again.", "not_logged_in"),
    ("Your access token could not be refreshed because your refresh token was revoked. Please log out and sign in "
     "again.", "not_logged_in"),
    ("Your access token could not be refreshed because you have since logged out or signed in to another account. "
     "Please sign in again.", "not_logged_in"),
    ("Your auth token is invalid or expired. Run `grok login` to re-authenticate.", "not_logged_in"),
    # offline, in the Go (Antigravity) and Rust (Codex) CLIs' words
    ('Eligibility check failed: Post "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist": '
     "proxyconnect tcp: dial tcp: lookup offline.invalid: no such host", "offline"),
    ("dial tcp: lookup daily-cloudcode-pa.googleapis.com: no such host", "offline"),
    ("dial tcp 142.250.75.10:443: connectex: A socket operation was attempted to an unreachable network.", "offline"),
    ("Reconnecting... 5/5 (workspace routing discovery failed)", "offline"),
    ("failed to refresh available models: Connection failed: error sending request for url "
     "(https://chatgpt.com/backend-api/codex/models)", "offline"),
    ("error sending request: client error (Connect): dns error: No such host is known. (os error 11001)", "offline"),
    # Claude Code 2.1.280's own words (from claude.exe), "Something went wrong" before (audit PRV-4)
    ("API Error: Unable to connect to API. Check your internet connection", "offline"),
    ("API Error: Connection error.", "offline"),
    ("API Error: Unable to connect to API (ECONNRESET)", "offline"),
    ("Connection dropped", "offline"),
    ("API Error: Request timed out.", "offline"),
    ("You're out of extra usage", "usage_limit"),
    ("You've hit your team's shared budget. Ask an admin to raise it.", "usage_limit"),
    ("Context limit reached · /compact or /clear to continue", None),
])
def test_classify_error(text, kind):
    assert base.classify_error(text) == kind


def test_expired_sign_ins_and_offline_reach_the_player_through_each_cli():
    """End to end through each CLI's own parser: not "Something went wrong" (api_error)."""
    from maplehelper.providers import gemini, grok
    expired = "Your access token could not be refreshed because your refresh token has expired. Please log out " \
              "and sign in again."
    events = [json.dumps({"type": "error", "message": expired}), json.dumps({"type": "turn.failed",
                                                                             "error": {"message": expired}})]
    assert codex.parse_events(events).error == "not_logged_in"
    grok_expired = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                    "errors": ["Your auth token is invalid or expired. Run `grok login` to re-authenticate."]}
    assert grok.to_result("", grok_expired, "", None).error == "not_logged_in"
    offline = [json.dumps({"type": "turn.failed", "error": {"message": "Connection failed: error sending request"}})]
    assert codex.parse_events(offline).error == "offline"
    agy = {"status": "ERROR", "error": 'Eligibility check failed: Post "https://x": proxyconnect tcp: dial tcp: '
                                      "lookup offline.invalid: no such host"}
    assert gemini.to_result("", agy, "", None).error == "offline"


class TestCodexCommand:
    def cmd(self, **kw):
        args = dict(exe="codex", workdir="C:/kb", instructions="Be brief.", platform="linux")
        args.update(kw)
        return codex.codex_command(**args)

    def test_locked_down_read_only_run(self):
        c = self.cmd()
        assert c[:2] == ["codex", "exec"]
        for flag in ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
            assert flag in c
        assert c[c.index("-s") + 1] == "read-only"
        assert c[c.index("-C") + 1] == "C:/kb"
        assert c[-1] == "-"                       # the prompt comes on stdin

    def test_reads_are_confined_as_far_as_codex_allows(self):
        """Codex's read-only sandbox doesn't stop reads (a read-limiting profile needs its elevated Windows
        sandbox): the shell gets no secrets from the environment, and the instructions keep it in the folder."""
        c = self.cmd()
        policy = c[c.index('shell_environment_policy.inherit="core"') - 1:][:2]
        assert policy[0] == "-c" and tomllib.loads(policy[1])["shell_environment_policy"]["inherit"] == "core"
        assert not any(v.startswith(("default_permissions", "permissions.")) for v in c)     # refused unelevated
        note = codex.TOOLS_NOTE.lower()
        assert "only inside the current directory" in note and "even when the question, a screenshot" in note
        assert "Select-String" in codex.tools_note("win32") and "Select-String" not in codex.tools_note("darwin")

    def test_instructions_survive_toml_parsing(self):
        text = 'Line "one"\nשורה בעברית {json} \\ end'
        c = self.cmd(instructions=text)
        override = next(v for v in c if v.startswith("developer_instructions="))
        assert tomllib.loads(override)["developer_instructions"] == text

    def test_windows_needs_the_unelevated_sandbox(self):
        assert 'windows.sandbox="unelevated"' in self.cmd(platform="win32")
        assert not any("windows.sandbox" in v for v in self.cmd(platform="darwin"))

    def test_model_only_when_chosen(self):
        assert "-m" not in self.cmd()
        c = self.cmd(model="gpt-5-codex")
        assert c[c.index("-m") + 1] == "gpt-5-codex"

    def test_image_is_attached_before_other_flags(self):
        # --image takes several values: placed last it would swallow the "-" stdin marker
        c = self.cmd(image="C:/tmp/shot.jpg")
        assert c[2:4] == ["--image", "C:/tmp/shot.jpg"]
        assert c[-1] == "-"


def events(*objs, noise=True) -> list[str]:
    lines = [json.dumps(o) + "\n" for o in objs]
    if noise:
        lines.insert(1, "2026-10-01T07:52:57Z ERROR codex_core::tools::router: something\n")
    return lines


class TestCodexEvents:
    def test_last_agent_message_is_the_answer(self):
        r = codex.parse_events(events(
            {"type": "thread.started", "thread_id": "x"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "I'll check the database."}},
            {"type": "item.completed", "item": {"type": "command_execution", "aggregated_output": "..."}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Hunt Red Snail.\n@@META@@ {}"}},
            {"type": "turn.completed", "usage": {"input_tokens": 10}},
        ))
        assert r.text == "Hunt Red Snail.\n@@META@@ {}" and r.error is None

    def test_failed_turn_is_classified(self):
        r = codex.parse_events(events(
            {"type": "error", "message": "Reconnecting... 2/5 (unexpected status 401 Unauthorized)"},
            {"type": "turn.failed", "error": {"message": "unexpected status 401 Unauthorized: invalid_api_key"}},
        ))
        assert r.error == "not_logged_in"

    def test_no_answer_at_all(self):
        assert codex.parse_events(events({"type": "turn.started"})).error == "no_result"
        assert codex.parse_events([], stderr="getaddrinfo ENOTFOUND api.openai.com").error == "offline"


@pytest.mark.parametrize("code,out,status,method", [
    (0, "Logged in using ChatGPT\n", "ok", "chatgpt"),
    (0, "Logged in using an API key - sk-proj-***abc\n", "ok", "api_key"),
    (1, "Not logged in\n", "logged_out", None),
    (0, "", "logged_out", None),
])
def test_codex_login_status(code, out, status, method):
    assert codex.parse_status(code, out) == {"status": status, "email": None, "method": method}


class TestDiscovery:
    def test_finds_claude_outside_the_finder_path(self, tmp_path, monkeypatch):
        # an app opened from Finder has no ~/.local/bin on PATH: the native installer's spot must still be found
        exe = tmp_path / "bin" / "claude"
        exe.parent.mkdir()
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        monkeypatch.setattr(base.shutil, "which", lambda _name: None)
        assert base.find_posix("claude", ["/nonexistent", str(exe.parent)]) == str(exe)

    def test_finds_an_npm_global_install_under_nvm_newest_node_first(self, tmp_path, monkeypatch):
        # npm i -g under nvm/volta/fnm/bun: an app opened from Finder has none of these on PATH (MAC-10)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        for v in ("v9.11.2", "v22.3.0", "v20.10.0"):
            (tmp_path / ".nvm" / "versions" / "node" / v / "bin").mkdir(parents=True)
        dirs = base.posix_dirs(["/opt/homebrew/bin"])
        nvm = [d for d in dirs if ".nvm" in d]
        assert [Path(d).parent.name for d in nvm] == ["v22.3.0", "v20.10.0", "v9.11.2"]
        assert dirs[0] == str(Path("/opt/homebrew/bin")) and str(tmp_path / ".volta" / "bin") in dirs
        exe = Path(nvm[1]) / "codex"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        monkeypatch.setattr(base.shutil, "which", lambda _name: None)
        assert base.find_posix("codex", ["/nonexistent"]) == str(exe)
        monkeypatch.setattr(base.sys, "platform", "darwin")
        assert nvm[0] in base.child_env([], {"PATH": "/usr/bin"})["PATH"]

    def test_windows_exe_path_ends_in_lowercase_exe(self, monkeypatch):
        # shutil.which("claude") takes the extension from PATHEXT (".EXE"); Claude Code started as claude.EXE
        # hangs when it runs its built-in rg, so every answer that greps the knowledge base never came back
        monkeypatch.setattr(base.shutil, "which", lambda _name: r"C:\Users\p\.local\bin\claude.EXE")
        assert base.find_windows_exe("claude", []) == r"C:\Users\p\.local\bin\claude.exe"

    def test_windows_codex_ignores_the_npm_cmd_shim(self, tmp_path, monkeypatch):
        # a .cmd shim goes through cmd.exe, which mangles the quoted instructions: only a real .exe is used
        exe = tmp_path / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        monkeypatch.setattr(base.shutil, "which", lambda _name: str(tmp_path / "npm" / "codex.cmd"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert codex.find_windows() == str(exe)

    def test_windows_codex_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(base.shutil, "which", lambda _name: str(tmp_path / "codex.cmd"))
        for var in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
            monkeypatch.setenv(var, str(tmp_path))
        monkeypatch.setattr(codex, "store_apps", lambda: [])
        assert codex.find_windows() is None

    def test_windows_codex_from_the_store_app(self, tmp_path, monkeypatch):
        """OpenAI's Microsoft Store app carries the CLI; its folder comes from the package registry."""
        exe = tmp_path / "OpenAI.Codex_26.9.1.0_x64__x" / "app" / "resources" / "codex.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        monkeypatch.setattr(base.shutil, "which", lambda _name: None)
        for var in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
            monkeypatch.setenv(var, str(tmp_path))
        monkeypatch.setattr(codex, "store_apps", lambda: [tmp_path / "gone" / "codex.exe", exe])
        assert codex.find_windows() == str(exe)


def test_creation_flags_are_windows_only():
    # subprocess raises ValueError for nonzero creationflags outside Windows
    assert (base.CREATE_NO_WINDOW != 0) == (base.sys.platform == "win32")


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


def test_api_keys_are_kept_per_provider(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring())
    providers.get("claude").save_api_key("sk-ant-1")
    providers.get("codex").save_api_key("sk-proj-2")
    providers.get("codex").delete_api_key()
    assert providers.get("claude").load_api_key() == "sk-ant-1"
    assert providers.get("codex").load_api_key() is None


class FakePopen:
    """Records the command and replays canned Codex output."""
    calls: list = []
    stdout_lines: list[str] = []

    def __init__(self, cmd, **kw):
        FakePopen.calls.append((cmd, kw))
        image = cmd[cmd.index("--image") + 1] if "--image" in cmd else None
        self.image_existed = bool(image) and open(image, "rb").read() == b"JPEGDATA"
        FakePopen.last = self
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        self.stdout = iter(line.encode() for line in FakePopen.stdout_lines)
        self.stderr = io.BytesIO(b"")
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class TestCodexBackend:
    @pytest.fixture
    def kb(self, kb_copy):
        # ask() writes drops.tsv into the knowledge base: never into the shared fixture
        from maplehelper.kb import KnowledgeBase
        return KnowledgeBase(kb_copy)

    def make(self, kb, monkeypatch, api_key=None):
        FakePopen.calls = []
        FakePopen.stdout_lines = events(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Looking…"}},
            {"type": "item.completed", "item": {"type": "agent_message",
                                                "text": 'Hunt **Red Snail**.\n@@META@@\n{"entities": ["monster/130101"]}'}},
            {"type": "turn.completed"},
        )
        monkeypatch.setattr(codex.subprocess, "Popen", FakePopen)
        from maplehelper.brain import Brain
        b = Brain(kb, provider="codex", api_key=api_key)
        b.backend.exe = "codex"
        return b

    def test_answer_goes_through_the_shared_post_processing(self, kb, monkeypatch):
        b = self.make(kb, monkeypatch)
        ans = b.ask("where is Red Snail?", None, None, b"JPEGDATA")
        assert ans.error is None and ans.text == "Hunt **Red Snail**."
        assert ans.entities[0] == "monster/130101"
        cmd, kw = FakePopen.calls[0]
        assert kw["cwd"] == str(kb.root)
        assert "<question>" in FakePopen.last.stdin.getvalue().decode()

    def test_screenshot_file_exists_during_the_run_and_is_removed_after(self, kb, monkeypatch):
        b = self.make(kb, monkeypatch)
        b.ask("hi", None, None, b"JPEGDATA")
        cmd, _ = FakePopen.calls[0]
        image = cmd[cmd.index("--image") + 1]
        assert FakePopen.last.image_existed
        assert not __import__("os").path.exists(image)

    def test_account_login_vs_api_key_env(self, kb, monkeypatch):
        monkeypatch.setenv("CODEX_API_KEY", "leftover")
        b = self.make(kb, monkeypatch)
        b.ask("hi", None, None, None)
        assert "CODEX_API_KEY" not in FakePopen.calls[0][1]["env"]   # the player's ChatGPT login
        b = self.make(kb, monkeypatch, api_key="sk-proj-9")
        b.ask("hi", None, None, None)
        assert FakePopen.calls[0][1]["env"]["CODEX_API_KEY"] == "sk-proj-9"
        assert "--image" not in FakePopen.calls[0][0]

    def test_not_installed(self, kb, monkeypatch):
        b = self.make(kb, monkeypatch)
        b.backend.exe = None
        monkeypatch.setattr(type(providers.get("codex")), "find_exe", lambda self: None)   # none on this PC
        assert b.ask("hi", None, None, None).error == "not_installed"
        assert not b.available()

    def test_runs_that_need_no_knowledge_base_get_no_shell(self, kb, monkeypatch):
        """The quick screenshot read and summaries had the shell and no confining note (audit SEC-3)."""
        b = self.make(kb, monkeypatch)
        b.backend.run("sync", b"JPEGDATA", tools=False)
        b.backend.summarize("Summarize.", "long text")
        b.ask("hi", None, None, None)
        quick, summary, full = (c for c, _ in FakePopen.calls)
        for c in (quick, summary):
            assert all(c[c.index(f) - 1] == "--disable" for f in codex.NO_SHELL)
            assert codex.NO_TOOLS_NOTE.strip() in next(v for v in c if v.startswith("developer_instructions="))
        assert "shell_tool" not in full and "unified_exec" not in full       # the answer reads the KB with it
        assert "only inside the current directory" in next(v for v in full if v.startswith("developer_instructions="))

    def test_a_feature_this_codex_doesnt_know_is_dropped(self, kb, monkeypatch):
        """Codex refuses to start on an unknown --disable name (an old Codex, or a newer one that dropped it): every
        answer was "Something went wrong" (audit PRV-7)."""
        monkeypatch.setattr(codex, "_unknown_features", set())
        b = self.make(kb, monkeypatch)
        ok = FakePopen.stdout_lines
        stderrs = [b"ERROR: Unknown feature flag: goals\n", b""]
        outs = [[], ok]

        class Picky(FakePopen):
            def __init__(self, cmd, **kw):
                super().__init__(cmd, **kw)
                self.stdout = iter(line.encode() for line in outs.pop(0))
                self.stderr = io.BytesIO(stderrs.pop(0))
        monkeypatch.setattr(codex.subprocess, "Popen", Picky)
        assert b.ask("hi", None, None, None).text == "Hunt **Red Snail**."
        first, second = (c for c, _ in FakePopen.calls)
        assert "goals" in first and "goals" not in second
        assert "goals" not in codex.codex_command("codex", "C:/kb", "x")       # left out from then on

    def test_an_old_cli_says_to_update(self):
        assert base.classify_error("error: unexpected argument '--ignore-rules' found") == "cli_outdated"
        assert base.classify_error("error: unknown option '--restricted'") == "cli_outdated"

    def test_a_silent_run_is_stopped_before_the_whole_timeout(self, kb, monkeypatch):
        """Codex had no stall check: a hung run kept "thinking" for the full 5 minutes (audit PRV-15)."""
        seen = {}

        class Lines(base.Lines):
            def __init__(self, proc, stall_s, label="CLI", deadline_s=None):
                seen["stall"] = stall_s
                super().__init__(proc, stall_s, label, deadline_s)
        monkeypatch.setattr(codex, "Lines", Lines)
        b = self.make(kb, monkeypatch)
        b.ask("hi", None, None, None)
        assert seen["stall"] == codex.STALL_TIMEOUT_S == 150

    def test_the_players_own_gateway_is_left_out(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:9999")
        assert "OPENAI_BASE_URL" not in codex.env()


class TestClaudeBackend:
    RATE = {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
        "five_hour": {"utilization": 0.7, "resetsAt": 2000}}}}

    def make(self, kb_copy, monkeypatch, *evs):
        from maplehelper.brain import Brain
        from maplehelper.kb import KnowledgeBase
        FakePopen.calls = []
        FakePopen.stdout_lines = [json.dumps(e) + "\n" for e in evs]
        monkeypatch.setattr(claude.subprocess, "Popen", FakePopen)
        b = Brain(KnowledgeBase(kb_copy), provider="claude")
        b.backend.exe = "claude"
        monkeypatch.setattr(b.backend, "prewarm", lambda: None)
        return b

    def test_plan_usage_reaches_the_answer(self, kb_copy, monkeypatch):
        b = self.make(kb_copy, monkeypatch, self.RATE, {"type": "result", "result": "Hi.\n@@META@@\n{}"})
        ans = b.ask("hi", None, None, None)
        assert ans.text == "Hi." and ans.limits == {"five_hour": {"used": 0.7, "resets": 2000}}

    def test_plan_usage_is_kept_when_the_answer_fails(self, kb_copy, monkeypatch):
        # a usage-limit stop still tells the meter where the plan stands
        b = self.make(kb_copy, monkeypatch, self.RATE)
        ans = b.ask("hi", None, None, None)
        assert ans.error == "no_result" and ans.limits["five_hour"]["used"] == 0.7


    def test_the_answer_is_the_text_after_the_last_tool_call(self, kb_copy, monkeypatch):
        """A lead-in written before a tool call in the same message is no part of the answer, even when the
        result's own text carries it."""
        def se(event):
            return {"type": "stream_event", "event": event}

        def text(t):
            return se({"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}})
        b = self.make(kb_copy, monkeypatch,
                      se({"type": "message_start", "message": {"model": "claude-sonnet-5"}}),
                      text("I'll grep drops.tsv. "), se({"type": "content_block_start",
                                                         "content_block": {"type": "tool_use", "name": "Grep"}}),
                      se({"type": "message_start", "message": {}}),
                      se({"type": "content_block_start", "content_block": {"type": "thinking"}}),
                      se({"type": "content_block_start", "content_block": {"type": "text"}}),
                      text("Hunt Red Snail."),
                      {"type": "result", "result": "I'll grep drops.tsv. Hunt Red Snail."})
        seen = []
        ans = b.ask("where should I hunt Red Snails today?", None, None, None, on_delta=seen.append)
        assert ans.text == "Hunt Red Snail." and ans.model == "claude-sonnet-5"
        assert seen[-1] == "Hunt Red Snail."


class ScriptProc:
    """A Claude Code process for the hedging tests. It replays its script once the question has arrived: a number
    waits that many seconds (unless the process is killed), a dict is one output line, "exit" ends the process
    while its output stays open (a child process of its own holding the pipe: nothing more comes, no end).
    stderr_open: its stderr never ends either."""
    scripts: list = []
    procs: list = []
    stderr_open = False

    def __init__(self, cmd, **kw):
        self.script = ScriptProc.scripts.pop(0)
        self.n = len(ScriptProc.procs)
        ScriptProc.procs.append(self)
        self.killed, self.sent, self.ended = threading.Event(), threading.Event(), threading.Event()
        self.returncode = None
        self.stdin = io.BytesIO()
        self.stdin.close = self.sent.set
        self.stderr = _OpenPipe() if ScriptProc.stderr_open else io.BytesIO(b"")
        self.stdout = self._lines()

    def _lines(self):
        try:
            self.sent.wait(10)
            for step in self.script:
                if self.killed.is_set():
                    return
                if step == "exit":
                    self.returncode = 0
                    self.ended.set()
                    time.sleep(30)               # the orphan holds the pipe; nothing ends it from here
                    return
                if isinstance(step, (int, float)):
                    if self.killed.wait(step):
                        return
                else:
                    yield (json.dumps(step) + "\n").encode()
        finally:
            if self.returncode is None:
                self.returncode = 0
            self.ended.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.ended.wait(timeout or 10)
        return self.returncode

    def kill(self):
        if self.returncode is None:
            self.returncode = -9
        self.killed.set()


class _OpenPipe:
    """A pipe nobody ever closes."""

    def read(self, n=-1):
        time.sleep(60)
        return b""


def _se(event):
    return {"type": "stream_event", "event": event}


def _text(t):
    return _se({"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}})


def _answer(*parts, delay=0.0):
    """A run that streams `parts` (with `delay` between them) and ends with its result."""
    steps = [_se({"type": "message_start", "message": {"model": "claude-sonnet-5"}})]
    for p in parts:
        steps += [delay, _text(p)]
    return steps + [{"type": "result", "result": "".join(parts)}]


# what Claude Code prints the moment the question arrives, before the server has answered anything
LOCAL = [{"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
         {"type": "system", "subtype": "status", "status": "requesting"}]


class TestHedging:
    """A run with no sign of life for HEDGE_AFTER_S gets a twin; the first to answer wins (claude.ClaudeBackend)."""

    def make(self, kb_copy, monkeypatch, *scripts, after=0.3):
        from maplehelper.brain import Brain
        from maplehelper.kb import KnowledgeBase
        ScriptProc.scripts, ScriptProc.procs = [list(s) for s in scripts], []
        monkeypatch.setattr(claude.subprocess, "Popen", ScriptProc)
        monkeypatch.setattr(claude, "HEDGE_AFTER_S", after)
        b = Brain(KnowledgeBase(kb_copy), provider="claude")
        b.backend.exe = "claude"
        monkeypatch.setattr(b.backend, "prewarm", lambda: None)
        return b

    def ask(self, b, question="where should I go to hunt snails today?"):
        seen = []
        ans = b.ask(question, None, None, None, on_delta=seen.append)
        return ans, seen

    def test_a_silent_run_gets_a_twin_and_the_faster_one_answers(self, kb_copy, monkeypatch):
        # the first run's server sits on the request (only Claude Code's own init/status lines): no sign of life
        b = self.make(kb_copy, monkeypatch, LOCAL + [30] + _answer("Slow."), _answer("Hunt ", "snails."))
        t = time.monotonic()
        ans, seen = self.ask(b)
        assert ans.text == "Hunt snails." and time.monotonic() - t < 5
        assert len(ScriptProc.procs) == 2 and ScriptProc.procs[0].killed.is_set()
        assert seen and all(s.startswith("Hunt") for s in seen)

    def test_thinking_is_a_sign_of_life(self, kb_copy, monkeypatch):
        """While the model thinks, Claude Code prints thinking_tokens: that run is working, no twin."""
        thinking = [{"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50}]
        b = self.make(kb_copy, monkeypatch, LOCAL + [0.05] + thinking + [1.5] + _answer("Thought it over."),
                      _answer("Twin."), after=0.8)
        ans, _ = self.ask(b)
        assert ans.text == "Thought it over." and len(ScriptProc.procs) == 1

    def test_the_first_run_wins_once_it_streams(self, kb_copy, monkeypatch):
        """Slow to start, but its text reaches the chat before the twin's: it keeps the question, the twin stops."""
        b = self.make(kb_copy, monkeypatch, [0.5] + _answer("First ", "answer.", delay=0.2),
                      [3] + _answer("Twin answer."))
        ans, seen = self.ask(b)
        assert ans.text == "First answer." and len(ScriptProc.procs) == 2
        assert ScriptProc.procs[1].killed.is_set() and all(s.startswith("First") for s in seen)

    def test_two_streams_never_interleave(self, kb_copy, monkeypatch):
        b = self.make(kb_copy, monkeypatch, [0.6] + _answer(*["A"] * 8, delay=0.05),
                      [0.1] + _answer(*["B"] * 8, delay=0.05), after=0.2)
        ans, seen = self.ask(b)
        assert ans.text == "B" * 8
        assert seen and all(set(s) == {"B"} for s in seen)

    def test_cancel_stops_both_runs(self, kb_copy, monkeypatch):
        b = self.make(kb_copy, monkeypatch, [30] + _answer("one"), [30] + _answer("two"), after=0.1)
        out = {}
        t = threading.Thread(target=lambda: out.update(ans=b.ask("hi", None, None, None)))
        t.start()
        for _ in range(100):
            if len(ScriptProc.procs) == 2:
                break
            time.sleep(0.05)
        b.cancel()
        t.join(5)
        assert not t.is_alive() and out["ans"].error
        assert all(p.killed.is_set() for p in ScriptProc.procs) and len(ScriptProc.procs) == 2

    def test_only_one_twin_and_never_after_text(self, kb_copy, monkeypatch):
        # the twin is as slow: no third run, and whichever answers first wins
        b = self.make(kb_copy, monkeypatch, [1.0] + _answer("one"), [3] + _answer("two"), [0] + _answer("three"),
                      after=0.1)
        ans, _ = self.ask(b)
        assert ans.text == "one" and len(ScriptProc.procs) == 2

    def test_a_quick_failure_is_no_reason_for_a_twin(self, kb_copy, monkeypatch):
        b = self.make(kb_copy, monkeypatch, [{"type": "result", "is_error": True, "result": "Claude usage limit "
                                                                                              "reached"}],
                      _answer("never"))
        ans, _ = self.ask(b)
        assert ans.error == "usage_limit" and len(ScriptProc.procs) == 1

    def test_a_failed_run_waits_for_its_twin(self, kb_copy, monkeypatch):
        """The first run dies after the twin started (a dropped connection): the twin's answer still comes."""
        b = self.make(kb_copy, monkeypatch, [0.4], [0.3] + _answer("Twin answer."), after=0.2)
        ans, _ = self.ask(b)
        assert ans.text == "Twin answer."

    def test_the_plan_usage_and_model_come_from_the_winner(self, kb_copy, monkeypatch):
        rate = {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "five_hour": {"utilization": 0.4, "resetsAt": 2000}}}}
        b = self.make(kb_copy, monkeypatch, [30], [rate] + _answer("Hi."))
        ans, _ = self.ask(b)
        assert ans.text == "Hi." and ans.limits["five_hour"]["used"] == 0.4 and ans.model == "claude-sonnet-5"

    def test_a_late_first_run_streams_the_moment_it_speaks(self, kb_copy, monkeypatch):
        """An eval case's first text came exactly at the hedge time (25.06 s): the twin's start must never hold back
        the first run's text. Silent past the hedge, then a slow stream: each piece reaches the chat as it comes."""
        b = self.make(kb_copy, monkeypatch, [0.6] + _answer("One ", "two ", "three.", delay=0.3), [30] + _answer("x"))
        times = []
        t = time.monotonic()
        ans = b.ask("where should I go to hunt snails today?", None, None, None,
                    on_delta=lambda s: times.append((round(time.monotonic() - t, 2), s)))
        assert ans.text == "One two three." and len(ScriptProc.procs) == 2
        assert times[0][1] == "One" and times[0][0] < 1.3            # 0.6 s silent + 0.3 s to the first piece
        assert times[-1][0] < 2.2 and time.monotonic() - t < 2.5      # the twin's kill doesn't hold the answer

    def test_two_questions_at_once_on_one_backend(self, kb_copy, monkeypatch):
        """Two asks together on one Brain, sharing its warm process (as the app's chat and its sync can): neither
        waits for the other, and neither gets a twin."""
        b = self.make(kb_copy, monkeypatch, *[_answer("Same ", "answer.", delay=0.2)] * 8, after=1.5)
        monkeypatch.setattr(b.backend, "prewarm", claude.ClaudeBackend.prewarm.__get__(b.backend))
        b.backend.prewarm()
        out, firsts = {}, {}

        def ask(k):
            t = time.monotonic()
            out[k] = b.ask("where should I go to hunt snails today?", None, None, None,
                           on_delta=lambda s: firsts.setdefault(k, time.monotonic() - t))
            out[k + "_s"] = time.monotonic() - t
        threads = [threading.Thread(target=ask, args=(k,)) for k in ("a", "b")]
        for th in threads:
            th.start()
        for th in threads:
            th.join(10)
        assert out["a"].text == out["b"].text == "Same answer."
        assert out["a_s"] < 1.4 and out["b_s"] < 1.4 and max(firsts.values()) < 1.0
        b.backend.shutdown()

    def test_the_race_alone(self):
        """base.Race without a CLI: what any provider's run would get."""
        seen = []
        race = base.Race(seen.append)

        def run_one(a):
            if a.n == 0:
                time.sleep(1.0)
                a.delta("late")
                return base.RawResult(text="late")
            a.activity()
            a.delta("quick")
            return base.RawResult(text="quick")
        r = race.run(run_one, hedge_after=0.1)
        assert r.text == "quick" and seen == ["quick"]
        assert race.attempts[0].lost


END_TURN = [_se({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}), _se({"type": "message_stop"})]
STATUS = {"type": "system", "subtype": "status", "status": "requesting"}


class TestRunEnds:
    """A run is over at its "result" line, not when the process ends: an eval answer was complete in 2 s and its run
    ended after 501 s (the chat would have stayed on "answering")."""

    @pytest.fixture(autouse=True)
    def quick(self, monkeypatch):
        monkeypatch.setattr(base, "OUTLIVE_LOG_S", 0.3)
        monkeypatch.setattr(base, "EXIT_GRACE_S", 0.3)
        monkeypatch.setattr(claude, "RESULT_GRACE_S", 0.3)
        monkeypatch.setattr(ScriptProc, "stderr_open", False)

    def ask(self, kb_copy, monkeypatch, script, **kw):
        b = TestHedging().make(kb_copy, monkeypatch, script, after=60, **kw)
        t = time.monotonic()
        ans = b.ask("where should I go to hunt snails today?", None, None, None)
        return ans, time.monotonic() - t

    def test_a_cli_that_never_exits_after_its_result(self, kb_copy, monkeypatch, caplog):
        caplog.set_level("INFO")
        ans, took = self.ask(kb_copy, monkeypatch, _answer("Hunt snails.") + [STATUS, 30])
        assert ans.text == "Hunt snails." and took < 2
        for _ in range(40):                      # stopped in the background, and the log says what it did
            if ScriptProc.procs[0].killed.is_set():
                break
            time.sleep(0.05)
        assert ScriptProc.procs[0].killed.is_set()
        assert "still running" in caplog.text and "system/status" in caplog.text

    def test_status_chatter_after_the_answer_without_a_result(self, kb_copy, monkeypatch):
        """No "result" line at all, only status lines: the answer's turn ended with its META block, so it's taken."""
        script = [_se({"type": "message_start", "message": {}}), _text('Hunt snails.\n@@META@@\n{}')] + END_TURN
        ans, took = self.ask(kb_copy, monkeypatch, script + [STATUS, 0.1] * 300)
        assert ans.text == "Hunt snails." and took < 3

    def test_status_chatter_is_no_sign_of_life(self, kb_copy, monkeypatch):
        monkeypatch.setattr(claude, "STALL_TIMEOUT_S", 0.8)
        ans, took = self.ask(kb_copy, monkeypatch, LOCAL + [STATUS, 0.1] * 300)
        assert ans.error == "timeout" and took < 3 and ScriptProc.procs[0].killed.is_set()

    def test_thinking_keeps_a_run_alive(self, kb_copy, monkeypatch):
        monkeypatch.setattr(claude, "STALL_TIMEOUT_S", 0.8)
        thinking = [{"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50}, 0.3]
        ans, _ = self.ask(kb_copy, monkeypatch, thinking * 6 + _answer("Thought it over."))
        assert ans.text == "Thought it over."

    def test_a_cli_that_exits_but_its_output_stays_open(self, kb_copy, monkeypatch):
        """Its own child process holds the pipe: reading stops shortly after the exit."""
        script = [_se({"type": "message_start", "message": {}}), _text('Hi.\n@@META@@\n{}')] + END_TURN + ["exit"]
        ans, took = self.ask(kb_copy, monkeypatch, script)
        assert ans.text == "Hi." and took < 3
        ans, took = self.ask(kb_copy, monkeypatch, LOCAL + ["exit"])       # nothing said: no answer, quickly
        assert ans.error == "no_result" and took < 3

    def test_an_answer_never_waits_for_stderr(self, kb_copy, monkeypatch):
        monkeypatch.setattr(ScriptProc, "stderr_open", True)
        ans, took = self.ask(kb_copy, monkeypatch, _answer("Hunt snails.") + [30])
        assert ans.text == "Hunt snails." and took < 2

    def test_the_plan_usage_comes_before_the_result(self, kb_copy, monkeypatch):
        """Live order: rate_limit_event, then result (the run stops reading at the result)."""
        rate = {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "five_hour": {"utilization": 0.3, "resetsAt": 2000}}}}
        ans, _ = self.ask(kb_copy, monkeypatch, _answer("Hi.")[:-1] + [rate, _answer("Hi.")[-1], 30])
        assert ans.limits["five_hour"]["used"] == 0.3


class _Stuck:
    """Lines, then a pipe that stays open (the process never ends by itself)."""

    def __init__(self, lines):
        self.lines, self.killed = lines, threading.Event()
        self.stdout = self._out()
        self.stderr = _OpenPipe()
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        self.returncode = None

    def _out(self):
        yield from (line.encode() if isinstance(line, str) else line for line in self.lines)
        self.killed.wait(30)

    def poll(self):
        return -9 if self.killed.is_set() else None

    def kill(self):
        self.killed.set()

    def wait(self, timeout=None):
        return 0


def test_grok_gemini_and_codex_stop_reading_at_their_last_event(kb_copy, monkeypatch):
    """The same for the other CLIs: done at Grok's "result", agy's "result" and Codex's turn.completed."""
    from maplehelper.brain import Brain
    from maplehelper.kb import KnowledgeBase
    from maplehelper.providers import gemini, grok
    monkeypatch.setattr(base, "OUTLIVE_LOG_S", 0.3)
    kb = KnowledgeBase(kb_copy)
    grok_out = [json.dumps(e) + "\n" for e in (
        {"type": "stream_event", "event": {"type": "message_start", "message": {}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta",
                                                                                   "text": "Hi.\n@@META@@\n{}"}}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Hi."})]
    agy_out = [json.dumps(e) + "\n" for e in (
        {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Hi.\n@@META@@\n{}"}},
        {"event": "result", "result": {"status": "SUCCESS"}})]
    codex_out = [json.dumps(e) + "\n" for e in (
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Hi.\n@@META@@\n{}"}},
        {"type": "turn.completed"})]
    for mod, name, out in ((grok, "grok", grok_out), (gemini, "gemini", agy_out), (codex, "codex", codex_out)):
        procs = []
        monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, _o=out, _p=procs, **k: _p.append(_Stuck(_o)) or _p[-1])
        if mod is grok:
            monkeypatch.setattr(grok, "guard_exe", lambda kb_root: None)
            monkeypatch.setattr(grok, "home", lambda: kb_copy.parent / "grok-home")
        if mod is gemini:
            monkeypatch.setattr(gemini, "home", lambda: kb_copy.parent / "agy-home")
        b = Brain(kb, provider=name)
        b.backend.exe = name + ".exe"
        t = time.monotonic()
        ans = b.ask("where should I go to hunt snails today?", None, None, None)
        assert ans.text == "Hi." and time.monotonic() - t < 3, name
        for _ in range(40):
            if procs[0].killed.is_set():
                break
            time.sleep(0.05)
        assert procs[0].killed.is_set(), name               # stopped in the background


class TestWarmProcess:
    def backend(self, kb, monkeypatch):
        from maplehelper.brain import Brain
        spawned = []

        class Proc:
            def __init__(self):
                self.killed = False
                spawned.append(self)

            def poll(self):
                return -9 if self.killed else None

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                return 0
        b = Brain(kb, provider="claude").backend
        b.brain.chat_shown(True)             # the chat in use: the warm process is kept and renewed (PRF-1)
        b.exe = "claude"
        monkeypatch.setattr(b, "_spawn", lambda *a, **k: Proc())
        return b, spawned

    def test_a_warm_process_that_waited_too_long_is_replaced(self, kb, monkeypatch):
        b, spawned = self.backend(kb, monkeypatch)
        b.prewarm()
        proc, age = b._take_warm()
        assert proc is spawned[0] and age < 5
        b.prewarm()
        b._warm_born -= claude.WARM_MAX_AGE_S + 1                 # it sat there past the limit
        proc, _ = b._take_warm()
        assert proc is None and spawned[1].killed                  # not used: the question starts a fresh one
        b.prewarm()
        b._warm_born -= claude.WARM_MAX_AGE_S + 1
        b._refresh(spawned[2])                                     # the timer: a fresh one waits again
        assert spawned[2].killed and b._warm is spawned[3]
        b._refresh(spawned[2])                                     # an old timer for one already gone: nothing
        assert len(spawned) == 4


class DeadProc:
    """A Claude Code too old for a flag Maple Helper passes: it exits at once, before reading the question, and
    says why on stderr only (issue #107)."""
    stderr_text = b"error: unknown option '--restricted'\n"

    def __init__(self, cmd, **kw):
        self.returncode = 1
        self.stdin = io.BytesIO()
        self.stdin.write = self._broken
        self.stdout = io.BytesIO(b"")
        self.stderr = io.BytesIO(DeadProc.stderr_text)

    @staticmethod
    def _broken(data):
        raise BrokenPipeError(32, "Broken pipe")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


class TestCliThatDiesAtStart:
    def make(self, kb_copy, monkeypatch):
        from maplehelper.brain import Brain
        from maplehelper.kb import KnowledgeBase
        monkeypatch.setattr(claude.subprocess, "Popen", DeadProc)
        b = Brain(KnowledgeBase(kb_copy), provider="claude")
        b.backend.exe = "claude"
        monkeypatch.setattr(b.backend, "prewarm", lambda: None)
        return b

    def test_an_outdated_cli_says_so(self, kb_copy, monkeypatch, caplog):
        # every answer was "Something went wrong" with nothing in the log: the question never reached the CLI,
        # and its stderr was never read
        b = self.make(kb_copy, monkeypatch)
        with caplog.at_level(logging.WARNING, logger="maplehelper.providers.claude"):
            ans = b.ask("hi", None, None, None)
        assert ans.error == "cli_outdated"
        assert "unknown option '--restricted'" in caplog.text           # "Report a problem" carries the cause

    def test_an_unknown_startup_failure_is_still_no_result(self, kb_copy, monkeypatch):
        monkeypatch.setattr(DeadProc, "stderr_text", b"Segmentation fault\n")
        b = self.make(kb_copy, monkeypatch)
        assert b.ask("hi", None, None, None).error == "no_result"


@pytest.mark.parametrize("raw,shown", [
    # the leak seen live: English planning after the tool calls, then the Hebrew answer
    ("This quest is in Kerning City (Victoria Island) - good, in game. Now for answer, I'll mention Stranger's "
     "Identity as doable now, and list others for later.\n\n**Stranger's Identity** ב-Kerning City, מתאים לרמה שלכם.",
     "**Stranger's Identity** ב-Kerning City, מתאים לרמה שלכם."),
    ("Let me check what drops there. The data says Mano drops it.\nכדאי לעשות גריינד על Mano.",
     "כדאי לעשות גריינד על Mano."),
    # legit answers stay whole: English answers, English names and list lines before the Hebrew
    ("I'll be honest: Mano is not worth it at your level. Go to Henesys instead.",
     "I'll be honest: Mano is not worth it at your level. Go to Henesys instead."),
    ("**Blue Snail Shell** (MSEA)\nנופל מ-Blue Snail ברמה 2.", "**Blue Snail Shell** (MSEA)\nנופל מ-Blue Snail ברמה 2."),
    ("Henesys → Ellinia → Sleepywood\nזו הדרך הכי קצרה.", "Henesys → Ellinia → Sleepywood\nזו הדרך הכי קצרה."),
    ("1. Talk to Shanks\n2. Now for the boat, I'll pay 150 mesos\nדברו עם Shanks.",
     "1. Talk to Shanks\n2. Now for the boat, I'll pay 150 mesos\nדברו עם Shanks."),
    ("Kerning City\n\nלכו ל-Kerning City.", "Kerning City\n\nלכו ל-Kerning City."),
    ("לכו ל-Henesys. Let me know if you need more.", "לכו ל-Henesys. Let me know if you need more."),
])
def test_an_english_planning_paragraph_before_a_hebrew_answer_is_dropped(raw, shown):
    from maplehelper.brain import split_meta, streamed_text
    assert split_meta(raw + "\n@@META@@\n{}")[0] == shown
    hebrew = any("֐" <= c <= "׿" for c in shown)     # an English answer is held back only as it streams
    assert streamed_text(raw, hebrew=hebrew) == shown


def test_planning_never_flashes_up_while_a_hebrew_answer_streams():
    from maplehelper.brain import streamed_text
    plan = "This quest is in Kerning City - good, in game. Now for answer, I'll mention it."
    assert streamed_text("This quest is in Kerning", hebrew=True) == ""         # no line yet: wait
    assert streamed_text(plan, hebrew=True) == ""
    assert streamed_text(plan + "\n\n", hebrew=True) == ""                         # planning: wait for the Hebrew
    assert streamed_text(plan + "\n\n**Stranger's Identity** ב-", hebrew=True) == "**Stranger's Identity** ב-"
    assert streamed_text("**Blue Snail** (MSEA)\n", hebrew=True) == "**Blue Snail** (MSEA)"   # a name line shows
    assert streamed_text("This quest is in Kerning", hebrew=False) == "This quest is in Kerning"   # English: as is
    # a short lead-in waits too (it showed, then went when the rest of the planning came), and is dropped
    assert streamed_text("Let me check.\n", hebrew=True) == ""
    assert streamed_text("Let me check.\nI'll mention Mano as the target now", hebrew=True) == ""
    assert streamed_text("Let me check the data.\nמאנו נמצא בחוף.", hebrew=True) == "מאנו נמצא בחוף."


def test_an_english_answer_keeps_its_first_paragraph():
    """strip_lead_in is for a Hebrew answer: an English one quoting a Hebrew name lost everything before it."""
    from maplehelper.brain import split_meta
    raw = ("Let me explain: Mano spawns at Thicket Around the Beach III every hour or so.\n"
           "In Hebrew the map is called 'סבך ליד החוף'.\n@@META@@\n{}")
    assert split_meta(raw, hebrew=False)[0].startswith("Let me explain: Mano spawns")
    assert split_meta(raw, hebrew=True)[0].startswith("In Hebrew")


def test_the_ai_is_told_not_to_narrate():
    from maplehelper.brain import REPLY_RULES
    assert "never narrate your process or plans" in REPLY_RULES


def test_guide_summaries_go_through_the_active_provider(kb):
    from maplehelper.brain import Brain
    b = Brain(kb, provider="codex")
    seen = {}

    def fake(instructions, text, timeout=90):
        seen.update(instructions=instructions, text=text, timeout=timeout)
        return "• Hunt snails"
    b.backend.summarize = fake
    assert b.summarize_guide("guide/1", "x" * 70000, "he") == "• Hunt snails"
    assert "Hebrew" in seen["instructions"] and len(seen["text"]) == 60000 and seen["timeout"] == 120


def test_only_claude_has_a_lighter_saver_model():
    assert providers.get("claude").saver_model == "haiku" and providers.get("claude").reports_usage
    assert providers.get("codex").saver_model is None and providers.get("codex").reports_usage   # read on demand


def test_switching_provider_swaps_the_backend(kb):
    from maplehelper.brain import Brain
    b = Brain(kb, provider="claude")
    assert isinstance(b.backend, claude.ClaudeBackend)
    b.provider = "codex"
    assert isinstance(b.backend, codex.CodexBackend)


def test_chatgpt_account_shows_its_email(monkeypatch):
    """`codex login status` has no email; the app-server's account/read has it."""
    monkeypatch.setattr(codex, "app_server", lambda method, params=None, timeout=20:
                        {"account": {"type": "chatgpt", "email": "p@x.com", "planType": "plus"}}
                        if method == "account/read" else None)
    assert codex.account_email() == "p@x.com"
    monkeypatch.setattr(codex, "app_server", lambda *a, **k: {"account": {"type": "apiKey"}})
    assert codex.account_email() is None


def test_model_names_and_chatgpt_list(monkeypatch):
    from maplehelper.providers.base import model_name
    assert model_name("claude-sonnet-4-5-20250929") == "Sonnet 4.5" and model_name("claude-opus-5") == "Opus 5"
    assert model_name("gpt-6.1-sol") == "GPT-6.1-Sol"
    monkeypatch.setattr(codex, "app_server", lambda method, params=None, timeout=20: {"data": [
        {"id": "gpt-6.1-sol", "displayName": "GPT-6.1-Sol", "isDefault": True},
        {"id": "gpt-5.5", "displayName": "GPT-5.5"}]})
    assert codex.Codex().models() == [(None, "GPT-6.1-Sol"), ("gpt-6.1-sol", "GPT-6.1-Sol"), ("gpt-5.5", "GPT-5.5")]
    monkeypatch.setattr(codex, "app_server", lambda *a, **k: None)
    assert codex.Codex().models() == [(None, "")]          # offline: just "OpenAI's default"


def test_codex_runs_without_the_store_alias_folder(monkeypatch):
    """Codex's sandbox can't start the Store's pwsh.exe app alias: keep that folder off its PATH."""
    monkeypatch.setattr(codex.sys, "platform", "win32")
    monkeypatch.setattr(codex.os, "pathsep", ";")            # Windows' separator, on any CI runner
    alias = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps"
    monkeypatch.setenv("PATH", ";".join([r"C:\Windows\System32", alias, r"C:\tools"]))
    path = codex.env()["PATH"].split(codex.os.pathsep)
    assert alias not in path and r"C:\Windows\System32" in path and r"C:\tools" in path


class TestSignIn:
    def test_login_runs_without_a_window_and_ends_a_waiting_one(self, monkeypatch):
        started = []

        class FakeProc:
            def __init__(self, cmd, **kw):
                self.cmd, self.kw, self.killed, self.stdout = cmd, kw, False, iter([b"Starting login"])
                started.append(self)

            def poll(self):
                return None

            def wait(self):
                return 0

            def kill(self):
                self.killed = True
        monkeypatch.setattr(base.subprocess, "Popen", FakeProc)
        first = base.open_login("codex.exe", ["login"])
        assert first.cmd == ["codex.exe", "login"]
        assert first.kw["creationflags"] == base.CREATE_NO_WINDOW and first.kw["stdin"] == base.subprocess.DEVNULL
        base.open_login("codex.exe", ["login"])     # a second click
        assert first.killed                          # frees the port the first one waits on
        base.stop_login()
        assert started[1].killed

    def test_login_failed_only_after_a_non_zero_exit(self):
        class P:
            def __init__(self, rc):
                self.returncode = rc

            def poll(self):
                return self.returncode
        assert base.login_failed(P(1)) and not base.login_failed(P(0))
        assert not base.login_failed(P(None)) and not base.login_failed(None)

    def test_login_that_cannot_start_returns_none(self, monkeypatch):
        def boom(*_a, **_k):
            raise OSError("Access is denied")
        monkeypatch.setattr(base.subprocess, "Popen", boom)
        monkeypatch.setattr(base.sys, "platform", "win32")
        assert base.open_login("codex.exe", ["login"]) is None

    @pytest.mark.parametrize("mod,prov", [(codex, "codex"), (claude, "claude")])
    def test_a_cli_windows_cannot_start_counts_as_not_installed(self, mod, prov, monkeypatch):
        # the Store app's codex.exe can refuse to start from outside the app: offer the installer, not a
        # sign-in button that does nothing
        def boom(*_a, **_k):
            raise PermissionError(13, "Access is denied")
        monkeypatch.setattr(mod, "find_codex" if prov == "codex" else "find_claude", lambda: "x.exe")
        monkeypatch.setattr(mod.subprocess, "run", boom)
        assert providers.get(prov).account()["status"] == "not_installed"


def test_claude_found_beside_an_npm_shim(tmp_path, monkeypatch):
    """A custom npm prefix: PATH has claude.cmd, the real claude.exe is in node_modules next to it."""
    import sys

    from maplehelper.providers import base, claude
    if sys.platform != "win32":
        return
    exe = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(claude, "find_windows_exe", lambda *a: None)
    monkeypatch.setattr(base.shutil, "which", lambda _n: str(tmp_path / "claude.cmd"))
    assert claude.find_claude() == str(exe)


class TestInstaller:
    """The official installers run in the background; the app shows their progress and errors (no console)."""

    def test_commands(self):
        win = base.installer_command("irm https://x/install.ps1 | iex", "curl x | bash", platform="win32")
        assert win[:2] == ["powershell", "-NoProfile"] and "-NonInteractive" in win
        assert "irm https://x/install.ps1 | iex" in win[-1] and "UTF8" in win[-1] and win[-1].endswith("exit 0")
        assert base.installer_command("w", "curl x | bash", platform="darwin") == \
            ["/bin/bash", "-c", "set -o pipefail; curl x | bash"]

    @pytest.mark.skipif(__import__("sys").platform != "win32", reason="runs PowerShell")
    @pytest.mark.parametrize("cmd,code,last", [
        ("Write-Output 'Downloading...'; Write-Output 'Installed!'", 0, "Installed!"),
        ("throw 'Failed to download manifest.'", 1, "ERROR: Failed to download manifest."),
        ("Write-Error 'Installation failed (exit code 3)'; exit 3", 3, "ERROR: Installation failed (exit code 3)"),
        ("Set-StrictMode -Version Latest; Write-Output 'done'", 0, "done"),     # strict mode left on (agy's)
    ])
    def test_runs_hidden_and_reports(self, cmd, code, last):
        inst = base.Installer(cmd, "true")
        assert inst.done.wait(60)
        assert inst.code == code and inst.status() == last


def test_every_provider_is_told_to_stay_on_the_game():
    """Claude declined off-topic questions by itself; ChatGPT and Gemini answered them ("how old is <politician>"),
    so the scope is spelled out, in the instructions and in the reminder sent with every question."""
    from maplehelper.brain import REPLY_RULES, SYSTEM_PROMPT
    assert "Scope: you help only with MapleStory Classic" in SYSTEM_PROMPT
    assert "which AI and model answers" in SYSTEM_PROMPT           # questions about the app itself are fine
    assert "Only MapleStory Classic" in REPLY_RULES


def test_codex_lead_in_from_a_stopped_run_is_no_answer():
    """Without turn.completed (timeout, cancel, quit) the last message is a lead-in, not the answer."""
    r = codex.parse_events(events({"type": "item.completed", "item": {"type": "agent_message",
                                                                      "text": "I'll grep drops.tsv."}}))
    assert r.error == "no_result"


def test_codex_runs_with_its_extras_switched_off():
    c = codex.codex_command("codex", "C:/kb", "x", platform="linux")
    assert 'web_search="disabled"' in c
    for f in ("apps", "browser_use", "computer_use", "image_generation", "multi_agent", "plugins", "hooks"):
        assert c[c.index(f) - 1] == "--disable"
    assert c[-1] == "-"


def test_claude_never_passes_a_stray_credential(monkeypatch):
    for k in claude.FOREIGN_AUTH:
        monkeypatch.setenv(k, "leftover")
    e = claude.env()
    assert not any(k in e for k in claude.FOREIGN_AUTH)
    assert claude.env("sk-ant-1")["ANTHROPIC_API_KEY"] == "sk-ant-1"


def test_an_api_key_without_credit_says_so():
    assert base.classify_error("Your credit balance is too low to access the Anthropic API") == "no_credit"


@pytest.mark.parametrize("question,ui,lang", [
    ("where do I hunt snails?", "he", "English"),        # an English player in a Hebrew app: English
    ("איפה מוצאים חלזונות?", "en", "Hebrew"),
    ("[about Mano] 42", "he", "Hebrew"),                 # nothing to tell by: the app's language
    ("42?", "en", "English"),
    ("which quests reward scrolls?", "he", "English"),   # a short English question: English too
    ("SAUNA ROB", "he", "Hebrew"),                      # a name alone is no sentence: the app's language
    ("Red Snail", "he", "Hebrew"),
])
def test_the_answer_language_follows_the_question(question, ui, lang):
    from maplehelper.brain import reply_language
    assert reply_language(question, ui) == lang


@pytest.fixture(scope="module")
def real_kb():
    from pathlib import Path

    from maplehelper.kb import KnowledgeBase
    root = Path(__file__).resolve().parent.parent / "data" / "kb"
    if not (root / "index.json").exists():
        pytest.skip("no real knowledge base")
    return KnowledgeBase(root)


@pytest.mark.parametrize("question,lang", [
    # a game name alone, its "of" / "to" no English sentence: the app's language
    ("Valley of Death", "Hebrew"), ("Return Scroll to Henesys", "Hebrew"), ("Piece of Ice", "Hebrew"),
    ("The Magic Rock", "Hebrew"), ("Red Potion", "Hebrew"),
    ("where is Valley of Death", "English"), ("how much is Red Potion", "English"),
    ("which quests reward scrolls?", "English"), ("where do I hunt snails?", "English"),
])
def test_a_game_name_alone_answers_in_the_apps_language(real_kb, question, lang):
    from maplehelper.brain import reply_language
    assert reply_language(question, "he", real_kb) == lang


def test_an_english_question_is_answered_in_english_even_with_hebrew_context(kb, tmp_path):
    """A player wrote in English and got Hebrew: earlier session summaries in Hebrew pulled the answer along."""
    from maplehelper.brain import build_prompt
    from maplehelper.store import History

    class Hist(History):
        def __init__(self):
            pass

        def summaries(self):
            return ["השחקן שאל על חלזונות והתאמן ב-Henesys."]

        def conversation(self):
            return [{"role": "user", "text": "מה נשמע"}, {"role": "assistant", "text": "הכל טוב"}]
    p = build_prompt("where do Red Snails spawn?", None, Hist(), kb, False, ui_lang="he")
    assert "Reply in English, whatever language the context above is in." in p
    p = build_prompt("where do Red Snails spawn?", None, Hist(), kb, False, kb_context=False, ui_lang="he")
    assert "Reply in English" in p


def test_the_ai_is_told_what_it_runs_on(kb):
    """Our instructions replace each CLI's own: Grok then didn't know its model ("not shown in this session")."""
    from maplehelper.brain import Brain
    b = Brain(kb, provider="grok")
    assert b.system_prompt().endswith("You run on Grok.")
    b.last_model = "grok-4.7"
    assert b.system_prompt().endswith("You run on Grok, model Grok 4.7.")   # PRV-19: readable
    b.provider = "claude"
    b.model = "sonnet"
    assert b.system_prompt().endswith("You run on Claude, model sonnet.")


def test_cli_output_in_the_log_carries_no_email_or_link():
    # LIF-12: CLI stderr went into the log unmasked, and the log ships in "Report a problem"
    from maplehelper.providers.base import scrub
    out = scrub("Not logged in as player.one+x@gmail.com, see https://claude.ai/login?code=abc then retry")
    assert "gmail" not in out and "code=abc" not in out and "<email>" in out and "<link>" in out


def test_the_warm_claude_process_is_replaced_when_its_instructions_change(kb_copy):
    """The warm process was kept while its instructions had gone stale (no model name before the first answer, an
    older official facts note): the instructions are part of what it must match now (audit PRV-17)."""
    from maplehelper.brain import Brain
    from maplehelper.kb import KnowledgeBase
    b = Brain(KnowledgeBase(kb_copy), provider="claude")
    before = b.backend._config()
    assert b.backend._config() == before
    b.last_model = "claude-sonnet-5"
    assert b.backend._config() != before


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="the tree kill is Windows'")
def test_stopping_a_cli_stops_what_it_started():
    """kill() ended the CLI only: its rg / PowerShell children ran on, and one holding the output open kept a reader
    waiting (audit PRV-8)."""
    import ctypes
    import subprocess
    import sys
    child = "import time; time.sleep(60)"
    parent = (f"import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', {child!r}]); "
              "print(p.pid, flush=True); p.wait()")
    p = subprocess.Popen([sys.executable, "-c", parent], stdout=subprocess.PIPE)
    pid = int(p.stdout.readline())

    def alive(pid):
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                             # STILL_ACTIVE
    assert alive(pid)
    base.kill(p)
    p.wait(timeout=10)
    for _ in range(50):
        if not alive(pid):
            break
        time.sleep(0.1)
    assert not alive(pid)
