"""Answer parsing, level detection and prompt assembly (no AI calls). Providers: test_providers.py."""
import pytest

from maplehelper import brain
from maplehelper.store import Character


class TestSplitMeta:
    def test_answer_without_meta(self):
        assert brain.split_meta("  Go to Henesys.  ") == ("Go to Henesys.", {})

    def test_answer_with_meta(self):
        raw = 'Hunt **Red Snail**.\n@@META@@\n{"entities": ["monster/130101"], "profile_update": {"level": 5}}'
        text, meta = brain.split_meta(raw)
        assert text == "Hunt **Red Snail**."
        assert meta == {"entities": ["monster/130101"], "profile_update": {"level": 5}}

    def test_meta_wrapped_in_code_fence(self):
        text, meta = brain.split_meta('Hi\n@@META@@\n```json\n{"entities": []}\n```')
        assert text == "Hi" and meta == {"entities": []}

    def test_malformed_meta_is_dropped_but_text_kept(self):
        assert brain.split_meta("Hi\n@@META@@\n{not json}") == ("Hi", {})
        assert brain.split_meta("Hi\n@@META@@\n") == ("Hi", {})


@pytest.mark.parametrize("text,level", [
    ("עליתי ללבל 16", 16),
    ("הגעתי ל-30 היום", 30),
    ("אני לבל 45 עכשיו", 45),
    ("I'm level 16", 16),
    ("just hit lvl 70!", 70),
    ("reached Lv. 120", 120),
])
def test_stated_level(text, level):
    assert brain.stated_level(text) == level


@pytest.mark.parametrize("text", ["where do level 30s grind?", "Red Snail is level 4", "I'm level 999", "hello"])
def test_no_stated_level(text):
    assert brain.stated_level(text) is None


def test_kb_has(kb):
    assert brain.kb_has(kb, "monster/130101")
    assert not brain.kb_has(kb, "monster/0")


class TestBuildPrompt:
    def char(self, level=5):
        return Character(id="c1", name="Tal", base_class="Beginner", job="Beginner", level=level, map="Henesys")

    def test_contains_profile_context_and_question(self, kb):
        p = brain.build_prompt("where does Red Snail live?", self.char(), None, kb, has_screenshot=True)
        assert "<player_profile>" in p and "Level: 5" in p
        assert "[monster/130101]" in p                      # page pre-fetched for the named monster
        assert "Monsters near the player's level" in p      # level digest
        assert "<screenshot>attached above</screenshot>" in p
        assert p.rstrip().endswith("</reply_rules>")
        assert "At most 6 short lines" in p

    def test_unknown_player_and_no_screenshot(self, kb):
        p = brain.build_prompt("hello", None, None, kb, has_screenshot=False, length="detailed")
        assert "<player_profile>unknown</player_profile>" in p
        assert "not available" in p and "At most 15 short lines" in p
        assert "<kb_context>" not in p

    def test_includes_history(self, kb, isolated_store):
        h = isolated_store.History("c1")
        h.append("user", "hi there")
        h.append("assistant", "hello!")
        h.add_summary("Worked on first job advancement.")
        p = brain.build_prompt("next?", self.char(), h, kb, has_screenshot=False)
        assert "Player: hi there" in p and "Helper: hello!" in p
        assert "first job advancement" in p

    def test_system_prompt_formats(self):
        # the system prompt uses {{ }} escapes around the META JSON; a bad escape would raise here
        s = brain.SYSTEM_PROMPT.format(length=brain.LENGTH["short"])
        assert '{"entities"' in s and brain.META in s


@pytest.mark.parametrize("meta", ['{"entities": null}', '{"profile_update": []}', '{"drop_groups": 5}', '[1, 2]'])
def test_malformed_meta_keeps_the_answer(meta):
    text, data = brain.split_meta("Go to Henesys.\n@@META@@\n" + meta)
    assert text == "Go to Henesys."
    assert all(not (k in data and not isinstance(data[k], t))
               for k, t in (("entities", list), ("profile_update", dict), ("drop_groups", list)))


def test_detail_tiles_cut_a_wide_grab_and_are_announced():
    from PIL import Image

    from maplehelper import capture
    tiles = capture.detail_tiles(Image.new("RGB", (3440, 1440)))
    assert len(tiles) == 3 and capture.detail_tiles(Image.new("RGB", (1280, 720))) == []
    assert all(max(Image.open(__import__("io").BytesIO(t)).size) <= capture.MAX_SIDE for t in tiles)
    p = brain.build_prompt("מה למכור?", None, None, _NoKb(), 3)
    assert "3 full-resolution parts" in p and "Reply in Hebrew," in p


