"""Gemini through the Antigravity CLI (agy): command, locked home, output parsing, sign-in, usage and discovery
(no real CLI calls)."""
import io
import json
from pathlib import Path

import pytest

from maplehelper import providers
from maplehelper.providers import base, gemini


def events(*evs):
    return [json.dumps(e) + "\n" for e in evs]


def step(**kw):
    return {"event": "step_update", "step_update": {"conversation_id": "abc-123", **kw}}


OK = {"event": "result", "result": {"conversation_id": "abc-123", "status": "SUCCESS", "response": "x"}}


@pytest.fixture(autouse=True)
def no_model_list(monkeypatch):
    """The `agy models` list is a module-level cache: every test starts without one, whatever ran before."""
    monkeypatch.setattr(gemini, "_models_cache", [])
    monkeypatch.setattr(gemini, "_models_at", 0.0)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "agy-home"
    monkeypatch.setattr(gemini, "home", lambda: h)
    return h


def test_registered_with_its_own_settings():
    g = providers.get("gemini")
    assert g.name == "gemini" and g.label == "Gemini" and g.model_setting == "gemini_model"
    assert g.reports_usage and g.login_code and not providers.get("claude").login_code
    from maplehelper.store import DEFAULT_SETTINGS
    assert "gemini_model" in DEFAULT_SETTINGS and DEFAULT_SETTINGS["gemini_model"] is None


def test_command_takes_the_question_on_stdin_with_our_agent():
    c = gemini.agy_command("agy.exe", "maplehelper", "gemini-3.8-flash-low")
    assert c[0] == "agy.exe" and "-p" not in c                       # no -p: the question comes on stdin
    assert c[c.index("--agent") + 1] == "maplehelper" and c[c.index("--output-format") + 1] == "stream-json"
    assert "--disable-slash-commands" in c and c[c.index("--model") + 1] == "gemini-3.8-flash-low"
    assert "--model" not in gemini.agy_command("agy", "maplehelper")


def test_home_settings_allow_reading_only_the_knowledge_base_and_screenshots(home, tmp_path):
    gemini.write_settings(tmp_path / "kb", api_key=False)
    s = json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text(encoding="utf-8"))
    assert s["permissions"]["allow"] == [f"read_file({(tmp_path / 'kb').resolve()})",
                                         f"read_file({(home / 'shots').resolve()})"]
    assert s["allowNonWorkspaceAccess"] is False and "modelProvider" not in s
    gemini.write_settings(tmp_path / "kb", api_key=True)
    s = json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text(encoding="utf-8"))
    assert s["modelProvider"] == "gemini"


def test_agent_file_holds_our_instructions_and_only_read_tools():
    text = gemini.agent_text("maplehelper", "You are Maple Helper.", gemini.TOOLS)
    head, body = text.split("---\n")[1], text.split("---\n")[2]
    assert "excludeDefaultComponents: true" in head and "  - view_file\n" in head and "run_command" not in head
    assert body.strip() == "You are Maple Helper."
    assert "tools: []" in gemini.agent_text("maplehelper-summary", "Summarize.", [])


def test_env_gives_the_cli_its_own_home(home, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "leftover")
    e = gemini.env()
    assert e["HOME"] == str(home) and "GEMINI_API_KEY" not in e
    if gemini.sys.platform == "win32":
        assert e["USERPROFILE"] == str(home)
    assert gemini.env("AIzaKEY")["GEMINI_API_KEY"] == "AIzaKEY"


def test_env_gives_the_cli_a_temp_folder_of_its_own(home, tmp_path, monkeypatch):
    """agy's tools read the process's temp folder whatever the allow list says: never the player's %TEMP%."""
    monkeypatch.setenv("TEMP", str(tmp_path / "players-temp"))
    monkeypatch.setenv("TMP", str(tmp_path / "players-temp"))
    e = gemini.env()
    assert e["TEMP"] == e["TMP"] == e["TMPDIR"] == str(gemini.tmp_dir()) and gemini.tmp_dir().is_dir()
    assert gemini.tmp_dir().is_relative_to(home)
    assert gemini.env(tmp=home / "tmp" / "run-1")["TEMP"] == str(home / "tmp" / "run-1")


