"""Grok through xAI's Grok Build CLI: command, locked home, read guard, output parsing, account and sign-in
(no real CLI calls; the real CLI was checked against a local mock of the xAI API while building this)."""
import io
import json
import subprocess
import sys

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
        assert "screenshot-0.jpg" in instructions and "read_file" in instructions
        assert p.kw["cwd"] == str(kb.root) and p.kw["env"]["GROK_HOME"] == str(home / ".grok")
        assert not list(grok.shots_dir().rglob("*.jpg"))                     # gone after

    def test_the_sync_screenshot_read_opens_only_the_screenshot(self, kb, home, monkeypatch):
        """light (the ⟳ sync, 60 s): read_file for the screenshot, no grep or list_dir in the knowledge base."""
        b = self.make(kb, monkeypatch, stream(START, delta("Lv 13\n@@META@@\n{}"), OK))
        assert b.ask("sync", None, None, b"JPEGDATA", light=True).error is None
        p = FakePopen.calls[0]
        assert p.cmd[p.cmd.index("--disallowed-tools") + 1] == "search_tool,use_tool,grep,list_dir"
        instructions = p.cmd[p.cmd.index("--system-prompt-override") + 1]
        assert "quick screenshot read" in instructions and "screenshot-0.jpg" in instructions
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
