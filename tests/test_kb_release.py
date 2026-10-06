"""The KB gate in front of every publish, and the packer (same manifest format as tools/release.py)."""
import hashlib
import json
import zipfile

import pytest

import kb_release


def test_fixture_kb_is_valid(kb_copy):
    summary = kb_release.validate(kb_copy)
    assert summary["count"] == 19 and set(summary["categories"]) == set(kb_release.CATEGORIES)


def test_rejects_unreadable_index(kb_copy):
    (kb_copy / "index.json").write_text("{", encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="unreadable"):
        kb_release.validate(kb_copy)


def test_rejects_too_few_entities_and_missing_categories(kb_copy):
    index = json.loads((kb_copy / "index.json").read_text(encoding="utf-8"))
    (kb_copy / "index.json").write_text(json.dumps([e for e in index if e["category"] == "monster"]), encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB) as e:
        kb_release.validate(kb_copy, min_entities=10)
    assert "only 5 entities" in str(e.value) and "missing categories" in str(e.value)


def test_rejects_big_drop_from_previous(kb_copy, tmp_path):
    prev = tmp_path / "prev-index.json"
    prev.write_text(json.dumps([{"key": f"x/{i}"} for i in range(100)]), encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="down from 100"):
        kb_release.validate(kb_copy, previous_index=prev)


def test_small_drop_is_fine(kb_copy, tmp_path):
    prev = tmp_path / "prev-index.json"
    prev.write_text(json.dumps([{"key": f"x/{i}"} for i in range(16)]), encoding="utf-8")
    assert kb_release.validate(kb_copy, previous_index=prev)["count"] == 19


def test_rejects_missing_pages(kb_copy):
    (kb_copy / "pages" / "monster" / "130101.md").unlink()
    with pytest.raises(kb_release.InvalidKB, match="1 entries without a page"):
        kb_release.validate(kb_copy)


def test_rejects_a_kb_without_the_release_guide(kb_copy):
    from maplehelper import availability
    assert kb_release.RELEASE_GUIDE == availability.RELEASE_GUIDE     # the page the app reads the live game from
    page = kb_copy / "pages" / f"{kb_release.RELEASE_GUIDE}.md"
    text = page.read_text(encoding="utf-8")
    page.write_text(text.replace("Not at launch", "Later"), encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="lost its section"):
        kb_release.validate(kb_copy)
    page.unlink()
    index = json.loads((kb_copy / "index.json").read_text(encoding="utf-8"))
    (kb_copy / "index.json").write_text(json.dumps([e for e in index if e["key"] != kb_release.RELEASE_GUIDE]),
                                        encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="no release guide"):
        kb_release.validate(kb_copy)


def test_pack_writes_zip_and_matching_manifest(kb_copy, tmp_path):
    out = tmp_path / "dist"
    m = kb_release.pack(kb_copy, out, version="2026.10.02.1200")
    assert m["version"] == "2026.10.02.1200"
    assert m["url"] == "https://github.com/Maple-Helper/maple-helper/releases/latest/download/kb.zip"
    assert hashlib.sha256((out / "kb.zip").read_bytes()).hexdigest() == m["sha256"]
    assert json.loads((out / "kb-manifest.json").read_text(encoding="utf-8")) == m
    with zipfile.ZipFile(out / "kb.zip") as z:
        names = z.namelist()
        assert "index.json" in names and "pages/monster/130101.md" in names   # files at the zip root
        assert json.loads(z.read("meta.json"))["version"] == "2026.10.02.1200"


def test_packed_kb_installs_through_the_real_updater(kb_copy, tmp_path, monkeypatch):
    """End to end: what CI packs is exactly what installed apps accept."""
    from maplehelper import updater
    out = tmp_path / "dist"
    m = kb_release.pack(kb_copy, out)
    net = {updater.MANIFEST_URL: (out / "kb-manifest.json").read_bytes(), m["url"]: (out / "kb.zip").read_bytes()}
    user_kb = tmp_path / "user-kb"
    monkeypatch.setattr(updater, "_get", lambda url, timeout=30: net.get(url))
    monkeypatch.setattr(updater, "USER_KB", user_kb)
    monkeypatch.setattr(updater, "kb_dir", lambda: user_kb)
    assert updater.update_kb() is True
    assert updater.local_version() == m["version"]
    assert len(json.loads((user_kb / "index.json").read_text(encoding="utf-8"))) == 19


def test_default_version_sorts_as_string(kb_copy, tmp_path):
    m = kb_release.pack(kb_copy, tmp_path / "d")
    assert len(m["version"]) == len("2026.10.02.1200") and m["version"] > "2000.01.01.0000"


def test_cli_exit_codes(kb_copy, capsys):
    assert kb_release.main(["validate", str(kb_copy)]) == 0
    assert kb_release.main(["validate", str(kb_copy), "--min-entities", "999"]) == 1
    assert "::error::" in capsys.readouterr().out


# ---------------------------------------------------------------- patch notes

