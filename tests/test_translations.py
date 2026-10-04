"""Hebrew made every night into the KB's he.json (tools/translate_kb.py), read by the app (maplehelper/translations.py)."""
import json
import shutil
import sys
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
    kb = tmp_path / "kb"
    shutil.copytree(REAL_KB, kb, ignore=shutil.ignore_patterns("img", "pages"))
    shutil.copytree(REAL_KB / "pages", kb / "pages")
    assert translate_kb.missing(kb) == []                  # everything there is today has its Hebrew
    # a quest whose journal line NiaMeowDB rewrote, and a news item rewritten: both to translate again
    page = kb / "pages" / "quest" / "506001.md"
    page.write_text(page.read_text(encoding="utf-8").replace("asked me to greet Rina", "asked me to say hello to Rina"),
                    encoding="utf-8")
    news = json.loads((kb / "news.json").read_text(encoding="utf-8"))
    news["items"][0]["hash"] = "rewritten"
    (kb / "news.json").write_text(json.dumps(news, ensure_ascii=False), encoding="utf-8")
    (kb / "last_run.json").write_text(json.dumps({"checked": 1, "changed": 0}), encoding="utf-8")
    jobs = translate_kb.missing(kb)
    assert {j["kind"] for j in jobs} == {"quest_tasks", "news"}
    monkeypatch.setattr(translate_kb, "translate", lambda key, texts: [f"עברית {n}" for n, _ in enumerate(texts)])
    count = translate_kb.run(kb, "key")
    assert count == sum(len(j["texts"]) if j["kind"] == "news" else 1 for j in jobs)
    made = json.loads((kb / "he.json").read_text(encoding="utf-8"))
    assert made["quest_tasks"]["quest/506001"]["he"].startswith("עברית")
    left = translate_kb.missing(kb)
    assert left == [], [(j["kind"], j["key"]) for j in left]   # the next night: nothing left
    item = json.loads((kb / "news.json").read_text(encoding="utf-8"))["items"][0]
    assert item["title_he"].startswith("עברית") and item["summary_he"]
    assert json.loads((kb / "last_run.json").read_text(encoding="utf-8"))["changed"] == count   # it publishes
    assert scrape_news.translations(kb=kb)[item["id"]]["source_hash"] == "rewritten"


def test_no_key_translates_nothing(monkeypatch, capsys):
    import translate_kb
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert translate_kb.main([]) == 0 and "no ANTHROPIC_API_KEY" in capsys.readouterr().out
