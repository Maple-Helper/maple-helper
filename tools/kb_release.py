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
CHANGELOG_KEEP = 30    # updates kept, so a player who skipped a few still sees everything they missed
MAX_LISTED = 300       # per list in one update; the rest is only counted
# maplehelper/availability.py reads what is in the live game from this guide: without it (or its two sections)
# the app can't tell released content from unreleased, so a KB lacking it is never published
RELEASE_GUIDE = "guide/maplestory-classic-worlds-release-date"
RELEASE_GUIDE_SECTIONS = ("Confirmed content", "Not at launch")
# players' drop and mesos reports per monster (tools/scrape_community.py); optional, but checked when present
COMMUNITY = "community.json"
# a community drop is shown when more players confirmed it than denied it: the app's rule too
# (maplehelper/kb.py COMMUNITY_MIN_SCORE), repeated here because CI runs this file without the app's packages
COMMUNITY_MIN_SCORE = 1

# the list pages read beside the entity pages (tools/meowdb_sections.py): file -> (its list, the fields each row
# needs, the fewest rows a good read has)
SECTIONS = {"skill_changes.json": ("skills", ("key", "name", "changes"), 10),
            "pets.json": ("pets", ("key", "name", "lifespan", "sold"), 5),
            "tiers.json": ("rows", ("key", "name", "cells"), 5)}

# the map connections maplehelper/routes.py finds the way with (tools/scrape_meowdb.py): optional, but never broken
ROUTES = "routes.json"
MIN_ROUTED_MAPS = 0.9   # routes.json must cover nearly every map page


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

    if (kb / COMMUNITY).exists():
        try:
            validate_community(json.loads((kb / COMMUNITY).read_text(encoding="utf-8")),
                               {e.get("key") for e in index if isinstance(e, dict)})
        except (OSError, ValueError) as e:
            problems.append(f"{COMMUNITY} unreadable: {e}")
        except InvalidKB as e:
            problems.append(str(e))

    keys = {e.get("key") for e in index if isinstance(e, dict)}
    for name in SECTIONS:
        problems += _section_problems(kb, name, keys)

    problems += _route_problems(kb, {e.get("key") for e in index if isinstance(e, dict)})

    if problems:
        raise InvalidKB("; ".join(problems))
    return {"count": count, "categories": sorted(seen)}


def validate_community(data, keys: set[str]) -> None:
    """community.json's shape, and that every monster and item it names is in this KB; raise InvalidKB."""
    problems: list[str] = []
    monsters = data.get("monsters") if isinstance(data, dict) else None
    if not isinstance(monsters, dict):
        raise InvalidKB(f"{COMMUNITY}: no 'monsters' object")

    def num(v) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    for mkey, entry in monsters.items():
        where = f"{COMMUNITY} {mkey}"
        if not mkey.startswith("monster/") or mkey not in keys:
            problems.append(f"{where}: not a monster of the KB")
        if not isinstance(entry, dict) or not isinstance(entry.get("drops"), list):
            problems.append(f"{where}: no drops list")
            continue
        for d in entry["drops"]:
            if not (isinstance(d, dict) and str(d.get("item", "")).startswith("item/") and d["item"] in keys
                    and all(num(d.get(f)) for f in ("up", "down", "score"))):
                problems.append(f"{where}: bad drop {str(d)[:80]}")
        m = entry.get("mesos")
        if m is not None and not (isinstance(m, dict) and num(m.get("min")) and num(m.get("max"))
                                  and 0 <= m["min"] <= m["max"] and num(m.get("count")) and m["count"] > 0
                                  and (m.get("chance") is None or num(m["chance"]) and 0 <= m["chance"] <= 100)):
            problems.append(f"{where}: bad mesos {str(m)[:80]}")
        if len(problems) > 20:
            break
    if problems:
        raise InvalidKB("; ".join(problems[:20]))


def _shown(entry: dict | None) -> set[str]:
    """The community drops of one monster a player sees (COMMUNITY_MIN_SCORE)."""
    return {d["item"] for d in (entry or {}).get("drops") or [] if d.get("score", 0) >= COMMUNITY_MIN_SCORE}


def _mesos_range(entry: dict | None) -> str | None:
    m = (entry or {}).get("mesos")
    return f"{m['min']}-{m['max']}" if m else None


def community_changes(old: dict, new: dict) -> int:
    """How many monsters' community data changed in a way the patch notes show: a drop shown or hidden, or the
    mesos range moved. Votes alone don't count, or every night would publish a new KB."""
    return sum(1 for k in old.keys() | new.keys()
               if _shown(old.get(k)) != _shown(new.get(k)) or _mesos_range(old.get(k)) != _mesos_range(new.get(k)))

