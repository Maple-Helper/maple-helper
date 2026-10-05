"""The installer script and the release workflows: settings a player or a release depends on (read as text)."""
import re
from pathlib import Path

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
