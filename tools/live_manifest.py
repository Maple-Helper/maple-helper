"""Writes assets/live.json: every live file under assets/ (live.kind) with its checksum and its kind's contract.

The app reads this list from GitHub's main branch every 15 minutes and downloads each file that differs from the copy
it shipped with (maplehelper/live.py, docs/LIVE.md). Run it after changing a live file; tests/test_live.py fails while
the list is out of date.

    python tools/live_manifest.py           # write it
    python tools/live_manifest.py --check   # exit 1 when it is out of date
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maplehelper import live  # noqa: E402

ASSETS = ROOT / "assets"
OUT = ASSETS / live.MANIFEST


def build() -> str:
    files = {}
    for p in sorted(ASSETS.rglob("*")):
        rel = p.relative_to(ASSETS).as_posix()
        k = live.kind(rel) if p.is_file() else None
        if k:
            files[rel] = {"sha256": live.text_sha(p.read_bytes()), "contract": live.CONTRACTS[k]}
    return json.dumps({"schema": 1, "files": files}, indent=1, sort_keys=True) + "\n"


def write() -> None:
    """Writes the list when it changed (the tools that write live files call it at their end)."""
    if not current():
        OUT.write_text(build(), encoding="utf-8", newline="\n")
        print(f"wrote {OUT.relative_to(ROOT)}")


def current() -> bool:
    try:
        return OUT.read_text(encoding="utf-8").replace("\r\n", "\n") == build()
    except OSError:
        return False


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        ok = current()
        print("assets/live.json is current" if ok else "assets/live.json is out of date: run python tools/live_manifest.py")
        sys.exit(0 if ok else 1)
    write()
