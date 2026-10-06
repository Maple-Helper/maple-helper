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