class _NoKb:
    def level_digest(self, *_):
        return ""

    def find_mentions(self, *_a, **_k):
        return []


def test_app_context_never_drives_the_heuristics():
    """An old 'I'm level 16' in a picked-up conversation must not reset the level of the next question."""
    p = brain.build_prompt("איפה כדאי לי להתאמן?", None, None, _NoKb(), False,
                           extra="<continuing>Player: אני לבל 16</continuing>")
    assert "<continuing>" in p and p.index("<continuing>") < p.index("<question>")
    assert brain.stated_level("איפה כדאי לי להתאמן?") is None


@pytest.mark.parametrize("text", ["what should I do once I'm level 30?", "when im level 70 which job", "כשאני אגיע ללבל 30 מה לעשות?"])
def test_plans_are_no_stated_level(text):
    assert brain.stated_level(text) is None


# ------------------------------------------------------------------ audit: drops, scope, HUD, model name

class _Backend:
    exe = "fake"

    def __init__(self, text):
        self.text = text

    def run(self, *_a, **_k):
        from maplehelper.providers.base import RawResult
        return RawResult(text=self.text)

    def prewarm(self):
        pass


def _brain(kb_copy, text):
    from maplehelper.kb import KnowledgeBase
    b = brain.Brain(KnowledgeBase(kb_copy))
    b.backend = _Backend(text)
    return b


def test_a_drop_group_of_the_wrong_shape_keeps_the_answer(kb_copy):
    b = _brain(kb_copy, 'Red Snail drops it.\n@@META@@\n{"drop_groups": [{"monster": "monster/130101", "items": 5}]}')
    ans = b.ask("who drops Red Potion?", None, None, None)
    assert ans.error is None and ans.text == "Red Snail drops it."


@pytest.mark.parametrize("q,drops", [
    ("למה אני נופל מהחבל ליד Mano", False),
    ("מה נופל מ-Mano?", True),
    ("מה בדרך כלל נופל מ-Mano?", True),
    ("מה שנופל מ-Mano", True),
    ("what does Mano drop", True),
])
def test_falling_is_no_drops_question(q, drops):
    assert bool(brain.DROP_WORDS.search(q)) is drops


def test_which_monsters_to_grind_is_no_hat_drops_question(kb):
    q = "I'm level 58 now. What are the best quests for me to do for EXP, and which monsters should I grind?"
    assert brain.item_keys_for_question(q, kb) == [] and not brain.is_reverse(q, kb)
    assert brain.is_reverse("which monsters drop potions?", kb)
    assert brain.is_reverse("which monsters give Red Potion?", kb)


def test_drops_are_presented_as_msea_reference():
    s = brain.SYSTEM_PROMPT.format(length="")
    assert "MSEA reference" in s and "not confirmed for Classic" in s
    assert "grepping names.tsv" in s and "grepping index.json" not in s


def test_reply_rules_allow_questions_about_the_helper_and_follow_the_scope():
    assert "Maple Helper itself" in brain.REPLY_RULES and "game scope" in brain.REPLY_RULES


@pytest.mark.parametrize("light", [False, True])
def test_the_hud_outranks_the_profile_with_a_screenshot(kb, light):
    char = Character(id="c1", name="Kalimero", base_class="Thief", job="Assassin", level=31)
    shot = brain.build_prompt("what's my level?", char, None, kb, has_screenshot=True, kb_context=not light)
    assert brain.HUD_RULE in shot and shot.index(brain.HUD_RULE) > shot.index("<player_profile>")
    assert brain.HUD_RULE not in brain.build_prompt("what's my level?", char, None, kb, has_screenshot=False,
                                                    kb_context=not light)


def test_the_cli_default_model_is_named(kb_copy, monkeypatch):
    """Codex never reports its model: "You run on ChatGPT." left the AI saying its model is unknown."""
    b = _brain(kb_copy, "")
    monkeypatch.setattr(b._provider, "default_model", lambda: "GPT-6.1-Sol")
    assert b._running_on().endswith("on Claude.")
    b.prewarm()
    assert b._running_on() == "\nYou run on Claude, model GPT-6.1-Sol."
    b.last_model = "claude-sonnet-5"
    assert b._running_on() == "\nYou run on Claude, model Sonnet 5."



def test_grind_is_a_noun_spelled_the_owners_way():
    """"לגרינד" in an answer reads "לעשות גריינד"; the spelling is "גריינד" (the owner, 2026-10-04)."""
    from maplehelper.brain import drop_keys
    assert drop_keys("לא מתאים לגרינד בלבל 31") == "לא מתאים לעשות גריינד ברמה 31"
    assert drop_keys("כדאי לגריינד על Ligator") == "כדאי לעשות גריינד על Ligator"
    assert drop_keys("הגרינד שלכם") == "הגריינד שלכם" and drop_keys("grind spot") == "grind spot"