class TestEvents:
    def test_streams_and_keeps_only_the_text_after_the_last_tool(self):
        seen = []
        text, result, _, conv = gemini.parse_events(events(
            {"event": "init", "conversation_id": "abc-123", "init": {"tools": []}},
            step(step_type="agent_response", state="ACTIVE", text_delta="I'll check. "),
            step(step_type="tool", state="ACTIVE", tool_name="view_file"),
            step(step_type="tool", state="DONE", tool_name="view_file"),
            step(step_type="agent_response", state="ACTIVE", text_delta="Hunt "),
            step(step_type="agent_response", state="DONE", text_delta="snails."),
            OK), seen.append)
        assert text == "Hunt snails." and seen[0] == "I'll check. " and seen[-1] == "Hunt snails."
        assert conv == "abc-123"
        r = gemini.to_result(text, result, "", "gemini-3.8-flash-low")
        assert r.error is None and r.text == "Hunt snails." and r.model == "gemini-3.8-flash-low"

    @pytest.mark.parametrize("error,kind", [
        ("authentication failed or timed out", "not_logged_in"),
        ("Please sign in to continue", "not_logged_in"),
        ("RESOURCE_EXHAUSTED: Five Hour Limit Remaining 0%", "usage_limit"),
        ("dial tcp: lookup cloudcode-pa.googleapis.com: no such host network", "offline"),
    ])
    def test_errors(self, error, kind):
        _, result, _, _ = gemini.parse_events(events(
            {"event": "result", "result": {"status": "ERROR", "error": error}}))
        assert gemini.to_result("", result, "", None).error == kind

    def test_an_answer_that_stopped_at_a_blocked_read(self):
        result = {"status": "SUCCESS", "response": "", "denied_actions": [{"action": "read_file"}]}
        assert gemini.to_result("", result, "", None).error == "denied"
        assert gemini.to_result("", {"status": "SUCCESS", "response": ""}, "", None).error == "no_result"
        assert gemini.to_result("", None, "", None).error == "no_result"


def test_models_usage_and_saver_model():
    out = ("Fetching available models...\ngemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
           "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\ngemini-3.1-pro-high\tGemini 3.1 Pro (High)\n"
           "claude-opus-5-5-low\tClaude Opus 5.5 (Low)\n")
    models = gemini.parse_models(out)
    assert [m for m, _ in models] == ["gemini-3.8-flash-high", "gemini-3.8-flash-low", "gemini-3.1-pro-high"]
    assert gemini.lightest(models) == "gemini-3.8-flash-low" and gemini.lightest([]) is None
    data = {"command": {"data": {"groups": [
        {"name": "Gemini Models", "buckets": [
            {"window": "weekly", "remaining_fraction": 0.99, "reset_time": "2026-10-10T08:49:39Z"},
            {"window": "5h", "remaining_fraction": 0.25, "reset_time": "2026-10-03T13:49:39Z"}]},
        {"name": "Claude and GPT models", "buckets": [{"window": "5h", "remaining_fraction": 0.0}]}]}}}
    u = gemini.parse_usage(data)
    assert round(u["five_hour"]["used"], 2) == 0.75 and round(u["seven_day"]["used"], 2) == 0.01
    assert u["five_hour"]["resets"] == 1791035379.0
    assert gemini.parse_usage({}) is None and gemini.parse_usage(None) is None


def test_forget_removes_the_run_and_keeps_only_the_last_logs(home):
    d = home / ".gemini" / "antigravity-cli"
    for sub in ("conversations", "annotations", "brain/abc-123", "brain/other-1", "log"):
        (d / sub).mkdir(parents=True)
    for f in ("conversations/abc-123.db", "conversations/abc-123.db-wal", "conversations/other-1.db",
              "annotations/abc-123.pbtxt", "brain/abc-123/n.md", *(f"log/cli-2026100{i}.log" for i in range(5))):
        (d / f).write_text("x")
    gemini.forget("abc-123")
    assert sorted(p.name for p in (d / "conversations").iterdir()) == ["other-1.db"]
    assert not (d / "annotations" / "abc-123.pbtxt").exists() and not (d / "brain" / "abc-123").exists()
    assert (d / "brain" / "other-1").exists() and len(list((d / "log").iterdir())) == 3
    gemini.forget("../../etc")             # never a path from outside
    assert (d / "brain" / "other-1").exists()


