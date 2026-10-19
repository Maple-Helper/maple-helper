"""macOS self-update: a Mac app stayed on 0.8.3 for two weeks because the Mac build only linked to new releases.
Now it downloads the release's disk image (hash-checked like the Windows installer) and, once the app has quit,
a script mounts the image, checks the new app's signature and swaps the two apps with renames.

The script runs here for real, with stand-ins for macOS's own tools (hdiutil, codesign, ditto, xattr, open) first
on PATH: the swap, the rollback and the reopen are what's tested, on any POSIX runner."""
import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from maplehelper import macapi, updater

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the update script is bash")

API = "https://api.github.com/repos/Maple-Helper/maple-helper/releases/latest"
DMG = b"fake disk image bytes"


def dl(tag: str, name: str) -> str:
    return f"https://github.com/Maple-Helper/maple-helper/releases/download/{tag}/{name}"


# ---------------------------------------------------------------- download

@pytest.fixture
def release(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "USER_KB", tmp_path / "kb")
    net: dict[str, bytes | None] = {}
    monkeypatch.setattr(updater, "_get", lambda url, timeout=30: net.get(url))

    def publish(tag="v0.2.0", dmg=DMG, sums=None):
        sums = sums if sums is not None else (f"{hashlib.sha256(b'exe').hexdigest()}  MapleHelper-Setup.exe\n"
                                              f"{hashlib.sha256(dmg).hexdigest()}  MapleHelper-macOS.dmg\n")
        assets = [{"name": "MapleHelper-Setup.exe", "browser_download_url": dl(tag, "MapleHelper-Setup.exe"), "size": 3},
                  {"name": "MapleHelper-macOS.dmg", "browser_download_url": dl(tag, "MapleHelper-macOS.dmg"),
                   "size": len(dmg)},
                  {"name": "SHA256SUMS.txt", "browser_download_url": dl(tag, "SHA256SUMS.txt")}]
        net[API] = json.dumps({"tag_name": tag, "assets": assets}).encode()
        net[dl(tag, "MapleHelper-Setup.exe")] = b"exe"
        net[dl(tag, "MapleHelper-macOS.dmg")] = dmg
        net[dl(tag, "SHA256SUMS.txt")] = sums.encode()
    return tmp_path, net, publish


def test_a_mac_downloads_the_disk_image(release):
    _, _, publish = release
    publish()
    path = updater.download_app_update("0.1.0", platform="darwin")
    assert path and path.endswith("MapleHelper-macOS-v0.2.0.dmg") and open(path, "rb").read() == DMG
    assert updater.installer_version(path) == "0.2.0"


def test_windows_still_downloads_the_installer(release):
    _, _, publish = release
    publish()
    assert updater.download_app_update("0.1.0", platform="win32").endswith("MapleHelper-Setup-v0.2.0.exe")


def test_a_disk_image_with_the_wrong_hash_is_never_kept(release):
    _, net, publish = release
    publish()
    net[dl("v0.2.0", "MapleHelper-macOS.dmg")] = bytes(len(DMG))      # same size, other bytes
    assert updater.download_app_update("0.1.0", platform="darwin") is None


def test_a_release_without_the_images_checksum_gives_no_mac_update(release):
    _, _, publish = release
    publish(sums=f"{hashlib.sha256(b'exe').hexdigest()}  MapleHelper-Setup.exe\n")
    assert updater.download_app_update("0.1.0", platform="darwin") is None


def test_old_disk_images_are_swept(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "USER_KB", tmp_path / "kb")
    folder = tmp_path / "updates"
    folder.mkdir()
    for n in ("MapleHelper-macOS-v0.0.1.dmg", "MapleHelper-macOS-v99.0.0.dmg", "MapleHelper-macOS-v99.0.0.part"):
        (folder / n).write_bytes(b"x")
    updater.remove_old_installers()
    assert [f.name for f in folder.iterdir()] == ["MapleHelper-macOS-v99.0.0.dmg"]


# ---------------------------------------------------------------- which copies update themselves

def make_app(root, name="Maple Helper.app", version="old"):
    exe = root / name / "Contents" / "MacOS" / "Maple Helper"
    exe.parent.mkdir(parents=True)
    exe.write_text(version)
    return exe


