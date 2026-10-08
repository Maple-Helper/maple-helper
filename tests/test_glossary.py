"""The "?" beside game terms: every term the app marks has an explanation, and marking never breaks
the order of an English block inside Hebrew."""
from maplehelper import bidi, glossary


def test_every_marked_term_is_explained_in_both_languages():
    for term in glossary.TERMS:
        assert glossary.explain(term, "he"), term
        assert glossary.explain(term, "en"), term


def test_tips_point_to_nothing_the_app_lacks():
    # COPY-13: the website glossary sent players to "the scroll simulator" / "the damage formula guide"
    for term in glossary.TERMS:
        he, en = glossary.explain(term, "he"), glossary.explain(term, "en")
        assert "סימולטור" not in he and "ראו את המדריך" not in he and "אימון" not in he, term
        assert "simulator" not in en and "See the" not in en, term
    assert "גריינד" in glossary.explain("grind", "he")


def test_only_the_first_appearance_is_marked():
    out = glossary.annotate("<p>ACC 47 and ACC 50, AP 5, Lv. 30, EXP 45</p>", "en")
    assert out.count("g:ACC") == 1 and out.count("g:AP") == 1
    assert "g:Lv." not in out and "g:EXP" not in out          # everyday words get no "?"
    assert glossary.annotate("<a href='x'>ACC</a>", "en").count("g:ACC") == 0     # never inside a link


def test_marks_go_after_an_english_block_in_hebrew():
    line = bidi.isolate_ltr_runs("יש לה Avoid 14 בלבד")
    out = glossary.annotate(line, "he")
    run = f"{bidi.LRE}Avoid\u00a014{bidi.PDF}"        # kept on one line (no-break space)
    assert run in out and out.index("g:Avoid") > out.index(run)


def test_no_mark_inside_a_kb_name_block():
    # "Bottomwear HP Scroll: Chaos" is one block in a Hebrew line (bidi.set_names): its HP is part of a name
    bidi.set_names(["Bottomwear HP Scroll: Chaos"])
    try:
        line = bidi.isolate_ltr_runs("קנו Bottomwear HP Scroll: Chaos ואז HP 50")
        out = glossary.annotate(line, "he")
        assert f"{bidi.LRI}Bottomwear HP Scroll: Chaos{bidi.PDI}" in out
        assert out.count("g:HP") == 1 and out.index("g:HP") > out.index(bidi.PDI)
    finally:
        bidi.set_names([])


def test_a_hebrew_terms_value_shows_once():
    """"ACC: 47" in a Hebrew line is two English runs; the value moved after the "?" was then written again
    ("ACC: 47 ? 47", review COPY-R1)."""
    import re
    from maplehelper import bidi
    for line, value in (("צריך ACC: 47 כדי לא לפספס", "47"), ("ל-Mano יש HP: 7,420 ו-MP: 30.", "7,420")):
        out = re.sub(r"<[^>]+>", "", glossary.annotate(bidi.plain(line, True), "he"))
        assert out.count(value) == 1, out