class Done:
    def __init__(self, out: str, code: int = 0, err: str = ""):
        self.stdout, self.returncode, self.stderr = out.encode(), code, err.encode()


OFFLINE = ('Error: Eligibility check failed: Post "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist": '
           "proxyconnect tcp: dial tcp 127.0.0.1:9: connectex: No connection could be made because the target machine "
           "actively refused it.")


class TestAccount:
    def test_not_installed(self, home, monkeypatch):
        monkeypatch.setattr(gemini, "find_agy", lambda: None)
        assert providers.get("gemini").account()["status"] == "not_installed"

    def test_signed_out_and_in(self, home, monkeypatch):
        monkeypatch.setattr(gemini, "find_agy", lambda: "agy.exe")
        monkeypatch.setattr(gemini, "_run", lambda args, timeout=30: Done(
            "Error: Please sign in to view available models."))
        assert providers.get("gemini").account() == {"status": "logged_out", "email": None}
        monkeypatch.setattr(gemini, "_run", lambda args, timeout=30: Done("gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"))
        assert providers.get("gemini").account() == {"status": "ok", "email": None}
        assert providers.get("gemini").saver_model == "gemini-3.8-flash-low"

    def test_offline_is_not_signed_out(self, home, monkeypatch):
        """Offline, agy ends 1 with only a network error: the player was told to sign in (which fails too)."""
        monkeypatch.setattr(gemini, "find_agy", lambda: "agy.exe")
        monkeypatch.setattr(gemini, "_models_cache", [])
        monkeypatch.setattr(gemini, "_run", lambda args, timeout=30: Done("", 1, OFFLINE))
        g = providers.get("gemini")
        assert g.account() == {"status": "offline", "email": None}
        assert g.models() == [(None, "")] and g.read_limits() is None and gemini.resolve_model(gemini.SAVER_ALIAS) is None
        # signed out says so even with a failing exit code
        monkeypatch.setattr(gemini, "_run", lambda args, timeout=30: Done("", 1, "Error: Please sign in to view "
                                                                                 "available models."))
        assert g.account() == {"status": "logged_out", "email": None}

    def test_cli_that_will_not_start_counts_as_not_installed(self, home, monkeypatch):
        monkeypatch.setattr(gemini, "find_agy", lambda: "agy.exe")
        monkeypatch.setattr(gemini, "_run", lambda args, timeout=30: None)
        assert providers.get("gemini").account()["status"] == "not_installed"

    def test_sign_in_runs_hidden_and_takes_the_code(self, home, monkeypatch):
        """agy opens the Google page itself: the app opening it too made a second tab."""
        seen, opened = {}, []
        monkeypatch.setattr(gemini, "find_agy", lambda: "agy.exe")
        monkeypatch.setattr(base, "open_login", lambda exe, args, env, cwd, keep_stdin: seen.update(
            exe=exe, args=args, env=env, cwd=cwd, keep=keep_stdin) or "proc")
        import webbrowser
        monkeypatch.setattr(webbrowser, "open", opened.append)
        assert providers.get("gemini").login() == "proc"
        assert seen["keep"] and seen["cwd"] == str(home) and seen["env"]["HOME"] == str(home)
        assert opened == []
        typed = []
        monkeypatch.setattr(base, "send_login_input", typed.append)
        providers.get("gemini").submit_login_code("  4/0AXl-code \n")
        assert typed == ["4/0AXl-code\n"]


def test_open_login_keeps_stdin_for_the_code_and_reports_lines(monkeypatch):
    lines = []

    class FakeProc:
        def __init__(self, cmd, **kw):
            self.cmd, self.kw, self.stdout = cmd, kw, iter([b"Please visit the URL\n"])
            self.stdin = io.BytesIO()
            self.stdin.flush = lambda: None
            FakeProc.last = self

        def poll(self):
            return None

        def wait(self):
            return 0

        def kill(self):
            pass
    monkeypatch.setattr(base.subprocess, "Popen", FakeProc)
    typed = []
    monkeypatch.setattr(base, "type_into_console", lambda pid, text: typed.append((pid, text)) or True)
    base.open_login("agy.exe", ["-p", "hi"], cwd="C:/h", keep_stdin=True, on_line=lines.append)
    p = FakeProc.last
    p.pid = 4242
    assert p.kw["stdin"] == base.subprocess.PIPE and p.kw["cwd"] == "C:/h"
    assert base.send_login_input("4/0code\n")
    if base.sys.platform == "win32":
        # Antigravity reads the code from its (hidden) console, not stdin: typed there, Enter as "\r"
        assert typed == [(4242, "4/0code\r")] and p.stdin.getvalue() == b""
    else:
        assert p.stdin.getvalue() == b"4/0code\n"
    import time
    for _ in range(50):
        if lines:
            break
        time.sleep(0.01)
    assert lines == ["Please visit the URL"]
    base.stop_login()
    assert not base.send_login_input("x")          # nothing waiting any more


