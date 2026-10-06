"""Hebrew made every night into the KB's he.json (tools/translate_kb.py), read by the app (maplehelper/translations.py)."""
import json
import shutil
import sys
import warnings
from pathlib import Path

import pytest

from maplehelper import translations

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


def test_the_kb_translation_is_read_first_and_only_while_its_english_holds(tmp_path):
    (tmp_path / "he.json").write_text(json.dumps({"quest_tasks": {"quest/1": {"en": "Talk to Arthur.", "he": "דברו עם Arthur."}}},
                                                 ensure_ascii=False), encoding="utf-8")
    assert translations.he(tmp_path, "quest_tasks", "quest/1", "Talk to Arthur.") == "דברו עם Arthur."
    assert translations.he(tmp_path, "quest_tasks", "quest/1", "Talk to Roxy.") is None     # rewritten: English again
    assert translations.he(None, "quest_tasks", "quest/1", "Talk to Arthur.") is None


@needs_kb
def test_a_night_translates_only_what_is_missing_and_publishes_it(tmp_path, monkeypatch):
    import scrape_news
    import translate_kb
    from maplehelper import quests
    from maplehelper.kb import KnowledgeBase
    kb = tmp_path / "kb"
    shutil.copytree(REAL_KB, kb, ignore=shutil.ignore_patterns("img", "pages"))
    shutil.copytree(REAL_KB / "pages", kb / "pages")
    fake = lambda key, texts: [f"עברית {n}" for n, _ in enumerate(texts)]      # noqa: E731
    # tonight's KB may still wait for Hebrew (no API key, the API down, more than MAX_TEXTS new texts): the nightly
    # only warns about that (kb-update.yml), so it is reported here, not a failed test that holds the KB back.
    # The mechanism is what's tested: the backlog is translated first, then two rewritten texts
    ceiling = translate_kb.MAX_TEXTS
    backlog = translate_kb.missing(kb)
    if backlog:
        warnings.warn(f"{len(backlog)} KB texts have no Hebrew yet: "
                      + ", ".join(f"{j['kind']}:{j['key']}" for j in backlog[:10]))
        monkeypatch.setattr(translate_kb, "translate", fake)
        monkeypatch.setattr(translate_kb, "MAX_TEXTS", 10 ** 6)
        translate_kb.run(kb, "key")
    assert translate_kb.missing(kb) == []                  # everything there is has its Hebrew
    monkeypatch.setattr(translate_kb, "MAX_TEXTS", ceiling)
    # a quest whose journal line NiaMeowDB rewrote, and a news item rewritten: both to translate again. Any quest
    # whose journal line is shown (Rina's greeting when it's there), so a rewrite of that one page can't break this
    real = KnowledgeBase(kb)

    def page_of(key):
        return kb / "pages" / "quest" / (key.split("/", 1)[1] + ".md")
    shown = [q for q in (quests.quest(real, k) for k, e in real.entities.items() if e.get("category") == "quest")
             if q and q.task and not q.needs and page_of(q.key).read_text(encoding="utf-8").count(q.task) == 1]
    q = next((q for q in shown if q.key == "quest/506001"), shown[0])
    text = page_of(q.key).read_text(encoding="utf-8")
    page_of(q.key).write_text(text.replace(q.task, q.task.rstrip(".!") + ", soon.", 1), encoding="utf-8")
    news = json.loads((kb / "news.json").read_text(encoding="utf-8"))
    news["items"][0]["hash"] = "rewritten"
    (kb / "news.json").write_text(json.dumps(news, ensure_ascii=False), encoding="utf-8")
    (kb / "last_run.json").write_text(json.dumps({"checked": 1, "changed": 0}), encoding="utf-8")
    jobs = translate_kb.missing(kb)
    assert {j["kind"] for j in jobs} == {"quest_tasks", "news"} and q.key in {j["key"] for j in jobs}
    monkeypatch.setattr(translate_kb, "translate", fake)
    count = translate_kb.run(kb, "key")
    assert count == sum(len(j["texts"]) if j["kind"] == "news" else 1 for j in jobs)
    made = json.loads((kb / "he.json").read_text(encoding="utf-8"))
    assert made["quest_tasks"][q.key]["he"].startswith("עברית")
    left = translate_kb.missing(kb)
    assert left == [], [(j["kind"], j["key"]) for j in left]   # the next night: nothing left
    item = json.loads((kb / "news.json").read_text(encoding="utf-8"))["items"][0]
    assert item["title_he"].startswith("עברית") and item["summary_he"]
    assert json.loads((kb / "last_run.json").read_text(encoding="utf-8"))["changed"] == count   # it publishes
    assert scrape_news.translations(kb=kb)[item["id"]]["source_hash"] == "rewritten"


def test_a_night_with_a_backlog_translates_at_most_its_ceiling(kb_copy, monkeypatch):
    """More texts than MAX_TEXTS: that many tonight, the rest stays missing for tomorrow (never all at once)."""
    import translate_kb
    jobs = [{"kind": "pet_skills", "key": f"pet/{n}", "en": f"Skill {n}"} for n in range(7)]
    monkeypatch.setattr(translate_kb, "missing", lambda root: jobs)
    sent = []
    monkeypatch.setattr(translate_kb, "translate", lambda key, texts: sent.extend(texts) or [f"he {t}" for t in texts])
    monkeypatch.setattr(translate_kb, "MAX_TEXTS", 5)
    assert translate_kb.run(kb_copy, "key") == 5 and len(sent) == 5


def test_no_key_translates_nothing(monkeypatch, capsys):
    import translate_kb
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert translate_kb.main([]) == 0 and "no ANTHROPIC_API_KEY" in capsys.readouterr().out
