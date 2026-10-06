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


# ---------------------------------------------------------------- a night's translations, simulated (no API)

def _jobs():
    return [{"kind": "quest_tasks", "key": f"quest/{n}", "en": f"Talk to Arthur about the {n} letters."}
            for n in range(3)]


def _night(tmp_path, monkeypatch, translate, jobs=None):
    import translate_kb
    monkeypatch.setattr(translate_kb, "translate", translate)
    monkeypatch.setattr(translate_kb.time, "sleep", lambda s: None)
    (tmp_path / "last_run.json").write_text(json.dumps({"checked": 1, "changed": 0}), encoding="utf-8")
    count = translate_kb.run(tmp_path, "key", jobs if jobs is not None else _jobs())
    try:
        made = json.loads((tmp_path / "he.json").read_text(encoding="utf-8"))
    except OSError:
        made = {}
    return count, made


def test_one_bad_answer_no_longer_stops_the_night(tmp_path, monkeypatch, capsys):
    def translate(key, texts):
        if len(texts) > 1:
            raise ValueError("the answer isn't 3 translations")      # the model merged two lines
        if "1 letters" in texts[0]:
            raise ValueError("the answer isn't 1 translations")
        return [f"דברו עם Arthur על {t.split()[-2]} המכתבים." for t in texts]
    count, made = _night(tmp_path, monkeypatch, translate)
    assert count == 2 and set(made["quest_tasks"]) == {"quest/0", "quest/2"}
    assert "::warning::translations: 1 problem" in capsys.readouterr().out      # SCP-9: the run says so itself


def test_a_busy_api_is_tried_again(tmp_path, monkeypatch):
    import urllib.error
    calls = []

    def translate(key, texts):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError("u", 529, "overloaded", {}, None)
        return [f"דברו עם Arthur {n}" for n, _ in enumerate(texts)]
    count, _ = _night(tmp_path, monkeypatch, translate)
    assert count == 3 and len(calls) == 3


@pytest.mark.parametrize("bad", ["Talk to Arthur about the letters.", "עלו לבל 10 ודברו עם Arthur."])
def test_a_translation_in_english_or_with_the_wrong_term_is_not_stored(tmp_path, monkeypatch, bad):
    count, made = _night(tmp_path, monkeypatch, lambda key, texts: [bad for _ in texts])
    assert count == 0 and not made.get("quest_tasks")


def test_an_icon_token_has_to_survive(tmp_path, monkeypatch):
    import translate_kb
    assert translate_kb.refused("Use [[img:item/2000000]] Red Potion now please.", "השתמשו ב-Red Potion עכשיו.")
    assert translate_kb.refused("Use [[img:item/2000000]] Red Potion now please.",
                                "השתמשו ב-[[img:item/2000000]] Red Potion עכשיו.") is None


def test_news_goes_first_and_an_empty_summary_is_never_sent(tmp_path, monkeypatch):
    import scrape_news
    sent = []

    def translate(key, texts):
        sent.extend(texts)
        return [f"עברית {t}" for t in texts]
    item = {"id": "n1", "title": "Big update", "summary": "", "hash": "h1", "date": "2026-10-05"}
    news = {"kind": "news", "key": "n1", "item": item, "texts": ["Big update", ""]}
    count, made = _night(tmp_path, monkeypatch, translate, _jobs()[:1] + [news])
    assert sent[0] == "Big update" and "" not in sent and "-" not in sent and count == 2
    assert made["news"]["n1"]["summary"] == ""
    i = dict(item)
    scrape_news._with_he(i, made["news"])
    assert i["title_he"] == "עברית Big update" and "summary_he" not in i


def test_a_night_with_nothing_to_translate_makes_no_request(tmp_path, monkeypatch):
    import translate_kb
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(translate_kb, "missing", lambda root: [])
    monkeypatch.setattr(translate_kb, "check", lambda key: pytest.fail("a request on a night with nothing new"))
    assert translate_kb.main([str(tmp_path)]) == 0


def test_a_bad_key_warns_in_the_run(tmp_path, monkeypatch, capsys):
    import translate_kb
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(translate_kb, "missing", lambda root: _jobs())
    monkeypatch.setattr(translate_kb, "check", lambda key: False)
    assert translate_kb.main([str(tmp_path)]) == 0
    assert "::warning::translations:" in capsys.readouterr().out


def test_the_owners_curated_hebrew_beats_the_nights(tmp_path, monkeypatch):
    import scrape_news
    (tmp_path / "he.json").write_text(json.dumps({
        "quest_tasks": {"quest/1": {"en": "Talk to Arthur.", "he": "תדברו עם ארתור."}},
        "news": {"n1": {"title": "מכונה", "summary": "מכונה", "source_hash": "h"}}}, ensure_ascii=False),
        encoding="utf-8")
    assets = tmp_path / "assets"
    (assets / "quest_tasks").mkdir(parents=True)
    (assets / "quest_tasks" / "he.json").write_text(json.dumps(
        {"quest/1": {"en": "Talk to Arthur.", "he": "דברו עם Arthur."}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(translations, "ASSETS", assets)
    assert translations.he(tmp_path, "quest_tasks", "quest/1", "Talk to Arthur.") == "דברו עם Arthur."
    news_dir = tmp_path / "news_tr"
    news_dir.mkdir()
    (news_dir / "he.json").write_text(json.dumps({"n1": {"title": "ידני", "summary": "ידני", "source_hash": "h"},
                                                  "n2": {"title": "ישן", "summary": "ישן", "source_hash": "old"}},
                                                 ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(scrape_news, "TRANSLATIONS", news_dir)
    data = json.loads((tmp_path / "he.json").read_text(encoding="utf-8"))
    data["news"]["n2"] = {"title": "חדש", "summary": "חדש", "source_hash": "new"}
    (tmp_path / "he.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    merged = scrape_news.translations(kb=tmp_path)
    assert merged["n1"]["title"] == "ידני" and merged["n2"]["title"] == "חדש"
