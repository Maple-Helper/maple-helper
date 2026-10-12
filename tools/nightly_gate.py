"""The nightly KB refresh's test gate: the full suite against tonight's candidate KB, without letting one edited
page on NiaMeowDB hold back every other update.

A data test that pins a value ("these 26 maps have no continent", "Mano drops X") fails the night the site changes
that value. That is a change for a human to look at, not a reason to keep last night's KB from players. So:

1. the whole suite runs on the candidate (data/kb); all green -> publish.
2. each failed test runs again on the KB players have now (the latest release's kb.zip):
   - passes there -> "drift": tonight's data changed what the test expects;
   - fails there too -> "known": already failing before tonight (flaky runner, or an older drift not fixed yet).
3. up to --max-failures of them: the night publishes and the list goes to an issue (kb-update.yml). More than that
   looks like a broken scrape, not a few edited pages: the night stops.

A broken scrape is also stopped earlier by `kb_release.py validate` (shape, entity counts vs last night).

    python tools/nightly_gate.py [--max-failures 25] [--report gate-report.json] [-- pytest args]
    python tools/nightly_gate.py --issue-body reports/ --test-result success   # the kb-drift issue's text
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "data" / "kb"
LASTFAILED = ROOT / ".pytest_cache" / "v" / "cache" / "lastfailed"


def pytest(*args: str) -> int:
    env = dict(os.environ, MAPLEHELPER_REQUIRE_REAL_KB="1")
    return subprocess.call([sys.executable, "-m", "pytest", *args], cwd=ROOT, env=env)


def last_failed() -> list[str]:
    try:
        return sorted(json.loads(LASTFAILED.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def released_kb(into: Path) -> None:
    """The kb.zip of the latest release (what players have now), unpacked into `into`."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["gh", "release", "download", "--repo", os.environ.get("GITHUB_REPOSITORY",
                        "Maple-Helper/maple-helper"), "--pattern", "kb.zip", "--dir", tmp], check=True)
        with zipfile.ZipFile(Path(tmp) / "kb.zip") as z:
            z.extractall(into)


def classify(failed: list[str]) -> tuple[list[str], list[str]]:
    """(drift, known): rerun `failed` with data/kb swapped for the released KB, then put the candidate back."""
    held = KB.with_name("kb-candidate")
    if held.exists():
        shutil.rmtree(held)
    KB.rename(held)
    try:
        released_kb(KB)
        LASTFAILED.unlink(missing_ok=True)
        rc = pytest("-q", *failed)
        still = set(last_failed()) if rc else set()
    finally:
        shutil.rmtree(KB, ignore_errors=True)
        held.rename(KB)
    return [t for t in failed if t not in still], [t for t in failed if t in still]


def summary(report: dict) -> str:
    lines = [f"## Nightly KB gate: {report['verdict']}", ""]
    for kind, title in (("drift", "Changed by tonight's data (passed on the released KB)"),
                        ("known", "Failing on the released KB too")):
        if report[kind]:
            lines += [f"**{title}** ({len(report[kind])}):", ""] + [f"- `{t}`" for t in report[kind]] + [""]
    return "\n".join(lines)


def issue_body(reports_dir: str, test_result: str, translate: str = "") -> str | None:
    """The kb-drift issue's text from every OS's gate report, or None when there's nothing to look at.
    translate: "crashed" when the night's Hebrew translation died on a bug (kb-update.yml)."""
    reports = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(reports_dir).glob("*.json"))}
    drift = sorted({t for r in reports.values() for t in r["drift"]})
    known = sorted({t for r in reports.values() for t in r["known"]})
    if not (drift or known or translate == "crashed") and test_result in ("success", "skipped"):
        return None
    server, repo, run = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    lines = [f"Nightly KB run: {server}/{repo}/actions/runs/{run}  ", f"Test result: **{test_result}**", ""]
    lines += [f"- `{name}`: {r['verdict']}" for name, r in reports.items()]
    if translate == "crashed":
        lines += ["", "### The Hebrew translation crashed",
                  "tools/translate_kb.py exited with an error (a bug, not the API): nothing new was translated tonight. "
                  "See the run's \"Translate what's new into Hebrew\" step."]
    if drift:
        lines += ["", "### Tests the new KB changed",
                  "The site changed a value these tests pin. Check the change is real, then update the test to read "
                  "the rule or the new value (PR CI tests the published KB, so these fail there until fixed).", ""]
        lines += [f"- [ ] `{t}`" for t in drift]
    if known:
        lines += ["", "### Failing on the published KB too", ""] + [f"- [ ] `{t}`" for t in known]
    if not reports and test_result not in ("success", "skipped"):
        lines += ["", "No gate report: the test job stopped before the tests (see the run)."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--max-failures", type=int, default=25)
    ap.add_argument("--report", default="gate-report.json")
    ap.add_argument("--issue-body", metavar="REPORTS_DIR", help="print the kb-drift issue's text (exit 1: none)")
    ap.add_argument("--test-result", default="success")
    ap.add_argument("--translate", default="")
    ap.add_argument("pytest_args", nargs="*", help="extra pytest arguments for the first run")
    a = ap.parse_args(argv)
    if a.issue_body:
        body = issue_body(a.issue_body, a.test_result, a.translate)
        if body is None:
            return 1
        print(body)
        return 0

    LASTFAILED.unlink(missing_ok=True)
    rc = pytest(*a.pytest_args)
    report = {"verdict": "pass", "drift": [], "known": []}
    if rc not in (0, 1):                     # interrupted, internal error, collection error: nothing to classify
        report["verdict"] = f"blocked: pytest exit code {rc}"
    elif rc == 1:
        failed = last_failed()
        if not failed:
            report["verdict"] = "blocked: pytest failed but recorded no failed test"
        elif len(failed) > a.max_failures:
            report["verdict"] = f"blocked: {len(failed)} failed tests (more than {a.max_failures})"
            report["drift"] = failed
        else:
            report["drift"], report["known"] = classify(failed)
            report["verdict"] = "pass with data changes to review"

    Path(a.report).write_text(json.dumps(report, indent=1), encoding="utf-8")
    text = summary(report)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 1 if report["verdict"].startswith("blocked") else 0


if __name__ == "__main__":
    sys.exit(main())