def _edit(kb, fn):
    index = json.loads((kb / "index.json").read_text(encoding="utf-8"))
    index = fn(index)
    (kb / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _drops(kb, rows):
    lines = ["monster\tmonster_level\tmonster_key\titem\titem_type\titem_key"]
    lines += [f"{m}\t1\t{mk}\t{i}\tEtc\t{ik}" for m, mk, i, ik in rows]
    (kb / "drops.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_patch_notes_list_exactly_what_changed(kb_copy, tmp_path):
    import shutil
    old = tmp_path / "old"
    shutil.copytree(kb_copy, old)
    index = json.loads((kb_copy / "index.json").read_text(encoding="utf-8"))
    mob, gone, page = [e for e in index if e["category"] == "monster"][:3]
    _drops(old, [(mob["name"], mob["key"], "Snail Shell", "item/1"), (mob["name"], mob["key"], "Red Potion", "item/2")])
    _drops(kb_copy, [(mob["name"], mob["key"], "Snail Shell", "item/1"), (mob["name"], mob["key"], "Blue Potion", "item/3")])

    def change(idx):
        for e in idx:
            if e["key"] == mob["key"]:
                e["props"] = {**(e.get("props") or {}), "HP": 999999}
            if e["key"] == page["key"]:
                e["hash"] = "different"
        idx = [e for e in idx if e["key"] != gone["key"]]
        return idx + [{"key": "monster/new", "name": "Brand New Mob", "category": "monster"}]
    _edit(kb_copy, change)

    d = kb_release.diff_kb(old, kb_copy)
    assert d["counts"] == {"added": 1, "removed": 1, "changed": 1, "updated": 1}
    assert d["added"][0]["name"] == "Brand New Mob" and d["removed"][0]["key"] == gone["key"]
    c = d["changed"][0]
    assert c["key"] == mob["key"] and ["HP", (mob.get("props") or {}).get("HP"), 999999] in c["props"]
    assert c["drops_added"] == ["Blue Potion"] and c["drops_removed"] == ["Red Potion"]
    assert d["updated"][0]["key"] == page["key"]


def test_changelog_is_newest_first_and_skips_no_op_updates(kb_copy, tmp_path):
    import shutil
    old = tmp_path / "old"
    shutil.copytree(kb_copy, old)
    assert kb_release.record_changes(kb_copy, old, "2026.10.01.0100") is None      # nothing changed
    assert not (kb_copy / "changelog.json").exists()
    _edit(kb_copy, lambda idx: idx + [{"key": "item/a", "name": "A", "category": "item"}])
    kb_release.record_changes(kb_copy, old, "2026.10.01.0100")
    _edit(kb_copy, lambda idx: idx + [{"key": "item/b", "name": "B", "category": "item"}])
    kb_release.record_changes(kb_copy, old, "2026.10.02.0100")
    log = json.loads((kb_copy / "changelog.json").read_text(encoding="utf-8"))
    assert [e["version"] for e in log] == ["2026.10.02.0100", "2026.10.01.0100"]


def test_app_shows_only_updates_newer_than_its_kb(kb_copy, tmp_path, monkeypatch):
    import shutil
    from maplehelper import updater
    old = tmp_path / "old"
    shutil.copytree(kb_copy, old)
    for v, key in (("2026.10.01.0100", "item/a"), ("2026.10.02.0100", "item/b")):
        _edit(kb_copy, lambda idx, key=key: idx + [{"key": key, "name": key, "category": "item"}])
        kb_release.record_changes(kb_copy, old, v)
    monkeypatch.setattr(updater, "kb_dir", lambda: kb_copy)
    assert [e["version"] for e in updater.changes_since("2026.10.01.0100")] == ["2026.10.02.0100"]
    assert len(updater.changes_since("")) == 2


def test_pack_with_previous_kb_ships_the_patch_notes(kb_copy, tmp_path):
    import shutil
    old = tmp_path / "old"
    shutil.copytree(kb_copy, old)
    _edit(kb_copy, lambda idx: idx + [{"key": "item/a", "name": "A", "category": "item"}])
    m = kb_release.pack(kb_copy, tmp_path / "d", version="2026.10.03.0100", previous_kb=old)
    with zipfile.ZipFile(tmp_path / "d" / "kb.zip") as z:
        log = json.loads(z.read("changelog.json"))
    assert log[0]["version"] == m["version"] and log[0]["counts"]["added"] == 1


def test_a_page_re_parsed_by_a_newer_scraper_is_not_called_updated(kb_copy, tmp_path):
    import shutil
    old = tmp_path / "old"
    shutil.copytree(kb_copy, old)
    index = json.loads((kb_copy / "index.json").read_text(encoding="utf-8"))
    reparsed, edited = index[0]["key"], index[1]["key"]

    def change(idx):
        for e in idx:
            if e["key"] == reparsed:
                e.update(hash="new-output", parser=2)      # only our own output format changed (SCP-20)
            if e["key"] == edited:
                e["hash"] = "site-edit"
        return idx
    _edit(kb_copy, change)
    d = kb_release.diff_kb(old, kb_copy)
    assert [u["key"] for u in d["updated"]] == [edited]


def test_rejects_many_entities_gone_at_once_even_within_ten_percent(kb_copy, tmp_path):
    index = json.loads((kb_copy / "index.json").read_text(encoding="utf-8"))
    prev = tmp_path / "prev-index.json"
    extra = [{"key": f"quest/x{i}", "name": "Q", "category": "quest"} for i in range(kb_release.MAX_REMOVED + 1)]
    prev.write_text(json.dumps(index + extra), encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="removed at once"):
        kb_release.validate(kb_copy, previous_index=prev)


def test_rejects_a_scrape_that_lost_a_categorys_stats(kb_copy, tmp_path):
    index = json.loads((kb_copy / "index.json").read_text(encoding="utf-8"))
    prev = tmp_path / "prev-index.json"
    prev.write_text(json.dumps(index), encoding="utf-8")
    assert any(e["category"] == "monster" and e.get("props") for e in index)
    _edit(kb_copy, lambda idx: [{**e, "props": {}} if e["category"] == "monster" else e for e in idx])
    with pytest.raises(kb_release.InvalidKB, match="monster: 0% of entries have stats"):
        kb_release.validate(kb_copy, previous_index=prev)


def test_rejects_a_page_left_behind_by_a_removed_entity(kb_copy):
    (kb_copy / "pages" / "item" / "gone.md").write_text("old item", encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="pages without an entry, e.g. item/gone"):
        kb_release.validate(kb_copy)
