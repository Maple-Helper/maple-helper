"""Z.AI and Muse Spark through Oh My Pi: the answer in omp's JSON events, errors, plan usage, the locked
environment, the run's command line and its key (no real omp calls; the real omp was checked live with a Z.AI key
and a Muse Code sign-in while building this)."""
import json
import subprocess
from pathlib import Path

import pytest

from maplehelper import providers
from maplehelper.providers import omp


def lines(*evs):
    return [json.dumps(e) + "\n" for e in evs]


def start(role="assistant"):
    return {"type": "message_start", "message": {"role": role, "content": []}}


def text_delta(t):
    return {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": t}}


def end(text="", tools=0, model="glm-5.3", cost=0.001, stop="stop", status=None, message=None):
    content = [{"type": "toolCall", "id": f"c{i}", "name": "grep", "arguments": {}} for i in range(tools)]
    if text:
        content.append({"type": "text", "text": text})
    m = {"role": "assistant", "content": content, "model": model, "usage": {"cost": {"total": cost}},
         "stopReason": stop}
    if status is not None:
        m.update(errorStatus=status, errorMessage=message or "")
    return {"type": "message_end", "message": m}


AGENT_END = {"type": "agent_end", "messages": []}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "omp-home"
    monkeypatch.setattr(omp, "home", lambda: h)
    return h


def test_answer_is_the_last_message_after_its_tool_calls():
    streamed = []
    a = omp.parse(lines(
        {"type": "session"}, start(), text_delta("Let me check the files."), end("Let me check the files.", tools=2),
        start(), text_delta("Snail drops "), text_delta("Snail Shell."), end("Snail drops Snail Shell.", cost=0.002),
        AGENT_END, end("never read: the run ended")),
        lambda ans, ev: ev is not None and streamed.append(ans.text))
    r = omp.to_result(a, "", a.ended)
    assert r.text == "Snail drops Snail Shell." and r.error is None
    assert r.model == "glm-5.3" and r.tool_calls == 2 and r.turns == 2 and r.cost_usd == pytest.approx(0.003)
    # the lead-in streamed, then the answer started over from nothing
    assert "Snail drops " in streamed and streamed[-1] == "Snail drops Snail Shell."


@pytest.mark.parametrize("status,message,code", [
    (401, "Authentication Failed", "not_logged_in"),         # a wrong Z.AI key
    (402, "402 Billing verification failed. Please check your payment method.", "no_credit"),   # Meta, no billing
    (429, "Too many requests", "usage_limit"),
    (500, "Unable to connect. ECONNREFUSED", "offline"),
    (500, "Internal error", "api_error"),
])
def test_errors_from_omp(status, message, code):
    a = omp.parse(lines(start(), end(stop="error", status=status, message=message), AGENT_END))
    assert omp.to_result(a, "", True).error == code


def test_no_answer_says_why_from_stderr():
    a = omp.parse(lines({"type": "session"}))
    assert omp.to_result(a, 'No API key found for provider "zai"', False).error == "not_logged_in"
    assert omp.to_result(a, "", False).error == "no_result"


ZAI_USAGE = {"reports": [{"provider": "zai", "limits": [
    {"id": "zai:features:zread:1mo", "window": {"durationMs": 2592000000, "resetsAt": 1}, "amount": {"usedFraction": 0.9}},
    {"id": "zai:features:search:5h", "window": {"durationMs": 18000000, "resetsAt": 1}, "amount": {"usedFraction": 0.9}},
    {"id": "zai:tokens:5h", "window": {"durationMs": 18000000, "resetsAt": 1791315052601},
     "amount": {"usedFraction": 0.02}}]}]}
MUSE_USAGE = {"reports": [{"provider": "muse-code", "limits": [
    {"id": "300m", "window": {"durationMs": 18000000, "resetsAt": 1791315600000}, "amount": {"usedFraction": 0.03}},
    {"id": "1w", "window": {"durationMs": 604800000, "resetsAt": 1791763200000}, "amount": {"usedFraction": 1.4}}]}]}


def test_plan_usage_windows():
    assert omp.parse_usage(ZAI_USAGE, "zai") == {"five_hour": {"used": 0.02, "resets": 1791315052.601}}
    assert omp.parse_usage(MUSE_USAGE, "muse-code") == {"five_hour": {"used": 0.03, "resets": 1791315600.0},
                                                        "seven_day": {"used": 1.0, "resets": 1791763200.0}}
    assert omp.parse_usage(MUSE_USAGE, "zai") is None          # another provider's report
    assert omp.parse_usage({"error": "x"}, "zai") is None


def test_env_keeps_the_players_own_setup_out(home, monkeypatch):
    for k in ("ZAI_API_KEY", "MODEL_API_KEY", "OMP_PROFILE", "PI_CODING_AGENT_DIR", "PI_SMOL_MODEL", "ANTHROPIC_BASE_URL",
              "OPENAI_API_KEY"):
        monkeypatch.setenv(k, "leftover")
    e = omp.env()
    assert e["PI_CODING_AGENT_DIR"] == str(home / "agent")
    assert not {"ZAI_API_KEY", "MODEL_API_KEY", "OMP_PROFILE", "PI_SMOL_MODEL", "ANTHROPIC_BASE_URL",
                "OPENAI_API_KEY"} & set(e)
    assert omp.env(zai_key="z.k")["ZAI_API_KEY"] == "z.k" and omp.env(meta_key="LLM_k")["MODEL_API_KEY"] == "LLM_k"


def test_command_takes_no_question_and_guards_its_tools():
    c = omp.command("omp.exe", "zai/glm-5.3", "C:/run/instructions.md", "C:/agent/guard.ts", ["C:/run/s-0.jpg"])
    assert c[c.index("--model") + 1] == "zai/glm-5.3" and c[c.index("--tools") + 1] == "read,grep,glob"
    assert c[c.index("--system-prompt") + 1] == "C:/run/instructions.md" and c[c.index("-e") + 1] == "C:/agent/guard.ts"
    assert c[-1] == "@C:/run/s-0.jpg" and "--no-session" in c and "--no-extensions" in c
    light = omp.command("omp.exe", "zai/glm-5.3", "i.md", "g.ts", tools=False)
    assert "--no-tools" in light and "-e" not in light and "--tools" not in light


def test_model_ids():
    zai, muse = providers.get("zai"), providers.get("muse")
    assert zai.model_id(None, "k") == "zai/glm-5.3"
    assert muse.model_id(None, None) == "muse-code/muse-spark-1.3-contributor"      # the Muse Code sign-in
    assert muse.model_id(None, "LLM_k") == "meta/muse-spark-1.3-contributor"        # Meta's Model API


@pytest.mark.parametrize("out,status,email", [
    ("1. player@example.com\n", "ok", "player@example.com"),
    ('No active credential found for provider "muse-code".\nConfigured providers: zai\n', "logged_out", None),
    ("error: getaddrinfo ENOTFOUND auth.meta.com\n", "offline", None),
])
def test_muse_account(out, status, email):
    assert omp.parse_account(out) == {"status": status, "email": email}


class FakeBrain:
    def __init__(self, kb, api_key=None):
        self.kb = type("KB", (), {"root": kb})()
        self.api_key, self.model = api_key, None

    def system_prompt(self):
        return "You are Maple Helper."


class FakeProc:
    """omp answering one question: what it was asked comes in on stdin."""
    calls: list = []

    def __init__(self, cmd, **kw):
        FakeProc.calls.append((cmd, kw))
        self.got = bytearray()
        self.stdin = self
        self.stdout = iter(lines(start(), text_delta("Hi."), end("Hi."), AGENT_END))
        self.stderr = type("E", (), {"readline": lambda s: b""})()
        self.returncode = 0
        shots = [Path(a[1:]) for a in cmd if a.startswith("@")]
        FakeProc.seen = {"shots": [s.read_bytes() for s in shots],
                         "instructions": Path(cmd[cmd.index("--system-prompt") + 1]).read_text(encoding="utf-8")}

    def write(self, b):
        self.got += b

    def close(self):
        FakeProc.prompt = bytes(self.got).decode("utf-8")

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def test_a_question_runs_on_the_stored_zai_key(home, tmp_path, monkeypatch):
    FakeProc.calls = []
    monkeypatch.setattr(omp, "find_omp", lambda: "omp.exe")
    monkeypatch.setattr(omp.Zai, "load_api_key", lambda self: "stored.key")
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    b = omp.OmpBackend(FakeBrain(tmp_path), providers.get("zai"))
    r = b.run("שאלה ארוכה " * 4000, [b"jpeg-1", b"jpeg-2"])
    assert r.text == "Hi." and r.error is None
    cmd, kw = FakeProc.calls[0]
    # app.py hands the backend no key when key mode is off: a key-only AI uses the stored one anyway
    assert kw["env"]["ZAI_API_KEY"] == "stored.key" and kw["cwd"] == str(tmp_path)
    assert FakeProc.prompt == "שאלה ארוכה " * 4000 and not any("שאלה" in a for a in cmd)     # stdin, never argv
    assert FakeProc.seen == {"shots": [b"jpeg-1", b"jpeg-2"], "instructions": "You are Maple Helper."}
    roots = json.loads((home / "agent" / omp.GUARD_ROOTS).read_text(encoding="utf-8"))["roots"]
    assert roots == [str(tmp_path.resolve())]
    assert not list((home / "shots").glob("run-*"))           # the screenshots went with the run


def fake_omp_token(signed_in: set):
    """`omp token <provider> [-l]` of the home the env names: ours (PI_CODING_AGENT_DIR set by env()) is empty; the
    player's own is signed in to the providers in signed_in (Muse Code by OAuth, Z.AI by a key)."""
    def run(args, env=None, **kw):
        ours = env.get("PI_CODING_AGENT_DIR") == str(omp.agent_dir())
        provider, listing = args[2], "-l" in args
        out, code = f'No active credential found for provider "{provider}".\n', 1
        if not ours and provider in signed_in:
            out, code = ("1. player@example.com\n", 0) if provider == "muse-code" else ("", 1) if listing else \
                ("their.key\n", 0)
        return subprocess.CompletedProcess(args, code, out.encode(), b"")
    return run


@pytest.fixture
def omp_found(home, monkeypatch):
    monkeypatch.setattr(omp, "find_omp", lambda: "omp.exe")
    monkeypatch.setattr(omp.Zai, "load_api_key", lambda self: None)
    monkeypatch.setattr(omp, "_player", {})
    monkeypatch.setattr(omp, "_own_account", {})


def test_zai_with_no_key_anywhere_never_starts_omp(omp_found, tmp_path, monkeypatch):
    FakeProc.calls = []
    monkeypatch.setattr(subprocess, "run", fake_omp_token(set()))
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    assert providers.get("zai").account()["status"] == "logged_out"
    assert omp.OmpBackend(FakeBrain(tmp_path), providers.get("zai")).run("q", None).error == "not_logged_in"
    assert FakeProc.calls == []


@pytest.mark.parametrize("name,provider,email", [("muse", "muse-code", "player@example.com"), ("zai", "zai", None)])
def test_the_players_own_omp_sign_in_counts(omp_found, tmp_path, monkeypatch, name, provider, email):
    """Signed in to omp itself before (`omp login muse-code`, a Z.AI key in omp): connected, and answers run on it."""
    FakeProc.calls = []
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "C:/players/omp")
    monkeypatch.setattr(subprocess, "run", fake_omp_token({provider}))
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    assert providers.get(name).account() == {"status": "ok", "email": email, "source": "omp"}
    assert omp.OmpBackend(FakeBrain(tmp_path), providers.get(name)).run("q", None).text == "Hi."
    cmd, kw = FakeProc.calls[0]
    # the player's omp home and its sign-in, with our settings laid over it
    assert kw["env"]["PI_CODING_AGENT_DIR"] == "C:/players/omp" and "ZAI_API_KEY" not in kw["env"]
    assert cmd[cmd.index("--config") + 1] == str(omp.agent_dir() / "config.yml")
    assert cmd[cmd.index("--model") + 1].startswith(f"{provider}/")