class TestDiscovery:
    def test_the_official_installer_location(self, tmp_path, monkeypatch):
        exe = tmp_path / "agy" / "bin" / "agy.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(gemini.shutil, "which", lambda _n: None)
        assert gemini.find_windows() == str(exe)
        monkeypatch.setattr(gemini.shutil, "which", lambda _n: "C:/elsewhere/agy.exe")
        assert gemini.find_windows() == "C:/elsewhere/agy.exe"

    def test_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(gemini.shutil, "which", lambda _n: None)
        assert gemini.find_windows() is None


class FakePopen:
    """Records each run and replays canned agy output (one batch per run); checks files while it runs."""
    calls: list = []
    outputs: list = []

    def __init__(self, cmd, **kw):
        self.agent = (gemini.home() / ".gemini" / "config" / "agents" / f"{cmd[cmd.index('--agent') + 1]}.md"
                      ).read_text(encoding="utf-8")
        self.shots = {p.name: p.read_bytes() for p in gemini.shots_dir().rglob("*.jpg")}
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        FakePopen.calls.append(self)
        self.cmd, self.kw = cmd, kw
        self.stdout = iter(line.encode() for line in FakePopen.outputs.pop(0))
        self.stderr = io.BytesIO(b"")
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


ANSWER = events(
    {"event": "init", "conversation_id": "abc-123"},
    step(step_type="agent_response", state="ACTIVE", text_delta="Hunt **Red Snail**.\n@@META@@\n"),
    step(step_type="agent_response", state="DONE", text_delta='{"entities": ["monster/130101"]}'), OK)
DENIED = events({"event": "result", "result": {"status": "SUCCESS", "response": "",
                                               "denied_actions": [{"action": "read_file"}]}})
BAD_MODEL = events({"event": "result", "result": {"status": "ERROR", "error": 'invalid model selection (--model "x")'}})


