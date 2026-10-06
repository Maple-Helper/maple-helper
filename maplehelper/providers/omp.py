"""Z.AI (GLM-5.3) and Muse Spark (Meta) through Oh My Pi, the open-source AI coding CLI (`omp`).

Each question runs `omp -p --mode json` headless, locked down:
  * our instructions replace its system prompt (a file in the run folder: the Windows command line holds 32K
    characters); the question goes in on stdin, which is then closed (omp left waiting on an open stdin hangs);
  * only read, grep and glob, with omp's extras (advisor, memory, skills, rules, LSP, bash, update check) off and
    a home of its own in Maple Helper's data folder (PI_CODING_AGENT_DIR), so the player's own omp setup and
    sign-ins never reach the answers;
  * reads only in the knowledge base: an extension of ours blocks every other path, and URLs and omp's own
    internal links (local://, artifact://...), which omp's read tool opens too;
  * screenshots are attached by omp itself (@file arguments), not read with a tool. GLM-5.3 reads text only: omp's
    vision model (GLM-5.3-Flash, from the same Z.AI key) describes the screenshot for it.
Z.AI connects with an API key only (Z.AI has no account sign-in in omp). Muse Spark signs in with the player's
Meta (Muse Code) account through omp's device flow, or takes a Meta Model API key.
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
from .base import CREATE_NO_WINDOW, HEDGE_AFTER_S, Attempt, Installer, Provider, Race, RawResult, classify_error, \
    child_env, find_posix, http_ok, run_installer

log = logging.getLogger(__name__)

STALL_TIMEOUT_S = 150
CHECK_TIMEOUT_S = 30
RUN_MAX_AGE_S = 3600        # a run folder this old was left behind by a quit or a crash

INSTALL_CMD = "irm https://omp.sh/install.ps1 | iex"
INSTALL_CMD_MAC = "curl -fsSL https://omp.sh/install | sh"
POSIX_DIRS = ["~/.bun/bin", "~/.omp/bin", "~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"]

ZAI_MODEL = "glm-5.3"
MUSE_MODEL = "muse-spark-1.3-contributor"
LOGIN_URL = re.compile(r"https://auth\.meta\.com/\S+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# credentials, profiles and model overrides in the player's environment that would replace the account, the key
# or the model Maple Helper chose
FOREIGN_ENV = ("OMP_PROFILE", "PI_CODING_AGENT_DIR", "PI_SMOL_MODEL", "PI_SLOW_MODEL", "PI_PLAN_MODEL",
               "ZAI_API_KEY", "MODEL_API_KEY", "META_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY")
FOREIGN_PREFIXES = ("ANTHROPIC_", "OPENAI_")

# omp's settings in our home: nothing of its own beyond the answer (no advisor reviewing it, no memory or skills,
# no update check), and the screenshot described by Z.AI's vision model for GLM-5.3, which reads text only
CONFIG = """advisor:
  enabled: false
memory:
  backend: off
autolearn:
  enabled: false
ttsr:
  enabled: false
startup:
  checkUpdate: false
  setupWizard: false
images:
  describeForTextModels: true
modelRoles:
  vision: zai/glm-5.3-flash
skills:
  enabled: false
lsp:
  enabled: false
bash:
  enabled: false
mcp:
  enableProjectConfig: false
disabledProviders:
  - native
  - mcp-json
  - omp-plugins
  - agent-plugins
  - claude
  - claude-plugins
  - codex
  - cursor
  - gemini
  - opencode
  - vscode
  - windsurf
  - github
  - cline
"""
# (disabledProviders: every place omp finds MCP servers, plugins and their hooks, its own folder and other tools'
# configs: a run on the player's own omp home starts none of theirs; an MCP server in their omp folder did before)

GUARD_ROOTS = "maplehelper-guard-roots.json"
# The read guard: an omp extension that sees each tool call before it runs. Anything unexpected (another tool, a
# URL, an internal link, a path that leaves the knowledge base, a broken roots file, any error) is blocked. The
# folders it allows come from the roots file beside it, so no path (a Hebrew user name) ever becomes source code.
GUARD_TS = r"""// Maple Helper: omp may read only the knowledge base.
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const REASON = "Only the knowledge base can be read.";
const TOOLS = new Set(["read", "grep", "glob"]);
const PATHY = /path|file|dir|glob|folder|root|cwd|target|location/i;
const win = process.platform === "win32";

