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
    assert drop_keys("לא מתאים לגרינד בלבל 31") == "לא מתאים לעשות גריינד בלבל 31"
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
