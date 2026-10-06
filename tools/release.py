"""Build and publish a release: exe (PyInstaller) → installer (Inno Setup) → GitHub Release
with MapleHelper-Setup.exe, kb.zip and kb-manifest.json.

Usage:
    python tools/release.py --kb-only        # refresh kb.zip/kb-manifest.json on the latest release (the nightly)
    python tools/release.py 0.1.0 --force    # emergency only: Windows installer + KB, no macOS DMG or portable zip

Full releases are made by the Release workflow (push a tag vX.Y.Z, see docs/RELEASING.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_release  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
KB = ROOT / "data" / "kb"
ISCC = Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
REPO = "Maple-Helper/maple-helper"


def run(cmd, **kw):
    print("›", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, cwd=ROOT, **kw)


def set_version(version: str) -> None:
    init = ROOT / "maplehelper" / "__init__.py"
    init.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', init.read_text(encoding="utf-8")),
                    encoding="utf-8")
    vi = ROOT / "packaging" / "version_info.txt"
    parts = (version.split(".") + ["0", "0", "0"])[:3]
    t = vi.read_text(encoding="utf-8")
    t = re.sub(r"filevers=\([^)]*\)", f"filevers=({', '.join(parts)}, 0)", t)
    t = re.sub(r"prodvers=\([^)]*\)", f"prodvers=({', '.join(parts)}, 0)", t)
    t = re.sub(r"'(FileVersion|ProductVersion)', '[^']*'", rf"'\1', '{version}'", t)
    vi.write_text(t, encoding="utf-8")


def record_patch_notes(version: str) -> None:
    """Compare against the KB players have now (the latest release's kb.zip) and add the
    differences to changelog.json, which the app shows as patch notes after the update."""
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(["gh", "release", "download", "--repo", REPO, "--pattern", "kb.zip", "--dir", tmp],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("no published kb.zip to compare with - no patch notes this time")
            return
        prev = Path(tmp) / "kb"
        with zipfile.ZipFile(Path(tmp) / "kb.zip") as z:
            z.extractall(prev)
        kb_release.refresh_tables(prev)     # both sides' drops by today's rules: only data changes are notes
        entry = kb_release.record_changes(KB, prev, version)
        print("patch notes:", json.dumps(entry["counts"]) if entry else "no visible changes")


def build_kb(patch_notes: bool = False) -> tuple[Path, Path]:
    version = time.strftime("%Y.%m.%d.%H%M", time.gmtime())   # UTC, like kb_release.pack
    kb_release.refresh_tables(KB)       # the zip (and the app bundling data/kb) carries tables of this very KB
    if patch_notes:
        record_patch_notes(version)
    meta = json.loads((KB / "meta.json").read_text(encoding="utf-8")) if (KB / "meta.json").exists() else {}
    meta["version"] = version
    (KB / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    zpath = DIST / "kb.zip"
    DIST.mkdir(exist_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in KB.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(KB))
    sha = hashlib.sha256(zpath.read_bytes()).hexdigest()
    manifest = DIST / "kb-manifest.json"
    # "checked" like kb_release.pack and the nightly's stamp: the app shows it as "knowledge base verified on"
    manifest.write_text(json.dumps({"version": version, "sha256": sha,
                                    "url": f"https://github.com/{REPO}/releases/latest/download/kb.zip",
                                    "checked": time.strftime("%Y-%m-%d", time.gmtime())}),
                        encoding="utf-8")
    print(f"kb {version}: {zpath.stat().st_size / 1e6:.1f} MB")
    return zpath, manifest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?")
    ap.add_argument("--kb-only", action="store_true")
    ap.add_argument("--notes", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    # a release cut here has no macOS DMG (Mac players' download link 404s once it is latest), no portable zip and
    # no tag/main check: the Release workflow makes full releases
    if not args.kb_only and not args.force:
        sys.exit("Full releases are made by the Release workflow: push a tag vX.Y.Z (docs/RELEASING.md). "
                 "--force publishes a Windows-only release from this PC anyway.")

    # the same gate as CI (release.yml, kb-update.yml): never ship or bundle a broken or partial KB
    try:
        print("KB valid:", kb_release.validate(KB, min_entities=500))
    except kb_release.InvalidKB as e:
        sys.exit(f"knowledge base at {KB} is not fit to ship: {e}")
    kb_zip, manifest = build_kb(patch_notes=args.kb_only)
    if args.kb_only:
        tag = subprocess.run(["gh", "release", "view", "--repo", REPO, "--json", "tagName", "-q", ".tagName"],
                             capture_output=True, text=True, check=True).stdout.strip()
        run(["gh", "release", "upload", tag, str(kb_zip), str(manifest), "--clobber", "--repo", REPO])
        return

    if not args.version:
        sys.exit("version required, e.g. 0.1.0")
    set_version(args.version)
    run([PY, "-m", "pytest", "tests", "-q"])
    run([PY, "-m", "PyInstaller", "packaging/maplehelper.spec", "--noconfirm", "--distpath", "dist", "--workpath", "build"])
    report = DIST / "selftest.txt"
    # the self-test's verdict is its exit code (run() raises on failure); the report says why
    try:
        run([DIST / "Maple Helper" / "Maple Helper.exe", "--selftest", report, "--require-kb"])
    finally:
        if report.exists():
            print(report.read_text(encoding="utf-8"))
    run([ISCC, f"/DAppVersion={args.version}", "/Q", "packaging/installer.iss"])
    setup = DIST / "MapleHelper-Setup.exe"
    # the in-app updater only runs an installer whose hash matches the release's SHA256SUMS.txt
    sums = DIST / "SHA256SUMS.txt"
    sums.write_text(f"{hashlib.sha256(setup.read_bytes()).hexdigest()}  {setup.name}\n", encoding="ascii")
    notes = args.notes or f"Maple Helper {args.version}"
    run(["gh", "release", "create", f"v{args.version}", str(setup), str(sums), str(kb_zip), str(manifest),
         "--repo", REPO, "--title", f"Maple Helper {args.version}", "--notes", notes])


if __name__ == "__main__":
    main()
