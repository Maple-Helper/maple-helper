"""Build data/kb/aliases.json: Hebrew names and transliterations Israeli players use for
monsters, maps, towns and NPCs, so "חילזון אדום" or "רד סנייל" resolve to Red Snail.

Generated with Claude Code in batches, then merged. Re-running only fills what's missing.
An alias given to entities with different names is dropped from all of them: the app would resolve it to
whichever loaded last ("ג'וניור סנטינל" named two monsters).

Usage: python tools/make_aliases.py monster map npc
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "data" / "kb"
OUT = KB / "aliases.json"
BATCH = 80

PROMPT = """You map MapleStory Classic names to the Hebrew names Israeli players type or say.
For each entry return 2-4 Hebrew aliases. ALWAYS include the phonetic Hebrew transliteration of the English
name as players say it ("רד סנייל", "דארק לורד", "הנסיס"), plus common spelling variants ("הניסיס"),
and a Hebrew translation only when players actually use one ("חילזון אדום").
Skip generic words that would cause false matches (e.g. do not alias a monster as just "עץ").
Return ONLY a JSON object: {"<key>": ["alias", ...], ...} with exactly the keys given.

Entries (key | English name):
"""


def claude_exe() -> str:
    sys.path.insert(0, str(ROOT))
    from maplehelper.providers.claude import find_claude
    exe = find_claude()
    if not exe:
        sys.exit("Claude Code not found")
    return exe


def ask(exe: str, rows: list[tuple[str, str]]) -> dict:
    text = PROMPT + "\n".join(f"{k} | {n}" for k, n in rows)
    r = subprocess.run([exe, "-p", "--restricted", "--strict-mcp-config", "--tools", "", "--model", "sonnet",
                        "--no-session-persistence"], input=text.encode("utf-8"), capture_output=True, timeout=600)
    out = r.stdout.decode("utf-8", errors="replace")
    m = re.search(r"\{.*\}", out, re.S)
    return json.loads(m.group(0)) if m else {}


def drop_ambiguous(aliases: dict[str, list[str]], names: dict[str, str]) -> tuple[dict[str, list[str]], set[str]]:
    """Aliases that name one thing only (entities that share a display name, like the towns' Regular Cab NPCs,
    count as one). Returns (aliases, the dropped alias forms)."""
    sys.path.insert(0, str(ROOT))
    from maplehelper.kb import _norm      # the form the app looks names up by
    owners: dict[str, set[str]] = {}
    for key, al in aliases.items():
        for a in al:
            owners.setdefault(_norm(a), set()).add((names.get(key) or key).lower())
    dropped = {a for a, who in owners.items() if len(who) > 1}
    kept = {key: [a for a in al if _norm(a) not in dropped] for key, al in aliases.items()}
    return {k: v for k, v in kept.items() if v}, dropped


def main(categories: list[str]):
    index = json.loads((KB / "index.json").read_text(encoding="utf-8"))
    aliases = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    names = {e["key"]: e["name"] for e in index}
    # the file's own rule on every run, not only after a newly asked batch: the shipped file still had "פייסון"
    # for both Pison and Pason
    aliases, dropped = drop_ambiguous(aliases, names)
    if dropped:
        print("dropped (names several things):", ", ".join(sorted(dropped)))
        OUT.write_text(json.dumps(aliases, ensure_ascii=False, indent=0), encoding="utf-8")
    exe = claude_exe()
    from maplehelper.kb import _VARIANT
    # one entry per base name, the plain entity first: "Zelya (Free Market)" and "Forgotten Hollow Instance 080003500"
    # got the same Hebrew alias as "Zelya" and "Forgotten Hollow", and the app opened the variant
    seen_names = {_VARIANT.sub("", e["name"]).strip().lower() for e in index if e["key"] in aliases}
    rows = []
    for e in sorted(index, key=lambda e: bool(_VARIANT.search(e["name"]))):
        if e["category"] in categories and e["key"] not in aliases:
            name = e["name"]
            base = _VARIANT.sub("", name).strip().lower()
            if base in seen_names or re.search(r"\(alt\)|\bPQ\b|\(KPQ\)", name):
                continue  # one entry per base name; skip party-quest variants
            seen_names.add(base)
            rows.append((e["key"], name))
    print(f"{len(rows)} names to alias")
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        try:
            got = ask(exe, batch)
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as ex:
            print("batch failed:", ex)
            continue
        valid = {k: [a for a in v if isinstance(a, str) and re.search(r"[֐-׿]", a)]
                 for k, v in got.items() if k in dict(batch)}
        aliases.update({k: v for k, v in valid.items() if v})
        aliases, dropped = drop_ambiguous(aliases, names)
        if dropped:
            print("  dropped (names several things):", ", ".join(sorted(dropped)))
        OUT.write_text(json.dumps(aliases, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"  {min(i + BATCH, len(rows))}/{len(rows)}")
    print(f"aliases.json: {len(aliases)} entries")
    shared: dict[str, set[str]] = {}
    for key, names in aliases.items():
        for n in names:
            shared.setdefault(n, set()).add(key)
    for n, keys in sorted(shared.items()):
        if len(keys) > 1:      # the app keeps the plain entity or drops the alias (KnowledgeBase._base_entity)
            print(f"  shared alias {n}: {', '.join(sorted(keys))}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:] or ["monster"])