class TestBackend:
    @pytest.fixture
    def kb(self, kb_copy):
        from maplehelper.kb import KnowledgeBase
        return KnowledgeBase(kb_copy)

    def make(self, kb, monkeypatch, *outputs, api_key=None, model=None):
        FakePopen.calls, FakePopen.outputs = [], [list(o) for o in outputs]
        monkeypatch.setattr(gemini.subprocess, "Popen", FakePopen)
        from maplehelper.brain import Brain
        b = Brain(kb, provider="gemini", api_key=api_key, model=model)
        b.backend.exe = "agy.exe"
        return b

    def test_answer_screenshot_and_instructions(self, kb, home, monkeypatch):
        b = self.make(kb, monkeypatch, ANSWER)
        ans = b.ask("where is Red Snail?", None, None, b"JPEGDATA")
        assert ans.error is None and ans.text == "Hunt **Red Snail**." and ans.entities[0] == "monster/130101"
        p = FakePopen.calls[0]
        assert p.kw["cwd"] == str(kb.root) and p.kw["creationflags"] == base.CREATE_NO_WINDOW
        assert p.kw["env"]["HOME"] == str(home) and "GEMINI_API_KEY" not in p.kw["env"]
        assert p.shots == {"screenshot-0.jpg": b"JPEGDATA"}                  # there while it ran
        assert "<question>" in p.stdin.getvalue().decode()
        assert str(kb.root.resolve()) in p.agent and "  - view_file" in p.agent
        # the screenshot's path (a folder per run) comes with the question: the agent file stays the same
        assert "screenshot-0.jpg" in p.stdin.getvalue().decode() and "screenshot-0.jpg" not in p.agent
        assert not list(gemini.shots_dir().rglob("*.jpg"))                    # and gone after
        run_tmp = Path(p.kw["env"]["TEMP"])                                   # a temp folder of the run's own
        assert run_tmp.parent == gemini.tmp_dir() and p.kw["env"]["TMP"] == str(run_tmp) and not run_tmp.exists()

    def test_the_sync_screenshot_read_opens_only_the_screenshot(self, kb, home, monkeypatch):
        """light (the ⟳ sync, 60 s): with the knowledge-base tools too the agent grepped for over two minutes."""
        b = self.make(kb, monkeypatch, ANSWER)
        assert b.ask("sync", None, None, b"JPEGDATA", light=True).error is None
        p = FakePopen.calls[0]
        assert p.cmd[p.cmd.index("--agent") + 1] == gemini.SHOT_AGENT
        assert "tools:\n  - view_file\nexcludeDefaultComponents" in p.agent
        assert "quick screenshot read" in p.agent and "grep_search," not in p.agent
        assert "screenshot-0.jpg" in p.stdin.getvalue().decode()
        b = self.make(kb, monkeypatch, ANSWER)                       # no screenshot: no tools at all
        b.ask("hi", None, None, None, light=True)
        assert FakePopen.calls[0].cmd[FakePopen.calls[0].cmd.index("--agent") + 1] == gemini.QUICK_AGENT
        assert "tools: []" in FakePopen.calls[0].agent

    def test_the_agent_and_settings_files_are_written_only_when_they_change(self, kb, home, monkeypatch):
        """Each question used to rewrite the agent file (the screenshot's per-run folder was in it)."""
        written = []
        real = gemini.os.replace
        # (the knowledge base's own tables are written the same way before the first question: not counted)
        monkeypatch.setattr(gemini.os, "replace", lambda a, b: (
            Path(b).parent == Path(kb.root) or written.append(Path(b).name), real(a, b)))
        b = self.make(kb, monkeypatch, ANSWER, ANSWER, ANSWER)
        b.ask("where is Red Snail?", None, None, b"JPEGDATA")
        assert sorted(written) == ["maplehelper.md", "settings.json"]
        b.ask("and Blue Snail?", None, None, b"JPEGDATA2")                # another screenshot, another run folder
        assert len(written) == 2
        b.length = "detailed"                                              # other instructions: the agent again
        b.ask("and Mano?", None, None, b"JPEGDATA3")
        assert written[2:] == ["maplehelper.md"]

    def test_api_key(self, kb, home, monkeypatch):
        b = self.make(kb, monkeypatch, ANSWER, api_key="AIzaKEY")
        b.ask("hi", None, None, None)
        assert FakePopen.calls[0].kw["env"]["GEMINI_API_KEY"] == "AIzaKEY"

    def test_a_blocked_read_is_asked_once_more(self, kb, home, monkeypatch):
        b = self.make(kb, monkeypatch, DENIED, ANSWER)
        assert b.ask("hi", None, None, None).text == "Hunt **Red Snail**."
        assert gemini.RETRY_NOTE in FakePopen.calls[1].stdin.getvalue().decode()
        b = self.make(kb, monkeypatch, DENIED, DENIED)
        assert b.ask("hi", None, None, None).error == "no_result" and len(FakePopen.calls) == 2

    def test_a_model_google_dropped_falls_back_to_the_default(self, kb, home, monkeypatch):
        b = self.make(kb, monkeypatch, BAD_MODEL, ANSWER, model="gemini-2.0-gone")
        assert b.ask("hi", None, None, None).error is None
        assert FakePopen.calls[0].cmd[FakePopen.calls[0].cmd.index("--model") + 1] == "gemini-2.0-gone"
        assert "--model" not in FakePopen.calls[1].cmd

    def test_summary_has_no_tools(self, kb, home, monkeypatch):
        # summaries ask for the saver alias: with the list already read it resolves without running `agy models`
        monkeypatch.setattr(gemini, "_models_cache", [("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)")])
        b = self.make(kb, monkeypatch, events(step(step_type="agent_response", state="DONE", text_delta="• Hunt"), OK))
        assert b.backend.summarize("Summarize.", "long text") == "• Hunt"
        p = FakePopen.calls[0]
        assert p.cmd[p.cmd.index("--agent") + 1] == gemini.SUMMARY_AGENT and "tools: []" in p.agent
        assert p.agent.rstrip().endswith("Summarize.")

    def test_not_installed(self, kb, home, monkeypatch):
        b = self.make(kb, monkeypatch)
        b.backend.exe = None
        monkeypatch.setattr(type(providers.get("gemini")), "find_exe", lambda self: None)   # none on this PC
        assert b.ask("hi", None, None, None).error == "not_installed"