@pytest.mark.parametrize("q", ["תן לי פרטים על Red Snail", "שלח לי מידע על Red Snail", "tell me about Red Snail"])
def test_a_monster_details_question_shows_every_drop(kb_copy, q):
    """The answer names one drop at most (it doesn't repeat the cards), so the tiles came from that one name; for
    "details about X" the app shows the monster's card and every drop from the KB, as for a drops question."""
    b = _brain(kb_copy, 'Red Snail is an early monster.\n@@META@@\n{"entities": ["monster/130101"]}')
    items = [k for k, e in b.kb.entities.items() if e["category"] == "item"][:3]
    b.kb.monster_drops = lambda key: items if key == "monster/130101" else []      # (the fixture's has none)
    ans = b.ask(q, None, None, None)
    assert ans.entities[:4] == ["monster/130101"] + items



def test_the_players_level_is_named_as_one():
    """"אתם ב-31" reads "אתם בלבל 31" (the owner: the word for level before the number)."""
    from maplehelper.brain import drop_keys
    assert drop_keys("הרבה מתחתיכם (אתם ב-31)") == "הרבה מתחתיכם (אתם ברמה 31)"
    assert drop_keys("אתם ב31 עכשיו") == "אתם ברמה 31 עכשיו"
    assert drop_keys("אתם בלבל 31") == "אתם ברמה 31" and drop_keys("עוד 3 לבלים") == "עוד 3 רמות"
    assert drop_keys("אתם ב-50% מהלבל") == "אתם ב-50% מהרמה" and drop_keys("הוא ב-10:00") == "הוא ב-10:00"
    # a count after "אתם/אני" stays a count, with or without ה- (review2 LOG-4)
    for count in ("אתם ב-50 אחוז מהדרך", "אני ב-3 משימות במקביל", "אני ב-2 קווסטים", "את ב-10 מפלצות",
                  "אתם ב-20 המפות הראשונות", "אתם ב-2 הערוצים", "אני ב-100 אלף mesos"):
        assert drop_keys(count) == count


@pytest.mark.parametrize("src,out", [("ולבל 30 כדאי", "ורמה 30 כדאי"), ("הלבלים הבאים", "הרמות הבאים"),
                                     ("כשהלבל עולה", "כשהרמה עולה"), ("שלבל 30", "שרמה 30"),
                                     ("לבל אפ", "עליית רמה"), ("עשיתי לבל-אפ", "עשיתי עליית רמה"),
                                     ("לבלינג מהיר", "עליית רמות מהיר"), ("בלבל-30", "ברמה-30"),
                                     ("זה בלבל אותי", "זה בלבל אותי"), ("לבלבל אותם", "לבלבל אותם"),
                                     ("מבלבל", "מבלבל"), ("עץ מלבלב", "עץ מלבלב")])
def test_level_slang_after_any_prefix_but_never_the_verb_confuse(src, out):
    """"לבל" after any prefix reads "רמה" and "לבל אפ" a climb; "בלבל" (confused) stays unless a number follows."""
    from maplehelper.brain import drop_keys
    assert drop_keys(src) == out



def test_slashed_stat_bonuses_are_written_one_per_stat():
    from maplehelper.brain import drop_keys
    assert drop_keys("עם STR/DEX/INT/LUK +1 ו-HP/MP +10") == "עם STR +1, DEX +1, INT +1, LUK +1 ו-HP +10, MP +10"
    assert drop_keys("W.DEF/M.DEF -2") == "W.DEF -2, M.DEF -2" and drop_keys("HP/MP recovery") == "HP/MP recovery"


# ---------------------------------------------------------------- launch audit (AI area)

def test_a_count_after_a_pronoun_is_no_level():
    """"Stirge הוא ב-5 מפות" became "הוא ברמה 5 מפות" (audit AI-7)."""
    assert brain.drop_keys("Stirge הוא ב-5 מפות") == "Stirge הוא ב-5 מפות"
    assert brain.drop_keys("הם ב-2 קבוצות") == "הם ב-2 קבוצות"
    assert brain.drop_keys("אתם ב-31 ולכן") == "אתם ברמה 31 ולכן"
    assert brain.drop_keys("אתם ב-31.") == "אתם ברמה 31."
    # the player's level whatever word follows (review CORE-5); a counted noun still keeps the count
    assert brain.drop_keys("אתם ב-31 כבר, אז") == "אתם ברמה 31 כבר, אז"
    assert brain.drop_keys("אתם ב-31 עם Assassin") == "אתם ברמה 31 עם Assassin"
    assert brain.drop_keys("הדמות שלכם ב-31 בדיוק") == "הדמות שלכם ברמה 31 בדיוק"
    assert brain.drop_keys("אתם ב-3 מפות שונות") == "אתם ב-3 מפות שונות"
    assert brain.drop_keys("אני ב-2 ערוצים") == "אני ב-2 ערוצים"
    assert brain.drop_keys("היא ב-5 מקומות") == "היא ב-5 מקומות"


