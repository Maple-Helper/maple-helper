"""Players' drop and mesos reports (community.json): the scraper, the KB gate and patch notes, the app's quality
rules, and every place they show. No network: the scraper reads recorded MeowDB answers (fixtures/community)."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel

import kb_release
import scrape_community
from maplehelper import availability, brain, quick, recent, sources
from maplehelper.i18n import I18n
from maplehelper.kb import COMMUNITY_MIN_SCORE, KnowledgeBase

FIX = Path(__file__).parent / "fixtures" / "community"
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_community = pytest.mark.skipif(not (REAL_KB / "community.json").exists(),
                                     reason="no community.json (a KB published before players' reports)")

# items the fixture KB gets for these tests (the recorded Snail answer names them by MeowDB's item ids)
ITEMS = {"item/413": "Bronze Ore", "item/348": "Snail Shell", "item/320": "Garnet Ore", "item/270": "Red Potion X",
         "item/709": "Green Skullcap"}


def _recorded(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _add_items(kb_root: Path, items=ITEMS, monsters=()):
    """Items (and map-less monsters, which the KB doesn't confirm in the game) added to a fixture KB copy."""
    index = json.loads((kb_root / "index.json").read_text(encoding="utf-8"))
    for key, name in items.items():
        index.append({"key": key, "id": key.split("/")[1], "name": name, "category": "item", "props": {},
                      "type": "Etc"})
        (kb_root / "pages" / "item" / f"{key.split('/')[1]}.md").write_text(f"# {name}\n", encoding="utf-8")
    for key, name in monsters:
        index.append({"key": key, "id": key.split("/")[1], "name": name, "category": "monster",
                      "props": {"Level": 30}})
        (kb_root / "pages" / "monster" / f"{key.split('/')[1]}.md").write_text(f"# {name}\n", encoding="utf-8")
    (kb_root / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _community(kb_root: Path, monsters: dict) -> None:
    (kb_root / "community.json").write_text(json.dumps({"source": "test", "fetched": "2026-10-04",
                                                        "monsters": monsters}), encoding="utf-8")


def _drop(item, up, down=0):
    return {"item": item, "up": up, "down": down, "score": up - down, "reqJob": None}


SNAIL, BLUE, CLOSED = "monster/100100", "monster/100101", "monster/999"


@pytest.fixture
def ckb(kb_copy) -> KnowledgeBase:
    """The fixture KB with community reports: Snail's (one drop denied more than confirmed, one single report) and
    mesos; Blue Snail's mesos only; a monster not in the game with a drop and mesos of its own."""
    _add_items(kb_copy, monsters=[(CLOSED, "Star Pixie")])
    _community(kb_copy, {
        SNAIL: {"drops": [_drop("item/413", 16, 1), _drop("item/348", 13), _drop("item/709", 1),
                          _drop("item/320", 2, 5), _drop("item/270", 1, 1)],
                "mesos": {"min": 18, "max": 23, "chance": 60, "count": 12, "trusted": 9}, "fetched": "2026-10-04"},
        BLUE: {"drops": [], "mesos": {"min": 5, "max": 5, "chance": None, "count": 1, "trusted": 1},
               "fetched": "2026-10-04"},
        CLOSED: {"drops": [_drop("item/320", 9)], "mesos": {"min": 90, "max": 99, "chance": 70, "count": 4,
                                                            "trusted": 4}, "fetched": "2026-10-04"},
    })
    return KnowledgeBase(kb_copy)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------- scraper (recorded answers, no network)

def _lookup(kb_root: Path):
    return scrape_community.item_lookup(json.loads((kb_root / "index.json").read_text(encoding="utf-8")))


def test_recorded_drops_map_to_kb_items_best_first(kb_copy):
    _add_items(kb_copy)
    drops, unknown = scrape_community.parse_drops(_recorded("drops_snail.json"), _lookup(kb_copy))
    keys = [d["item"] for d in drops]
    assert keys[:2] == ["item/413", "item/348"]                         # by score: 15, then 13
    assert drops[0] == {"item": "item/413", "up": 16, "down": 1, "score": 15, "reqJob": None}
    # the item MeowDB calls "Red Potion" is item/270 there; here item/270 is named otherwise, so the name decides:
    # the fixture KB's one "Red Potion" (item/2000000)
    assert "item/2000000" in keys and "item/270" not in keys
    assert unknown == len(_recorded("drops_snail.json")["drops"]) - len(drops)   # items this KB doesn't have
    assert all(d["score"] >= drops[i + 1]["score"] for i, d in enumerate(drops[:-1]))


def test_item_lookup_prefers_the_id_when_the_name_agrees(kb_copy):
    _add_items(kb_copy, {"item/1": "Twin", "item/2": "Twin", "item/3": "Solo"})
    lookup = _lookup(kb_copy)
    assert lookup(2, "Twin") == "item/2"            # two items of that name: the id tells them apart
    assert lookup(99, "Solo") == "item/3"           # an id the KB doesn't know, but a name only one item has
    assert lookup(3, "Renamed Solo") == "item/3"    # the name changed, the id still is the KB's
    assert lookup(99, "Twin") is None and lookup(None, "Nothing") is None


def test_recorded_mesos_summary_and_no_reports():
    assert scrape_community.parse_mesos(_recorded("mesos_snail.json")) == {
        "min": 2, "max": 2, "chance": 100.0, "count": 1, "trusted": 1}
    assert scrape_community.parse_mesos(_recorded("mesos_none.json")) is None


@pytest.mark.parametrize("payload", [None, [], {"drops": "x"}, {"drops": [{"itemId": 1, "itemName": "Solo",
                                                                            "upvotes": "many", "downvotes": 0}]}])
def test_bad_drop_payloads_are_refused(kb_copy, payload):
    _add_items(kb_copy, {"item/1": "Solo"})
    with pytest.raises(scrape_community.BadPayload):
        scrape_community.parse_drops(payload, _lookup(kb_copy))


def test_bad_mesos_payloads_are_refused():
    for payload in (None, {"reports": []}, {"summary": {"count": 2, "medianMin": "lots", "medianMax": 3}}):
        with pytest.raises(scrape_community.BadPayload):
            scrape_community.parse_mesos(payload)


def _fake_site(answers: dict):
    """get(url) from recorded answers by monster id; an id it doesn't list answers with nothing (site down)."""
    def get(url):
        mid = url.rsplit("=", 1)[1]
        kind = "drops" if "/drops?" in url else "mesos"
        return answers.get(mid, {}).get(kind)
    return get


def test_scrape_writes_the_whole_file_and_counts_changes(kb_copy):
    _add_items(kb_copy)
    answers = {mid: {"drops": _recorded("drops_snail.json"), "mesos": _recorded("mesos_snail.json")}
               for mid in ("100100", "100101", "130101", "1210100")}
    answers["5130104"] = {"drops": {"drops": []}, "mesos": _recorded("mesos_none.json")}
    stats = scrape_community.scrape(kb_copy, get=_fake_site(answers), log=lambda *_: None)
    data = json.loads((kb_copy / "community.json").read_text(encoding="utf-8"))
    assert stats["failed"] == 0 and stats["drops"] == 4 and stats["mesos"] == 4 and stats["changed"] == 4
    assert set(data["monsters"]) == {"monster/100100", "monster/100101", "monster/130101", "monster/1210100"}
    assert data["monsters"]["monster/100100"]["mesos"]["min"] == 2
    kb_release.validate_community(data, set(KnowledgeBase(kb_copy).entities))
    # the same answers again: nothing a player would see changed
    assert scrape_community.scrape(kb_copy, get=_fake_site(answers), log=lambda *_: None)["changed"] == 0


def test_scrape_keeps_the_previous_file_when_the_site_is_down(kb_copy, monkeypatch):
    _add_items(kb_copy)
    _community(kb_copy, {SNAIL: {"drops": [_drop("item/413", 3)], "mesos": None, "fetched": "2026-10-01"}})
    before = (kb_copy / "community.json").read_text(encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="unanswered"):
        scrape_community.scrape(kb_copy, get=_fake_site({}), log=lambda *_: None)
    assert (kb_copy / "community.json").read_text(encoding="utf-8") == before
    monkeypatch.setattr(scrape_community, "get_json", lambda url: None)
    assert scrape_community.main(["--kb", str(kb_copy), "--limit", "1"]) == 1      # a warning, the file kept
    assert (kb_copy / "community.json").read_text(encoding="utf-8") == before


def test_one_unanswered_monster_keeps_its_previous_entry(kb_copy, monkeypatch):
    _add_items(kb_copy)
    monkeypatch.setattr(scrape_community, "MAX_FAILED", 0.5)
    _community(kb_copy, {SNAIL: {"drops": [_drop("item/413", 3)], "mesos": None, "fetched": "2026-10-01"}})
    answers = {mid: {"drops": {"drops": []}, "mesos": _recorded("mesos_none.json")}
               for mid in ("100101", "130101", "1210100", "5130104")}
    stats = scrape_community.scrape(kb_copy, get=_fake_site(answers), log=lambda *_: None)
    data = json.loads((kb_copy / "community.json").read_text(encoding="utf-8"))
    assert stats["failed"] == 1 and data["monsters"][SNAIL]["fetched"] == "2026-10-01"


def test_last_run_counts_community_changes(kb_copy):
    (kb_copy / "last_run.json").write_text(json.dumps({"checked": 40, "changed": 2}), encoding="utf-8")
    scrape_community.bump_last_run(kb_copy, 3)
    assert json.loads((kb_copy / "last_run.json").read_text(encoding="utf-8")) == {
        "checked": 40, "changed": 5, "community_changed": 3}


# ---------------------------------------------------------------- the KB gate and patch notes

def test_validate_checks_community_json(ckb):
    assert kb_release.validate(ckb.root)["count"] > 19
    bad = {SNAIL: {"drops": [_drop("item/404", 1)], "mesos": {"min": 9, "max": 3, "count": 1}},
           "monster/31337": {"drops": []}}
    _community(ckb.root, bad)
    with pytest.raises(kb_release.InvalidKB) as e:
        kb_release.validate(ckb.root)
    assert "bad drop" in str(e.value) and "bad mesos" in str(e.value) and "monster/31337" in str(e.value)
    (ckb.root / "community.json").write_text("{", encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="unreadable"):
        kb_release.validate(ckb.root)


def test_patch_notes_list_community_drops_and_mesos(ckb, tmp_path):
    import shutil
    old = tmp_path / "old"
    shutil.copytree(ckb.root, old)
    new = ckb.root
    monsters = json.loads((new / "community.json").read_text(encoding="utf-8"))["monsters"]
    monsters[SNAIL]["drops"][1] = _drop("item/348", 1, 3)        # Snail Shell now denied: hidden
    monsters[SNAIL]["drops"].append(_drop("item/2000000", 4))    # a new drop
    monsters[SNAIL]["drops"][0]["up"] = 40                        # votes alone are no change
    monsters[BLUE]["mesos"] = None                                # its mesos reports are gone
    _community(new, monsters)
    d = kb_release.diff_kb(old, new)
    rows = {r["key"]: r for r in d["changed"]}
    assert rows[SNAIL]["community_added"] == ["Red Potion"]
    assert rows[SNAIL]["community_removed"] == ["Snail Shell"]
    assert "mesos" not in rows[SNAIL] and rows[BLUE]["mesos"] == ["5-5", None]
    assert kb_release.community_changes(json.loads((old / "community.json").read_text())["monsters"], monsters) == 2
    # the app's patch notes and "Updated" chip read them
    t = I18n("en")
    day = date.fromisoformat(kb_release.record_changes(new, old, "2026.10.04.0100")["date"])
    kb2 = KnowledgeBase(new)
    found = recent.recent(kb2, today=day)
    lines = recent.lines(t, kb2, found[SNAIL])
    assert "New community drop: Red Potion" in lines and "Community drop removed: Snail Shell" in lines
    assert [bidi_free(ln) for ln in recent.lines(t, kb2, found[BLUE])] == ["Mesos (community): 5–5 → —"]
    assert "new community drop Red Potion" in recent.ai_lines(kb2, [SNAIL], today=day)[0]


def test_the_first_kb_with_reports_is_no_flood_of_changes(ckb, tmp_path):
    import shutil
    old = tmp_path / "old"
    shutil.copytree(ckb.root, old)
    (old / "community.json").unlink()
    assert not [r for r in kb_release.diff_kb(old, ckb.root)["changed"] if "community_added" in r or "mesos" in r]


def test_a_wished_items_community_drop_affects_the_player():
    row = {"key": SNAIL, "name": "Snail", "community_added": ["Snail Shell"]}

    class KB:
        def get(self, k):
            return {"name": "Snail Shell"} if k == "item/348" else None
    assert recent.why(KB(), row, None, {"item/348"}) == "wish"


# ---------------------------------------------------------------- the app's quality rules

def test_drops_more_players_denied_are_hidden_and_single_reports_marked(ckb):
    drops = ckb.community_drops(SNAIL)
    assert [d["item"] for d in drops] == ["item/413", "item/348", "item/709"]     # best first; 320 and 270 hidden
    assert all(d["score"] >= COMMUNITY_MIN_SCORE for d in drops)
    assert [d["single"] for d in drops] == [False, False, True]
    assert ckb.community_vote(SNAIL, "item/320") is None
    assert ckb.community_vote(SNAIL, "item/413")["up"] == 16


def test_only_monsters_in_the_game(ckb):
    assert not availability.of(ckb).monster_key_open(CLOSED)
    assert ckb.community_drops(CLOSED) == [] and ckb.community_mesos(CLOSED) is None
    assert CLOSED not in ckb.droppers.get("item/320", [])
    assert not availability.of(ckb).item_open("item/320")          # its only source isn't in the game


def test_mesos(ckb):
    assert ckb.community_mesos(SNAIL) == (18, 23, 60.0, 12)
    assert ckb.community_mesos(BLUE) == (5, 5, None, 1)
    assert ckb.community_mesos("monster/130101") is None
    assert ckb.mesos_per_kill(SNAIL) == pytest.approx(20.5 * 0.6)
    assert ckb.mesos_per_kill(BLUE) == 5


def test_community_drops_come_first_everywhere(ckb):
    assert ckb.drop_lists(SNAIL)[sources.COMMUNITY] == ["item/413", "item/348", "item/709"]
    assert ckb.monster_drops(SNAIL)[:3] == ["item/413", "item/348", "item/709"]
    assert ckb.drop_source(SNAIL, "item/709") == sources.COMMUNITY
    assert ckb.droppers["item/413"] == [SNAIL]
    assert availability.of(ckb).item_open("item/413")              # a community drop of an open monster
    g = ckb.drop_groups(["item/413"])[0]
    assert g["sources"] == {"item/413": sources.COMMUNITY} and g["votes"] == {"item/413": (16, 1)}


def test_drop_table_and_ai_digests(ckb):
    ckb.ensure_drop_table()
    rows = (ckb.root / "drops.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[0].endswith("\tsource\tvotes")
    assert any(r.startswith("Snail\t") and r.endswith("\titem/413\tcommunity\t16 up 1 down") for r in rows)
    digest = ckb.drops_digest(SNAIL)
    assert "Bronze Ore [item/413] (16 confirmed, 1 denied)" in digest and "Green Skullcap [item/709] (single report)" \
        in digest and "Garnet Ore" not in digest
    assert "Mesos of Snail (community, median of 12 player reports): 18-23 per drop, dropped on 60% of kills" in digest
    assert "no community data" in ckb.drops_digest("monster/130101")
    assert "Mesos of Blue Snail (community, median of 1 player report): 5-5" in ckb.mesos_digest(BLUE)
    assert "Snail (Lv 1) [monster/100100] (16 confirmed, 1 denied)" in ckb.droppers_digest("item/413")
    assert "no community data" in ckb.droppers_digest("item/2000000")
    assert "| 18-23 |" in ckb.level_digest(1)


def test_ai_prefetch_and_prompt(ckb):
    p = brain.build_prompt("what does Snail drop?", None, None, ckb, False)
    assert "(16 confirmed, 1 denied)" in p and "Mesos of Snail" in p
    p = brain.build_prompt("which monsters drop Bronze Ore?", None, None, ckb, False)
    assert "Bronze Ore [item/413] (community, 16 confirmed 1 denied)" in p
    assert "Community reports of monsters dropping Bronze Ore" in p
    assert "אין נתונים מהקהילה" in brain.SYSTEM_PROMPT and "דיווח יחיד" in brain.SYSTEM_PROMPT
    assert "16 ✓" in brain.SYSTEM_PROMPT and "דיווח יחיד" in brain.REPLY_RULES


def test_instant_drops_answer_says_the_mesos(ckb):
    for lang, line in (("en", "Mesos 18–23 (Community)"), ("he", "מזו 18–23 (קהילה)")):
        a = quick.answer("what does Snail drop" if lang == "en" else "מה Snail מפיל", ckb, I18n(lang))
        assert line in a.text and a.entities[:3] == [SNAIL, "item/413", "item/348"]
    who = quick.answer("who drops Bronze Ore", ckb, I18n("en"))
    assert who.drop_groups[0]["votes"] == {"item/413": (16, 1)}


# ---------------------------------------------------------------- what shows (offscreen Qt)

def _texts(w) -> list[str]:
    return [lb.text() for lb in w.findChildren(QLabel)]


def test_cards_tiles_and_groups_show_votes_and_mesos(ckb, app):
    from maplehelper.ui.widgets import DropGroupCard, EntityCard, TileGrid
    for lang, mesos, single in (("he", "מזו 18–23 (קהילה)", "דיווח יחיד"), ("en", "Mesos 18–23 (Community)",
                                                                           "Single report")):
        t = I18n(lang)
        card = EntityCard(ckb, SNAIL, lang)
        assert mesos in bidi_free(card.mesos_label.text())
        assert "12" in card.mesos_label.toolTip()
        assert not hasattr(EntityCard(ckb, "monster/130101", lang), "mesos_label")
        grid = TileGrid(ckb, ["item/413", "item/709"], "x", t.rtl, t=t, srcs=[sources.COMMUNITY], monster=SNAIL)
        votes = [lb for lb in grid.findChildren(QLabel) if lb.objectName() == "VoteTag"]
        assert [bidi_free(v.text()) for v in votes] == ["16 ✓", single]
        assert votes[1].property("single") == "true"
        group = DropGroupCard(ckb, SNAIL, ["item/413"], t, {"item/413": sources.COMMUNITY})
        assert [lb for lb in group.findChildren(QLabel) if lb.objectName() == "VoteTag"]
    plain = TileGrid(ckb, ["item/413"], "x", False, t=I18n("en"))
    assert not [lb for lb in plain.findChildren(QLabel) if lb.objectName() == "VoteTag"]


def bidi_free(s: str) -> str:
    return "".join(ch for ch in s if ch not in "‪‬⁦⁩‏")


def test_wishlist_dropper_rows_show_votes(ckb, app):
    from maplehelper.ui.wishlist import WishlistDialog
    d = WishlistDialog(["item/413"], ckb, "en", "")
    assert "16 ✓" in [bidi_free(x) for x in _texts(d)]
    d.close()


def test_tools_show_mesos(ckb, app, tmp_path, monkeypatch):
    from maplehelper import store
    from maplehelper.ui.tools import ToolsDialog
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    p = store.Profiles()
    p.add("Kiwi", "Thief", "Thief", 3)
    d = ToolsDialog(ckb, p, store.Settings(), "en", "", {}, "calc")
    d.calc_input.setText("Snail")
    d._fill_calc()
    texts = [bidi_free(x) for x in _texts(d)]
    assert "18–23" in texts and any("About 12 mesos per kill" in x for x in texts)
    d.calc_input.setText("Red Snail")
    d._fill_calc()
    assert any("No community data" in x for x in [bidi_free(x) for x in _texts(d)])
    d.close()


def test_training_spots_label_the_most_mesos(ckb, app, tmp_path, monkeypatch):
    from maplehelper import combat, store
    from maplehelper.ui.tools import ToolsDialog
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    p = store.Profiles()
    p.add("Kiwi", "Thief", "Thief", 2)
    d = ToolsDialog(ckb, p, store.Settings(), "en", "", {}, "train")
    rows = combat.spots(ckb, 2, n=6)
    if len([s for s in rows if ckb.community_mesos(s.monster.key)]) < 2:
        pytest.skip("the fixture's spots don't have two monsters with mesos")
    d._fill_train()
    texts = [bidi_free(x) for x in _texts(d)]
    assert "Most mesos" in texts and "Mesos 18–23 (Community)" in texts
    assert d._most_mesos(rows).monster.key == SNAIL
    d.close()


# ---------------------------------------------------------------- the real knowledge base

@needs_community
def test_real_community_json_loads_and_maps_to_kb_keys():
    data = json.loads((REAL_KB / "community.json").read_text(encoding="utf-8"))
    kb = KnowledgeBase(REAL_KB)
    kb_release.validate_community(data, set(kb.entities))              # every monster and item is the KB's own
    monsters = data["monsters"]
    assert len([m for m in monsters.values() if m["drops"]]) >= 50
    assert len([m for m in monsters.values() if m["mesos"]]) >= 20
    snail = kb.monster_keys("Snail")[0]
    assert kb.community_drops(snail) and "Snail Shell" in [kb.get(d["item"])["name"] for d in kb.community_drops(snail)]
    shown = [m for m in monsters if kb.community_drops(m)]
    assert all(availability.of(kb).monster_key_open(m) for m in shown)
