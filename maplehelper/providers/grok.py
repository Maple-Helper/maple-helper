"""Grok through xAI's Grok Build CLI (`grok`): the player's SuperGrok / X Premium+ plan, or an xAI API key.

Each question runs `grok` headless (a prompt file, streaming JSON in Claude Code's message format),
locked down:
  * our instructions replace its system prompt; only read_file, grep and list_dir remain, with web
    search, sub-agents, plan mode and the MCP meta-tools off;
  * a home of its own in Maple Helper's data folder (GROK_HOME, HOME/USERPROFILE), and the settings it
    would otherwise pick up from Claude Code and Cursor on this PC (rules, skills, hooks, MCP servers)
    switched off, so the player's own setup never reaches the answers;
  * reads only in the knowledge base and the screenshot folder: on Windows a PreToolUse hook of ours
    denies every other path (Grok's own read_file reads anything otherwise, its sandbox doesn't apply
    there); on macOS the "strict" sandbox profile.
Sign-in: `grok login --device-auth` opens a link that already carries the code in the browser (the app
opens it only when grok says it couldn't) and waits for the player to approve there (no code to paste). The sign-in lives in our home.
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
from .base import CREATE_NO_WINDOW, Installer, Provider, RawResult, StreamText, classify_error, child_env, \
    find_posix, http_ok, run_installer

log = logging.getLogger(__name__)
STALL_TIMEOUT_S = 150
CHECK_TIMEOUT_S = 30

INSTALL_CMD = "irm https://x.ai/cli/install.ps1 | iex"
INSTALL_CMD_MAC = "curl -fsSL https://x.ai/cli/install.sh | bash"
POSIX_DIRS = ["~/.grok/bin", "~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"]

TOOLS = ["read_file", "grep", "list_dir"]
SAVER_ALIAS = "grok-fast"          # saver mode before the model list was read: the account's fast model
LOGIN_URL = re.compile(r"https://accounts\.x\.ai/\S+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# what Grok would pick up from other tools on this PC, and its own extras: all off for Maple Helper
OFF = {f"GROK_{k}_ENABLED": "0" for k in (
    "CLAUDE_AGENTS", "CLAUDE_HOOKS", "CLAUDE_MCPS", "CLAUDE_RULES", "CLAUDE_SKILLS", "CURSOR_AGENTS", "CURSOR_HOOKS",
    "CURSOR_MCPS", "CURSOR_RULES", "CURSOR_SKILLS", "MANAGED_MCPS")}
OFF.update({"GROK_MEMORY": "0", "GROK_TELEMETRY_ENABLED": "0", "GROK_DISABLE_AUTOUPDATER": "1",
            "GROK_SESSION_RECAP": "0", "GROK_PROMPT_SUGGESTIONS": "0", "GROK_WEB_FETCH": "0", "GROK_SUBAGENTS": "0",
            "GROK_FEEDBACK_ENABLED": "0", "GROK_TURN_SUMMARY": "0", "GROK_WORKFLOWS": "0"})
# credentials and endpoints in the player's environment that would override the account Maple Helper chose
FOREIGN_ENV = ("XAI_API_KEY", "GROK_CODE_XAI_API_KEY", "GROK_DEPLOYMENT_KEY", "GROK_XAI_API_BASE_URL",
               "GROK_CLI_BASE_URL", "GROK_AGENT", "GROK_SANDBOX", "GROK_CONFIG", "GROK_CONFIG_PATH",
               "GROK_AUTH_PROVIDER_COMMAND", "GROK_AUTH_PROVIDER_ACCESS_TOKEN")


def home() -> Path:
    """Maple Helper's own Grok home: config, sign-in, sessions, our read guard."""
    from ..store import DATA_DIR
    return DATA_DIR / "grok"


def grok_home() -> Path:
    return home() / ".grok"


def shots_dir() -> Path:
    # inside GROK_HOME: the macOS "strict" sandbox reads there (and in the knowledge base), nowhere else
    return grok_home() / "maplehelper-shots"


def find_windows() -> str | None:
    p = shutil.which("grok")
    if p and p.lower().endswith(".exe"):
        return p
    dirs = [os.environ.get("GROK_BIN_DIR", ""), str(Path(os.environ.get("USERPROFILE", "")) / ".grok" / "bin")]
    for d in dirs:
        c = Path(d) / "grok.exe"
        if d and c.exists():
            return str(c)
    return None