def _section_problems(kb: Path, name: str, keys: set) -> list[str]:
    """A list page's file, when there is one: its rows, each with the fields the app reads, most naming a KB entry
    (a row whose entry is missing is skipped by the app; most of them missing is a broken read)."""
    path = kb / name
    if not path.exists():
        return []
    field, needs, least = SECTIONS[name]
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get(field)
    except (OSError, ValueError, AttributeError) as e:
        return [f"{name} unreadable: {e}"]
    if not isinstance(rows, list) or len(rows) < least:
        return [f"{name}: {len(rows) if isinstance(rows, list) else 0} {field} (minimum {least})"]
    bad = [r for r in rows if not isinstance(r, dict) or any(f not in r for f in needs)]
    if bad:
        return [f"{name}: {len(bad)} malformed {field}, e.g. {str(bad[0])[:80]}"]
    lost = [r["key"] for r in rows if r["key"] not in keys]
    if len(lost) * 2 > len(rows):
        return [f"{name}: {len(lost)} of {len(rows)} {field} name no KB entry, e.g. {', '.join(lost[:3])}"]
    return []

def _route_problems(kb: Path, keys: set) -> list[str]:
    """routes.json, when there is one: readable, its maps the KB's own, every portal leading to one of them."""
    path = kb / ROUTES
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        maps = {str(m["id"]): m for m in data["maps"]}
        taxi = list(data.get("taxi") or [])
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [f"{ROUTES} unreadable: {e}"]
    problems = []
    pages = {k.partition("/")[2] for k in keys if str(k).startswith("map/")}
    unknown = sorted(set(maps) - pages)
    if unknown:
        problems.append(f"{ROUTES}: {len(unknown)} maps without a map page, e.g. {', '.join(unknown[:5])}")
    if pages and len(set(maps) & pages) < len(pages) * MIN_ROUTED_MAPS:
        problems.append(f"{ROUTES} covers only {len(set(maps) & pages)} of {len(pages)} maps")
    dangling = sorted({p.get("to") for m in maps.values() for p in m.get("portals") or []} - set(maps))
    if dangling:
        problems.append(f"{ROUTES}: portals to unknown maps, e.g. {', '.join(map(str, dangling[:5]))}")
    if set(taxi) - set(maps):
        problems.append(f"{ROUTES}: taxi towns that are no maps: {', '.join(sorted(set(taxi) - set(maps)))}")
    return problems


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


def _community(kb: Path) -> dict[str, dict]:
    try:
        return json.loads((kb / COMMUNITY).read_text(encoding="utf-8")).get("monsters") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _exits(kb: Path) -> dict[str, list[str]]:
    """map key -> the names of the maps its portals lead to (routes.json), for the patch notes."""
    try:
        maps = json.loads((kb / ROUTES).read_text(encoding="utf-8"))["maps"]
        names = {m["id"]: m.get("name") or m["id"] for m in maps}
        return {f"map/{m['id']}": sorted({names.get(p["to"], p["to"]) for p in m.get("portals") or []}) for m in maps}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _brief(e: dict) -> dict:
    return {"key": e["key"], "name": e.get("name") or e["key"], "category": e.get("category", "")}


def diff_kb(old: Path, new: Path) -> dict:
    """What a player would notice between two KBs: entries added/removed, stats and drops changed,
    and entries whose page text changed without a stat change ("updated").
    Community reports (community.json) count as changes of their monster: "community_added" / "community_removed"
    (the drops shown, by name) and "mesos" [old range, new range] ("18-23", None for no reports)."""
    a, b = _index(old), _index(new)
    da, db = _drops(old), _drops(new)
    ca, cb = _community(old), _community(new)
    if not (old / COMMUNITY).exists():
        cb = ca     # the first KB with players' reports: every monster's would read as a new drop, none is a change

    # a map whose portals now lead elsewhere: "Connected maps: A, B → A, B, C" (no routes.json on a side: no change)
    xa, xb = _exits(old), _exits(new)
    added = [_brief(b[k]) for k in sorted(b.keys() - a.keys())]
    removed = [_brief(a[k]) for k in sorted(a.keys() - b.keys())]
    changed, updated = [], []
    for k in sorted(a.keys() & b.keys()):
        pa, pb = a[k].get("props") or {}, b[k].get("props") or {}
        props = [[f, pa.get(f), pb.get(f)] for f in sorted(pa.keys() | pb.keys()) if pa.get(f) != pb.get(f)]
        if k in xa and k in xb and xa[k] != xb[k]:
            props.append(["Connected maps", ", ".join(xa[k]) or None, ", ".join(xb[k]) or None])
        oa, ob = da.get(k, {}), db.get(k, {})
        drops_added = sorted(ob[i] for i in ob.keys() - oa.keys())
        drops_removed = sorted(oa[i] for i in oa.keys() - ob.keys())
        renamed = a[k].get("name") != b[k].get("name")
        sa, sb = _shown(ca.get(k)), _shown(cb.get(k))
        item = lambda i: (b.get(i) or a.get(i) or {}).get("name") or i  # noqa: E731
        community_added = sorted(item(i) for i in sb - sa)
        community_removed = sorted(item(i) for i in sa - sb)
        mesos = [_mesos_range(ca.get(k)), _mesos_range(cb.get(k))]
        mesos = mesos if mesos[0] != mesos[1] else None
        if props or drops_added or drops_removed or renamed or community_added or community_removed or mesos:
            c = _brief(b[k])
            if renamed:
                c["old_name"] = a[k].get("name")
            if props:
                c["props"] = props
            if drops_added:
                c["drops_added"] = drops_added
            if drops_removed:
                c["drops_removed"] = drops_removed
            if community_added:
                c["community_added"] = community_added
            if community_removed:
                c["community_removed"] = community_removed
            if mesos:
                c["mesos"] = mesos
            changed.append(c)
        elif a[k].get("hash") != b[k].get("hash"):
            updated.append(_brief(b[k]))
    # the list pages' changes (a skill's COT change, a pet's lifespan, a class's tier) land on their entries, so the
    # patch notes and the "Updated" chips show them like any stat change
    by_key = {c["key"]: c for c in changed}
    for row in section_changes(old, new):
        c = by_key.get(row["key"])
        if c:
            c.setdefault("props", []).extend(row["props"])
        else:
            changed.append(row)
            by_key[row["key"]] = row
    updated = [u for u in updated if u["key"] not in by_key]
    changed.sort(key=lambda c: c["key"])
    counts = {"added": len(added), "removed": len(removed), "changed": len(changed), "updated": len(updated)}
    return {"counts": counts, "added": added[:MAX_LISTED], "removed": removed[:MAX_LISTED],
            "changed": changed[:MAX_LISTED], "updated": updated[:MAX_LISTED]}


