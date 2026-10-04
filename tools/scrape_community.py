"""Download what players report on NiaMeowDB for each monster: its drops (with votes) and its mesos.

Used with permission from the NiaMeowDB team. MeowDB's monster pages load these two lists in the browser, so the
page scrape (scrape_meowdb.py) never sees them; they come from the site's public JSON instead:
    /msclassic/api/drops?monsterId=<id>          {"drops": [{"itemId", "itemName", "upvotes", "downvotes", ...}]}
    /msclassic/api/mesos-reports?monsterId=<id>  {"reports": [...], "summary": {"count", "medianMin", ...}}
<id> is the KB monster's own id (index.json "id"), the same number as its meowdb.com page.

Output: data/kb/community.json
    {"source": ..., "fetched": "2026-10-04",
     "monsters": {"monster/2": {"drops": [{"item": "item/413", "up": 16, "down": 1, "score": 15, "reqJob": null}],
                                "mesos": {"min": 2, "max": 2, "chance": 100, "count": 1, "trusted": 1} | null,
                                "fetched": "2026-10-04"}}}
Every drop is kept with its votes, including the down-voted ones: what to show is the app's rule
(kb.COMMUNITY_MIN_SCORE), so it can change without a new scrape. A monster with neither list is left out.

The file is written whole or not at all: a run that can't reach the site for too many monsters, or whose result
fails tools/kb_release.validate_community, keeps the previous file (a monster the site didn't answer for this time
keeps its previous entry too).

Usage:
    python tools/scrape_community.py            # every monster of data/kb/index.json (~6 min)
    python tools/scrape_community.py --limit 5  # quick test
    python tools/scrape_meowdb.py --community   # the same, as the nightly job runs it
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import kb_release
from scrape_meowdb import BASE, DELAY_SECONDS, KB, fetch

FILE = kb_release.COMMUNITY
DROPS_API = f"{BASE}/msclassic/api/drops?monsterId={{id}}"
MESOS_API = f"{BASE}/msclassic/api/mesos-reports?monsterId={{id}}"
MAX_FAILED = 0.1      # more monsters than this unanswered (site down, API moved): keep the previous file


class BadPayload(ValueError):
    pass


def item_lookup(index: list[dict]):
    """A function (itemId, itemName) -> KB item key or None.

    MeowDB's itemId is the KB item's own id, so item/<itemId> is the item when its name agrees. A name it doesn't
    agree with means the id isn't the KB's (or the item was renamed): the name decides then, when only one KB item
    has it; else the id, when the KB has that item at all."""
    items = {e["key"]: e for e in index if e.get("category") == "item" and e.get("key")}
    by_name: dict[str, list[str]] = {}
    for k, e in items.items():
        by_name.setdefault(str(e.get("name") or "").strip().lower(), []).append(k)

    def lookup(item_id, name) -> str | None:
        by_id = f"item/{item_id}" if item_id is not None else ""
        low = str(name or "").strip().lower()
        if by_id in items and str(items[by_id].get("name") or "").strip().lower() == low:
            return by_id
        named = by_name.get(low, [])
        if len(named) == 1:
            return named[0]
        return by_id if by_id in items else None
    return lookup


def _int(v, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        raise BadPayload(f"{what} is not a number: {v!r}")
    return int(v)


def parse_drops(payload, lookup) -> tuple[list[dict], int]:
    """(the drops as community.json stores them, best score first; how many named an item the KB doesn't have)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("drops"), list):
        raise BadPayload("drops: no 'drops' list")
    out, unknown, seen = [], 0, set()
    for d in payload["drops"]:
        if not isinstance(d, dict):
            raise BadPayload(f"drops: not an object: {d!r}"[:120])
        key = lookup(d.get("itemId"), d.get("itemName"))
        if not key:
            unknown += 1
            continue
        if key in seen:
            continue           # one item reported twice: the first (highest scored) report stands
        seen.add(key)
        up, down = _int(d.get("upvotes"), "upvotes"), _int(d.get("downvotes"), "downvotes")
        score = _int(d["score"], "score") if d.get("score") is not None else up - down
        out.append({"item": key, "up": up, "down": down, "score": score, "reqJob": d.get("reqJob")})
    out.sort(key=lambda r: (-r["score"], -r["up"]))
    return out, unknown


def parse_mesos(payload) -> dict | None:
    """The reports' summary as {"min", "max", "chance", "count", "trusted"}, or None when nobody reported."""
    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), dict):
        raise BadPayload("mesos: no 'summary'")
    s = payload["summary"]
    count = _int(s.get("count") or 0, "count")
    if not count or s.get("medianMin") is None or s.get("medianMax") is None:
        return None
    lo, hi = _int(s["medianMin"], "medianMin"), _int(s["medianMax"], "medianMax")
    chance = s.get("medianDropChancePct")
    return {"min": min(lo, hi), "max": max(lo, hi),
            "chance": round(float(chance), 1) if isinstance(chance, (int, float)) else None,
            "count": count, "trusted": _int(s.get("trustedCount") or 0, "trustedCount")}


