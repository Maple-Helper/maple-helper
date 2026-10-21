"""Live content (maplehelper/live.py): the owner's files reach every app with no release, and nothing a bad or
half-served file says can break one."""
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from maplehelper import live

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """live.py reading and writing a throwaway folder, with nothing downloaded yet."""
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(live, "FILES_DIR", tmp_path / "live" / "assets")
    monkeypatch.setattr(live, "STATE_FILE", tmp_path / "live" / "state.json")
    live.start("9.9.9")
    yield tmp_path
    live._files.clear()
    live._gen += 1
    live._reset_caches()


def _server(files: dict[str, bytes], contract: dict | None = None, listed: dict | None = None):
    """A fake GitHub: get(url) for the list and the files (a listed sha may differ from what is served)."""
    entries = {}
    for rel, data in files.items():
        k = live.kind(rel)
        entries[rel] = {"sha256": (listed or {}).get(rel, live.text_sha(data)),
                        "contract": (contract or {}).get(rel, live.CONTRACTS[k] if k else 1)}
    manifest = json.dumps({"schema": 1, "files": entries}).encode()
    calls = []

    def get(url, timeout=30):
        calls.append(url)
        if url == live.raw_url(live.MANIFEST):
            return manifest
        for rel, data in files.items():
            if url == live.raw_url(rel):
                return data
        return None
    get.calls = calls
    return get


def _facts_with(text: str) -> bytes:
    d = json.loads((ROOT / "assets/official/facts.json").read_text(encoding="utf-8"))
    d["facts"] = d["facts"] + [{"id": "live-test", "he": text, "en": text}]
    return json.dumps(d, ensure_ascii=False).encode()


def test_the_manifest_is_current():
    import live_manifest
    assert live_manifest.current(), "assets/live.json is out of date: run python tools/live_manifest.py"


def test_every_live_kind_has_a_contract_and_the_config_only_names_real_switches():
    listed = json.loads((ROOT / "assets/live.json").read_text(encoding="utf-8"))["files"]
    assert {"live/config.json", "official/facts.json", "ai/system.md", "items/he.json"} <= set(listed)
    for rel, e in listed.items():
        assert e["contract"] == live.CONTRACTS[live.kind(rel)]
    # what the owner writes into the config reads: a switch that switches something, an announcement that shows
    cfg = json.loads((ROOT / "assets/live/config.json").read_text(encoding="utf-8"))
    assert set(cfg["flags"]) <= set(live.FLAGS) and all(isinstance(v, bool) for v in cfg["flags"].values())
    assert cfg["min_app"] == "" or re.fullmatch(r"\d+\.\d+(\.\d+)?", cfg["min_app"])
    ids = [a["id"] for a in cfg["announcements"]]
    assert len(ids) == len(set(ids)) and all(i.strip() for i in ids)
    for a in cfg["announcements"]:
        assert a.get("he") or a.get("en"), a["id"]
        assert not a.get("link") or live.SAFE_LINK.fullmatch(a["link"]), a["id"]
        for d in ("from", "until"):
            assert not a.get(d) or live._when(a[d], False), (a["id"], d)
    from maplehelper.i18n import STRINGS
    for key, row in cfg["strings"].items():
        assert key in STRINGS and set(row) <= {"he", "en"}, key


def test_every_switch_is_read_somewhere():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "maplehelper").rglob("*.py"))
    for flag in live.FLAGS:
        if flag.startswith("provider_"):
            assert 'live.enabled(f"provider_{self.provider}")' in code
        else:
            assert f'live.enabled("{flag}")' in code, flag


def test_kinds():
    assert live.kind("live/config.json") == "config"
    assert live.kind("ai/system.md") == "ai"
    assert live.kind("guides/he/beginner-guide.json") == "guides"
    assert live.kind("skills/he.json") == "translations"
    for never in ("notes/whatsnew.json", "brand/app.ico", "guides/img/x.png", "../maplehelper/app.py", "ai/x.py"):
        assert live.kind(never) is None


def test_a_changed_file_comes_live_and_wins(fresh):
    data = _facts_with("עובדה חיה")
    gen = live.generation()
    assert live.sync("9.9.9", _server({"official/facts.json": data})) == {"official/facts.json"}
    assert live.asset("official/facts.json") == live.FILES_DIR / "official/facts.json"
    assert live.generation() > gen
    from maplehelper import official
    assert any(f.get("id") == "live-test" for f in official.facts())
    # nothing new: no download, nothing changed
    get = _server({"official/facts.json": data})
    assert live.sync("9.9.9", get) == set()
    assert get.calls == [live.raw_url(live.MANIFEST)]


def test_a_file_like_the_shipped_one_is_never_downloaded(fresh):
    same = (ROOT / "assets/official/facts.json").read_bytes()
    get = _server({"official/facts.json": same.replace(b"\r\n", b"\n")})
    assert live.sync("9.9.9", get) == set()
    assert get.calls == [live.raw_url(live.MANIFEST)]
    assert live.asset("official/facts.json") == live.bundled("official/facts.json")


