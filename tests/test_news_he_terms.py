"""The news' Hebrew in the app's terms: the translation pipeline (tools/translate_kb.py, tools/translate_news.py,
tools/scrape_news.py) never lets "לבל" reach news.json, and the KB release check catches a KB that has it."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import kb_release  # noqa: E402
import scrape_news  # noqa: E402
import translate_kb  # noqa: E402
import translate_news  # noqa: E402

# the machine translation of Founder's Access, as the published KB carried it
BAD = {"title": "פרטי Founder's Access: תקרת לבל 100", "summary": "אפשר להגיע עד לבל 100.",
       "highlights": ["תקרת הלבל היא 100. את הג'וב הראשון לוקחים בלבל 10", "צ'ק-אין לדמויות מלבל 10 ומעלה"],
       "commentary": "הוספנו את תקרת הלבל למדריך."}


def _item():
    i = {"id": "fa", "title": "Founder's Access", "summary": "Level cap 100.", "highlights": ["a", "b"],
         "commentary": "c"}
    i["hash"] = "h"
    return i


def _texts(i: dict) -> list[str]:
    return [i["title_he"], i["summary_he"], i["commentary_he"], *i["highlights_he"]]


def test_a_translation_with_the_gamer_word_reaches_news_json_with_rama():
    i = _item()
    scrape_news._with_he(i, {"fa": {**BAD, "source_hash": "h", "body_hash": scrape_news._body_hash(i)}})
    assert not any("לבל" in x for x in _texts(i))
    assert i["title_he"] == "פרטי Founder's Access: תקרת רמה 100"
    assert "ברמה 10" in i["highlights_he"][0] and "מרמה 10" in i["highlights_he"][1]
    assert kb_release._news_hebrew_problems([i]) == []


def test_the_release_check_catches_hebrew_news_with_the_gamer_word():
    i = {**_item(), "title_he": BAD["title"], "summary_he": "", "highlights_he": [], "commentary_he": ""}
    assert "fa" in kb_release._news_hebrew_problems([i])[0]
    ok = {**_item(), "title_he": "אל תתנו לזה לבלבל אתכם", "summary_he": "", "highlights_he": []}
    assert kb_release._news_hebrew_problems([ok]) == []           # the verb "to confuse" is no level word


def test_the_nightly_translation_is_put_in_the_apps_terms(monkeypatch):
    answer = json.dumps([BAD["title"], BAD["summary"]], ensure_ascii=False)
    monkeypatch.setattr(translate_kb, "_post", lambda key, body: {"content": [{"type": "text", "text": answer}]})
    out = translate_kb.translate("key", ["Founder's Access: level cap 100", "You can reach level 100."])
    assert out == ["פרטי Founder's Access: תקרת רמה 100", "אפשר להגיע עד רמה 100."]
    assert "never \"לבל\", in any form" in translate_kb.RULES


def test_a_hand_made_news_translation_is_put_in_the_apps_terms(tmp_path, monkeypatch):
    monkeypatch.setattr(translate_news, "OUT", tmp_path)
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"strings": {"fa": BAD["summary"]}, "titles": {"fa": BAD["title"]},
                               "highlights": {"fa": BAD["highlights"]}, "commentary": {"fa": BAD["commentary"]},
                               "hashes": {"fa": "h"}, "body_hashes": {"fa": "b"}}, ensure_ascii=False),
                   encoding="utf-8")
    assert translate_news.import_("he", src) == 1
    row = json.loads((tmp_path / "he.json").read_text(encoding="utf-8"))["fa"]
    assert "לבל" not in json.dumps(row, ensure_ascii=False) and row["summary"] == "אפשר להגיע עד רמה 100."


def test_the_next_night_cleans_hebrew_news_already_published(tmp_path):
    """A KB published before the pipeline cleaned the news still said "תקרת לבל 100" and failed kb_release validate:
    the next night's update rewrites what news.json has, even when the news page can't be fetched, and counts it
    as a change so the clean KB is published (the review, UI-5)."""
    i = {**_item(), "title_he": BAD["title"], "summary_he": BAD["summary"], "highlights_he": BAD["highlights"],
         "commentary_he": BAD["commentary"]}
    ok = {**_item(), "id": "ok", "title_he": "אל תתנו לזה לבלבל אתכם"}
    (tmp_path / "news.json").write_text(json.dumps({"items": [i, ok]}, ensure_ascii=False), encoding="utf-8")
    assert kb_release._news_hebrew_problems([i])
    assert scrape_news.update(tmp_path, lambda url: None) == 1          # the site down tonight: still cleaned
    items = json.loads((tmp_path / "news.json").read_text(encoding="utf-8"))["items"]
    assert kb_release._news_hebrew_problems(items) == []
    assert items[0]["title_he"] == "פרטי Founder's Access: תקרת רמה 100" and items[1] == ok
    assert scrape_news.clean_hebrew(tmp_path) == 0                    # once clean, no change a night


def test_shipped_guides_and_the_nightly_prompt_use_the_apps_words():
    # COPY-11: the guides said "דמג'" next to the tools' "נזק", and wrote jobs and the Subway in Hebrew letters
    for f in (Path(__file__).resolve().parent.parent / "assets" / "guides" / "he").glob("*.json"):
        text = json.dumps(json.loads(f.read_text(encoding="utf-8")), ensure_ascii=False)
        for word in ("דמג'", "סאבוויי", "מג'ים", "וריור", "קשתים"):
            assert word not in text, (f.name, word)
    for rules in (translate_kb.RULES, translate_kb.REVIEW):
        assert "never \"דמג'\"" in rules and "Subway" in rules