def test_the_app_bundle_is_found_from_its_executable(tmp_path):
    exe = make_app(tmp_path)
    assert macapi.app_bundle(str(exe)) == tmp_path / "Maple Helper.app"
    assert macapi.app_bundle("/usr/local/bin/python3") is None
    assert macapi.app_bundle(str(tmp_path / "x")) is None


@posix_only
def test_only_a_writable_lasting_copy_updates_itself(tmp_path):
    exe = make_app(tmp_path)
    assert macapi.can_self_update(str(exe))
    assert not macapi.can_self_update("/Volumes/Maple Helper/Maple Helper.app/Contents/MacOS/Maple Helper")
    assert not macapi.can_self_update("/private/var/folders/x/T/AppTranslocation/ab/d/Maple Helper.app/Contents/MacOS/"
                                      "Maple Helper")
    assert not macapi.can_self_update("/usr/bin/python3")
    if os.geteuid() != 0:                                  # root writes anywhere
        os.chmod(tmp_path, 0o555)
        try:
            assert not macapi.can_self_update(str(exe))    # /Applications owned by another admin
        finally:
            os.chmod(tmp_path, 0o755)


def test_self_updates_asks_the_platforms_own_rule(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(macapi, "can_self_update", lambda exe=None: True)
    monkeypatch.setattr(updater, "installed_copy", lambda: False)
    assert updater.self_updates("darwin") and not updater.self_updates("win32")
    monkeypatch.setattr(sys, "frozen", False)
    assert not updater.self_updates("darwin")             # a run from source never updates itself


def test_a_mac_that_can_update_downloads_instead_of_linking(monkeypatch):
    from maplehelper import app
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app.osapi, "IS_MAC", True)
    monkeypatch.setattr(macapi, "can_self_update", lambda exe=None: True)
    monkeypatch.setattr(updater, "newer_release", lambda current: ("9.9.9", "url"))
    monkeypatch.setattr(app.threading, "Thread", lambda target, daemon=None: SimpleNamespace(start=target))
    calls = []
    fake = SimpleNamespace(pending_installer=None, _downloading=False,
                           main_thread=SimpleNamespace(call=SimpleNamespace(emit=lambda fn: fn())),
                           update_found=lambda v: calls.append(("found", v)),
                           announce_update=lambda v, url: calls.append(("announce", v)))
    app.MapleHelperApp.check_app_update_silently(fake)
    assert calls == [("found", "9.9.9")]
    monkeypatch.setattr(macapi, "can_self_update", lambda exe=None: False)   # run from the disk image
    app.MapleHelperApp.check_app_update_silently(fake)
    assert calls[-1] == ("announce", "9.9.9")


# ---------------------------------------------------------------- the install script, run for real

STUBS = {
    # hdiutil attach IMG ... -mountpoint MNT: "mount" the image by copying the folder named in it
    "hdiutil": """#!/bin/bash
echo "hdiutil $*" >> "$STUB_LOG"
if [ "$1" = attach ]; then
  [ -n "${FAIL_MOUNT:-}" ] && exit 1
  img="$2"; while [ $# -gt 0 ]; do [ "$1" = -mountpoint ] && mnt="$2"; shift; done
  cp -R "$(cat "$img")/." "$mnt/"
fi
exit 0
""",
    "codesign": """#!/bin/bash
echo "codesign $*" >> "$STUB_LOG"
for a in "$@"; do last="$a"; done
[ -e "$last/BROKEN" ] && exit 1
exit 0
""",
    "ditto": '#!/bin/bash\necho "ditto $*" >> "$STUB_LOG"\ncp -R "$1" "$2"\n',
    "xattr": '#!/bin/bash\necho "xattr $*" >> "$STUB_LOG"\nexit 0\n',
    "open": '#!/bin/bash\necho "open $*" >> "$STUB_LOG"\nexit 0\n',
}


