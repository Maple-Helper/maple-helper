"""Every night, after the scrape: Hebrew for what the knowledge base brought in English and has no Hebrew for yet,
into the KB's own he.json (maplehelper/translations.py reads it; tools/scrape_news.py merges its news part).

What it translates: a quest's journal line (shown as "what to do" when there is nothing to bring), a pet skill's
description, a skill change's note, a skill's description and its level 1 / max level effects (the Skills tab,
kind "skill_desc"), an item's description and Meow Notes (the item details window, kinds "item_desc" and
"item_notes"), and a news item's title, summary, highlights and note. Only text with no
translation made from its current English; at most MAX_TEXTS a night, so a fault can never run up the bill.

    ANTHROPIC_API_KEY=... python tools/translate_kb.py [data/kb]      # translate what's missing
    ANTHROPIC_API_KEY=... python tools/translate_kb.py --check        # one tiny request: is the key good?

No key, a bad key or the API down: says so and leaves the KB as it is (a warning, never a failed night).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

MODEL = "claude-opus-5"   # Haiku wrote word-for-word Hebrew ("אחרונים 30 ימים" for "last 30 days")
EFFORT = "medium"         # short texts: enough thought for natural Hebrew, at a fraction of the default's tokens
API = "https://api.anthropic.com/v1/messages"
MAX_TEXTS = 120          # a night's ceiling, whatever the KB brings
BUDGET_S = 20 * 60       # a night's time: past it no new batch starts, so a slow API never runs the job out of
                         # time (a cancelled job publishes nothing, review2 LOG-6); what's done is kept
BATCH = 20               # texts a request
RETRY_WAIT = 20          # seconds before another try on a busy API (then 40)
HEBREW = re.compile(r"[א-ת]")
ICON = re.compile(r"\[\[img:[^\]]*\]\]")
DIGITS = re.compile(r"\d+")

RULES = """You translate short MapleStory Classic texts from English to Hebrew for an Israeli players' app.
- Natural, fluent Hebrew as an Israeli gamer writes it. Address the reader in the plural ("אתם").
- Keep every proper noun in English letters exactly as given: NPC, monster, item, map, town, skill, job and game
  names (Arthur, Henesys, Blue Snail, Founder's Access). A Hebrew prefix on one takes a hyphen: "ל-Arthur", "ב-Henesys".
- A level is "רמה" (never "לבל", in any form: "ברמה 10", "מרמה 10", "תקרת הרמות", "הרמה"); levelling is "עליית
  רמות"; money is "mesos" in English letters; numbers stay as they are.
- Jobs and classes stay in English too, with a hyphen after a prefix ("ל-Warriors", "ל-Magicians"), never "וריור"
  or "מג'ים"; places too ("Subway", never "סאבוויי"). The app's words: "נזק" (never "דמג'"), "גריינד", "פוטים",
  "אינבנטורי", "Free Market", "קווסט", "דרופ".
- Translate the meaning completely; add nothing, drop nothing.
- Write it as an Israeli would say it, never word for word: "last 30 days" is "נשארים 30 יום" (not "אחרונים 30
  ימים"), "cannot be traded" is "אי אפשר לסחור בהם" (not "לא ניתן להסחר"), "gifting" is "שליחת מתנה", "random" is
  "אקראי". A plural noun after a number agrees with it ("30 יום", "3 כתבות").
Answer with a JSON array of strings only: the translations, in the order given, one per input."""


def _post(key: str, body: dict) -> dict:
    req = urllib.request.Request(API, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


REVIEW = """You proofread Hebrew translations of short MapleStory Classic texts for an Israeli players' app. You get a
JSON array of {"en", "he"} pairs. For each, fix the Hebrew where it:
- misses, adds or changes meaning against the English (a wrong verb, a wrong subject, a lost condition);
- reads word for word instead of as an Israeli gamer writes ("והם אחרונים 30 ימים" -> "ונשארים 30 יום",
  "לא ניתן להסחר" -> "אי אפשר לסחור בהם", "ותנו סטייל רנדומלי" -> "ונותנים תסרוקת אקראית");
- has a grammar slip: gender or number agreement, a wrong verb form, a broken construct state.
Keep what the translation rules require: proper nouns in English letters exactly as given (a Hebrew prefix takes a
hyphen: "ב-Henesys"), jobs and places in English ("ל-Magicians", "Subway"), "רמה" for a level (never "לבל"),
"נזק" for damage (never "דמג'"), "mesos" in English letters, the numbers, the [[img:...]] tokens, the plural "אתם"
address. A translation that is already right stays exactly as it is.
Answer with a JSON array of strings only: the final Hebrew, in the order given, one per pair."""


def _ask(key: str, system: str, content: list, count: int) -> list[str]:
    """One request whose answer is a JSON array of count non-empty strings; raises ValueError when it isn't."""
    body = {"model": MODEL, "max_tokens": 16000, "system": system, "output_config": {"effort": EFFORT},
            "messages": [{"role": "user", "content": json.dumps(content, ensure_ascii=False)}]}
    answer = _post(key, body)
    text = "".join(b.get("text", "") for b in answer.get("content", []) if b.get("type") == "text").strip()
    start, end = text.find("["), text.rfind("]")
    try:
        out = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    except json.JSONDecodeError:
        out = None
    if not isinstance(out, list) or len(out) != count or not all(isinstance(x, str) and x.strip() for x in out):
        raise ValueError(f"the answer isn't {count} translations ({answer.get('stop_reason')})")
    return out


def translate(key: str, texts: list[str]) -> list[str]:
    """Hebrew for each text, in order: a translation, then a proofreading pass over it against the English (the
    night's Hebrew is shown to players as it is, with no one reading it first). Raises on an API error or a
    translation that isn't one string per text; a failed proofreading keeps the translation."""
    out = _ask(key, RULES, texts, len(texts))
    try:
        out = _ask(key, REVIEW, [{"en": en, "he": he} for en, he in zip(texts, out)], len(texts))
    except (ValueError, OSError) as e:          # a bad answer, or the API busy or down (HTTPError is an OSError):
        print(f"translations: proofreading skipped for {len(texts)} text(s) ({e})")   # the translation is kept
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
    # every text the Skills tab shows (skillbook.py: a description, then the level 1 and max level effects)
    from maplehelper import skillbook
    for skills in skillbook.book(kb).values():
        for s in skills:
            for key, en in skillbook.texts(s).items():
                if not translations.he(root, skillbook.KIND, key, en):
                    jobs.append({"kind": skillbook.KIND, "key": key, "en": en})
    # an item's description and its Meow Notes (the item details window; the app ships most of them in
    # assets/items/he.json, so a night translates only an item NiaMeowDB added or rewrote)
    from maplehelper import itemdetails
    for k in kb.entities:
        if not k.startswith("item/"):
            continue
        d = itemdetails.details(kb, k)
        texts = [("item_desc", k, d.description)]
        texts += [("item_notes", itemdetails.note_key(k, "about", n), a) for n, a in enumerate(d.about)]
        texts += [("item_notes", itemdetails.note_key(k, "post", n), p.text) for n, p in enumerate(d.posts)]
        for kind, key, en in texts:
            if en and not translations.he(root, kind, key, en):
                jobs.append({"kind": kind, "key": key, "en": en})
    import scrape_news
    made = scrape_news.translations(kb=root)
    try:
        items = json.loads((root / "news.json").read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError, AttributeError):
        items = []
    for i in items:
        tr = made.get(i.get("id")) or {}
        head_ok = (tr.get("source_hash") == i.get("hash") and tr.get("title")
                   and (tr.get("summary") or not str(i.get("summary") or "").strip()))
        body = list(i.get("highlights") or []) + ([i["commentary"]] if i.get("commentary") else [])
        body_ok = not body or tr.get("body_hash") == scrape_news._body_hash(i)
        if not (head_ok and body_ok):
            jobs.append({"kind": "news", "key": i["id"], "item": i,
                         "texts": [i.get("title") or "", i.get("summary") or ""] + body})
    return jobs


def _parts(j: dict) -> list[str]:
    return [str(t or "") for t in j["texts"]] if j["kind"] == "news" else [j["en"]]


def refused(en: str, he: str) -> str | None:
    """Why a translation can't be stored (it waits for the next night instead); None when it can."""
    if not HEBREW.search(he) and len(en.split()) >= 4:
        return "no Hebrew in it"             # (a short title that is all names may rightly stay in English letters)
    from maplehelper.brain import _LEVEL_WORD
    if _LEVEL_WORD.search(he):              # the app's rule: the verb "לבלבל" (to confuse) is no level word
        return 'it says "לבל"'
    if sorted(ICON.findall(en)) != sorted(ICON.findall(he)):
        return "an icon token was lost"
    return None


def _translate(key: str, texts: list[str]) -> list[str]:
    """translate() with two more tries on a busy or failing API (429, 5xx, 529, a dropped connection)."""
    for attempt in range(3):
        try:
            return translate(key, texts)
        except urllib.error.HTTPError as e:
            if attempt == 2 or not (e.code == 429 or e.code >= 500):
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(RETRY_WAIT * (attempt + 1))
    raise AssertionError("unreachable")


def run(root: Path, key: str, jobs: list[dict] | None = None) -> int:
    """Translate what's missing into root/he.json; returns how many texts were translated."""
    jobs = missing(root) if jobs is None else jobs
    # news first: it is what goes stale soonest, and it came last behind any batch that kept failing
    jobs = [j for j in jobs if j["kind"] == "news"] + [j for j in jobs if j["kind"] != "news"]
    # whole jobs up to the night's ceiling; an empty text (a news item with no summary) stays empty, never sent
    # (it was sent as "-", and its "translation" showed as the Hebrew summary of an item that has none)
    picked, texts = [], []
    for j in jobs:
        wanted = [t for t in _parts(j) if t.strip()]
        if picked and len(texts) + len(wanted) > MAX_TEXTS:
            break
        picked.append(j)
        texts += wanted
    if not texts:
        print("translations: nothing new to translate")
        return 0
    made: dict[str, str] = {}
    problems: list[str] = []
    began = time.monotonic()
    for n in range(0, len(texts), BATCH):
        if time.monotonic() - began > BUDGET_S:
            problems.append(f"out of time after {len(made)} (the rest waits for the next night)")
            break
        chunk = texts[n:n + BATCH]
        try:
            try:
                got: list[str | None] = list(_translate(key, chunk))
            except ValueError:
                # one bad answer used to stop the night, and the same batch failed again every night after:
                # the texts one at a time, a text that still fails waits for tomorrow
                got = []
                for t in chunk:
                    if time.monotonic() - began > BUDGET_S:
                        got.append(None)            # out of time: waits for the next night
                        continue
                    try:
                        got += _translate(key, [t])
                    except ValueError as e:
                        got.append(None)
                        problems.append(f"{t[:40]!r}: {e}")
        except Exception as e:  # noqa: BLE001 - what's done is kept; the rest waits for tomorrow
            problems.append(f"stopped after {len(made)} ({e})")
            break
        for en, he in zip(chunk, got):
            if he is None:
                continue
            why = refused(en, he)
            if why:
                problems.append(f"{en[:40]!r}: {why}")
                continue
            if sorted(DIGITS.findall(en)) != sorted(DIGITS.findall(he)):
                print(f"translations: check the numbers of {en[:60]!r} -> {he[:60]!r}")   # (number words: kept)
            made[en] = he
    path = root / "he.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    import scrape_news
    count = 0
    for j in picked:
        en_parts = _parts(j)
        if any(t.strip() and t not in made for t in en_parts):
            continue                # all of a job or none of it: the rest of it is retried tomorrow
        part = [made[t] if t.strip() else "" for t in en_parts]
        size = sum(1 for t in en_parts if t.strip())
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
    left = sum(len([t for t in _parts(j) if t.strip()]) for j in jobs) - count
    print(f"translations: {count} texts translated ({left} left for the next night)")
    if problems:
        # the step's own "|| echo ::warning::" fires only on a crash: a failed night has to say so itself
        print(f"::warning::translations: {len(problems)} problem(s) tonight, e.g. {problems[0]}")
    return count


def main(argv: list[str]) -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("::warning::translations: no ANTHROPIC_API_KEY, nothing translated")
        return 0
    if "--check" in argv:
        return 0 if check(key) else 1
    root = Path(next((a for a in argv if not a.startswith("--")), ROOT / "data" / "kb"))
    jobs = missing(root)
    if not jobs:                    # no request at all on a night with nothing new
        print("translations: nothing new to translate")
        return 0
    if not check(key):
        print("::warning::translations: the API key or the API failed the check, nothing translated")
        return 0
    run(root, key, jobs)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
