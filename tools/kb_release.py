"""Knowledge-base checks and packing used by CI (and by hand).

    python tools/kb_release.py validate data/kb [--previous old/index.json] [--min-entities N]
    python tools/kb_release.py pack data/kb dist-kb [--version 2026.10.02.1200] [--previous-kb old_kb_dir]

`validate` is the gate in front of every KB publish: a broken scrape must never reach players.
`pack` writes kb.zip + kb-manifest.json in the format maplehelper/updater.py reads (the same
format tools/release.py writes): {"version", "sha256", "url": ".../releases/latest/download/kb.zip"}.
Versions are zero-padded UTC timestamps, so plain string comparison orders them.
With a previous KB, `pack` also records what changed (new / removed entries, stat and drop changes)
in changelog.json, which the app shows players as patch notes after an update.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path

REPO = "Maple-Helper/maple-helper"
CATEGORIES = ["monster", "item", "map", "quest", "npc", "skill", "class", "guide", "shop", "crafting", "formula"]
MIN_KEEP_RATIO = 0.9   # an update may not lose more than 10% of the previous entities
CHANGELOG = "changelog.json"
NEWS = "news.json"           # MapleStory Classic news (tools/scrape_news.py); optional, but never broken
CHANGELOG_KEEP = 30    # updates kept, so a player who skipped a few still sees everything they missed
MAX_LISTED = 300       # per list in one update; the rest is only counted
# maplehelper/availability.py reads what is in the live game from this guide: without it (or its two sections)
# the app can't tell released content from unreleased, so a KB lacking it is never published
RELEASE_GUIDE = "guide/maplestory-classic-worlds-release-date"
RELEASE_GUIDE_SECTIONS = ("Confirmed content", "Not at launch")


class InvalidKB(Exception):
    pass


def validate(kb: Path, previous_index: Path | None = None, min_entities: int = 1,
             categories: list[str] = CATEGORIES) -> dict:
    """Raise InvalidKB listing every problem found; return a small summary when the KB is usable."""
    problems: list[str] = []
    try:
        index = json.loads((kb / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InvalidKB(f"index.json unreadable: {e}") from e
    if not isinstance(index, list):
        raise InvalidKB("index.json is not a list")

    count = len(index)
    if count < min_entities:
        problems.append(f"only {count} entities (minimum {min_entities})")
    if previous_index and previous_index.exists():
        prev = len(json.loads(previous_index.read_text(encoding="utf-8")))
        if count < prev * MIN_KEEP_RATIO:
            problems.append(f"{count} entities, down from {prev} (more than {100 - MIN_KEEP_RATIO * 100:.0f}% lost)")

    seen = {e.get("category") for e in index if isinstance(e, dict)}
    missing_cats = [c for c in categories if c not in seen]
    if missing_cats:
        problems.append("missing categories: " + ", ".join(missing_cats))

    missing_pages = []
    for e in index:
        key = e.get("key", "") if isinstance(e, dict) else ""
        cat, _, slug = key.partition("/")
        if not (cat and slug and e.get("name")):
            problems.append(f"malformed entry: {str(e)[:80]}")
            continue
        if not (kb / "pages" / cat / f"{slug}.md").exists():
            missing_pages.append(key)
    if missing_pages:
        problems.append(f"{len(missing_pages)} entries without a page, e.g. {', '.join(missing_pages[:5])}")

    guide = kb / "pages" / f"{RELEASE_GUIDE}.md"
    if RELEASE_GUIDE not in {e.get("key") for e in index if isinstance(e, dict)} or not guide.exists():
        problems.append(f"no release guide ({RELEASE_GUIDE}): the app can't tell what is in the game without it")
    else:
        text = guide.read_text(encoding="utf-8", errors="replace")
        lost = [h for h in RELEASE_GUIDE_SECTIONS if not re.search(rf"^{re.escape(h)}\s*$", text, re.M)]
        if lost:
            problems.append(f"the release guide lost its section(s): {', '.join(lost)}")

    news = kb / NEWS
    if news.exists():
        try:
            items = json.loads(news.read_text(encoding="utf-8")).get("items")
            if not isinstance(items, list) or not all(isinstance(n, dict) and n.get("id") and n.get("title")
                                                      and n.get("date") for n in items):
                problems.append("news.json: items without an id, a title or a date")
        except (OSError, ValueError, AttributeError) as e:
            problems.append(f"news.json unreadable: {e}")

    if problems:
        raise InvalidKB("; ".join(problems))
    return {"count": count, "categories": sorted(seen)}


# ---------------------------------------------------------------- patch notes

def _index(kb: Path) -> dict[str, dict]:
    try:
        return {e["key"]: e for e in json.loads((kb / "index.json").read_text(encoding="utf-8")) if "key" in e}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _drops(kb: Path) -> dict[str, dict[str, str]]:
    """monster key -> {item key: item name}"""
    out: dict[str, dict[str, str]] = {}
    try:
        with open(kb / "drops.tsv", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f, delimiter="	"):
                if r.get("monster_key") and r.get("item_key"):
                    out.setdefault(r["monster_key"], {})[r["item_key"]] = r.get("item") or r["item_key"]
    except OSError:
        pass
    return out


def _brief(e: dict) -> dict:
    return {"key": e["key"], "name": e.get("name") or e["key"], "category": e.get("category", "")}


def diff_kb(old: Path, new: Path) -> dict:
    """What a player would notice between two KBs: entries added/removed, stats and drops changed,
    and entries whose page text changed without a stat change ("updated")."""
    a, b = _index(old), _index(new)
    da, db = _drops(old), _drops(new)
    added = [_brief(b[k]) for k in sorted(b.keys() - a.keys())]
    removed = [_brief(a[k]) for k in sorted(a.keys() - b.keys())]
    changed, updated = [], []
    for k in sorted(a.keys() & b.keys()):
        pa, pb = a[k].get("props") or {}, b[k].get("props") or {}
        props = [[f, pa.get(f), pb.get(f)] for f in sorted(pa.keys() | pb.keys()) if pa.get(f) != pb.get(f)]
        oa, ob = da.get(k, {}), db.get(k, {})
        drops_added = sorted(ob[i] for i in ob.keys() - oa.keys())
        drops_removed = sorted(oa[i] for i in oa.keys() - ob.keys())
        renamed = a[k].get("name") != b[k].get("name")
        if props or drops_added or drops_removed or renamed:
            c = _brief(b[k])
            if renamed:
                c["old_name"] = a[k].get("name")
            if props:
                c["props"] = props
            if drops_added:
                c["drops_added"] = drops_added
            if drops_removed:
                c["drops_removed"] = drops_removed
            changed.append(c)
        elif a[k].get("hash") != b[k].get("hash"):
            updated.append(_brief(b[k]))
    counts = {"added": len(added), "removed": len(removed), "changed": len(changed), "updated": len(updated)}
    out = {"counts": counts, "added": added[:MAX_LISTED], "removed": removed[:MAX_LISTED],
           "changed": changed[:MAX_LISTED], "updated": updated[:MAX_LISTED]}
    # news items new since the previous KB (tools/scrape_news.py): the patch notes' News tab and the chat's news
    # card read news.json itself; the changelog says an update brought news, so a news-only night is an update too
    na, nb = _news(old), _news(new)
    fresh = [nb[i] for i in nb if i not in na]
    if fresh:
        counts["news"] = len(fresh)
        out["news"] = [{k: n.get(k) for k in ("id", "title", "date", "region", "official")} for n in fresh[:MAX_LISTED]]
    return out


def _news(kb: Path) -> dict[str, dict]:
    """news.json's items by id, newest first; {} without one."""
    try:
        items = json.loads((kb / NEWS).read_text(encoding="utf-8")).get("items", [])
        return {n["id"]: n for n in items if isinstance(n, dict) and n.get("id")}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def record_changes(kb: Path, previous_kb: Path, version: str) -> dict | None:
    """Prepend this update's changes to kb/changelog.json (newest first). None when nothing changed."""
    d = diff_kb(previous_kb, kb)
    if not any(d["counts"].values()):
        return None
    path = kb / CHANGELOG
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
        log = log if isinstance(log, list) else []
    except (OSError, json.JSONDecodeError):
        log = []
    entry = {"version": version, "date": time.strftime("%Y-%m-%d", time.gmtime()), **d}
    log = [entry] + [e for e in log if e.get("version") != version]
    path.write_text(json.dumps(log[:CHANGELOG_KEEP], ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


def pack(kb: Path, out: Path, version: str | None = None, previous_kb: Path | None = None) -> dict:
    """Stamp the version into meta.json, zip the KB (files at the zip root) and write the manifest."""
    version = version or time.strftime("%Y.%m.%d.%H%M", time.gmtime())
    if previous_kb:
        record_changes(kb, previous_kb, version)
    meta_path = kb / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["version"] = version
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    out.mkdir(parents=True, exist_ok=True)
    zpath = out / "kb.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(p for p in kb.rglob("*") if p.is_file()):
            z.write(f, f.relative_to(kb).as_posix())
    manifest = {"version": version, "sha256": hashlib.sha256(zpath.read_bytes()).hexdigest(),
                "url": f"https://github.com/{REPO}/releases/latest/download/kb.zip",
                # when NiaMeowDB was last checked; the nightly run moves it on even when nothing changed
                "checked": time.strftime("%Y-%m-%d", time.gmtime())}
    (out / "kb-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("kb", type=Path)
    v.add_argument("--previous", type=Path)
    v.add_argument("--min-entities", type=int, default=1)
    p = sub.add_parser("pack")
    p.add_argument("kb", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--version")
    p.add_argument("--previous-kb", type=Path, help="the published KB, to record patch notes against")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "validate":
            print("KB valid:", json.dumps(validate(a.kb, a.previous, a.min_entities)))
        else:
            print("Packed:", json.dumps(pack(a.kb, a.out, a.version, a.previous_kb)))
    except InvalidKB as e:
        print(f"::error::Knowledge base rejected: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
