"""Every UI string exists in Hebrew and English with the same placeholders."""
import string

import pytest

from maplehelper.i18n import STRINGS, I18n


def fields(s: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


@pytest.mark.parametrize("key", sorted(STRINGS))
def test_both_languages_with_same_placeholders(key):
    entry = STRINGS[key]
    assert entry.get("he") and entry.get("en"), f"{key} is missing a language"
    assert fields(entry["he"]) == fields(entry["en"]), f"{key} placeholders differ"


PROVIDER_SUFFIXES = ("_claude", "_codex", "_gemini", "_grok")


@pytest.mark.parametrize("key", sorted(k for k in STRINGS if k.endswith(PROVIDER_SUFFIXES)))
def test_provider_variants_have_a_base_string(key):
    assert key.rsplit("_", 1)[0] in STRINGS


def test_provider_lookup():
    t = I18n("en")
    assert t.p("ob_install", "codex") == "Install ChatGPT (Codex, OpenAI's official tool)"
    assert t.p("ob_install", "claude") == "Install Claude Code (Anthropic's official tool)"      # no _claude variant: the base string
    assert t.p("err_usage_limit", None) == t("err_usage_limit")


def test_lookup_and_fallbacks():
    assert I18n("en")("hotkey_taken", key="F9").startswith("F9 is taken")
    assert I18n("xx").lang == "he" and I18n("he").rtl and not I18n("en").rtl
    assert I18n("en")("no_such_key") == "no_such_key"


@pytest.mark.parametrize("key", ["ob_privacy", "ob_privacy_codex", "ob_privacy_gemini", "ob_privacy_grok"])
def test_privacy_line_names_everything_sent_to_the_ai(key):
    # The prompt also carries the character, recent chat and summaries (README "Data and privacy"),
    # so the onboarding line must not say only the question and screenshot leave the computer.
    en, he = STRINGS[key]["en"], STRINGS[key]["he"]
    assert "Only your question" not in en and " PC" not in en
    assert "character" in en and "summaries" in en
    assert "הדמות" in he and "סיכומים" in he


def test_telemetry_hint_names_what_is_sent():
    # telemetry.py sends the app version, OS and a random install id with every event.
    en, he = STRINGS["telemetry_hint"]["en"], STRINGS["telemetry_hint"]["he"]
    assert "only which features" not in en
    assert "version" in en and "random id" in en
    assert "גרסת" in he and "מזהה אקראי" in he


@pytest.mark.parametrize("key,kw", [("news_btn_new", {}), ("sell_summary", {"mesos": "10"}), ("farm_every", {}),
                                    ("spot_crowd", {}), ("craft_head_all", {"prof": "Smithing"}),
                                    ("price_fm", {"median": "5", "low": "5", "high": "5"}),
                                    ("price_fm_trend", {"trend": "+3%", "days": 7, "window": 30}),
                                    ("route_walk_alt", {})])
def test_a_count_of_one_reads_as_one(key, kw):
    """"1 חדשות", "1 items": a count that can be 1 has its own "_one" string (the audit, HEB-8)."""
    assert f"{key}_one" in STRINGS and "1 " not in I18n("he")(key, n=1, **kw)
    assert I18n("en")(key, n=1, **kw) == STRINGS[f"{key}_one"]["en"].format(n=1, **kw)


def test_votes_and_a_mesos_loss_read_right():
    t = I18n("he")
    assert "1 שחקנים" not in t("votes_tip", up=1, down=2)
    assert "ב--" not in t("grind_tip_mesos_down", n="1,250") and "ירד" in t("grind_tip_mesos_down", n="1,250")


def test_hebrew_mac_settings_paths_match_the_english():
    """The macOS 15 permission name and the full Function Keys path were added to the English only; a Hebrew Mac
    player on macOS 15 didn't find the setting (the review, UI-4)."""
    from maplehelper.i18n import I18n
    he = lambda key: I18n("he")(key).replace(" ", " ")      # (whole names: i18n.WHOLE_NAMES)
    en = I18n("en")
    assert "macOS 15" in en("perm_screen_body") and "macOS 15: הקלטת מסך ושמע מערכת" in he("perm_screen_body")
    for key in ("ob_done_hint_mac", "hotkey_fn_mac"):
        assert "Keyboard Shortcuts > Function Keys" in en(key)
        assert "הגדרות המערכת > מקלדת > קיצורי מקלדת > מקשי פונקציה" in he(key)
