"""Translation round-trip for the news summaries (tools/scrape_news.py), the way tools/translate_guides.py does the
guides: titles stay as published; each item's one-line summary gets a Hebrew version.

  export <lang> <out.json> [--kb data/kb]   writes {"strings": {id: English summary}, "hashes": {id: hash}} for every
                                            news item whose translation is missing or stale
  import <lang> <in.json>                   reads {id: translation} (the "strings" object, translated) next to the
                                            export's hashes, and merges it into assets/news/<lang>.json

assets/news/<lang>.json = {id: {"summary", "source_hash"}}. A translation is stamped with the hash of the English it
was made from (title + summary): the nightly scrape copies it into the KB's news.json only while that still matches,
so a summary NiaMeowDB rewrites shows in English until it is translated again, never a stale sentence.
Machine translation isn't run in CI: the export goes to a translator (or an AI by hand), the import is committed,
and the next nightly KB carries it to every player without an app update.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "news"


def _items(kb: Path) -> list[dict]:
    try:
        return [i for i in json.loads((kb / "news.json").read_text(encoding="utf-8")).get("items", [])
                if isinstance(i, dict) and i.get("id") and i.get("summary")]
    except (OSError, ValueError, AttributeError):
        return []


def _current(lang: str) -> dict:
    try:
        data = json.loads((OUT / f"{lang}.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def export(lang: str, out: Path, kb: Path) -> int:
    have = _current(lang)
    todo = [i for i in _items(kb) if (have.get(i["id"]) or {}).get("source_hash") != i["hash"]]
    out.write_text(json.dumps({"strings": {i["id"]: i["summary"] for i in todo},
                               "hashes": {i["id"]: i["hash"] for i in todo}}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"{len(todo)} summaries to translate -> {out}")
    return len(todo)


def import_(lang: str, src: Path) -> int:
    data = json.loads(src.read_text(encoding="utf-8"))
    hashes = data.get("hashes") or {}
    done = data.get("strings") or {}
    have = _current(lang)
    n = 0
    for nid, text in done.items():
        if nid in hashes and isinstance(text, str) and text.strip():
            have[nid] = {"summary": text.strip(), "source_hash": hashes[nid]}
            n += 1
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{lang}.json").write_text(json.dumps(dict(sorted(have.items())), ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
    print(f"{n} summaries imported into assets/news/{lang}.json")
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("export", "import"))
    ap.add_argument("lang")
    ap.add_argument("file", type=Path)
    ap.add_argument("--kb", type=Path, default=ROOT / "data" / "kb")
    a = ap.parse_args()
    export(a.lang, a.file, a.kb) if a.cmd == "export" else import_(a.lang, a.file)