@pytest.fixture
def mac(tmp_path):
    """An installed app, a "disk image" holding a new one, and macOS's tools stubbed on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    apps = tmp_path / "Applications"
    apps.mkdir()
    make_app(apps, version="old")
    volume = tmp_path / "volume"
    make_app(volume, version="new")
    updates = tmp_path / "updates"
    updates.mkdir()
    dmg = updates / "MapleHelper-macOS-v0.2.0.dmg"
    dmg.write_text(str(volume))                         # the stub "mounts" this folder
    gone = subprocess.Popen(["true"])
    gone.wait()                                         # the app's process: already quit
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(tmp_path / "calls.log"),
           "TMPDIR": str(tmp_path)}

    def run(reopen=False, pid=gone.pid, **extra):
        script = updates / "update.sh"
        log = tmp_path / "update.log"
        script.write_text(macapi.update_script(str(dmg), str(apps / "Maple Helper.app"), pid, reopen, str(log)))
        code = subprocess.run(["/bin/bash", str(script)], env={**env, **extra}, timeout=60).returncode
        calls = (tmp_path / "calls.log").read_text() if (tmp_path / "calls.log").exists() else ""
        return code, log.read_text(), calls

    def installed():
        return (apps / "Maple Helper.app" / "Contents" / "MacOS" / "Maple Helper").read_text()

    return SimpleNamespace(run=run, installed=installed, apps=apps, volume=volume, dmg=dmg, tmp=tmp_path)


@posix_only
def test_the_script_installs_the_new_app(mac):
    code, log, calls = mac.run()
    assert code == 0 and "installed" in log
    assert mac.installed() == "new"
    assert sorted(p.name for p in mac.apps.iterdir()) == ["Maple Helper.app"]     # no .old, no .update left
    assert not mac.dmg.exists()                                                     # nor the 100 MB image
    assert "codesign --verify --deep --strict" in calls and "hdiutil detach" in calls
    assert "open" not in calls.replace("noautoopen", "")       # an update on Quit leaves the app closed


@posix_only
def test_update_now_reopens_the_new_app_with_whats_new(mac):
    code, _, calls = mac.run(reopen=True)
    assert code == 0 and mac.installed() == "new"
    assert f"open {mac.apps / 'Maple Helper.app'} --args --updated" in calls


@posix_only
@pytest.mark.parametrize("break_it,why", [
    (lambda m: (m.volume / "Maple Helper.app" / "BROKEN").write_text(""), "signature is broken"),
    (lambda m: __import__("shutil").rmtree(m.volume / "Maple Helper.app"), "no Maple Helper.app"),
])
def test_a_bad_image_leaves_the_old_app(mac, break_it, why):
    break_it(mac)
    code, log, calls = mac.run(reopen=True)
    assert code == 1 and why in log
    assert mac.installed() == "old"
    assert sorted(p.name for p in mac.apps.iterdir()) == ["Maple Helper.app"]
    assert mac.dmg.exists()                     # kept: the next start offers it again without a download
    assert f"open {mac.apps / 'Maple Helper.app'}\n" in calls       # the player isn't left with no app


@posix_only
def test_an_image_that_wont_mount_leaves_the_old_app(mac):
    code, log, _ = mac.run(FAIL_MOUNT="1")
    assert code == 1 and "could not mount" in log and mac.installed() == "old"


@posix_only
def test_the_app_is_never_replaced_while_it_runs(mac):
    sleeper = subprocess.Popen(["sleep", "300"])
    try:
        script_pid = sleeper.pid
        # a shortened wait: the real one is a minute
        script = macapi.update_script(str(mac.dmg), str(mac.apps / "Maple Helper.app"), script_pid, False,
                                      str(mac.tmp / "update.log")).replace("seq 1 240", "seq 1 2")
        (mac.tmp / "update.sh").write_text(script)
        code = subprocess.run(["/bin/bash", str(mac.tmp / "update.sh")], timeout=60).returncode
    finally:
        sleeper.kill()
        sleeper.wait()
    assert code == 1 and "still running" in (mac.tmp / "update.log").read_text()
    assert mac.installed() == "old"


@posix_only
def test_paths_with_quotes_and_spaces_stay_one_word(tmp_path):
    script = macapi.update_script("/x/it's a dmg.dmg", "/Applications/Maple Helper.app", 1, False, "/x/l.log")
    assert "DMG='/x/it'\\''s a dmg.dmg'" in script
    assert subprocess.run(["/bin/bash", "-n", "-c", script]).returncode == 0       # parses


def test_run_update_needs_a_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    assert macapi.run_update(str(tmp_path / "x.dmg"), False, tmp_path / "logs") is False