def test_model_names():
    assert base.model_name("gemini-3.8-flash-low") == "Gemini 3.8 Flash Low"


def test_install_uses_googles_official_script():
    assert "antigravity.google/cli/install.ps1" in gemini.INSTALL_CMD
    assert "antigravity.google/cli/install.sh" in gemini.INSTALL_CMD_MAC


def test_the_account_email_comes_from_agys_log(home):
    assert gemini.signed_in_email() is None
    log = home / ".gemini" / "antigravity-cli" / "cli.log"
    log.parent.mkdir(parents=True)
    log.write_text("I1003 server_oauth.go:203] OAuth: authenticated successfully as old@gmail.com\n"
                   "I1003 other line\n"
                   "I1003 server_oauth.go:203] OAuth: authenticated successfully as player@gmail.com\n")
    assert gemini.signed_in_email() == "player@gmail.com"


def test_an_api_key_question_does_not_block_the_sign_in(home, tmp_path):
    """modelProvider from an API-key run made agy refuse every sign-in check ("GEMINI_API_KEY is not set")."""
    gemini.write_settings(tmp_path / "kb", api_key=True)
    gemini.sign_in_mode()
    s = json.loads(gemini.settings_path().read_text(encoding="utf-8"))
    assert "modelProvider" not in s and s["permissions"]["allow"]          # the allow-list stays


def test_forget_also_drops_agys_summaries_and_run_files(home):
    d = home / ".gemini" / "antigravity-cli"
    for sub in ("presence", "implicit"):
        (d / sub).mkdir(parents=True)
        (d / sub / "x").write_text("x")
    for name in gemini.RUN_LEFTOVERS:
        (d / name).write_text("x")
    gemini.forget(None)
    assert not any((d / name).exists() for name in gemini.RUN_LEFTOVERS)
    assert not list((d / "presence").iterdir()) and not list((d / "implicit").iterdir())


def test_saver_mode_works_before_the_model_list_is_read(home, monkeypatch):
    monkeypatch.setattr(gemini, "_models_cache", [])
    assert providers.get("gemini").saver_model == gemini.SAVER_ALIAS
    monkeypatch.setattr(gemini, "read_models", lambda max_age=10: [("gemini-3.8-flash-low", "x")])
    assert gemini.resolve_model(gemini.SAVER_ALIAS) == "gemini-3.8-flash-low"
    assert gemini.resolve_model("gemini-3.1-pro-high") == "gemini-3.1-pro-high"
    assert base.model_name(gemini.SAVER_ALIAS) == "Gemini Flash Low"


def test_a_check_that_hangs_is_not_not_installed(home, monkeypatch):
    monkeypatch.setattr(gemini, "find_agy", lambda: "agy.exe")

    def hang(args, timeout=30):
        raise gemini.CheckFailed()
    monkeypatch.setattr(gemini, "_run", hang)
    monkeypatch.setattr(gemini, "_models_cache", [])
    assert providers.get("gemini").account()["status"] == "logged_out"
    assert providers.get("gemini").models() == [(None, "")] and providers.get("gemini").read_limits() is None


def test_signed_out_question_stops_before_agy_opens_a_sign_in(home, monkeypatch, kb_copy):
    from maplehelper.kb import KnowledgeBase
    b = TestBackend().make(KnowledgeBase(kb_copy), monkeypatch,
                           ["Authentication required. Please visit the URL to log in:\n", "  https://accounts...\n"])
    assert b.ask("hi", None, None, None).error == "not_logged_in"


def test_usage_is_read_only_when_signed_in(home, monkeypatch):
    """Signed out, agy answers /usage by opening a Google sign-in in the browser (it did when Settings opened)."""
    calls = []
    monkeypatch.setattr(gemini, "read_models", lambda max_age=10: [])
    monkeypatch.setattr(gemini, "_run", lambda args, timeout=30: calls.append(args))
    assert providers.get("gemini").read_limits() is None and calls == []