def find_grok() -> str | None:
    """Grok Build from xAI's installer (~/.grok/bin)."""
    return find_windows() if sys.platform == "win32" else find_posix("grok", POSIX_DIRS)


def env(api_key: str | None = None) -> dict:
    e = child_env(POSIX_DIRS)
    for k in FOREIGN_ENV:
        e.pop(k, None)
    e.update(OFF)
    e["GROK_HOME"] = str(grok_home())
    e["HOME"] = str(home())
    if sys.platform == "win32":
        e["USERPROFILE"] = str(home())
    if api_key:
        e["XAI_API_KEY"] = api_key
    return e


GUARD_PS1 = r"""# Maple Helper: Grok may read only the knowledge base and the screenshot (a PreToolUse hook).
# Grok lets a read through unless the hook prints a deny, so anything unexpected (a request it can't read, a missing
# or broken roots file, any error at all) ends in the deny at the bottom. This file is plain ASCII and never changes:
# the folders it allows come from maplehelper-guard-roots.json beside it, so no path (a Hebrew user name) ever
# becomes part of the script's source.
$ErrorActionPreference = 'Stop'
$deny = '{"decision":"block","reason":"Only the knowledge base and the screenshot can be read.","hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Only the knowledge base and the screenshot can be read."}}'
try {
  # Grok sends UTF-8: read the bytes, not the console's code page (862/1255 garbled a Hebrew path)
  $buf = New-Object IO.MemoryStream
  [Console]::OpenStandardInput().CopyTo($buf)
  $in = [Text.Encoding]::UTF8.GetString($buf.ToArray()).TrimStart([char]0xFEFF) | ConvertFrom-Json
  $cfg = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'maplehelper-guard-roots.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
  $ti = $in.tool_input
  if (-not $ti -or $ti -isnot [Management.Automation.PSCustomObject]) { throw 'no tool call' }
  # every path in the call, under any key that names one (file_path, filePath, paths, glob...), not only the three
  # Grok uses today: a key the guard didn't know was let through
  $paths = New-Object Collections.ArrayList
  function Collect($v, [bool]$pathy, [bool]$glob) {
    if ($v -is [Management.Automation.PSCustomObject]) {
      foreach ($pr in $v.PSObject.Properties) {
        $l = $pr.Name.ToLowerInvariant()
        $isPath = $pathy -or [bool]($l -match 'path|file|dir|glob|folder|root|cwd|target|location')
        Collect $pr.Value $isPath ($glob -or $l.Contains('glob'))
      }
    } elseif ($v -is [string]) {
      if ($pathy -and $v.Length -gt 0) { [void]$paths.Add(@($v, $glob)) }
    } elseif ($v -is [Collections.IEnumerable]) {
      foreach ($x in $v) { Collect $x $pathy $glob }
    }
  }
  Collect $ti $false $false
  if ($paths.Count -eq 0) { [void]$paths.Add(@('.', $false)) }   # no path (a grep with none): the folder it runs in
  $base = if ($in.cwd) { [string]$in.cwd } else { (Get-Location).Path }
  $ok = $true
  foreach ($p in $paths) {
    $s = $p[0]
    # a wildcard in a path ("pages/*.md" as a grep path) is a glob too: it was a deny (review PLT-12)
    $isGlob = $p[1] -or ($s.IndexOfAny([char[]]'*?') -ge 0)
    if ($s.StartsWith('~')) { $ok = $false; break }     # a home folder Grok may expand
    # a glob is matched under the folder it runs in: never one that starts elsewhere or climbs out
    if ($isGlob -and ([IO.Path]::IsPathRooted($s) -or $s.Contains(':') -or $s.Contains('..'))) { $ok = $false; break }
    $full = [IO.Path]::GetFullPath([IO.Path]::Combine($base, $(if ($isGlob) { '.' } else { $s })))
    $in_root = $false
    foreach ($root in $cfg.roots) {
      $root = [string]$root
      if (-not $root) { continue }
      if ($full -ieq $root -or $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { $in_root = $true; break }
    }
    if (-not $in_root) { $ok = $false; break }
  }
  if ($ok) { exit 0 }
} catch { }
[Console]::Out.Write($deny)
exit 2
"""
GUARD_ROOTS = "maplehelper-guard-roots.json"

