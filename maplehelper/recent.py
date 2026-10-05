"""What changed lately in the knowledge base, per entity: the "Updated" chip on cards, the update notice that lists
the player's own changes first, and the "Recent KB change" lines the AI gets.

Read from the KB's changelog.json (tools/kb_release.record_changes: every update's added / removed / changed
entries, with props old → new, drops added / removed, and the players' reports: community drops shown / hidden and
the mesos range). "Lately" is the update's own date, not when this app
downloaded it: a player who opens the app days after an update still sees what it changed that week.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import bidi, sources

DAYS = 7              # how long a change keeps its "Updated" chip
GEAR_LEVELS = 10      # equipment within this many levels of the player's is "gear for you"
_JOB = re.compile(r"^REQ LEV .*\bJOB (?P<jobs>[A-Za-z/ ]+?)\s*$", re.M)
JOB_WORDS = {"Mage": "Magician"}           # the item pages' "JOB Mage" is the Magician class


@dataclass
class Recent:
    key: str
    name: str
    date: str                                                   # the newest update that changed it ("2026-10-02")
    props: dict[str, list] = field(default_factory=dict)        # field -> [old, new] over the window
    drops_added: list[str] = field(default_factory=list)
    drops_removed: list[str] = field(default_factory=list)
    old_name: str = ""
    community_added: list[str] = field(default_factory=list)       # community drops now shown (players' reports)
    community_removed: list[str] = field(default_factory=list)
    mesos: list = field(default_factory=list)                       # [old range, new range] ("18-23", None)


def changelog(kb) -> list[dict]:
    """The KB's patch notes, newest first (as updater.changelog, but of this KB object's own folder)."""
    path = getattr(kb, "root", None)
    path = path / "changelog.json" if path else None
    try:
        stamp = path.stat().st_mtime if path else None
    except OSError:
        stamp = None
    memo = getattr(kb, "_changelog", None)
    if memo and memo[0] == stamp:
        return memo[1]
    try:
        log = json.loads(path.read_text(encoding="utf-8")) if stamp is not None else []
    except (OSError, json.JSONDecodeError):
        log = []
    out = [e for e in log if isinstance(e, dict) and e.get("version")] if isinstance(log, list) else []
    try:
        kb._changelog = (stamp, out)
    except AttributeError:
        pass
    return out


def _day(e: dict) -> date | None:
    try:
        return date.fromisoformat(str(e.get("date") or "")[:10])
    except ValueError:
        return None


def recent(kb, days: int = DAYS, today: date | None = None, log: list[dict] | None = None) -> dict[str, Recent]:
    """key -> what the updates of the last `days` days changed in it (props old → new across them)."""
    today = today or date.today()
    if log is None:
        log = changelog(kb)
        memo = getattr(kb, "_recent", None)        # every card asks: worked out once per changelog and day
        if memo and memo[0] is log and memo[1] == (today, days):
            return memo[2]
        found = recent(kb, days, today, log)
        try:
            kb._recent = (log, (today, days), found)
        except AttributeError:
            pass
        return found
    since = today - timedelta(days=days)
    entries = [e for e in log if (d := _day(e)) and since <= d <= today]
    out: dict[str, Recent] = {}
    for e in sorted(entries, key=lambda e: (str(e.get("date")), str(e.get("version")))):       # oldest first
        for r in e.get("changed") or []:
            if not isinstance(r, dict) or not r.get("key"):
                continue
            rc = out.setdefault(r["key"], Recent(r["key"], r.get("name") or r["key"], str(e.get("date"))))
            rc.date, rc.name = str(e.get("date")), r.get("name") or rc.name
            for f, old, new in r.get("props") or []:
                if f in rc.props:
                    rc.props[f][1] = new          # the oldest value it had in the window, the newest it has now
                else:
                    rc.props[f] = [old, new]
            rc.drops_added += [d for d in r.get("drops_added") or [] if d not in rc.drops_added]
            rc.drops_removed += [d for d in r.get("drops_removed") or [] if d not in rc.drops_removed]
            rc.old_name = rc.old_name or r.get("old_name") or ""
            rc.community_added += [d for d in r.get("community_added") or [] if d not in rc.community_added]
            rc.community_removed += [d for d in r.get("community_removed") or [] if d not in rc.community_removed]
            m = r.get("mesos")
            if isinstance(m, list) and len(m) == 2:
                rc.mesos = [rc.mesos[0] if rc.mesos else m[0], m[1]]     # the oldest range in the window, the newest
    return out


def of(kb, key: str, today: date | None = None) -> Recent | None:
    return recent(kb, today=today).get(key)


def _labels(kb, key: str, f: str, new) -> tuple[str, str]:
    """(before, after) build labels for a changed prop, when the page's change history shows that very change."""
    stamp = sources.stat_source(kb, key)
    c = sources.change_for(stamp, f, new)
    return (stamp.before, stamp.source) if c and stamp.before else ("", "")