def test_back_to_the_shipped_copy_when_main_has_it_again(fresh):
    live.sync("9.9.9", _server({"official/facts.json": _facts_with("x")}))
    same = (ROOT / "assets/official/facts.json").read_bytes()
    assert live.sync("9.9.9", _server({"official/facts.json": same})) == {"official/facts.json"}
    assert live.asset("official/facts.json") == live.bundled("official/facts.json")
    assert not (live.FILES_DIR / "official/facts.json").exists()


def test_a_file_that_doesnt_match_its_checksum_or_doesnt_parse_is_skipped(fresh):
    data = _facts_with("x")
    assert live.sync("9.9.9", _server({"official/facts.json": data}, listed={"official/facts.json": "0" * 64})) == set()
    broken = b"{not json"
    assert live.sync("9.9.9", _server({"official/facts.json": broken})) == set()
    assert live.asset("official/facts.json") == live.bundled("official/facts.json")


def test_an_unreadable_list_keeps_what_is_in_use(fresh):
    live.sync("9.9.9", _server({"official/facts.json": _facts_with("x")}))
    assert live.sync("9.9.9", lambda url, timeout=30: b"<html>rate limited</html>") is None
    assert live.sync("9.9.9", lambda url, timeout=30: None) is None
    assert live.asset("official/facts.json") != live.bundled("official/facts.json")


def test_another_contract_a_new_file_or_a_foreign_path_never_comes_live(fresh):
    get = _server({"official/facts.json": _facts_with("x"), "official/new.json": b"{}",
                   "notes/whatsnew.json": b"{}", "../evil.json": b"{}"},
                  contract={"official/facts.json": live.CONTRACTS["facts"] + 1})
    assert live.sync("9.9.9", get) == set()
    assert get.calls == [live.raw_url(live.MANIFEST)]


def test_a_new_app_version_drops_what_the_old_one_downloaded(fresh):
    live.sync("9.9.9", _server({"official/facts.json": _facts_with("x")}))
    live.start("9.9.9")                       # a restart keeps it
    assert live.asset("official/facts.json") != live.bundled("official/facts.json")
    live.start("10.0.0")                      # an update drops it
    assert live.asset("official/facts.json") == live.bundled("official/facts.json")
    assert not (live.FILES_DIR / "official/facts.json").exists()


def test_a_test_copy_can_follow_a_branch(monkeypatch):
    assert "/main/assets/live.json" in live.raw_url("live.json")
    monkeypatch.setenv("MAPLEHELPER_LIVE_BRANCH", "feat/live-control")
    assert "/feat/live-control/assets/" in live.raw_url("live.json")
    monkeypatch.setenv("MAPLEHELPER_LIVE_BRANCH", "../../x")
    assert "/main/" in live.raw_url("live.json")


# ---------------------------------------------------------------- the config

def _config(fresh, cfg: dict) -> None:
    live.sync("9.9.9", _server({live.CONFIG: json.dumps(cfg, ensure_ascii=False).encode()}))


def test_switches_and_the_lowest_version(fresh):
    assert live.enabled("mesowatch") and not live.outdated("0.1.0")
    _config(fresh, {"flags": {"mesowatch": False, "telemetry": True}, "min_app": "0.19.0"})
    assert not live.enabled("mesowatch") and live.enabled("telemetry") and live.enabled("osms_shop")
    assert live.outdated("0.18.3") and not live.outdated("0.19.0") and not live.outdated("1.0")
    from maplehelper import mesowatch, osms, serverstatus, updater
    assert mesowatch.snapshot(refresh=False) is None and osms.items(refresh=False) is None
    _config(fresh, {"flags": {"osms_shop": False, "server_status": False, "kb_update": False, "app_update": False}})
    assert osms.items(refresh=False) is None and serverstatus.fetch() is None
    assert updater.fetch_kb() == "uptodate" and updater.newer_release("0.0.1") is None