function here(): string {
  const d = (import.meta as any).dir;
  return typeof d === "string" ? d : path.dirname(fileURLToPath(import.meta.url));
}

function real(p: string): string {
  try { return fs.realpathSync.native(p); } catch { return path.resolve(p); }
}

function same(a: string, b: string): boolean {
  return win ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function inside(full: string, roots: string[]): boolean {
  return roots.some(r => same(full, r) || (win ? full.toLowerCase().startsWith(r.toLowerCase() + path.sep)
                                                : full.startsWith(r + path.sep)));
}

function collect(v: unknown, pathy: boolean, out: string[]): void {
  if (typeof v === "string") { if (pathy) out.push(v); return; }
  if (Array.isArray(v)) { for (const x of v) collect(x, pathy, out); return; }
  if (v && typeof v === "object") {
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) collect(x, pathy || PATHY.test(k), out);
  }
}

// one path as omp reads it: ";" separates several, and a ":..." after the file is a line range or a member
function allowed(spec: string, roots: string[]): boolean {
  for (let p of spec.split(";")) {
    p = p.trim();
    if (!p) continue;
    // (a ".." step anywhere: in a glob's pattern part it would climb out past the folder checked below)
    if (p.includes("://") || p.startsWith("~") || /(^|[\\/])\.\.([\\/]|$)/.test(p)) return false;
    if (/^[a-z][\w+.-]*:\d+\//i.test(p)) return false;                   // a bare host:port/ URL
    const drive = /^[a-z]:[\\/]/i.test(p) ? p.slice(0, 3) : "";
    const rest = p.slice(drive.length);
    const colon = rest.indexOf(":");
    if (colon === 0) return false;
    // "skill:x", "agent:y": an internal link, not a file (a file's line range follows a name with an extension)
    if (!drive && colon > 0 && !/[.\\/]/.test(rest.slice(0, colon))) return false;
    p = drive + (colon > 0 ? rest.slice(0, colon) : rest);
    // a glob: the folder it starts in
    const wild = p.search(/[*?[{]/);
    if (wild >= 0) p = p.slice(0, wild).replace(/[^\\/]*$/, "") || ".";
    if (!inside(real(path.resolve(process.cwd(), p)), roots)) return false;
  }
  return true;
}

export default function (pi: any) {
  pi.on("tool_call", async (event: any) => {
    try {
      if (!TOOLS.has(String(event?.toolName))) return { block: true, reason: REASON };
      const cfg = JSON.parse(fs.readFileSync(path.join(here(), "maplehelper-guard-roots.json"), "utf8"));
      const roots: string[] = (cfg.roots || []).filter((r: unknown) => typeof r === "string" && r).map(real);
      if (!roots.length) return { block: true, reason: REASON };
      const paths: string[] = [];
      collect(event.input ?? {}, false, paths);
      if (!paths.length) paths.push(".");          // no path (a grep with none): the folder it runs in
      for (const p of paths) if (!allowed(p, roots)) return { block: true, reason: REASON };
      return undefined;
    } catch {
      return { block: true, reason: REASON };
    }
  });
}
"""


def home() -> Path:
    """Maple Helper's own omp home: settings, the Muse sign-in, the read guard, run folders."""
    from ..store import DATA_DIR
    return DATA_DIR / "omp"


def agent_dir() -> Path:
    return home() / "agent"


def shots_dir() -> Path:
    return home() / "shots"


def find_windows() -> str | None:
    p = shutil.which("omp")
    if p and p.lower().endswith(".exe"):
        return p
    user, local = os.environ.get("USERPROFILE", ""), os.environ.get("LOCALAPPDATA", "")
    for c in ([Path(user) / ".bun" / "bin" / "omp.exe", Path(user) / ".omp" / "bin" / "omp.exe"] if user else []) + \
            ([Path(local) / "omp" / "omp.exe"] if local else []):
        if c.exists():
            return str(c)
    return None


def find_omp() -> str | None:
    """Oh My Pi from its installer (bun's global bin)."""
    return find_windows() if sys.platform == "win32" else find_posix("omp", POSIX_DIRS)


def env(zai_key: str | None = None, meta_key: str | None = None, player: bool = False) -> dict:
    """player: the player's own omp home (their Z.AI key or Muse sign-in made in omp itself), with their omp profile
    and keys as they set them; else Maple Helper's home, with nothing of theirs."""
    e = child_env(POSIX_DIRS)
    for k in list(e):
        if player and k in ("OMP_PROFILE", "PI_CODING_AGENT_DIR", "ZAI_API_KEY", "MODEL_API_KEY", "META_API_KEY"):
            continue
        if k in FOREIGN_ENV or k.startswith(FOREIGN_PREFIXES):
            e.pop(k, None)
    if not player:
        e["PI_CODING_AGENT_DIR"] = str(agent_dir())
    if zai_key:
        e["ZAI_API_KEY"] = zai_key
    if meta_key:
        e["MODEL_API_KEY"] = meta_key
    return e


# whether the player's own omp is signed in to a provider (omp's name), as last checked: a question runs on it
# without asking omp again (that takes a second or two)
PLAYER_MAX_AGE_S = 600
_player: dict[str, tuple[float, dict]] = {}
_own_account: dict[str, tuple[float, dict]] = {}


def player_account(provider: str, max_age: float = PLAYER_MAX_AGE_S) -> dict:
    """The player's own omp sign-in for a provider: {'status': 'ok' | 'logged_out' | 'offline', 'email': ...}.
    `omp token <provider> -l` names OAuth accounts ("1. <email>"); a provider with a stored key or a key in the
    environment answers `omp token <provider>` with it (exit 0)."""
    got = _player.get(provider)
    if got and time.monotonic() - got[0] < max_age:
        return got[1]
    e = env(player=True)
    r = _run(["token", provider, "-l"], e)
    out = (r.stdout + r.stderr).decode("utf-8", errors="replace") if r else ""
    acc = parse_account(out) if r else {"status": "logged_out", "email": None}
    if acc["status"] == "logged_out" and r is not None:
        k = _run(["token", provider], e)       # an API key (Z.AI's): printed, never kept here
        if k is not None and k.returncode == 0 and k.stdout.strip():
            acc = {"status": "ok", "email": None}
    _player[provider] = (time.monotonic(), acc)
    return acc


def _write(path: Path, text: str) -> None:
    """Written only when it changed, through a temporary file (a run reading it never sees half of it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
        log.warning("couldn't write %s", path.name, exc_info=True)
        try:
            os.remove(tmp)
        except OSError:
            pass


def prepare(kb_root=None) -> Path:
    """Our settings and, for a knowledge base, the read guard and its roots file. The guard's path."""
    d = agent_dir()
    _write(d / "config.yml", CONFIG)
    guard = d / "maplehelper-guard.ts"
    _write(guard, GUARD_TS)
    if kb_root is not None:
        # ASCII JSON (\u escapes): the same text whatever the PC's code pages
        _write(d / GUARD_ROOTS, json.dumps({"roots": [str(Path(kb_root).resolve())]}, indent=1))
    return guard


def command(exe: str, model: str, system_file, guard=None, shots: list | None = None, tools: bool = True) -> list[str]:
    """The run's command line. The question itself goes in on stdin (see the module's docstring)."""
    cmd = [exe, "-p", "--mode", "json", "--model", model, "--thinking", "low"]
    cmd += ["--tools", "read,grep,glob"] if tools else ["--no-tools"]
    cmd += ["--no-session", "--no-extensions", "--no-skills", "--no-rules", "--no-lsp", "--no-title",
            # our settings on top of whichever home the run uses (the player's own, for their sign-in)
            "--config", str(agent_dir() / "config.yml"),
            # a path with no line break: omp reads the instructions from the file
            "--system-prompt", str(system_file)]
    if tools and guard:
        cmd += ["-e", str(guard)]          # --no-extensions still loads the ones named here
    return cmd + [f"@{s}" for s in shots or []]


def classify(text: str, status=None) -> str | None:
    """An error omp reported: the HTTP status where it says one (Z.AI's "Authentication Failed" is a 401), else
    the words."""
    if status == 402:
        return "no_credit"             # Meta: "Billing verification failed" (a key with no billing set up)
    if status in (401, 403):
        return "not_logged_in"
    if status == 429:
        return "usage_limit"
    t = text.lower()
    if "no api key" in t or "no active credential" in t or "authentication failed" in t or "not logged in" in t:
        return "not_logged_in"
    return classify_error(text)


class Answer:
    """The answer in omp's JSON events: the text of the last assistant message (the ones before a tool call were
    a lead-in), what it cost and which model gave it."""

    def __init__(self):
        self.text = ""
        self.model: str | None = None
        self.cost = 0.0
        self.priced = False
        self.tool_calls = 0
        self.turns = 0
        self.error: str | None = None
        self.detail = ""
        self.ended = False             # agent_end: the run is complete

    def feed(self, ev: dict) -> bool:
        """One event; True when the visible text changed."""
        t = ev.get("type")
        if t == "message_start" and (ev.get("message") or {}).get("role") == "assistant":
            changed = bool(self.text)
            self.text = ""
            return changed
        if t == "message_update":
            e = ev.get("assistantMessageEvent") or {}
            if e.get("type") == "text_delta" and isinstance(e.get("delta"), str):
                self.text += e["delta"]
                return True
            return False
        if t == "message_end":
            m = ev.get("message") or {}
            if m.get("role") != "assistant":
                return False
            self.turns += 1
            self.model = m.get("model") or self.model
            cost = ((m.get("usage") or {}).get("cost") or {}).get("total")
            if isinstance(cost, (int, float)):
                self.cost += cost
                self.priced = True
            content = m.get("content") if isinstance(m.get("content"), list) else []
            self.tool_calls += sum(1 for b in content if isinstance(b, dict) and b.get("type") == "toolCall")
            if m.get("stopReason") == "error":
                self.detail = str(m.get("errorMessage") or "")
                self.error = classify(self.detail, m.get("errorStatus")) or "api_error"
            text = "".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
            if text and text != self.text:       # the whole message, should a delta have been missed
                self.text = text
                return True
            return False
        if t == "agent_end":
            self.ended = True
        return False


def alive(ev: dict) -> bool:
    """A sign of life from the model (omp's own session and start lines come before the server says anything)."""
    return ev.get("type") in ("message_update", "message_start", "message_end", "tool_execution_start",
                              "tool_execution_end", "turn_end", "agent_end")


def parse(lines, on_event=None) -> Answer:
    """Reads omp's JSON lines into an Answer, up to agent_end. on_event(answer, changed): after each event."""
    a = Answer()
    for line in lines:
        if line is None:
            if on_event:
                on_event(a, None)
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        changed = a.feed(ev)
        if on_event:
            on_event(a, ev if changed or alive(ev) else None)
        if a.ended:
            break
    return a


def to_result(a: Answer, stderr: str, finished: bool) -> RawResult:
    if a.error:
        log.warning("omp error: %s", base.scrub(a.detail[-500:]))
        return RawResult(error=a.error)
    if not a.text.strip():
        if stderr.strip() or not finished:
            log.warning("omp gave no answer: %s", base.scrub(stderr.strip()[-1500:]))
        return RawResult(error=classify(stderr) or "no_result")
    return RawResult(text=a.text, model=a.model, cost_usd=a.cost if a.priced else None,
                     tool_calls=a.tool_calls, turns=a.turns)


def parse_usage(data, provider: str) -> dict | None:
    """`omp usage -j -p <provider>`: the 5-hour and weekly windows, in usage.parse's shape. Z.AI's monthly
    feature quotas (web search, Zread) aren't the plan's: only its token windows count."""
    try:
        reports = data["reports"]
    except (KeyError, TypeError):
        return None
    out = {}
    for r in reports if isinstance(reports, list) else []:
        if not isinstance(r, dict) or r.get("provider") != provider:
            continue
        for lim in r.get("limits") or []:
            if not isinstance(lim, dict) or ":features:" in str(lim.get("id", "")):
                continue
            w, amount = lim.get("window") or {}, lim.get("amount") or {}
            used, ms = amount.get("usedFraction"), w.get("durationMs")
            if not isinstance(used, (int, float)) or not isinstance(ms, (int, float)):
                continue
            hours = ms / 3_600_000
            name = "five_hour" if hours <= 6 else "seven_day" if 6 * 24 <= hours <= 8 * 24 else None
            if not name or name in out:
                continue
            resets = w.get("resetsAt")
            out[name] = {"used": max(0.0, min(1.0, float(used))),
                         "resets": resets / 1000 if isinstance(resets, (int, float)) else None}
    return out or None


def _run(args: list[str], e: dict | None = None, timeout: float = CHECK_TIMEOUT_S) -> subprocess.CompletedProcess | None:
    exe = find_omp()
    if not exe:
        return None
    prepare()
    try:
        return subprocess.run([exe, *args], capture_output=True, timeout=timeout, env=e or env(), cwd=str(home()),
                              stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        log.warning("omp %s failed", args[:1], exc_info=True)
        return None


def read_usage(provider: str, e: dict | None = None) -> dict | None:
    r = _run(["usage", "-j", "-p", provider], e)
    if not r or r.returncode != 0:
        return None
    try:
        return parse_usage(json.loads(r.stdout.decode("utf-8", errors="replace")), provider)
    except ValueError:
        return None


def parse_account(output: str) -> dict:
    """`omp token muse-code -l`: "1. <email>" per signed-in account; "No active credential found" when none."""
    low = output.lower()
    if "no active credential" in low or "no credential" in low:
        return {"status": "logged_out", "email": None}
    m = re.search(r"^\s*1\.\s+(\S+)", output, re.M)
    if m:
        email = EMAIL.search(m.group(1))
        return {"status": "ok", "email": email.group(0) if email else None}
    if any(s in low for s in base.OFFLINE):
        return {"status": "offline", "email": None}
    return {"status": "logged_out", "email": None}


def sweep(max_age: float = RUN_MAX_AGE_S) -> None:
    """Run folders a quit or a crash left behind (a run removes its own when it ends)."""
    now = time.time()
    try:
        for d in shots_dir().glob("run-*"):
            try:
                if now - d.stat().st_mtime > max_age:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


class _Omp(Provider):
    saver_model = None
    reports_usage = True
    reinstall_fixes_login = False
    default = ""
    omp_provider = ""            # omp's name for the provider (models, usage)

    def find_exe(self) -> str | None:
        return find_omp()

    def models(self) -> list[tuple[str | None, str]]:
        return [(self.default, base.model_name(self.default))]

    def default_model(self) -> str | None:
        return base.model_name(self.default)

    def install(self) -> Installer:
        return run_installer(INSTALL_CMD, INSTALL_CMD_MAC)

    def backend(self, brain):
        return OmpBackend(brain, self)

    # what a run needs: the model id omp knows, and the environment carrying the key
    def model_id(self, model: str | None, api_key: str | None) -> str:
        raise NotImplementedError

    def run_env(self, api_key: str | None) -> dict:
        raise NotImplementedError

    def run_key(self, api_key: str | None) -> str | None:
        return api_key

    def own_signed_in(self) -> bool:
        """Signed in in Maple Helper's own omp home (a sign-in made from the app)."""
        return False

    def uses_player(self, api_key: str | None) -> bool:
        """No key and no sign-in of the app's own, but the player's omp is signed in to this provider: the answers
        run on that, as with the other AIs' CLIs."""
        return not api_key and not self.own_signed_in() and player_account(self.omp_provider)["status"] == "ok"

    def player_status(self) -> dict | None:
        """The account() of the player's own omp sign-in, when there is one."""
        acc = player_account(self.omp_provider, max_age=0)
        return {"status": "ok", "email": acc.get("email"), "source": "omp"} if acc["status"] == "ok" else None

    def read_limits(self) -> dict | None:
        return read_usage(self.omp_provider, env(player=self.uses_player(None)))


class Zai(_Omp):
    name = "zai"
    label = "Z.AI"
    keyring_user = "zai_api_key"
    model_setting = "zai_model"
    key_only = True
    default = ZAI_MODEL
    omp_provider = "zai"

    def account(self) -> dict:
        if not find_omp():
            return {"status": "not_installed", "email": None}
        if self.load_api_key():
            return {"status": "ok", "email": None, "method": "api_key"}
        # a Z.AI key the player already gave omp itself (`omp login zai`, or ZAI_API_KEY)
        return self.player_status() or {"status": "logged_out", "email": None}

    def login(self) -> subprocess.Popen | None:
        return None                # a key is the only way in

    def logout(self) -> bool:
        self.delete_api_key()
        return True

    def test_api_key(self, key: str) -> bool:
        return http_ok("https://api.z.ai/api/paas/v4/models", {"Authorization": f"Bearer {key}"})

    def run_key(self, api_key: str | None) -> str | None:
        # the stored key is the connection, whatever the key-mode setting says
        return api_key or self.load_api_key()

    def model_id(self, model: str | None, api_key: str | None) -> str:
        return f"zai/{model or ZAI_MODEL}"

    def run_env(self, api_key: str | None) -> dict:
        return env(zai_key=api_key, player=self.uses_player(api_key))

    def read_limits(self) -> dict | None:
        key = self.load_api_key()
        return read_usage("zai", env(zai_key=key, player=self.uses_player(key)))


class Muse(_Omp):
    name = "muse"
    label = "Muse Spark"
    keyring_user = "meta_api_key"
    model_setting = "muse_model"
    default = MUSE_MODEL
    omp_provider = "muse-code"

    def _own(self, max_age: float = PLAYER_MAX_AGE_S) -> dict:
        """The sign-in in Maple Helper's own omp home (made from the app), as last checked."""
        got = _own_account.get("muse-code")
        if got and time.monotonic() - got[0] < max_age:
            return got[1]
        r = _run(["token", "muse-code", "-l"])
        acc = parse_account((r.stdout + r.stderr).decode("utf-8", errors="replace")) if r else \
            {"status": "logged_out", "email": None}
        _own_account["muse-code"] = (time.monotonic(), acc)
        return acc

    def own_signed_in(self) -> bool:
        return self._own()["status"] == "ok"

    def account(self) -> dict:
        if not find_omp():
            return {"status": "not_installed", "email": None}
        own = self._own(max_age=0)
        if own["status"] == "ok":
            return own
        # a Muse Code sign-in the player already made in omp itself
        return self.player_status() or own

    def login(self) -> subprocess.Popen | None:
        """Device sign-in, hidden: omp prints Meta's link with the code in it (opened here) and waits for the
        player to approve it in the browser, then ends 0. No code to paste."""
        exe = find_omp()
        if not exe:
            return None
        prepare()
        opened = []

        def on_line(text: str):
            m = LOGIN_URL.search(text)
            if m and not opened:
                opened.append(m.group(0))
                import webbrowser
                webbrowser.open(m.group(0))
        return base.open_login(exe, ["login", "muse-code"], env(), cwd=str(home()), on_line=on_line)

    def logout(self) -> bool:
        """omp has no sign-out: the sign-in lives in the credential store of our own omp home, which is removed
        (our settings and guard are written again before the next run)."""
        ok = True
        for name in ("agent.db", "agent.db-wal", "agent.db-shm"):
            try:
                (agent_dir() / name).unlink(missing_ok=True)
            except OSError:
                log.warning("couldn't remove %s", name, exc_info=True)
                ok = False
        return ok

    def test_api_key(self, key: str) -> bool:
        return http_ok("https://api.meta.ai/v1/models", {"Authorization": f"Bearer {key}"})

    def model_id(self, model: str | None, api_key: str | None) -> str:
        # a key goes through Meta's Model API; the sign-in through Muse Code's plan
        return f"{'meta' if api_key else 'muse-code'}/{model or MUSE_MODEL}"

    def run_env(self, api_key: str | None) -> dict:
        return env(meta_key=api_key, player=self.uses_player(api_key))


class OmpBackend:
    """Runs questions for a Brain through omp, one fresh process per question."""

    def __init__(self, brain, provider: _Omp):
        self.brain = brain
        self.provider = provider
        self.exe = find_omp()
        self._race: Race | None = None

    def prewarm(self) -> None:
        # no process can wait for the question: the screenshot is named on omp's command line
        if self.exe:
            prepare(self.brain.kb.root)

    def drop_warm(self) -> None:
        pass

    def cancel(self) -> None:
        if self._race:
            self._race.cancel()

    def shutdown(self) -> None:
        self.cancel()

    def _spawn(self, cmd: list[str], e: dict) -> subprocess.Popen:
        return subprocess.Popen(cmd, cwd=str(self.brain.kb.root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=e, creationflags=CREATE_NO_WINDOW)

    @staticmethod
    def _send(proc: subprocess.Popen, text: str) -> bool:
        try:
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.close()
            return True
        except OSError:
            return False

    def _attempt(self, a: Attempt, cmd: list[str], e: dict, prompt: str) -> RawResult:
        try:
            proc = self._spawn(cmd, e)
        except OSError as exc:
            log.error("could not start omp: %s", exc)
            return RawResult(error=f"launch_failed: {exc}")
        if not a.track(proc):
            return RawResult(error="no_result")
        err: list[bytes] = []
        broken: list[bool] = []

        def drain():
            # omp says "Failed to load extension" as it starts, long before the model's first tool call, and then goes
            # on with no guard at all: a run whose guard didn't load is stopped right there
            for line in iter(proc.stderr.readline, b""):
                err.append(line)
                if b"failed to load extension" in line.lower() and "-e" in cmd:
                    broken.append(True)
                    base.kill(proc)
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        if not self._send(proc, prompt):
            reader.join(timeout=2)
            if a.lost:
                return RawResult(error="no_result")
            return RawResult(error=classify(b"".join(err).decode("utf-8", errors="replace")) or "no_result")
        lines = base.Lines(proc, STALL_TIMEOUT_S, f"omp {self.provider.label} (run {a.n})")

        def on_event(ans: Answer, ev):
            if ev is None:
                return
            lines.touch()
            a.activity()
            if ev.get("type") in ("message_update", "message_start", "message_end") and ans.text:
                a.delta(ans.text)
        answer = parse(lines, on_event)
        lines.finish()
        if not answer.ended or answer.error:
            reader.join(timeout=2)       # its words say what went wrong (an answer doesn't wait for them)
        stderr = b"".join(err).decode("utf-8", errors="replace")
        if broken:
            log.error("omp didn't load the read guard, run stopped: %s", base.scrub(stderr[-500:]))
            return RawResult(error="api_error")
        if lines.stalled:
            log.warning("omp stalled for %ss, stopped: %s", STALL_TIMEOUT_S, base.scrub(stderr[-1000:]))
            return RawResult(error="timeout")
        if a.lost:
            return RawResult(error="no_result")
        return to_result(answer, stderr, answer.ended)

    def _exec(self, instructions: str, prompt: str, model: str | None, tools: bool, shots: list,
              folder: Path, on_delta=None, race: Race | None = None) -> RawResult:
        """race: a summary's own (no hedging, and the chat's Stop is not for it); a question's is self._race."""
        p = self.provider
        key = p.run_key(self.brain.api_key)
        # (no key of the app's: the player's own omp sign-in, when it has one)
        e = p.run_env(key)
        if p.key_only and not key and "ZAI_API_KEY" not in e and not p.uses_player(key):
            return RawResult(error="not_logged_in")      # Z.AI with no key anywhere: nothing to run on
        system_file = folder / "instructions.md"
        system_file.write_text(instructions, encoding="utf-8")
        guard = prepare(self.brain.kb.root)
        try:
            guarded = guard.read_text(encoding="utf-8") == GUARD_TS
        except OSError:
            guarded = False
        if tools and not guarded:
            log.warning("omp read guard couldn't be written: this run gets no file tools")
            tools = False           # omp's read opens anything (URLs too) with no guard
        cmd = command(self.exe, p.model_id(model or self.brain.model, key), system_file, guard, shots, tools)
        if race is None:
            race = self._race = Race(on_delta, f"omp {p.label}")
            hedge = HEDGE_AFTER_S
        else:
            hedge = None
        return race.run(lambda a: self._attempt(a, cmd, e, prompt), hedge)

    def _folder(self) -> Path:
        shots_dir().mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="run-", dir=shots_dir()))

    def run(self, prompt: str, screenshot_jpeg: bytes | None, on_raw_delta=None, model: str | None = None,
            tools: bool = True) -> RawResult:
        if not self.exe:
            return RawResult(error="not_installed")
        folder = self._folder()
        try:
            shots = []
            for i, jpeg in enumerate(screenshot_jpeg if isinstance(screenshot_jpeg, list) else [screenshot_jpeg]):
                if jpeg:
                    shots.append(folder / f"screenshot-{i}.jpg")
                    shots[-1].write_bytes(jpeg)
            return self._exec(self.brain.system_prompt(), prompt, model, tools, shots, folder, on_raw_delta)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def summarize(self, instructions: str, text: str, timeout: int = 90) -> str | None:
        if not self.exe:
            return None
        folder = self._folder()
        result: list[RawResult] = []
        race = Race(None, f"omp {self.provider.label} summary")
        try:
            t = threading.Thread(target=lambda: result.append(
                self._exec(instructions, text, None, False, [], folder, race=race)), daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive():
                race.cancel()
                t.join(5)
                return None
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        r = result[0] if result else None
        return (r.text or "").strip() or None if r and not r.error else None