def lines(t, kb, r: Recent) -> list[str]:
    """What changed, a line each: "Weapon Attack 30 → 33 (COT2 → Launch)" (one left-to-right block)."""
    out = [sources.change_line(f, _value(old), _value(new), *_labels(kb, r.key, f, new))
           for f, (old, new) in r.props.items() if old != new]
    if r.drops_added:
        out.append(t("pn_drops_added", items=", ".join(r.drops_added)))
    if r.drops_removed:
        out.append(t("pn_drops_removed", items=", ".join(r.drops_removed)))
    if r.old_name:
        out.append(t("pn_renamed", name=bidi.ltr_block(r.old_name, t.rtl)))
    if r.community_added:
        out.append(t("pn_community_added", items=", ".join(r.community_added)))
    if r.community_removed:
        out.append(t("pn_community_removed", items=", ".join(r.community_removed)))
    if r.mesos and r.mesos[0] != r.mesos[1]:
        # "18-23 → 20-25" one left-to-right block: in a Hebrew line the arrow still points from old to new
        change = f"{_mesos(r.mesos[0])} → {_mesos(r.mesos[1])}"
        out.append(t("pn_mesos", change=f"{bidi.LRI}{change}{bidi.PDI}"))
    return out


def _mesos(v) -> str:
    return str(v).replace("-", "–") if v else "—"


def _value(v) -> str:
    return "—" if v is None or v == "" else str(v)


def _date(d: str) -> str:
    try:
        y, m, dd = (int(x) for x in d.split("-"))
        return f"{dd}.{m}.{y}"
    except ValueError:
        return d


def tip(t, kb, r: Recent) -> str:
    return "\n".join([t("updated_tip_head", date=_date(r.date)), *lines(t, kb, r)])


def ai_lines(kb, keys, today: date | None = None, limit: int = 8) -> list[str]:
    """ "Recent KB change (2026-10-02): Long Sword: Weapon Attack 30 -> 33 (COT2 -> Launch)" for each pre-fetched
    entity that changed in the last week."""
    found = recent(kb, today=today)
    out = []
    for k in dict.fromkeys(keys):
        r = found.get(k)
        if not r:
            continue
        bits = []
        for f, (old, new) in r.props.items():
            before, after = _labels(kb, k, f, new)
            bits.append(f"{f} {_value(old)} -> {_value(new)}" + (f" ({before} -> {after})" if before else ""))
        bits += [f"new drop {d}" for d in r.drops_added] + [f"drop removed {d}" for d in r.drops_removed]
        bits += [f"new community drop {d}" for d in r.community_added]
        bits += [f"community drop removed {d}" for d in r.community_removed]
        if r.mesos and r.mesos[0] != r.mesos[1]:
            bits.append(f"community mesos {r.mesos[0] or 'none'} -> {r.mesos[1] or 'none'}")
        if r.old_name:
            bits.append(f"formerly {r.old_name}")
        if bits:
            out.append(f"Recent KB change ({r.date}): {r.name}: {'; '.join(bits)}")
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- what matters to the active character

def item_jobs(kb, key: str) -> set[str] | None:
    """The classes an equip is for, from its page's requirement line ("REQ LEV 20 REQ INT 30 JOB Mage"); None
    when it names none (any class)."""
    m = _JOB.search(kb.page(key)) if kb.get(key) else None
    if not m:
        return None
    return {JOB_WORDS.get(j.strip(), j.strip()) for j in m.group("jobs").split("/") if j.strip()}


def why(kb, row: dict, char, wished: set[str]) -> str | None:
    """Why a changed row matters to this character: "wish" (an item on their wishlist, or a wished item's drop
    changed), "gear" (equipment for their class within GEAR_LEVELS of their level), "train" (a monster in their
    training range: an ACC, HP or EXP change moves where to train), or None."""
    from . import combat
    key = row.get("key") or ""
    names = {(kb.get(k) or {}).get("name") for k in wished}
    dropped = [*(row.get("drops_added") or []), *(row.get("drops_removed") or []),
               *(row.get("community_added") or []), *(row.get("community_removed") or [])]
    if key in wished or names & set(dropped):
        return "wish"
    if not char or not getattr(char, "level", 0):
        return None
    level = int(char.level)
    e = kb.get(key) or {}
    props = e.get("props") or {}
    if key.startswith("item/") and str(e.get("type") or "").startswith("Equip"):
        req = props.get("Level Requirement") or 0
        if not isinstance(req, (int, float)) or abs(int(req) - level) > GEAR_LEVELS:
            return None
        jobs = item_jobs(kb, key)
        base = getattr(char, "base_class", "") or ""
        return "gear" if jobs is None or not base or base in jobs else None
    if key.startswith("monster/"):
        lv = props.get("Level")
        if isinstance(lv, (int, float)) and level - combat.SPOT_BELOW <= lv <= level + combat.SPOT_ABOVE:
            return "train"
    return None


KINDS = ("added", "changed", "updated", "removed")


def split(entries: list[dict], kb, char, wished) -> tuple[list[tuple[str, str, dict]], list[dict]]:
    """(the changes that matter to the character: (why, kind, row), newest update first and each entity once;
    the entries without them, for "More changes")."""
    wished = set(wished or ())
    mine: list[tuple[str, str, dict]] = []
    seen: set[str] = set()
    rest: list[dict] = []
    for e in entries:
        left = dict(e)
        counts = dict(e.get("counts") or {})
        for kind in KINDS:
            keep = []
            for r in e.get(kind) or []:
                # a page whose text alone changed ("updated") is no change to the game: never "affects you"
                reason = why(kb, r, char, wished) if isinstance(r, dict) and kind != "updated" else None
                if reason:
                    if r.get("key") not in seen:
                        seen.add(r.get("key"))
                        mine.append((reason, kind, r))
                    counts[kind] = max(0, counts.get(kind, len(e.get(kind) or [])) - 1)
                else:
                    keep.append(r)
            left[kind] = keep
        left["counts"] = counts
        rest.append(left)
    return mine, rest
