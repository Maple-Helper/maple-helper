"""Grok through xAI's Grok Build CLI: command, locked home, read guard, output parsing, account and sign-in
(no real CLI calls; the real CLI was checked against a local mock of the xAI API while building this)."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from maplehelper import providers
from maplehelper.providers import base, grok


def stream(*evs):
    return [json.dumps(e) + "\n" for e in evs]


def se(event):
    return {"type": "stream_event", "event": event}


def delta(text):
    return se({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})


START = se({"type": "message_start", "message": {"model": "grok-4.6"}})
OK = {"type": "result", "subtype": "success", "is_error": False, "result": "x"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "grok-home"
    monkeypatch.setattr(grok, "home", lambda: h)
    return h


def test_registered_with_its_own_settings():
    g = providers.get("grok")
    assert g.name == "grok" and g.label == "Grok" and g.model_setting == "grok_model" and not g.reports_usage
    from maplehelper.store import DEFAULT_SETTINGS
    assert "grok_model" in DEFAULT_SETTINGS and DEFAULT_SETTINGS["grok_model"] is None


def test_command_is_locked_down():
    c = grok.grok_command("grok.exe", "C:/q.txt", "You are Maple Helper.", "grok-4.6", platform="win32")
    assert c[c.index("--prompt-file") + 1] == "C:/q.txt"
    assert c[c.index("--system-prompt-override") + 1] == "You are Maple Helper."
    assert c[c.index("--tools") + 1] == "read_file,grep,list_dir"
    assert c[c.index("--disallowed-tools") + 1] == "search_tool,use_tool"
    for flag in ("--disable-web-search", "--no-subagents", "--no-plan", "--include-partial-messages"):
        assert flag in c
    assert c[c.index("--permission-mode") + 1] == "dontAsk" and c[c.index("--model") + 1] == "grok-4.6"
    assert "--sandbox" not in c                                     # Windows: the hook guards reads
    assert grok.grok_command("grok", "q", "x", platform="darwin")[-2:] == ["--sandbox", "strict"]
    no_tools = grok.grok_command("grok", "q", "x", tools=False, platform="win32")
    assert no_tools[no_tools.index("--disallowed-tools") + 1] == "search_tool,use_tool,read_file,grep,list_dir"
    shot_only = grok.grok_command("grok", "q", "x", tools=["read_file"], platform="win32")
    assert shot_only[shot_only.index("--disallowed-tools") + 1] == "search_tool,use_tool,grep,list_dir"


def test_env_keeps_the_players_setup_and_other_tools_out(home, monkeypatch):
    for k in ("XAI_API_KEY", "GROK_XAI_API_BASE_URL", "GROK_AGENT"):
        monkeypatch.setenv(k, "leftover")
    e = grok.env()
    assert not any(k in e for k in ("XAI_API_KEY", "GROK_XAI_API_BASE_URL", "GROK_AGENT"))
    assert e["GROK_HOME"] == str(home / ".grok") and e["HOME"] == str(home)
    for k in ("GROK_CLAUDE_HOOKS_ENABLED", "GROK_CLAUDE_MCPS_ENABLED", "GROK_CLAUDE_SKILLS_ENABLED",
              "GROK_CURSOR_MCPS_ENABLED", "GROK_MEMORY", "GROK_TURN_SUMMARY", "GROK_TELEMETRY_ENABLED"):
        assert e[k] == "0"
    assert grok.env("xai-KEY")["XAI_API_KEY"] == "xai-KEY"


def hook_command(home):
    hook = json.loads((home / ".grok" / "hooks" / "maplehelper.json").read_text(encoding="utf-8"))
    assert "matcher" not in hook["hooks"]["PreToolUse"][0]
    return hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def guard_decides(home, cwd, path, tool="read_file", key="target_file", raw=None):
    """What the real hook answers for one read, run as Grok runs it: allow / deny / fail. The compiled guard is a bare
    path Grok starts directly; the PowerShell one is a command line (Windows PowerShell 5.1 running the script)."""
    cmd = hook_command(home)
    script = str(home / ".grok" / "maplehelper-guard.ps1")
    if grok.BARE_PATH.fullmatch(cmd):
        args = [cmd]
    else:
        assert script in cmd
        args = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script]
    data = raw if raw is not None else json.dumps({"cwd": str(cwd), "tool_name": tool, "tool_input": {key: path}},
                                                  ensure_ascii=False).encode("utf-8")     # Grok sends raw UTF-8
    r = subprocess.run(args, input=data, capture_output=True)
    if r.returncode == 2 and b'"permissionDecision":"deny"' in r.stdout:
        return "deny"
    # Grok lets anything but an explicit deny through: a hook that errors (exit 1, no JSON) is an open door
    return "allow" if r.returncode == 0 else f"fail {r.returncode}: {r.stderr[-300:]!r}"


@pytest.fixture(params=["compiled", "powershell"])
def guard_kind(request, monkeypatch):
    """Every guard test runs against both guards: the compiled one, and the PowerShell one it falls back to."""
    if request.param == "powershell":
        monkeypatch.setattr(grok, "guard_exe", lambda kb_root: None)
    return request.param


@pytest.mark.skipif(sys.platform != "win32", reason="the read guard is a Windows hook")
def test_read_guard_allows_only_the_knowledge_base_and_the_screenshot(home, tmp_path, guard_kind):
    kb = tmp_path / "kb"
    kb.mkdir()
    grok.write_guard(kb)
    assert hook_command(home).endswith(".exe" if guard_kind == "compiled" else '.ps1"')

    def decide(path, tool="read_file", key="target_file"):
        return guard_decides(home, kb, path, tool, key)
    assert decide("henesys.md") == "allow" and decide(str(kb / "pages" / "a.md")) == "allow"
    assert decide(str(grok.shots_dir() / "run-1" / "screenshot-0.jpg")) == "allow"
    assert decide(r"C:\Users\Someone\secret.txt") == "deny" and decide(r"..\settings.json") == "deny"
    assert decide(str(kb) + r"\..\x.txt") == "deny" and decide(r"C:\Windows", "list_dir", "target_directory") == "deny"
    assert decide(r"C:\x", "grep", "path") == "deny"


@pytest.mark.skipif(sys.platform != "win32", reason="the read guard is a Windows hook")
def test_read_guard_checks_every_key_that_names_a_path(home, tmp_path, guard_kind):
    """Only target_file / target_directory / path were checked: the same read under file_path, filePath, paths or a
    glob went through (audit SEC-1). Every path-like key is checked now; a grep pattern is no path."""
    kb = tmp_path / "kb"
    kb.mkdir()
    grok.write_guard(kb)
    win = "C:\\Windows\\win.ini"

    def decide(tool_input, cwd=kb):
        return guard_decides(home, kb, None, raw=json.dumps(
            {"cwd": str(cwd), "tool_name": "read_file", "tool_input": tool_input}, ensure_ascii=False).encode("utf-8"))
    for ti in ({"file_path": win}, {"filePath": win}, {"target_file": "", "file_path": win},
               {"target_file": None, "file_path": win}, {"target_file": "a.md", "file_path": win},
               {"pattern": "x", "paths": ["C:\\Users"]}, {"pattern": "x", "paths": ["a.md", "C:\\Users"]},
               {"pattern": "x", "glob": "C:/Users/**"}, {"pattern": "x", "glob": "../**"},
               {"pattern": "x", "glob_pattern": "C:\\*"}, {"options": {"dir": "C:\\Users"}},
               {"target_file": "~/.ssh/id_rsa"}, {"FILE": win}):
        assert decide(ti) == "deny", ti
    for ti in ({"file_path": "a.md"}, {"filePath": str(kb / "pages" / "b.md")}, {"paths": ["a.md", "pages"]},
               {"pattern": "C:\\\\Users", "glob": "**/*.md"}, {"pattern": "\\d+"},
               {"path": "pages", "include_hidden": True, "max_depth": 2}):
        assert decide(ti) == "allow", ti
    assert decide({"pattern": "x", "glob": "*.md"}, cwd="C:\\") == "deny"        # a glob runs where Grok runs


@pytest.mark.skipif(sys.platform != "win32", reason="the read guard is a Windows hook")
def test_no_trusted_hook_means_no_file_tools(home, tmp_path, monkeypatch):
    """Grok lets a read through when its hook fails: a PowerShell guard whose quoted path the shell would change
    ($ or ` in the user name) can't be trusted, so the run reads nothing (audit SEC-15)."""
    kb = tmp_path / "kb"
    kb.mkdir()
    monkeypatch.setattr(grok, "guard_exe", lambda kb_root: None)
    assert grok.write_guard(kb) is True
    odd = tmp_path / "a$b" / "grok"
    monkeypatch.setattr(grok, "home", lambda: odd)
    assert grok.write_guard(kb) is False


@pytest.mark.skipif(sys.platform != "win32", reason="the read guard is a Windows hook")
@pytest.mark.parametrize("user", ["גבי", "עמית", "O’Neil‘s ‚x‛", "John Smith"])
def test_read_guard_holds_under_a_hebrew_or_quoted_user_folder(tmp_path, monkeypatch, user, guard_kind):
    """The data folder sits under the Windows user name. ב and ג are the bytes D7 91 / D7 92, which PowerShell 5.1
    read as ‘ ’ (quote marks) in a script without a BOM: the hook broke and Grok could read the whole disk. Other
    Hebrew letters garbled the folders, and Grok couldn't read the knowledge base at all. Curly quotes in a name are
    PowerShell quote marks too."""
    root = tmp_path / user / "AppData" / "Roaming" / "MapleHelper"
    home = root / "grok"
    monkeypatch.setattr(grok, "home", lambda: home)
    kb = root / "kb"
    (kb / "pages").mkdir(parents=True)
    grok.write_guard(kb)
    source = (home / ".grok" / "maplehelper-guard.ps1").read_bytes()
    assert source.startswith(b"\xef\xbb\xbf") and source[3:].isascii()       # a BOM, and no path in the source
    cmd = hook_command(home)
    # Grok hands a command with a space or a quote to its shell, unquoted: the hook broke and the read went through
    # (checked with a mock of the xAI API). The compiled guard goes in only as a bare path (its 8.3 name if need be)
    assert grok.BARE_PATH.fullmatch(cmd) or cmd.startswith("powershell ")

    def decide(path, tool="read_file", key="target_file"):
        return guard_decides(home, kb, path, tool, key)
    assert decide("henesys.md") == "allow" and decide(str(kb / "pages" / "בדיקה.md")) == "allow"
    assert decide(str(grok.shots_dir() / "run-1" / "screenshot-0.jpg")) == "allow"
    assert decide(r"C:\Windows\win.ini") == "deny" and decide(str(root / "history.json")) == "deny"
    assert decide(str(tmp_path / user / ".ssh"), "list_dir", "target_directory") == "deny"


@pytest.mark.skipif(sys.platform != "win32", reason="the read guard is a Windows hook")
def test_read_guard_fails_closed(home, tmp_path, guard_kind):
    """Anything the hook can't make sense of is a deny, never an error (which Grok would let through)."""
    kb = tmp_path / "kb"
    kb.mkdir()
    grok.write_guard(kb)
    assert guard_decides(home, kb, None, raw=b"not json {") == "deny"
    assert guard_decides(home, kb, None, raw=b"") == "deny"
    assert guard_decides(home, kb, None, raw=b'{"cwd": "x"}') == "deny"                 # no tool call at all
    # no path at all: the folder it runs in decides (the knowledge base, where Grok runs)
    assert guard_decides(home, kb, None, raw=json.dumps({"cwd": str(kb), "tool_input": {"pattern": "x"}}).encode()) \
        == "allow"
    assert guard_decides(home, kb, None, raw=json.dumps({"cwd": "C:\\", "tool_input": {"pattern": "x"}}).encode()) \
        == "deny"
    roots = home / ".grok" / grok.GUARD_ROOTS
    roots.write_text("{broken", encoding="utf-8")
    assert guard_decides(home, kb, "henesys.md") == "deny"
    roots.unlink()
    assert guard_decides(home, kb, "henesys.md") == "deny"
    grok.write_guard(kb)                                     # written again when the next question starts
    assert guard_decides(home, kb, "henesys.md") == "allow"


@pytest.mark.skipif(sys.platform != "win32", reason="the read guard is a Windows hook")
def test_compiled_guard_is_stricter_on_odd_paths_and_rebuilt_when_gone(home, tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    grok.write_guard(kb)
    exe = home / ".grok" / grok.GUARD_EXE
    assert hook_command(home) == str(exe)
    odd = {"cwd": str(kb), "tool_name": "read_file", "tool_input": {"target_file": ["C:\\Windows\\win.ini"]}}
    assert guard_decides(home, kb, None, raw=json.dumps(odd).encode()) == "deny"      # a path that isn't text
    exe.unlink()                                          # removed (an antivirus): built again for the next question
    grok.write_guard(kb)
    assert exe.exists() and guard_decides(home, kb, "henesys.md") == "allow"


@pytest.mark.skipif(sys.platform != "win32", reason="the read guard is a Windows hook")
def test_a_compiled_guard_that_fails_its_check_is_not_used(home, tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    kb.mkdir()
    monkeypatch.setattr(grok, "_guard_ok", {})
    monkeypatch.setattr(grok, "_guard_says", lambda exe, cwd, target: (0, b""))       # it lets everything through
    grok.write_guard(kb)
    assert hook_command(home).startswith("powershell ") and guard_decides(home, kb, r"C:\Windows\win.ini") == "deny"
    monkeypatch.setattr(grok, "_csc", lambda: None)                 # no C# compiler: the PowerShell guard too
    (home / ".grok" / grok.GUARD_EXE).unlink()
    monkeypatch.setattr(grok, "_guard_ok", {})
    grok.write_guard(kb)
    assert hook_command(home).startswith("powershell ")


class TestStream:
    def test_streams_and_keeps_the_last_message(self):
        seen = []
        text, result, model = grok.parse_stream(stream(
            {"type": "system", "subtype": "init", "model": "grok-4.6"},
            START, delta("I'll check. "), se({"type": "message_stop"}),
            START, delta("Hunt "), delta("snails."), OK), seen.append)
        assert text == "Hunt snails." and seen[-1] == "Hunt snails." and model == "grok-4.6"
        r = grok.to_result(text, result, "", model)
        assert r.error is None and r.text == "Hunt snails." and r.model == "grok-4.6"

    @pytest.mark.parametrize("errors,kind", [
        (["Not signed in. To authenticate without a browser, run: grok login --device-code"], "not_logged_in"),
        (["rate limit exceeded (429)"], "usage_limit"),
        (["error sending request: dns error: no such host network"], "offline"),
    ])
    def test_errors(self, errors, kind):
        res = {"type": "result", "subtype": "error_during_execution", "is_error": True, "errors": errors}
        assert grok.to_result("", res, "", None).error == kind

    def test_nothing_at_all(self):
        assert grok.to_result("", None, "", None).error == "no_result"
        assert grok.to_result("", dict(OK, result=""), "", None).error == "no_result"


def test_models_and_saver():
    out = "Default model: grok-4.6\n\nAvailable models:\n  * grok-4.6 (default)\n  - grok-4.6-fast\n"
    models = grok.parse_models(out)
    assert [m for m, _ in models] == ["grok-4.6", "grok-4.6-fast"] and grok.lightest(models) == "grok-4.6-fast"


class Done:
    def __init__(self, out, code=0):
        self.stdout, self.stderr, self.returncode = out.encode(), b"", code


class TestAccount:
    def test_statuses(self, home, monkeypatch):
        g = providers.get("grok")
        monkeypatch.setattr(grok, "_models_cache", [])
        monkeypatch.setattr(grok, "find_grok", lambda: None)
        assert g.account()["status"] == "not_installed"
        monkeypatch.setattr(grok, "find_grok", lambda: "grok.exe")
        monkeypatch.setattr(grok, "_run", lambda args, timeout=30: Done("You are not authenticated.\n"))
        assert g.account() == {"status": "logged_out", "email": None}
        monkeypatch.setattr(grok, "_run", lambda args, timeout=30: Done(
            "Signed in as player@x.com\n\nAvailable models:\n  * grok-4.6 (default)\n  - grok-4.6-fast\n"))
        assert g.account() == {"status": "ok", "email": "player@x.com"}
        assert g.saver_model == "grok-4.6-fast"

        def hang(args, timeout=30):
            raise grok.CheckFailed()
        monkeypatch.setattr(grok, "_run", hang)
        assert g.account()["status"] == "logged_out"              # a hang isn't "not installed"

    def test_sign_in_is_the_device_flow_opened_once(self, home, monkeypatch):
        seen, opened = {}, []
        monkeypatch.setattr(grok, "find_grok", lambda: "grok.exe")
        monkeypatch.setattr(base, "open_login", lambda exe, args, env, cwd, on_line: seen.update(
            exe=exe, args=args, env=env, on_line=on_line) or "proc")
        import webbrowser
        monkeypatch.setattr(webbrowser, "open", opened.append)
        assert providers.get("grok").login() == "proc"
        assert seen["args"] == ["login", "--device-auth"] and seen["env"]["GROK_HOME"] == str(home / ".grok")
        url = "https://accounts.x.ai/oauth2/device?user_code=ABCD-1234"
        for line in ("To sign in, open this URL in your browser:", url, "Confirm this code in your browser:"):
            seen["on_line"](line)
        assert opened == []                     # grok opens it itself: the app opening it too made two tabs
        seen["on_line"]("  (Could not open browser automatically — open the URL above manually.)")
        assert opened == [url]                  # only when grok couldn't


class FakePopen:
    calls: list = []
    outputs: list = []

    def __init__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        pf = cmd[cmd.index("--prompt-file") + 1]
        self.prompt_file = Path(pf)
        self.prompt = open(pf, encoding="utf-8").read()
        self.shots = [p.name for p in grok.shots_dir().rglob("*.jpg")]
        FakePopen.calls.append(self)
        self.stdout = iter(line.encode() for line in FakePopen.outputs.pop(0))
        self.stderr = io.BytesIO(b"")
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class TestBackend:
    @pytest.fixture
    def kb(self, kb_copy):
        from maplehelper.kb import KnowledgeBase
        return KnowledgeBase(kb_copy)

    def make(self, kb, monkeypatch, *outputs, api_key=None):
        FakePopen.calls, FakePopen.outputs = [], [list(o) for o in outputs]
        monkeypatch.setattr(grok.subprocess, "Popen", FakePopen)
        monkeypatch.setattr(grok, "guard_exe", lambda kb_root: None)       # the guard has tests of its own
        from maplehelper.brain import Brain
        b = Brain(kb, provider="grok", api_key=api_key)
        b.backend.exe = "grok.exe"
        return b

    def test_answer_with_screenshot(self, kb, home, monkeypatch):
        b = self.make(kb, monkeypatch, stream(START, delta('Hunt **Red Snail**.\n@@META@@\n{"entities": ["monster/130101"]}'), OK))
        ans = b.ask("where is Red Snail?", None, None, b"JPEGDATA")
        assert ans.error is None and ans.text == "Hunt **Red Snail**." and ans.entities[0] == "monster/130101"
        p = FakePopen.calls[0]
        assert "<question>" in p.prompt and p.shots == ["screenshot-0.jpg"]
        instructions = p.cmd[p.cmd.index("--system-prompt-override") + 1]
        # the per-run screenshot path goes with the question: the instructions stay the same (audit PRV-10)
        assert "screenshot-0.jpg" in p.prompt and "screenshot-0.jpg" not in instructions and "read_file" in instructions
        assert p.kw["cwd"] == str(kb.root) and p.kw["env"]["GROK_HOME"] == str(home / ".grok")
        # the question file sits in the run's folder in GROK_HOME (macOS's strict sandbox reads there, audit PRV-6)
        assert p.prompt_file.parent.parent == grok.shots_dir()
        assert not list(grok.shots_dir().rglob("*.jpg")) and not list(grok.shots_dir().rglob("*.txt"))   # gone after

    def test_the_sync_screenshot_read_opens_only_the_screenshot(self, kb, home, monkeypatch):
        """light (the ⟳ sync, 60 s): read_file for the screenshot, no grep or list_dir in the knowledge base."""
        b = self.make(kb, monkeypatch, stream(START, delta("Lv 13\n@@META@@\n{}"), OK))
        assert b.ask("sync", None, None, b"JPEGDATA", light=True).error is None
        p = FakePopen.calls[0]
        assert p.cmd[p.cmd.index("--disallowed-tools") + 1] == "search_tool,use_tool,grep,list_dir"
        instructions = p.cmd[p.cmd.index("--system-prompt-override") + 1]
        assert "quick screenshot read" in instructions and "screenshot-0.jpg" in p.prompt
        assert "read it with read_file, grep and list_dir" not in instructions

    def test_api_key_and_summary(self, kb, home, monkeypatch):
        b = self.make(kb, monkeypatch, stream(START, delta("• Hunt"), OK), api_key="xai-1")
        monkeypatch.setattr(grok, "_models_cache", [("grok-4.6", "grok-4.6"), ("grok-4.6-fast", "grok-4.6-fast")])
        assert b.backend.summarize("Summarize.", "long text") == "• Hunt"
        p = FakePopen.calls[0]
        assert p.kw["env"]["XAI_API_KEY"] == "xai-1" and p.cmd[p.cmd.index("--system-prompt-override") + 1] == "Summarize."
        assert "read_file" in p.cmd[p.cmd.index("--disallowed-tools") + 1]       # a summary reads nothing
        assert p.cmd[p.cmd.index("--model") + 1] == "grok-4.6-fast"              # on the light model

    def test_not_installed(self, kb, home, monkeypatch):
        b = self.make(kb, monkeypatch)
        b.backend.exe = None
        monkeypatch.setattr(type(providers.get("grok")), "find_exe", lambda self: None)
        assert b.ask("hi", None, None, None).error == "not_installed"


def test_install_uses_xais_official_script():
    assert grok.INSTALL_CMD == "irm https://x.ai/cli/install.ps1 | iex"
    assert grok.INSTALL_CMD_MAC == "curl -fsSL https://x.ai/cli/install.sh | bash"


def test_the_account_email_comes_from_grok_s_sign_in_file(home, monkeypatch):
    """`grok models` only says "You are logged in with grok.com": the email is in auth.json."""
    assert grok.signed_in_email() is None
    (home / ".grok").mkdir(parents=True)
    (home / ".grok" / "auth.json").write_text(json.dumps({"https://auth.x.ai::abc": {
        "key": "secret", "auth_mode": "oauth", "email": "player@x.com", "refresh_token": "r"}}), encoding="utf-8")
    assert grok.signed_in_email() == "player@x.com"
    monkeypatch.setattr(grok, "_models_cache", [])
    monkeypatch.setattr(grok, "find_grok", lambda: "grok.exe")
    monkeypatch.setattr(grok, "_run", lambda args, timeout=30: Done(
        "You are logged in with grok.com.\n\nAvailable models:\n  * grok-4.7 (default)\n"))
    assert providers.get("grok").account() == {"status": "ok", "email": "player@x.com"}


@pytest.mark.parametrize("text,kind", [
    ("session 0194a401-7f22 error: model returned empty response", None),          # an id, not a 401
    ("request id 7a3429fe failed: internal server error", None),
    ("HTTP 401 Unauthorized", "not_logged_in"),
    ("status: 429 Too Many Requests", "usage_limit"),
    ("Error: xAI API returned status 429.", "usage_limit"),                       # at the end of a sentence
    ("HTTP 401.", "not_logged_in"),
    ("took 429.5 s, then failed", None),
    ("id 429-ab failed", None),
    ("waited 1429 ms", None),
])
def test_status_codes_count_only_on_their_own(text, kind):
    """"401" / "429" anywhere in stderr made an unrelated failure a sign-out or a limit (audit PRV-5)."""
    assert grok.classify(text) == kind


def test_offline_is_not_signed_out(home, monkeypatch):
    """No connection: no model list, and a sign-in would fail too (audit PRV-13)."""
    monkeypatch.setattr(grok, "_models_cache", [])
    monkeypatch.setattr(grok, "find_grok", lambda: "grok.exe")
    monkeypatch.setattr(grok, "_run", lambda args, timeout=30: Done(
        "Error: error sending request for url (https://api.x.ai/v1/models): dns error: no such host", 1))
    assert providers.get("grok").account() == {"status": "offline", "email": None}
    assert providers.get("grok").models() == [(None, "")]


def test_the_offline_log_line_has_no_email_or_link(home, monkeypatch, caplog):
    """`grok models` output is where the account email is read from, and the log ships in a report (review PLT-10)."""
    import logging
    monkeypatch.setattr(grok, "_models_cache", [])
    monkeypatch.setattr(grok, "find_grok", lambda: "grok.exe")
    monkeypatch.setattr(grok, "_run", lambda args, timeout=30: Done(
        "Logged in as player@x.com\nError: error sending request for url (https://api.x.ai/v1/models): dns error", 1))
    with caplog.at_level(logging.WARNING, logger="maplehelper"):
        assert providers.get("grok").account()["status"] == "offline"
    assert "no connection" in caplog.text and "player@x.com" not in caplog.text and "api.x.ai" not in caplog.text
    import inspect

    from maplehelper.providers import codex                # Codex's stall line, the same way
    assert 'STALL_TIMEOUT_S, base.scrub(stderr[-1000:]))' in inspect.getsource(codex)


def test_each_run_s_saved_session_is_removed(home, tmp_path):
    """Grok saves every run under GROK_HOME/sessions (no flag turns it off): the run's own one goes when it ends,
    and old ones a crash left at the next start (audit PRV-3)."""
    import os
    import time
    group = grok.grok_home() / "sessions" / "C%3A%5Ckb"
    mine, other, old = group / "01a10d99-127d-7c42", group / "01a10d99-74d0-7082", group / "0190aaaa-0000-0000"
    for d in (mine, other, old):
        d.mkdir(parents=True)
        (d / "summary.json").write_text("{}", encoding="utf-8")
    os.utime(old, (time.time() - 2 * 86400,) * 2)

    class Ended:
        def wait(self, timeout=None):
            return 0
    grok.drop_session(Ended(), "01a10d99-127d-7c42")
    grok.drop_session(Ended(), "../..")                      # never a path
    for _ in range(100):
        if not mine.exists():
            break
        time.sleep(0.05)
    assert not mine.exists() and other.exists() and old.exists()
    grok.sweep_sessions()
    assert other.exists() and not old.exists()


def test_grok_s_prompt_history_goes_with_the_sessions(home):
    """Grok also writes every run's whole prompt to sessions/<folder>/prompt_history.jsonl, next to the sessions
    (review PLT-1): the run's cleanup and the startup sweep both remove it."""
    import time
    group = grok.grok_home() / "sessions" / "C%3A%5Ckb"
    run = group / "01a10d99-127d-7c42"
    run.mkdir(parents=True)
    history = group / "prompt_history.jsonl"
    history.write_text('{"prompt":"the whole question"}\n', encoding="utf-8")

    class Ended:
        def wait(self, timeout=None):
            return 0
    grok.drop_session(Ended(), "01a10d99-127d-7c42")
    for _ in range(100):
        if not history.exists():
            break
        time.sleep(0.05)
    assert not history.exists() and not run.exists()
    history.write_text("{}\n", encoding="utf-8")
    grok.sweep_sessions()
    assert not history.exists()


def test_startup_sweeps_every_per_run_leftover(home, tmp_path, monkeypatch):
    """A quit mid-answer left Grok's question file (the whole prompt, with the profile and history) in %TEMP% and
    Gemini's temp folders behind; the startup sweep only knew the screenshots (audit LIF-8 / PRV-11)."""
    import os
    import tempfile
    import time

    from maplehelper import app
    from maplehelper.providers import gemini
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(gemini, "home", lambda: tmp_path / "antigravity")
    old = time.time() - 2 * 3600
    left = [temp / "maplehelper-grok-abc.txt", gemini.tmp_dir() / "run-1", grok.shots_dir() / "run-2",
            gemini.shots_dir() / "run-3"]
    fresh = gemini.tmp_dir() / "run-4"                    # a run going on now
    for p in left + [fresh]:
        if p.suffix:
            p.write_text("q", encoding="utf-8")
        else:
            (p / "question.txt").parent.mkdir(parents=True)
            (p / "question.txt").write_text("q", encoding="utf-8")
    for p in left:
        os.utime(p, (old, old))
    app._remove_stray_screenshots()
    assert not any(p.exists() for p in left) and fresh.exists()


def test_the_startup_sweep_runs_off_the_ui_thread():
    """Old versions kept every Grok session: the first sweep after the update froze the UI ~1.7 s per thousand
    (review PLT-8). It only touches files, so it runs on a thread."""
    import inspect

    from maplehelper import app
    src = inspect.getsource(app)
    assert "threading.Thread(target=_remove_stray_screenshots, daemon=True).start()" in src
    assert "singleShot(9000, _remove_stray_screenshots)" not in src