def test_a_key_goes_with_its_hebrew_prefix():
    assert brain.drop_keys("ראו item/294 ו-(monster/5) ו-;") == "ראו ו-;"          # audit AI-23


def test_bare_about_is_no_detail_question():
    assert not brain.DETAIL_WORDS.search("what about Lupin vs Ligator at level 35?")      # audit AI-8
    assert brain.DETAIL_WORDS.search("tell me about Mano")


def test_meta_is_the_last_block_and_its_own_json():
    """audit AI-14: a second block or trailing braces lost the META, a quoted marker cut the answer."""
    two = 'A\n@@META@@\n{"entities": []}\n@@META@@\n{"profile_update": {"level": 31}}'
    assert brain.split_meta(two) == ("A", {"profile_update": {"level": 31}})
    assert brain.split_meta('A\n@@META@@\n{"profile_update": {"level": 31}} and {x}')[1] == {"profile_update": {"level": 31}}
    assert brain.split_meta("Use the @@META@@ marker\nmore\n@@META@@\n{}") == ("Use the @@META@@ marker\nmore", {})
    assert brain.split_meta("Answer\n@@META") == ("Answer", {})


def test_profile_levels_out_of_range_and_other_characters():
    assert brain.split_meta('A\n@@META@@\n{"profile_update": {"level": 0}}')[1] == {"profile_update": {}}  # AI-15
    assert brain.stated_level("im lvl 15 on my other char") is None
    assert brain.stated_level("I'm level 10 and my friend is level 40") is None
    assert brain.stated_level("I'm level 16") == 16
    # friends who are only company, or in another sentence, take nothing from the player's own level (review CORE-7)
    assert brain.stated_level("I'm level 30, played with friends all day") == 30
    assert brain.stated_level("אני רמה 30 עם חבר") == 30
    assert brain.stated_level("my friend is level 40. I'm level 10") == 10
    assert brain.stated_level("אני רמה 30 בדמות אחרת") is None


@pytest.mark.parametrize("q", ["who's Grendel", "can't find Mano", "sauna robe stats", "2nd job warrior level?"])
def test_short_english_questions_get_english(q):
    assert brain.reply_language(q, "he") == "English"            # audit AI-17
    assert brain.reply_language("Mano", "he") == "Hebrew"         # a name alone: the app's language


def test_english_planning_line_is_dropped_but_an_answer_is_not():
    raw = "Let me check the data for the player first.\nMano is a level 20 boss.\n@@META@@\n{}"
    assert brain.split_meta(raw)[0] == "Mano is a level 20 boss."               # audit AI-19
    keep = "Let's head to Perion first, the Warrior instructor is there.\nThen talk to him."
    assert brain.split_meta(keep)[0] == keep


def test_a_brace_in_the_tables_note_never_breaks_the_prompt(monkeypatch):
    import importlib
    from maplehelper import tables
    monkeypatch.setattr(tables, "prompt_note", lambda: "a {brace} note")
    fresh = importlib.reload(brain)
    try:
        assert "a {brace} note" in fresh.SYSTEM_PROMPT.format(length="x")     # audit AI-24
    finally:
        monkeypatch.undo()
        importlib.reload(brain)


def test_scope_is_not_called_official_and_summaries_leave_the_level_out():
    assert "official (Nexon)" not in brain.SYSTEM_PROMPT                  # audit AI-16
    assert "Leave out the character's level" in brain.SUMMARY_PROMPT      # audit AI-20


def test_an_english_plural_never_gets_a_hebrew_ending():
    """Seen live: "Warrior-ים". The plural stays English, irregular ones too; Hebrew words are left alone."""
    from maplehelper.brain import drop_keys
    assert drop_keys("זה טוב ל-Warrior-ים.") == "זה טוב ל-Warriors."
    assert drop_keys("Bowman-ים ו-Thief-ים") == "Bowmen ו-Thieves"
    assert drop_keys("הרבה Lemon-ים במפה") == "הרבה Lemons במפה"
    assert drop_keys("ל-Henesys עם חברים") == "ל-Henesys עם חברים"
