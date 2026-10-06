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


def test_uninstall_asks_before_deleting_the_players_data():
    """SEC-12: the data folder (chats, characters, Grok/Gemini sign-ins) goes only when the player says yes:
    never on a silent uninstall, "No" the default, and nothing but {userappdata}\\MapleHelper."""
    code = _section(ISS, "Code")
    proc = code[code.index("procedure CurUninstallStepChanged"):]
    proc = proc[:proc.index("\nend;") + 5]
    assert "usPostUninstall" in proc and "UninstallSilent" in proc
    assert "MB_DEFBUTTON2" in proc and "= IDYES" in proc
    assert re.findall(r"ExpandConstant\('([^']*)'\)", proc) == ["{userappdata}\\MapleHelper"]
    assert proc.count("DelTree(") == 1 and "DelTree(DataDir," in proc
    msgs = _section(ISS, "CustomMessages")
    for lang in ("hebrew", "english"):
        assert re.search(rf"^{lang}\.DeleteUserData=.*%1", msgs, re.M), lang
    # saved API keys aren't in that folder: the question says so (review3 SEC12-a)
    assert re.search(r"^english\.DeleteUserData=.*Credential Manager; remove them in Settings first\.", msgs, re.M)
    assert re.search(r"^hebrew\.DeleteUserData=.*במנהל האישורים של Windows: מחקו אותם קודם בהגדרות\.", msgs, re.M)


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


def test_release_kb_job_retries_while_the_nightly_re_uploads():
    """The kb job is outside the publish group: a download mid --clobber found no kb.zip (the seed went into the
    installers) or a mismatched pair (review PLT-5). It retries, and takes the seed only if no manifest was seen."""
    steps = _workflow("release.yml")["jobs"]["kb"]["steps"]
    run = next(s for s in steps if s.get("name") == "Get the knowledge base")["run"]
    assert "for i in 1 2 3 4 5 6" in run and "sha256sum -c --status" in run and "--clobber" in run
    assert run.index('if [ -z "$seen" ]') < run.index("gh release download kb-seed")


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


def test_release_py_leaves_full_releases_to_the_workflow(monkeypatch):
    # a release cut from a PC had no macOS DMG and no portable zip: Mac players' download link 404'd
    import release
    monkeypatch.setattr(release, "run", lambda *a, **k: pytest.fail("ran " + repr(a)))
    with pytest.raises(SystemExit) as e:
        release.main(["9.9.9"])
    assert "Release workflow" in str(e.value)


def test_nightly_kb_manifest_says_when_it_was_checked(tmp_path, monkeypatch):
    import json
    import shutil

    import release
    kb = tmp_path / "kb"
    shutil.copytree(Path(__file__).parent / "fixtures" / "kb", kb)
    monkeypatch.setattr(release, "KB", kb)
    monkeypatch.setattr(release, "DIST", tmp_path / "dist")
    _, manifest = release.build_kb()
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", m["checked"]) and m["url"].endswith("/latest/download/kb.zip")