# The same guard as a small program, built with the C# compiler that comes with Windows (.NET Framework 4): a read
# costs ~50 ms instead of ~650 ms. Grok runs a hook command with spaces or quotes in it through its shell (pwsh on
# this PC), so the PowerShell guard started two PowerShells for every read_file, grep and list_dir; a bare path is
# started directly. Same rules as GUARD_PS1, and stricter on odd input: a path that isn't a string is a deny.
GUARD_CS = r"""// Maple Helper: Grok may read only the knowledge base and the screenshot (a PreToolUse hook).
// Anything unexpected ends in the deny: Grok lets a read through unless the hook prints one.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

static class Guard {
    const string Deny = "{\"decision\":\"block\",\"reason\":\"Only the knowledge base and the screenshot can be read.\",\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"Only the knowledge base and the screenshot can be read.\"}}";

    static int Main() {
        try {
            if (Allowed()) return 0;
        } catch { }
        try {
            byte[] b = Encoding.ASCII.GetBytes(Deny);
            Stream o = Console.OpenStandardOutput();
            o.Write(b, 0, b.Length);
            o.Flush();
        } catch { }
        return 2;
    }

    static bool Allowed() {
        MemoryStream buf = new MemoryStream();
        Console.OpenStandardInput().CopyTo(buf);
        string text = new UTF8Encoding(false).GetString(buf.ToArray()).TrimStart('\uFEFF');
        JavaScriptSerializer js = new JavaScriptSerializer();
        js.MaxJsonLength = int.MaxValue;
        Dictionary<string, object> input = js.DeserializeObject(text) as Dictionary<string, object>;
        string roots = File.ReadAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "maplehelper-guard-roots.json"), Encoding.UTF8);
        Dictionary<string, object> cfg = js.DeserializeObject(roots) as Dictionary<string, object>;
        object ti;
        if (input == null || cfg == null || !input.TryGetValue("tool_input", out ti)) return false;
        Dictionary<string, object> tool = ti as Dictionary<string, object>;
        if (tool == null) return false;      // an empty one ({} from a list_dir) is the folder it runs in, as in GUARD_PS1
        // every path in the call, under any key that names one (file_path, filePath, paths, glob...), not only the
        // three Grok uses today: a key the guard didn't know was let through
        List<string[]> paths = new List<string[]>();
        Collect(tool, false, false, paths);
        if (paths.Count == 0) paths.Add(new string[] { ".", "" });   // no path (a grep with none): the folder it runs in
        object cwd;
        string at = input.TryGetValue("cwd", out cwd) && cwd is string && ((string)cwd).Length > 0
            ? (string)cwd : Environment.CurrentDirectory;
        object list;
        if (!cfg.TryGetValue("roots", out list) || list is string || !(list is IEnumerable)) return false;
        foreach (string[] p in paths) {
            if (p[0].StartsWith("~")) return false;               // a home folder Grok may expand
            // a wildcard in a path ("pages/*.md" as a grep path) is a glob too: it was a deny (review PLT-12)
            bool glob = p[1] == "glob" || p[0].IndexOfAny(new char[] { '*', '?' }) >= 0;
            // a glob is matched under the folder it runs in: never one that starts elsewhere or climbs out
            if (glob && (Path.IsPathRooted(p[0]) || p[0].Contains(":") || p[0].Contains(".."))) return false;
            if (!Inside(Path.GetFullPath(Path.Combine(at, glob ? "." : p[0])), (IEnumerable)list)) return false;
        }
        return true;
    }

    static readonly string[] PathWords = { "path", "file", "dir", "glob", "folder", "root", "cwd", "target", "location" };

    static bool PathKey(string k) {
        string l = k.ToLowerInvariant();
        foreach (string w in PathWords) if (l.Contains(w)) return true;
        return false;
    }

    // the strings under a path key, in arrays and objects too; a number or true/false names no file
    static void Collect(object v, bool pathy, bool glob, List<string[]> into) {
        Dictionary<string, object> d = v as Dictionary<string, object>;
        if (d != null) {
            foreach (KeyValuePair<string, object> kv in d)
                Collect(kv.Value, pathy || PathKey(kv.Key), glob || kv.Key.ToLowerInvariant().Contains("glob"), into);
            return;
        }
        string s = v as string;
        if (s != null) {
            if (pathy && s.Length > 0) into.Add(new string[] { s, glob ? "glob" : "" });
            return;
        }
        IEnumerable e = v as IEnumerable;
        if (e != null) foreach (object x in e) Collect(x, pathy, glob, into);
    }

    static bool Inside(string full, IEnumerable roots) {
        foreach (object r in roots) {
            string root = r as string;
            if (string.IsNullOrEmpty(root)) continue;
            if (string.Equals(full, root, StringComparison.OrdinalIgnoreCase)
                || full.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase)) return true;
        }
        return false;
    }
}
"""
GUARD_EXE = "maplehelper-guard.exe"
# what Grok starts directly, without a shell: letters (Hebrew too), digits and _ . ~ - only (checked against Grok
# 1.0.46 with a mock of the xAI API). A path with a space went to the shell unquoted, the hook failed and the read
# went through: such a path never becomes the command (its 8.3 name does, else the PowerShell guard)
BARE_PATH = re.compile(r"[A-Za-z]:\\[\w.~\\-]+")
# a user name with $ ` % or " in it: inside the PowerShell guard's quoted command line, Grok's shell (pwsh or cmd)
# would expand it, the hook would fail, and Grok lets a read through when its hook fails
SHELL_EXPANDS = re.compile(r'[$`%"]')
_guard_ok: dict[tuple, bool] = {}       # (exe, its mtime, the knowledge base) -> passed its check in this session
_guard_lock = threading.Lock()


