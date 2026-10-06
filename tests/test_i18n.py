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
    assert t.p("ob_install", "codex") == "Install ChatGPT"
    assert t.p("ob_install", "claude") == "Install Claude Code"      # no _claude variant: the base string
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