def get_json(url: str):
    text = fetch(url)
    time.sleep(DELAY_SECONDS)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def scrape(kb: Path = KB, limit: int | None = None, get=None, log=print) -> dict:
    """Fetch every monster's lists and write community.json; returns {"monsters", "drops", "mesos", "failed",
    "unknown", "written", "changed"}. Raises kb_release.InvalidKB (and writes nothing) on a bad result."""
    get = get or get_json
    index = json.loads((kb / "index.json").read_text(encoding="utf-8"))
    monsters = [e for e in index if e.get("category") == "monster" and str(e.get("id") or "").isdigit()]
    if limit:
        monsters = monsters[:limit]
    lookup = item_lookup(index)
    path = kb / FILE
    try:
        previous = json.loads(path.read_text(encoding="utf-8")).get("monsters") or {}
    except (OSError, ValueError, AttributeError):
        previous = {}
    today = time.strftime("%Y-%m-%d", time.gmtime())
    out: dict[str, dict] = {}
    failed = unknown = 0
    for n, e in enumerate(monsters, 1):
        key = e["key"]
        try:
            drops_raw, mesos_raw = get(DROPS_API.format(id=e["id"])), get(MESOS_API.format(id=e["id"]))
            if drops_raw is None or mesos_raw is None:
                raise BadPayload("no answer")
            drops, missing = parse_drops(drops_raw, lookup)
            mesos = parse_mesos(mesos_raw)
        except BadPayload as err:
            failed += 1
            log(f"[{n}/{len(monsters)}] {e.get('name')}: {err}" + (" (previous kept)" if key in previous else ""))
            if key in previous:
                out[key] = previous[key]
            continue
        unknown += missing
        if drops or mesos:
            out[key] = {"drops": drops, "mesos": mesos, "fetched": today}
        log(f"[{n}/{len(monsters)}] {e.get('name')}: {len(drops)} drops" + (", mesos" if mesos else ""))
    if monsters and failed > len(monsters) * MAX_FAILED:
        raise kb_release.InvalidKB(f"community: {failed} of {len(monsters)} monsters unanswered; previous file kept")
    data = {"source": "NiaMeowDB community reports (meowdb.com)", "fetched": today, "monsters": out}
    kb_release.validate_community(data, {e["key"] for e in index if "key" in e})
    changed = kb_release.community_changes(previous, out)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    tmp.replace(path)          # whole or not at all: a reader never sees half a file
    return {"monsters": len(monsters), "drops": sum(1 for v in out.values() if v["drops"]),
            "mesos": sum(1 for v in out.values() if v["mesos"]), "failed": failed, "unknown": unknown,
            "written": len(out), "changed": changed}


def bump_last_run(kb: Path, changed: int) -> None:
    """Add the community changes to last_run.json's "changed", which decides whether tonight's KB is published."""
    p = kb / "last_run.json"
    try:
        run = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        run = {"checked": 0, "changed": 0}
    run["changed"] = int(run.get("changed") or 0) + changed
    run["community_changed"] = changed
    p.write_text(json.dumps(run), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--kb", type=Path, default=KB)
    a = ap.parse_args(argv)
    try:
        stats = scrape(a.kb, a.limit)
    except kb_release.InvalidKB as err:
        print(f"::warning::Community drops not updated: {err}")
        return 1
    bump_last_run(a.kb, stats["changed"])
    print("Community:", json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