def _csc() -> str | None:
    windir = os.environ.get("WINDIR") or r"C:\Windows"
    for fw in ("Framework64", "Framework"):
        c = Path(windir) / "Microsoft.NET" / fw / "v4.0.30319" / "csc.exe"
        if c.exists():
            return str(c)
    return None


def _short(path: Path) -> str:
    """The 8.3 name of a path (C:\\Users\\5D0B~1\\...): no spaces or Hebrew letters, where the drive keeps them."""
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    return buf.value if 0 < n < 1024 else str(path)


def _guard_says(exe: Path, cwd: str, target) -> tuple[int, bytes] | None:
    data = target if isinstance(target, bytes) else \
        json.dumps({"cwd": cwd, "tool_name": "read_file", "tool_input": {"target_file": target}}).encode()
    try:
        r = subprocess.run([str(exe)], input=data, capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.returncode, r.stdout


def guard_exe(kb_root) -> str | None:
    """The compiled guard, built when missing or out of date and checked once per session against the roots file
    already written: it must allow a knowledge-base file and deny the folder above it and a broken request.
    None (the PowerShell guard then) when it can't be built, fails the check, or its path would need a shell."""
    d = grok_home()
    exe, src = d / GUARD_EXE, d / "maplehelper-guard.cs"
    try:
        _write(src, GUARD_CS)
        if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
            csc = _csc()
            if not csc:
                return None
            tmp = d / f"maplehelper-guard-{os.getpid()}.exe"
            r = subprocess.run([csc, "-nologo", "-optimize+", "-target:exe", "-r:System.Web.Extensions.dll",
                                f"-out:{tmp}", str(src)], capture_output=True, timeout=120, cwd=str(d),
                               creationflags=CREATE_NO_WINDOW)
            if r.returncode or not tmp.exists():
                log.warning("Grok read guard not built (%s): %s", r.returncode,
                            r.stdout.decode("utf-8", errors="replace")[-500:])
                return None
            os.replace(tmp, exe)       # fails while a run's hook has it open: the PowerShell guard this time
        kb = str(Path(kb_root).resolve())
        key = (str(exe), exe.stat().st_mtime_ns, kb)
    except (OSError, subprocess.TimeoutExpired):
        log.warning("Grok read guard not built", exc_info=True)
        return None
    if key not in _guard_ok:
        deny = b'"permissionDecision":"deny"'
        inside, above, broken = (_guard_says(exe, kb, "x.md"), _guard_says(exe, kb, r"..\x.md"),
                                 _guard_says(exe, kb, b"not json {"))
        _guard_ok[key] = inside == (0, b"") and bool(above) and above[0] == 2 and deny in above[1] and \
            bool(broken) and broken[0] == 2 and deny in broken[1]
        if not _guard_ok[key]:
            log.warning("Grok read guard failed its check (%s, %s, %s): using the PowerShell one", inside, above, broken)
    if not _guard_ok[key]:
        return None
    path = str(exe) if BARE_PATH.fullmatch(str(exe)) else _short(exe)
    return path if BARE_PATH.fullmatch(path) else None


def write_guard(kb_root) -> bool:
    """Windows: the read guard (a hook in our Grok home). Written when the knowledge base path changes.
    The folders go in a JSON file of their own, not into the script: Windows PowerShell 5.1 read the script in the
    ANSI code page, a Hebrew user name in a pasted path turned into stray quote marks (ב/ג: a parse error, so the
    hook failed and Grok read anything; other letters: garbled folders, so it read nothing).
    The hook is the compiled guard where it builds and passes its check (guard_exe), else the PowerShell script.
    False when the hook can't be trusted to run (see SHELL_EXPANDS): the run then gets no file tools."""
    if sys.platform != "win32":
        return True
    roots = [str(Path(p).resolve()) for p in (kb_root, shots_dir())]
    # ASCII JSON (\u escapes) read as UTF-8: the same text whatever the PC's code pages
    _write(grok_home() / GUARD_ROOTS, json.dumps({"roots": roots}, indent=1))
    script = grok_home() / "maplehelper-guard.ps1"
    _write(script, GUARD_PS1, encoding="utf-8-sig")       # the BOM: PowerShell 5.1 reads it as UTF-8 regardless
    # a bare path: Grok starts it directly (with a space or a quote in it, Grok would hand it to its shell)
    with _guard_lock:                  # the prewarm and a question can get here together
        exe = guard_exe(kb_root)
    cmd = exe or f'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{script}"'
    # no matcher: it runs for each of the three tools (a tool-name matcher didn't match them)
    _write(grok_home() / "hooks" / "maplehelper.json",
           json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": cmd, "timeout": 10}]}]}},
                      indent=1))
    if not exe and SHELL_EXPANDS.search(str(script)):
        log.warning("Grok read guard: no compiled guard and a script path its shell would change: no file tools")
        return False
    return True


