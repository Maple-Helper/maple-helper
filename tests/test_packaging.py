"""The installer script and the release workflows: settings a player or a release depends on (read as text)."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")


def _section(text: str, name: str) -> str:
    start = text.index(f"[{name}]")
    nxt = re.search(r"^\[\w+\]", text[start + 1:], re.M)
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


def test_installer_falls_back_to_english():
    # Inno uses the first language when the Windows locale matches none: a Spanish PC got the Hebrew wizard
    langs = re.findall(r'^Name: "(\w+)"', _section(ISS, "Languages"), re.M)
    assert langs == ["english", "hebrew"]


def test_hebrew_installer_names_its_own_buttons():
    msgs = _section(ISS, "Messages")
    assert "hebrew.ButtonFinish=&סיום" in msgs and "'סיום'" in msgs


def test_hebrew_installer_speaks_in_plural():
    # the stock Hebrew.isl texts use the singular ("אם תצא", "האם אתה בטוח", "שתאפשר")
    msgs = _section(ISS, "Messages")
    for key in ("ExitSetupMessage", "ApplicationsFound", "ApplicationsFound2", "PrepareToInstallNeedsRestart",
                "ConfirmUninstall"):
        line = re.search(rf"^hebrew\.{key}=(.*)$", msgs, re.M)
        assert line, key
        for singular in ("תצא ", "אתה", "שתאפשר", "תרצה", "הפעל "):
            assert singular not in line.group(1), (key, singular)


def test_update_clears_old_package_metadata():
    assert 'Type: filesandordirs; Name: "{app}\\_internal\\*.dist-info"' in _section(ISS, "InstallDelete")


def _workflow(name: str) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def test_ci_never_cancels_a_run_on_main():
    # two quick merges cancelled the first merge's run: a main commit was left with no CI result
    assert _workflow("ci.yml")["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_every_workflow_job_has_a_time_limit():
    for name in ("ci.yml", "test.yml", "release.yml", "kb-update.yml"):
        for job_id, job in _workflow(name)["jobs"].items():
            assert "uses" in job or "timeout-minutes" in job, (name, job_id)


def test_only_the_publish_jobs_wait_for_each_other():
    # on the whole workflow, a tag pushed during the ~1 h nightly waited behind it (or was cancelled while pending)
    release, nightly = _workflow("release.yml"), _workflow("kb-update.yml")
    assert "concurrency" not in release
    assert release["jobs"]["publish"]["concurrency"] == nightly["jobs"]["publish"]["concurrency"] == \
        {"group": "release-assets", "cancel-in-progress": False}
    assert nightly["concurrency"]["group"] != "release-assets"


def test_release_verifies_downloads_on_a_rerun_too():
    steps = _workflow("release.yml")["jobs"]["publish"]["steps"]
    verify = next(s for s in steps if s.get("name") == "Verify what players will download")
    assert "if" not in verify and 'if [ "$SKIP" = true ]' in verify["run"]


def test_every_kb_gate_asks_for_500_entities():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert re.findall(r"--min-entities (\d+)", text) == ["500", "500"]


def test_release_round_trip_updates_over_the_published_installer():
    build = _workflow("release.yml")["jobs"]["build"]["steps"]
    assert any("-UpgradeFrom prev-release/MapleHelper-Setup.exe" in s.get("run", "") for s in build)
    assert "[string]$UpgradeFrom" in (ROOT / "packaging" / "build.ps1").read_text(encoding="utf-8")


def test_workflows_run_the_apps_python():
    for name in ("ci.yml", "test.yml", "release.yml", "kb-update.yml"):
        for job in _workflow(name)["jobs"].values():
            for step in job.get("steps", []):
                if "setup-python" in step.get("uses", ""):
                    assert step["with"]["python-version"] == "3.13", name
