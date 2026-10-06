"""Every night, after the scrape: Hebrew for what the knowledge base brought in English and has no Hebrew for yet,
into the KB's own he.json (maplehelper/translations.py reads it; tools/scrape_news.py merges its news part).

What it translates: a quest's journal line (shown as "what to do" when there is nothing to bring), a pet skill's
description, a skill change's note, and a news item's title, summary, highlights and note. Only text with no
translation made from its current English; at most MAX_TEXTS a night, so a fault can never run up the bill.

    ANTHROPIC_API_KEY=... python tools/translate_kb.py [data/kb]      # translate what's missing
    ANTHROPIC_API_KEY=... python tools/translate_kb.py --check        # one tiny request: is the key good?

No key, a bad key or the API down: says so and leaves the KB as it is (a warning, never a failed night).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

MODEL = "claude-haiku-4-5-20251001"
API = "https://api.anthropic.com/v1/messages"
MAX_TEXTS = 120          # a night's ceiling, whatever the KB brings
BATCH = 20               # texts a request

RULES = """You translate short MapleStory Classic texts from English to Hebrew for an Israeli players' app.
- Natural, fluent Hebrew as an Israeli gamer writes it. Address the reader in the plural ("אתם").
- Keep every proper noun in English letters exactly as given: NPC, monster, item, map, town, skill, job and game
  names (Arthur, Henesys, Blue Snail, Founder's Access). A Hebrew prefix on one takes a hyphen: "ל-Arthur", "ב-Henesys".
- A level is "רמה" (never "לבל", in any form: "ברמה 10", "מרמה 10", "תקרת הרמות", "הרמה"); levelling is "עליית
  רמות"; money is "mesos" in English letters; numbers stay as they are.
- Translate the meaning completely; add nothing, drop nothing.
Answer with a JSON array of strings only: the translations, in the order given, one per input."""


def _post(key: str, body: dict) -> dict:
    req = urllib.request.Request(API, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def translate(key: str, texts: list[str]) -> list[str]:
    """Hebrew for each text, in order; raises on an API error or an answer that isn't one string per text."""
    body = {"model": MODEL, "max_tokens": 8000, "system": RULES,
            "messages": [{"role": "user", "content": json.dumps(texts, ensure_ascii=False)}]}
    answer = _post(key, body)
    text = "".join(b.get("text", "") for b in answer.get("content", []) if b.get("type") == "text").strip()
    start, end = text.find("["), text.rfind("]")
    out = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    if not isinstance(out, list) or len(out) != len(texts) or not all(isinstance(x, str) and x.strip() for x in out):
        raise ValueError(f"the answer isn't {len(texts)} translations")
    import scrape_news
    # the prompt's rule alone let "תקרת לבל 100" through into the news: the app's own Hebrew rules, applied
    return [scrape_news.he_text(x) for x in out]


def check(key: str) -> bool:
    try:
        print("he:", translate(key, ["Talk to Arthur in Henesys."])[0])
        print("API key OK")
        return True
    except urllib.error.HTTPError as e:
        print(f"API key problem: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:  # noqa: BLE001
        print(f"API problem: {e}")
    return False


# ---------------------------------------------------------------- what's missing

def missing(root: Path) -> list[dict]:
    """[{kind, key, en, ...}] every text the KB has in English with no Hebrew made from it."""
    from maplehelper import quests, sitedata, translations
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(root)
    jobs = []
    for k, e in kb.entities.items():
        if e.get("category") == "quest":
            q = quests.quest(kb, k)
            if q and q.task and not q.needs and not translations.he(root, "quest_tasks", k, q.task):
                jobs.append({"kind": "quest_tasks", "key": k, "en": q.task})
    for s in sitedata.pet_skills(kb):
        if s.text and not translations.he(root, "pet_skills", s.key, s.text):
            jobs.append({"kind": "pet_skills", "key": s.key, "en": s.text})
    for k, ch in sitedata.skill_changes(kb).items():
        if ch.note and not translations.he(root, "skill_changes", k, ch.note):
            jobs.append({"kind": "skill_changes", "key": k, "en": ch.note})
    import scrape_news
    made = scrape_news.translations(kb=root)
    try:
        items = json.loads((root / "news.json").read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError, AttributeError):
        items = []
    for i in items:
        tr = made.get(i.get("id")) or {}
        head_ok = tr.get("source_hash") == i.get("hash") and tr.get("summary") and tr.get("title")
        body = list(i.get("highlights") or []) + ([i["commentary"]] if i.get("commentary") else [])
        body_ok = not body or tr.get("body_hash") == scrape_news._body_hash(i)
        if not (head_ok and body_ok):
            jobs.append({"kind": "news", "key": i["id"], "item": i,
                         "texts": [i.get("title") or "", i.get("summary") or ""] + body})
    return jobs


def run(root: Path, key: str) -> int:
    """Translate what's missing into root/he.json; returns how many texts were translated."""
    jobs = missing(root)
    texts = []
    for j in jobs:
        for t in (j["texts"] if j["kind"] == "news" else [j["en"]]):
            texts.append(t or "-")
    if not texts:
        print("translations: nothing new to translate")
        return 0
    texts = texts[:MAX_TEXTS]
    done: list[str] = []
    for n in range(0, len(texts), BATCH):
        try:
            done += translate(key, texts[n:n + BATCH])
        except Exception as e:  # noqa: BLE001 - what's done is kept; the rest waits for tomorrow
            print(f"translations: stopped after {len(done)} ({e})")
            break
    path = root / "he.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    import scrape_news
    at = 0
    count = 0
    for j in jobs:
        size = len(j["texts"]) if j["kind"] == "news" else 1
        if at + size > len(done):
            break
        part = done[at:at + size]
        at += size
        if j["kind"] == "news":
            i = j["item"]
            row = {"title": part[0], "summary": part[1], "source_hash": i.get("hash")}
            hl = list(i.get("highlights") or [])
            row["highlights"] = part[2:2 + len(hl)]
            if i.get("commentary"):
                row["commentary"] = part[2 + len(hl)]
            row["body_hash"] = scrape_news._body_hash(i)
            data.setdefault("news", {})[j["key"]] = row
        else:
            data.setdefault(j["kind"], {})[j["key"]] = {"en": j["en"], "he": part[0]}
        count += size
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    scrape_news.apply_translations(root)              # news.json carries its Hebrew, as a night's scrape writes it
    last = root / "last_run.json"
    try:
        stats = json.loads(last.read_text(encoding="utf-8"))
        stats["changed"] = int(stats.get("changed") or 0) + count     # a night of new Hebrew alone still publishes
        last.write_text(json.dumps(stats), encoding="utf-8")
    except (OSError, ValueError):
        pass
    print(f"translations: {count} texts translated ({len(texts) - count} left for the next night)")
    return count


def main(argv: list[str]) -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("translations: no ANTHROPIC_API_KEY, nothing translated")
        return 0
    if "--check" in argv:
        return 0 if check(key) else 1
    root = Path(next((a for a in argv if not a.startswith("--")), ROOT / "data" / "kb"))
    if not check(key):
        return 0
    run(root, key)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