def _write(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text(encoding=encoding) == text:
            return
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        log.warning("couldn't write %s", path.name, exc_info=True)
        try:
            os.remove(tmp)
        except OSError:
            pass


def grok_command(exe: str, prompt_file, instructions: str, model: str | None = None,
                 tools: bool | list[str] = True, platform: str = sys.platform) -> list[str]:
    """tools: True all three, False none (a summary), or the ones this call keeps (["read_file"]: the screenshot)."""
    keep = TOOLS if tools is True else (tools or [])
    cmd = [exe, "--prompt-file", str(prompt_file), "--system-prompt-override", instructions,
           "--tools", ",".join(TOOLS),
           # MCP meta-tools always, and the read tools this call doesn't keep
           "--disallowed-tools", ",".join(["search_tool", "use_tool"] + [t for t in TOOLS if t not in keep]),
           "--disable-web-search", "--no-subagents", "--no-plan", "--permission-mode", "dontAsk",
           "--output-format", "streaming-messages-json", "--include-partial-messages"]
    if platform == "darwin":
        cmd += ["--sandbox", "strict"]        # reads: the working folder (knowledge base) and GROK_HOME only
    if model:
        cmd += ["--model", model]
    return cmd


def tools_note(kb_root) -> str:
    return (f"\n\nTools: the knowledge base is the current folder ({Path(kb_root).resolve()}): read it with "
            "read_file, grep and list_dir. Nothing outside it (and the screenshot) can be read. You cannot write "
            "files, run commands or use the web.")


# A quick screenshot read: the knowledge base isn't open, so the model doesn't go looking for it
SHOT_NOTE = ("\n\nThis is a quick screenshot read. The knowledge base is not open to you this time: do not search, "
             "list or open any folder. Your only tool is read_file, for the player's game screenshot (its path comes "
             "with the question). Open it first, then answer from it and the player's profile.")


def shots_line(shots: list[Path]) -> str:
    """The screenshot's path, with the question (as Gemini's): each run has a folder of its own, so in the
    instructions it changed them on every screenshot question."""
    return ("\n\nThe player's game screenshot is " + ", ".join(str(s) for s in shots) +
            ": open it with read_file first, before answering.") if shots else ""


def classify(text: str) -> str | None:
    t = text.lower()
    if "not signed in" in t or "not authenticated" in t or "sign in again" in t or base.http_status(t, 401):
        return "not_logged_in"
    if "rate limit" in t or "rate_limit" in t or "usage limit" in t or "quota" in t or base.http_status(t, 429):
        return "usage_limit"
    return classify_error(text)


def parse_stream(lines, on_delta=None, stats: dict | None = None) -> tuple[str, dict | None, str | None]:
    """(answer, result event, model) from Claude-Code-format stream lines: the text of the last message after its
    last tool call (text before a tool call is a lead-in). stats: filled with "tool_calls" and "turns" where the
    stream tells them (the evals)."""
    text, result, model = StreamText(), None, None
    tools: set = set()
    blocks = False
    for line in lines:
        if line is None:            # (base.Lines: nothing came for a moment)
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        blocks = base.note_tool_use(ev, tools) or blocks
        if t == "stream_event":
            se = ev.get("event") or {}
            if se.get("type") == "message_start":
                model = (se.get("message") or {}).get("model") or model
            if text.feed(se) and on_delta:
                on_delta(text.text)
        elif t == "system" and ev.get("subtype") == "init":
            model = ev.get("model") or model
        elif t == "result":
            result = ev
            break                   # the answer is complete: whatever the process does next doesn't matter
    if stats is not None:
        turns = (result or {}).get("num_turns")
        stats.update(tool_calls=len(tools) if blocks else None, turns=turns if isinstance(turns, int) else None)
    return text.text, result, model


def to_result(text: str, result: dict | None, stderr: str, model: str | None) -> RawResult:
    if not result or result.get("is_error") or result.get("subtype") != "success":
        detail = " ".join(str(e) for e in ((result or {}).get("errors") or [])) + "\n" + stderr
        log.warning("Grok gave no answer: %s", base.scrub(detail.strip()[-1500:]))
        return RawResult(error=classify(detail) or ("api_error" if result else "no_result"))
    answer = text or str(result.get("result") or "")
    if not answer.strip():
        return RawResult(error="no_result")
    return RawResult(text=answer, model=model)


def parse_models(output: str) -> list[tuple[str, str]]:
    """`grok models`: "  * grok-4.6 (default)" / "  - grok-4.6-fast" under "Available models:"."""
    out = []
    for line in output.splitlines():
        m = re.match(r"\s*[*-]\s+([\w.:-]+)", line)
        if m:
            out.append((m.group(1), base.model_name(m.group(1))))    # shown as "Grok 4.6", like the others
    return out


def signed_in_email() -> str | None:
    """The account's email from the sign-in grok saved in our home (auth.json: one entry per account, each
    with "email"); `grok models` only says "You are logged in with grok.com"."""
    try:
        data = json.loads((grok_home() / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in (data.values() if isinstance(data, dict) else []):
        if isinstance(entry, dict) and isinstance(entry.get("email"), str) and "@" in entry["email"]:
            return entry["email"]
    return None


def lightest(models: list[tuple[str, str]]) -> str | None:
    return next((m for m, _ in models if "fast" in m or "mini" in m), None)


_models_cache: list[tuple[str, str]] = []
_models_at = 0.0
_models_email: str | None = None
_check_lock = threading.Lock()


class CheckFailed(Exception):
    """grok was found but didn't answer in time: neither "not installed" nor "signed out"."""


class Offline(CheckFailed):
    """grok couldn't reach xAI: the sign-in may be fine, so not "signed out" (as Gemini's)."""


def _run(args: list[str], timeout: float = CHECK_TIMEOUT_S) -> subprocess.CompletedProcess | None:
    exe = find_grok()
    if not exe:
        return None
    with _check_lock:
        grok_home().mkdir(parents=True, exist_ok=True)
        try:
            return subprocess.run([exe, *args], capture_output=True, timeout=timeout, env=env(), cwd=str(home()),
                                  stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as e:
            log.warning("grok %s timed out", args[:1])
            raise CheckFailed() from e
        except OSError:
            log.warning("grok %s can't start", args[:1], exc_info=True)
            return None


def read_models(max_age: float = 10.0) -> tuple[list[tuple[str, str]], str | None] | None:
    """`grok models`: (the list, the account's email when it says) signed in, ([], None) signed out,
    None when grok is missing or won't start."""
    global _models_cache, _models_at, _models_email
    if _models_cache and time.monotonic() - _models_at < max_age:
        return _models_cache, _models_email
    r = _run(["models"])
    if r is None:
        return None
    out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    if "not authenticated" in out.lower() or "not signed in" in out.lower():
        return [], None
    found = parse_models(out)
    if not found and r.returncode != 0 and classify(out) == "offline":
        # no list because there's no connection: offering a sign-in would fail too
        log.warning("grok models: no connection: %s", base.scrub(out.strip()[-300:]))
        raise Offline()
    email = EMAIL.search(out)
    if found:
        _models_cache, _models_at, _models_email = found, time.monotonic(), email.group(0) if email else None
    return found, email.group(0) if email else None


def resolve_model(model: str | None) -> str | None:
    if model != SAVER_ALIAS:
        return model
    try:
        got = _models_cache or (read_models() or ([], None))[0]
    except CheckFailed:
        return None
    return lightest(got)


SESSION_ID = re.compile(r"[\w-]{8,100}")
SESSION_MAX_AGE_S = 24 * 3600


def drop_session(proc, session_id) -> None:
    """Grok saves every run as a session (the whole question, the ~12 KB instructions, every file it read) under
    GROK_HOME/sessions/<folder>/<id>, and no flag turns that off: each one is removed once its process has ended.
    One a crash left behind goes at the next start (sweep_sessions)."""
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return

    def drop():
        try:
            proc.wait(timeout=30)
        except (subprocess.TimeoutExpired, OSError):
            pass
        for d in (grok_home() / "sessions").glob(f"*/{session_id}"):
            shutil.rmtree(d, ignore_errors=True)
        _drop_prompt_history()
    threading.Thread(target=drop, daemon=True).start()


def _drop_prompt_history() -> None:
    """Grok also appends every run's whole prompt to sessions/<folder>/prompt_history.jsonl. This GROK_HOME is the
    app's own, so nothing else reads it: it goes with the sessions."""
    for h in (grok_home() / "sessions").glob("*/prompt_history.jsonl"):
        try:
            h.unlink(missing_ok=True)
        except OSError:
            pass


def sweep_sessions(max_age: float = SESSION_MAX_AGE_S) -> None:
    """Sessions older than a day (a quit or crash before drop_session ran)."""
    now = time.time()
    for d in (grok_home() / "sessions").glob("*/*"):
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
    _drop_prompt_history()


class Grok(Provider):
    name = "grok"
    label = "Grok"
    keyring_user = "xai_api_key"
    model_setting = "grok_model"
    reports_usage = False      # the CLI reports tokens per session, not the plan's quota

    @property
    def saver_model(self) -> str | None:
        return lightest(_models_cache) or SAVER_ALIAS

    def find_exe(self) -> str | None:
        return find_grok()

    def models(self) -> list[tuple[str | None, str]]:
        try:
            got = read_models()
        except CheckFailed:
            return [(None, "")]
        return [(None, "")] + (got[0] if got else [])

    def account(self) -> dict:
        if not find_grok():
            return {"status": "not_installed", "email": None}
        try:
            got = read_models(max_age=0)
        except Offline:
            return {"status": "offline", "email": None}
        except CheckFailed:
            return {"status": "logged_out", "email": None}
        if got is None:
            return {"status": "not_installed", "email": None}
        found, email = got
        if not found:
            return {"status": "logged_out", "email": None}
        return {"status": "ok", "email": email or signed_in_email()}

    def logout(self) -> bool:
        r = _run(["logout"])
        return bool(r) and r.returncode == 0

    def login(self) -> subprocess.Popen | None:
        """Device sign-in, hidden: grok prints a link carrying the code (opened here) and waits for the player
        to approve it in the browser, then ends 0. No code to paste."""
        exe = find_grok()
        if not exe:
            return None
        grok_home().mkdir(parents=True, exist_ok=True)
        url = []

        def on_line(text: str):
            # grok opens the link in the browser itself (the app opening it too made two tabs); only when it
            # says it couldn't does the app open it
            m = LOGIN_URL.search(text)
            if m and not url:
                url.append(m.group(0))
            elif "could not open browser" in text.lower() and url:
                import webbrowser
                webbrowser.open(url[0])
        return base.open_login(exe, ["login", "--device-auth"], env(), cwd=str(home()), on_line=on_line)

    def install(self) -> Installer:
        return run_installer(INSTALL_CMD, INSTALL_CMD_MAC)

    def test_api_key(self, key: str) -> bool:
        return http_ok("https://api.x.ai/v1/api-key", {"Authorization": f"Bearer {key}"})

    def backend(self, brain):
        return GrokBackend(brain)


class GrokBackend:
    """Runs questions for a Brain through grok, one fresh process per question."""

    def __init__(self, brain):
        self.brain = brain
        self.exe = find_grok()
        self._proc: subprocess.Popen | None = None
        self._running: set[subprocess.Popen] = set()

    def prewarm(self) -> None:
        # no process can wait for the question: grok takes it as a file named on its command line. The read guard
        # is built (~1 s, once) and checked ahead of the first question instead
        if self.exe:
            write_guard(self.brain.kb.root)

    def shutdown(self) -> None:
        self.cancel()
        for p in list(self._running):
            if p.poll() is None:
                base.kill(p)

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            base.kill(self._proc)

    def _exec(self, instructions: str, prompt: str, model: str | None, tools: bool | list[str] = True,
              on_delta=None, answer: bool = True, timeout: float | None = None,
              folder: Path | None = None) -> RawResult:
        """folder: the run's own folder in shots_dir() (one is made when None). The question goes in a file there,
        not in %TEMP%: macOS's strict sandbox reads only GROK_HOME and the knowledge base, and a folder a quit left
        behind is removed at the next start (app._remove_stray_screenshots)."""
        own = folder is None
        if own:
            shots_dir().mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix="run-", dir=shots_dir()))
        prompt_file = folder / "question.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        try:
            r = self._once(instructions, prompt_file, model, tools, on_delta, answer, timeout)
            if model and r.error == "bad_model":
                log.warning("Grok model %s unknown, using the default", model)
                r = self._once(instructions, prompt_file, None, tools, on_delta, answer, timeout)
            return RawResult(error="api_error") if r.error == "bad_model" else r
        finally:
            if own:
                shutil.rmtree(folder, ignore_errors=True)
            else:
                try:
                    prompt_file.unlink()
                except OSError:
                    pass

    def _once(self, instructions, prompt_file, model, tools, on_delta, answer, timeout) -> RawResult:
        cmd = grok_command(self.exe, prompt_file, instructions, model, tools)
        try:
            p = subprocess.Popen(cmd, cwd=str(self.brain.kb.root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=env(self.brain.api_key), creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            return RawResult(error=f"launch_failed: {e}")
        if answer:
            self._proc = p
        self._running.add(p)
        err: list[bytes] = []
        reader = threading.Thread(target=lambda: err.extend(iter(lambda: p.stderr.read(4096), b"")), daemon=True)
        reader.start()
        # read until the result line, not until the process exits (see base.Lines); status lines aren't progress
        out = base.Lines(p, STALL_TIMEOUT_S, "Grok", deadline_s=timeout)

        def lines():
            for line in out:
                if line is not None and base.line_kind(line) != "system/status":
                    out.touch()
                yield line
        stats: dict = {}
        try:
            text, result, used = parse_stream(lines(), on_delta, stats)
            out.finish()
            if not result or result.get("is_error") or result.get("subtype") != "success":
                reader.join(timeout=5)       # its words say what went wrong (an answer doesn't wait for them)
            stderr = b"".join(err).decode("utf-8", errors="replace")
        finally:
            self._running.discard(p)
        drop_session(p, (result or {}).get("session_id"))
        if out.stalled:
            log.warning("Grok stalled, stopped: %s", base.scrub(stderr[-1000:]))
            return RawResult(error="timeout")
        errors = " ".join(str(e) for e in ((result or {}).get("errors") or [])) + stderr
        if model and re.search(r"model.{0,40}(not found|unknown|invalid|not available)", errors, re.I):
            return RawResult(error="bad_model")
        r = to_result(text, result, stderr, used or model)
        r.tool_calls, r.turns = stats.get("tool_calls"), stats.get("turns")
        return r

    def run(self, prompt: str, screenshot_jpeg: bytes | None, on_raw_delta=None, model: str | None = None,
            tools: bool = True) -> RawResult:
        b = self.brain
        shots_dir().mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="run-", dir=shots_dir()))
        try:
            shots = []
            for i, jpeg in enumerate(screenshot_jpeg if isinstance(screenshot_jpeg, list) else [screenshot_jpeg]):
                if jpeg:
                    shots.append(folder / f"screenshot-{i}.jpg")
                    shots[-1].write_bytes(jpeg)
            if (tools or shots) and self.exe and not write_guard(b.kb.root):
                tools, shots = False, []        # no hook to trust: no file tools (Grok reads anything unguarded)
            if tools:
                reads, note = True, tools_note(b.kb.root)
            elif shots:
                # the ⟳ sync: the screenshot only (opened with read_file). With grep and list_dir too, a model goes
                # digging in the knowledge base and the sync gives up at 60 s (seen with Gemini: over two minutes)
                reads, note = ["read_file"], SHOT_NOTE
            else:
                reads, note = False, ""
            # the instructions stay the same from question to question: the screenshot's per-run path goes with it
            return self._exec(b.system_prompt() + note, prompt + shots_line(shots), resolve_model(model or b.model),
                              reads, on_raw_delta, folder=folder)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def summarize(self, instructions: str, text: str, timeout: int = 90) -> str | None:
        if not self.exe:
            return None
        r = self._exec(instructions, text, resolve_model(SAVER_ALIAS), tools=False, answer=False, timeout=timeout)
        return r.text.strip() or None