def test_announcements_in_order_by_date_version_and_language(fresh):
    _config(fresh, {"announcements": [
        {"id": "a", "he": "שלום", "en": "Hello", "title_he": "כותרת", "link": "https://maplehelper.app/x",
         "link_text_en": "Read", "toast": True},
        {"id": "b", "en": "English only", "level": "warning"},
        {"id": "later", "he": "x", "from": "2026-11-01"},
        {"id": "over", "he": "x", "until": "2026-10-20"},
        {"id": "today", "he": "x", "from": "2026-10-21 18:00", "until": "2026-10-21"},
        {"id": "old-app", "he": "x", "max_app": "0.18.0"},
        {"id": "new-app", "he": "x", "min_app": "0.20.0"},
        {"id": "en-only", "he": "x", "lang": "en"},
        {"id": "bad-date", "he": "x", "until": "next week"},
        {"id": "bad-link", "he": "x", "link": "javascript:alert(1)"},
        {"id": "", "he": "no id"}, {"id": "empty"}, "junk"]})
    now = datetime(2026, 10, 21, 19, 0)
    he = live.announcements("0.19.0", "he", (), now)
    assert [a["id"] for a in he] == ["a", "b", "today", "bad-link"]
    assert he[0] == {"id": "a", "text": "שלום", "title": "כותרת", "link": "https://maplehelper.app/x",
                     "link_text": "Read", "level": "info", "toast": True}
    assert he[1]["text"] == "English only" and he[1]["level"] == "warning"
    assert he[3]["link"] == ""
    assert [a["id"] for a in live.announcements("0.19.0", "he", ("a", "b"), now)] == ["today", "bad-link"]
    assert "en-only" in [a["id"] for a in live.announcements("0.19.0", "en", (), now)]
    assert [a["id"] for a in live.announcements("0.19.0", "he", (), datetime(2026, 10, 21, 17, 0))] == [
        "a", "b", "bad-link"]


def test_a_text_fix_needs_the_same_placeholders(fresh):
    from maplehelper.i18n import I18n
    t = I18n("he")
    _config(fresh, {"strings": {"err_generic": {"he": "תקלה קטנה, נסו שוב."},
                                "live_more": {"he": "ועוד {count}"},
                                "news_strip_head": {"en": "From {date}", "he": "כתבה מ-{date}!"},
                                "no_such_key": {"he": "x"}}})
    assert t("err_generic") == "תקלה קטנה, נסו שוב."
    assert I18n("en")("err_generic") == "Something went wrong. Try again."      # English not fixed
    assert t("live_more", n=3) == "ועוד 3 הודעות"                                 # a renamed placeholder: ignored
    assert t("news_strip_head", date="1.1") == "כתבה מ-1.1!"
    assert I18n("en")("news_strip_head", date="1.1") == "From 1.1"


def test_the_ai_instructions_come_live_and_a_broken_copy_falls_back(fresh):
    from maplehelper import brain
    shipped = brain.SYSTEM_PROMPT
    system = (ROOT / "assets/ai/system.md").read_text(encoding="utf-8")
    new = system.replace("You are Maple Helper,", "You are Maple Helper (live),")
    try:
        live.sync("9.9.9", _server({"ai/system.md": new.encode()}))
        brain.load_prompts()
        assert "Maple Helper (live)" in brain.SYSTEM_PROMPT
        assert brain.SYSTEM_PROMPT.replace(" (live)", "", 1) == shipped
        # a copy that lost its markers or breaks .format: the shipped one
        for broken in (new.replace("@@TABLES_NOTE@@", ""), new + "\n{oops}", new.replace("@@META@@", "META")):
            live.sync("9.9.9", _server({"ai/system.md": broken.encode()}))
            brain.load_prompts()
            assert brain.SYSTEM_PROMPT == shipped
    finally:
        live._files.clear()
        brain.load_prompts()
    assert brain.SYSTEM_PROMPT == shipped


def test_the_ai_files_are_the_prompt_the_app_sends():
    from maplehelper import brain, tables
    system = (ROOT / "assets/ai/system.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    note = tables.prompt_note().replace("{", "{{").replace("}", "}}")
    assert brain.SYSTEM_PROMPT == system.rstrip("\n").replace("@@TABLES_NOTE@@", note) + "\n"
    assert "drop_groups" in brain.SYSTEM_PROMPT.format(length="x")
    assert re.search(r"\{length\}", brain.REPLY_RULES)


def test_a_switched_off_ai_says_so(fresh):
    from maplehelper import brain as brain_mod
    from maplehelper.kb import KnowledgeBase
    _config(fresh, {"flags": {"provider_claude": False}})
    b = brain_mod.Brain(KnowledgeBase(Path(__file__).parent / "fixtures" / "kb"), provider="claude")
    assert b.ask("hi", None, None, None).error == "live_off"
    from maplehelper.i18n import STRINGS
    assert STRINGS["err_live_off"]["he"] and STRINGS["err_live_off"]["en"]


def test_the_announcement_strip(fresh):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from maplehelper.i18n import I18n
    from maplehelper.ui.announce import AnnouncementStrip
    strip = AnnouncementStrip()
    item = {"id": "a", "text": "שלום ל-Henesys", "title": "", "link": "https://maplehelper.app", "link_text": "",
            "level": "info", "toast": False}
    strip.show_items([item, {**item, "id": "b"}], I18n("he"))
    assert not strip.isHidden() and strip.item()["id"] == "a"
    assert "Maple" in strip.head.text() and "ועוד הודעה אחת" in strip.head.text()      # (bidi marks around them)
    assert not strip.link.isHidden() and "לפרטים" in strip.link.text()
    got = []
    strip.dismissed.connect(got.append)
    strip.close_btn.click()
    assert got == ["a"]
    strip.show_items([], I18n("he"))
    assert strip.isHidden()