def _section(kb: Path, name: str) -> dict | None:
    try:
        data = json.loads((kb / name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _rows(data: dict | None, field: str, key=lambda r: r.get("key")) -> dict:
    return {key(r): r for r in (data or {}).get(field) or [] if isinstance(r, dict) and r.get("key")}


def _graded(cell: dict) -> str | None:
    """A tier cell as the patch notes show it: "A 4,390"."""
    return " ".join(x for x in (cell.get("grade"), cell.get("value")) if x) or None


def section_changes(old: Path, new: Path) -> list[dict]:
    """Changed rows ({key, name, category, props: [[field, old, new]]}) from the list pages' files. A file the old
    KB didn't have yet is where the list starts, not news: its first publish lists nothing."""
    out: list[dict] = []
    a, b = _section(old, "skill_changes.json"), _section(new, "skill_changes.json")
    if a is not None and b is not None:
        ra, rb = _rows(a, "skills"), _rows(b, "skills")
        for k, r in rb.items():
            was = {c.get("field"): c for c in (ra.get(k) or {}).get("changes") or []}
            props = []
            for c in r.get("changes") or []:
                old_c = was.get(c.get("field"))
                if old_c is None:
                    props.append([c.get("field"), c.get("before"), c.get("after")])      # a new change record
                elif old_c.get("after") != c.get("after"):
                    props.append([c.get("field"), old_c.get("after"), c.get("after")])  # its new value moved
            if props:          # (a change told only in words, a skill that moved job, has no value to list)
                out.append({"key": k, "name": r.get("name") or k, "category": "skill", "props": props})
    a, b = _section(old, "pets.json"), _section(new, "pets.json")
    if a is not None and b is not None:
        ra, rb = _rows(a, "pets"), _rows(b, "pets")
        fields = (("lifespan", "Lifespan"), ("hunger", "Hunger"), ("commands_text", "Commands to Lv 30"),
                  ("availability", "Cash Shop"))
        for k, r in rb.items():
            was = ra.get(k) or {}
            props = [[label, was.get(f), r.get(f)] for f, label in fields if was.get(f) != r.get(f)]
            if props:
                out.append({"key": k, "name": r.get("name") or k, "category": "item", "props": props})
    a, b = _section(old, "tiers.json"), _section(new, "tiers.json")
    if a is not None and b is not None:
        base = lambda r: r.get("key") if not r.get("variant") else None  # noqa: E731  (the class's own row)
        ra, rb = _rows(a, "rows", base), _rows(b, "rows", base)
        for k, r in rb.items():
            if k is None:
                continue
            was = (ra.get(k) or {}).get("cells") or {}
            props = []
            for col, cell in (r.get("cells") or {}).items():
                old_c = was.get(col) or {}
                if (old_c.get("grade"), old_c.get("value")) != (cell.get("grade"), cell.get("value")):
                    props.append([f"{col} (community tier list)", _graded(old_c), _graded(cell)])
            if props:
                out.append({"key": k, "name": r.get("name") or k, "category": "class", "props": props})
    return out


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
