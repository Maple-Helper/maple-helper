"""Settings, character profiles and conversation history."""
import json


def test_settings_defaults_and_persistence(isolated_store):
    s = isolated_store.Settings()
    assert s["hotkey_toggle"] == "F9" and s["language"] is None
    s["language"] = "en"
    assert json.loads(isolated_store.Settings.path.read_text(encoding="utf-8"))["language"] == "en"
    assert isolated_store.Settings()["language"] == "en"


def test_settings_keep_new_defaults_for_old_files(isolated_store):
    isolated_store.Settings.path.write_text('{"language": "he"}', encoding="utf-8")
    s = isolated_store.Settings()
    assert s["language"] == "he" and s["appearance"] == "light"


def test_provider_defaults_to_claude(isolated_store):
    assert isolated_store.Settings()["provider"] == "claude"


class TestApiKeyMode:
    def test_legacy_flag_belongs_to_claude(self, isolated_store):
        # settings written before Codex support kept a single bool for the Anthropic key
        isolated_store.Settings.path.write_text('{"api_key_fallback": true}', encoding="utf-8")
        s = isolated_store.Settings()
        assert s.api_key_mode("claude") and not s.api_key_mode("codex")

    def test_each_provider_keeps_its_own_flag(self, isolated_store):
        s = isolated_store.Settings()
        s.set_api_key_mode("codex", True)
        assert s.api_key_mode("codex") and not s.api_key_mode("claude")
        s.set_api_key_mode("claude", True)
        s.set_api_key_mode("codex", False)
        again = isolated_store.Settings()
        assert again.api_key_mode("claude") and not again.api_key_mode("codex")


def test_corrupt_settings_fall_back_to_defaults(isolated_store):
    isolated_store.Settings.path.write_text("{oops", encoding="utf-8")
    assert isolated_store.Settings()["font_size"] == 14


class TestProfiles:
    def make(self, store):
        p = store.Profiles()
        p.add("Tal", "Warrior", "Warrior", 12)
        return p

    def test_add_sets_active_and_persists(self, isolated_store):
        self.make(isolated_store)
        again = isolated_store.Profiles()
        assert again.active.name == "Tal" and again.active.level == 12

    def test_apply_update(self, isolated_store):
        p = self.make(isolated_store)
        changed = p.apply_update({"level": "30", "job": "Fighter", "map": "Perion",
                                  "quests_started": ["Q1", "Q2"], "note": "wants Power Strike"})
        assert ("level", 30) in changed and ("job", "Fighter") in changed
        assert p.active.active_quests == ["Q1", "Q2"]
        changed = p.apply_update({"quests_completed": ["Q1", "missing"], "note": "wants Power Strike"})
        assert changed == [("quest-", "Q1")]        # duplicate note and unknown quest are ignored
        assert isolated_store.Profiles().active.active_quests == ["Q2"]

    def test_apply_update_rejects_bad_levels(self, isolated_store):
        p = self.make(isolated_store)
        for bad in (0, 251, "abc", None, ""):
            assert p.apply_update({"level": bad}) == []
        assert p.active.level == 12

    def test_no_active_character(self, isolated_store):
        assert isolated_store.Profiles().apply_update({"level": 5}) == []

    def test_summary(self, isolated_store):
        c = self.make(isolated_store).active
        c.notes = [f"n{i}" for i in range(15)]
        s = c.summary()
        assert "Level: 12" in s and "n14" in s and "n4" not in s   # only the last 10 notes


class TestHistory:
    def test_recent_and_corrupt_lines(self, isolated_store):
        h = isolated_store.History("abc")
        for i in range(25):
            h.append("user", f"msg {i}")
        with h.log.open("a", encoding="utf-8") as f:
            f.write("not json\n")
        recent = h.recent()
        assert len(recent) == 19 and recent[-1]["text"] == "msg 24"

    def test_summaries_roll_and_clear(self, isolated_store):
        h = isolated_store.History("abc")
        for i in range(12):
            h.add_summary(f"s{i}")
        assert h.summaries() == [f"s{i}" for i in range(2, 12)]
        h.clear()
        assert h.recent() == [] and h.summaries() == []


def test_profiles_written_by_a_newer_version_still_load(tmp_path, monkeypatch):
    """A newer version (or a preview build) may save fields this one doesn't know: skip them, don't crash."""
    import json

    from maplehelper import store
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    (tmp_path / "profiles.json").write_text(json.dumps({"active": "a", "characters": [
        {"id": "a", "name": "Kiwi", "base_class": "Thief", "job": "Assassin", "level": 34, "from_the_future": 1}]}))
    p = store.Profiles()
    assert p.active.name == "Kiwi" and p.active.level == 34


def test_launch_waits_for_a_running_update(monkeypatch):
    from maplehelper import setupwait
    states = iter([True, True, False])
    monkeypatch.setattr(setupwait, "setup_running", lambda: next(states))
    assert setupwait.wait_for_setup(limit_s=5, step_s=0) is True
    monkeypatch.setattr(setupwait, "setup_running", lambda: False)
    assert setupwait.wait_for_setup(limit_s=5, step_s=0) is False


def test_damaged_install_is_explained_not_a_traceback(tmp_path, monkeypatch):
    """A missing file of the install (e.g. shiboken6.Shiboken) shows a reinstall prompt and logs the error."""
    import ctypes
    import sys
    import webbrowser

    from maplehelper import setupwait, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    shown, opened = [], []
    if sys.platform == "win32":
        monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda *a: shown.append(a) or 6)
    # a real osascript alert waits up to 10 minutes for a click that never comes on a headless runner
    monkeypatch.setattr(setupwait, "_mac_alert", lambda message, buttons: shown.append(message) or "")
    monkeypatch.setattr(webbrowser, "open", opened.append)
    setupwait.report_broken_install(ModuleNotFoundError("No module named 'shiboken6.Shiboken'"))
    assert "shiboken6.Shiboken" in (tmp_path / "logs" / "startup-error.log").read_text(encoding="utf-8")
    if sys.platform == "win32":
        assert shown and opened == [setupwait.DOWNLOAD_URL]
    elif sys.platform == "darwin":
        assert shown and not opened


def test_malformed_ai_profile_update_is_ignored(isolated_store):
    """A reply with {"name": ...} or lists where text belongs once broke every later start."""
    p = isolated_store.Profiles()
    p.add("Amit", "Warrior", "Fighter", 30)
    changed = p.apply_update({"map": {"name": "Henesys"}, "job": ["Page"], "note": ["x"],
                              "quests_started": "Pio's Quest", "quests_completed": [{"name": "y"}]})
    assert changed == [("quest+", "Pio's Quest")]
    assert p.apply_update(["not", "a", "dict"]) == []
    c = isolated_store.Profiles().active
    assert c.map == "" and c.job == "Fighter" and c.active_quests == ["Pio's Quest"] and c.notes == []
    assert "Pio's Quest" in c.summary()


def test_profiles_drop_bad_saved_values_on_load(isolated_store):
    isolated_store.Profiles.path.write_text(json.dumps({"active": "a", "characters": [
        {"id": "a", "name": "Amit", "base_class": "Warrior", "job": "Fighter", "level": 30,
         "map": {"name": "Henesys"}, "notes": [["x"]]},
        "garbage", {"id": "b"}]}), encoding="utf-8")
    p = isolated_store.Profiles()
    assert [c.id for c in p.characters] == ["a"]
    assert p.active.map == "" and p.active.notes == []


def test_hud_job_names_keep_class_and_job_consistent(isolated_store):
    """An Old School HUD says "Archer": the class becomes Bowman and an old Thief job doesn't survive."""
    p = isolated_store.Profiles()
    p.add("Kalimero", "Thief", "Assassin", 30)
    p.apply_update({"level": 15, "job": "Archer", "base_class": "Archer"})
    c = p.active
    assert (c.base_class, c.job, c.level) == ("Bowman", "Bowman", 15)
    assert c.job_label == "Bowman" and c.job_shown == "Archer"   # one name everywhere: the KB's (the owner)
    p.apply_update({"job": "Hunter"})
    assert (p.active.base_class, p.active.job) == ("Bowman", "Hunter") and p.active.job_label == "Hunter"
    p.apply_update({"base_class": "Warrior", "level": 5})
    assert (p.active.base_class, p.active.job) == ("Warrior", "Beginner")
    p.apply_update({"job": "Not a job"})
    assert p.active.job == "Beginner"


def test_sync_takes_the_hud_name(isolated_store):
    p = isolated_store.Profiles()
    p.add("Kalimero", "Bowman", "Bowman", 15)
    # a longer HUD name is never taken silently (the chat asks: "this is the same character?"), letter case is
    assert p.apply_update({"name": "KalimeroZz"}) == [] and p.active.name == "Kalimero"
    assert p.apply_update({"name": "KALIMERO"}) == [("name", "KALIMERO")]
    assert p.apply_update({"name": "not a name!"}) == [] and p.active.name == "KALIMERO"


def test_another_character_on_the_hud_is_not_the_active_one():
    from maplehelper.store import hud_name, same_character
    assert same_character("kalimerozz", "KalimeroZz") and not same_character("Kalimero", "KalimeroZz")
    assert not same_character("Ayash", "Ayashii") and not same_character("Ayash", "Ayashii", seen=False)
    assert not same_character("KalimeroZz", "NewGuy99") and not same_character("Al", "Alpha")
    assert hud_name({"name": " NewGuy99 "}) == "NewGuy99" and hud_name({"name": "a b"}) is None


def test_another_name_never_renames_the_active_character(isolated_store):
    p = isolated_store.Profiles()
    p.add("KalimeroZz", "Bowman", "Bowman", 15)
    assert p.apply_update({"name": "NewGuy99"}) == [] and p.active.name == "KalimeroZz"
    assert p.find_by_name("kalimerozz") is p.active


def test_alt_with_a_longer_name_is_never_merged(isolated_store):
    """'Amit' and the alt 'AmitBow' are two characters once the HUD confirmed 'Amit', or when both are saved."""
    from maplehelper.store import same_character
    p = isolated_store.Profiles()
    p.add("Amit", "Bowman", "Bowman", 45)
    p.apply_update({"name": "Amit"})                      # the HUD confirms the name
    assert p.apply_update({"name": "AmitBow", "level": 31}) == [("level", 31)] or p.active.name == "Amit"
    assert p.active.name == "Amit"
    assert not same_character("Amit", "AmitBow", seen=False, others=("AmitBow",))
    assert not same_character("Kalimero", "KalimeroZz") and not same_character("KalimeroZz", "Kalimero")


def test_damaged_files_never_crash_the_start(isolated_store):
    p = isolated_store.Settings.path
    p.write_bytes(b"\xff\xfe broken")
    assert isolated_store.Settings()["hotkey_toggle"] == "F9"
    p.write_text("[1, 2]", encoding="utf-8")
    assert isolated_store.Settings()["hotkey_toggle"] == "F9"
    isolated_store.Profiles.path.write_text("[1]", encoding="utf-8")
    assert isolated_store.Profiles().characters == []


def test_last_good_copy_is_used(isolated_store):
    s = isolated_store.Settings()
    s["language"] = "en"
    s["language"] = "he"                       # the second save keeps the first as .bak
    isolated_store.Settings.path.write_text("", encoding="utf-8")   # a power cut emptied it
    assert isolated_store.Settings()["language"] == "en"


def test_a_damaged_required_field_repairs_the_character(isolated_store):
    """An old corruption (job saved as an object, level 31.0) must not lose the whole character."""
    isolated_store.Profiles.path.write_text(json.dumps({"active": "a", "characters": [
        {"id": "a", "name": "Amit", "base_class": "Thief", "job": {"x": 1}, "level": 31.0,
         "exp_pct": "12", "stats": {"acc": "50", "hp": 900}}]}), encoding="utf-8")
    c = isolated_store.Profiles().active
    assert c and c.name == "Amit" and c.level == 31 and c.job == "Thief" and c.exp_pct is None
    assert c.stats == {"hp": 900}


def test_history_skips_lines_of_the_wrong_shape(isolated_store):
    h = isolated_store.History("x")
    h.append("user", "hi")
    with h.log.open("a", encoding="utf-8") as f:
        f.write('[1, 2]\n{"role": "user"}\n"text"\n')
    assert [r["text"] for r in h.recent()] == ["hi"]


def test_history_file_is_trimmed(isolated_store, monkeypatch):
    h = isolated_store.History("x")
    monkeypatch.setattr(isolated_store.History, "MAX_BYTES", 2000)
    monkeypatch.setattr(isolated_store.History, "KEEP_BYTES", 1500)
    for i in range(100):
        h.append("user", f"question {i}")
    recs = h.recent(1000)
    assert len(recs) <= 30 and recs[-1]["text"] == "question 99"


def test_finished_quests_match_loosely_and_the_list_is_capped(isolated_store):
    p = isolated_store.Profiles()
    p.add("Ayash", "Thief", "Bandit", 30)
    p.apply_update({"quests_started": ["Maya's Medicine"]})
    assert p.apply_update({"quests_started": ["maya's  medicine"]}) == []          # the same quest again
    assert p.apply_update({"quests_completed": ["Mayas medicine"]}) == [("quest-", "Maya's Medicine")]
    for i in range(40):
        p.apply_update({"quests_started": [f"Quest {i}"]})
    c = isolated_store.Profiles().active
    assert len(c.active_quests) == isolated_store.MAX_ACTIVE_QUESTS and c.active_quests[-1] == "Quest 39"
    shown = c.summary().split("Active quests: ")[1].split("\n")[0].split(", ")
    assert len(shown) == isolated_store.QUESTS_IN_PROMPT and shown[-1] == "Quest 39"


def test_old_piles_of_quests_and_notes_are_capped_on_load(isolated_store):
    isolated_store.Profiles.path.write_text(json.dumps({"active": "a", "characters": [
        {"id": "a", "name": "Kiwi", "base_class": "Thief", "job": "Assassin", "level": 34,
         "active_quests": [f"q{i}" for i in range(200)], "notes": [f"n{i}" for i in range(100)]}]}), encoding="utf-8")
    c = isolated_store.Profiles().active
    assert c.active_quests[-1] == "q199" and len(c.active_quests) == isolated_store.MAX_ACTIVE_QUESTS
    assert c.notes[-1] == "n99" and len(c.notes) == isolated_store.MAX_NOTES


def test_nan_and_infinity_in_profiles_never_block_the_start(isolated_store):
    raw = ('{"active": "a", "characters": ['
           '{"id": "a", "name": "A", "base_class": "Thief", "job": "Hermit", "level": Infinity,'
           ' "stats": {"acc": NaN, "hp": Infinity, "mp": 1e400, "dmg_min": 50}},'
           '{"id": "b", "name": "B", "base_class": "Thief", "job": "Hermit", "level": 1e400},'
           '{"id": "c", "name": "C", "base_class": "Thief", "job": "Hermit", "level": NaN}]}')
    isolated_store.Profiles.path.write_text(raw, encoding="utf-8")
    p = isolated_store.Profiles()
    assert [c.level for c in p.characters] == [1, 1, 1]
    assert p.active.stats == {"dmg_min": 50}


def test_unknown_fields_survive_a_save(isolated_store):
    """After a downgrade the fields of the newer version are kept on disk, not deleted by the first save."""
    isolated_store.Profiles.path.write_text(json.dumps({"active": "a", "future_top": [1], "characters": [
        {"id": "a", "name": "Kiwi", "base_class": "Thief", "job": "Assassin", "level": 34,
         "future_field": "keep me"}]}), encoding="utf-8")
    p = isolated_store.Profiles()
    p.set_active("a")
    p.apply_update({"level": 35})
    saved = json.loads(isolated_store.Profiles.path.read_text(encoding="utf-8"))
    assert saved["future_top"] == [1]
    assert saved["characters"][0]["future_field"] == "keep me" and saved["characters"][0]["level"] == 35


def test_history_trim_gets_well_under_the_cap_with_long_answers(isolated_store, monkeypatch):
    monkeypatch.setattr(isolated_store.History, "MAX_BYTES", 40_000)
    monkeypatch.setattr(isolated_store.History, "KEEP_BYTES", 30_000)
    h = isolated_store.History("long")
    for i in range(60):
        h.append("user", f"q{i}")
        h.append("assistant", "שלום " * 300)          # ~3 KB a line in UTF-8
    size = h.log.stat().st_size
    assert size <= 40_000
    before = h.log.read_bytes()
    h.append("user", "one more")                      # under the cap: appended, not rewritten
    assert h.log.read_bytes().startswith(before)
    recs = h.recent(5)
    assert len(recs) == 5 and recs[-1]["text"] == "one more"
    assert all(json.loads(line) for line in h.log.read_text(encoding="utf-8").splitlines())


def test_recent_reads_the_tail_of_a_big_log(isolated_store):
    h = isolated_store.History("tail")
    with h.log.open("w", encoding="utf-8") as f:
        for i in range(5000):
            f.write(json.dumps({"t": i, "role": "user", "text": f"q{i} " + "x" * 50}) + "\n")
    assert [r["text"].split()[0] for r in h.recent(3)] == ["q4997", "q4998", "q4999"]
    assert len(h.recent(2000)) == 2000


def _mac_startup_failure(tmp_path, monkeypatch, exc, answer):
    import subprocess
    import sys
    import webbrowser

    from maplehelper import setupwait, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(setupwait, "_language", lambda: "en")
    calls, opened = [], []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=answer + "\n", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    setupwait.report_broken_install(exc)
    return calls, opened


def test_mac_startup_failure_is_shown_not_silent(tmp_path, monkeypatch):
    """The macOS bundle has no Dock icon: a failed start showed nothing at all. Now a system alert says so."""
    calls, opened = _mac_startup_failure(tmp_path, monkeypatch, PermissionError("read-only data folder"), "Close")
    assert len(calls) == 1 and calls[0][0] == "osascript" and not opened
    message = calls[0][calls[0].index("end run") + 1]
    assert "couldn't start" in message and "startup-error.log" in message and "the Mac" in message


def test_mac_damaged_install_offers_the_download_page(tmp_path, monkeypatch):
    from maplehelper import setupwait
    calls, opened = _mac_startup_failure(tmp_path, monkeypatch, ModuleNotFoundError("No module named 'x'"), "Download")
    assert calls[0][-2:] == ["Close", "Download"] and "Applications" in calls[0][-3]
    assert opened == [setupwait.RELEASES_URL]
    assert setupwait.MAC_TEXT["broken"]["he"] != setupwait.BROKEN_TEXT["he"]
    assert setupwait.MAC_TEXT["startup"]["he"] != setupwait.STARTUP_TEXT["he"]


def test_f12_hotkey_loads_as_the_default_on_windows(isolated_store, monkeypatch):
    # Windows never lets a program register F12: a saved F12 is the default key there, and stays F12 on macOS
    import json
    isolated_store.Settings.path.write_text(json.dumps({"hotkey_toggle": "F12", "hotkey_voice": "F7"}), "utf-8")
    monkeypatch.setattr(isolated_store.sys, "platform", "win32")
    s = isolated_store.Settings()
    assert (s["hotkey_toggle"], s["hotkey_voice"]) == ("F9", "F7")
    # the default is the other hotkey's: the next default, never two hotkeys on one key
    isolated_store.Settings.path.write_text(json.dumps({"hotkey_toggle": "F12", "hotkey_voice": "F9"}), "utf-8")
    s = isolated_store.Settings()
    assert (s["hotkey_toggle"], s["hotkey_voice"]) == ("F10", "F9")
    isolated_store.Settings.path.write_text(json.dumps({"hotkey_toggle": "F12", "hotkey_voice": "F12"}), "utf-8")
    s = isolated_store.Settings()
    assert (s["hotkey_toggle"], s["hotkey_voice"]) == ("F9", "F10")
    monkeypatch.setattr(isolated_store.sys, "platform", "darwin")
    assert isolated_store.Settings()["hotkey_toggle"] == "F12"


def test_editing_a_character_keeps_it_and_resets_what_the_hud_confirmed(isolated_store):
    # the character card's edit: a hand-picked job drops the game's own job name, a rename waits for the HUD again
    p = isolated_store.Profiles()
    c = p.add("Kiwi", "Thief", "Assassin", 30)
    c.job_shown, c.name_seen = "Assassin", True
    p.edit(c.id, "Kiwi", "Thief", "Assassin", 31)                   # only the level: nothing else forgotten
    assert (c.level, c.job_shown, c.name_seen) == (31, "Assassin", True)
    p.edit(c.id, "Kiwo", "Thief", "Bandit", 32)
    again = isolated_store.Profiles().characters[0]                  # saved
    assert (again.id, again.name, again.job, again.level) == (c.id, "Kiwo", "Bandit", 32)
    assert again.job_shown == "" and again.name_seen is False
    p.edit("no-such-id", "X", "Thief", "Thief", 1)                  # a deleted character: nothing happens
    assert [ch.name for ch in isolated_store.Profiles().characters] == ["Kiwo"]


def test_a_new_portrait_replaces_the_old_file(isolated_store, tmp_path, monkeypatch):
    monkeypatch.setattr(isolated_store, "AVATAR_DIR", tmp_path / "avatars")
    (tmp_path / "avatars").mkdir()
    p = isolated_store.Profiles()
    assert p.set_avatar(b"png") is None and p.avatar_path() is None      # no character yet: nothing written
    c = p.add("Kiwi", "Thief", "Assassin", 30)
    p.set_active(c.id)
    clock = iter([1000, 2000])
    monkeypatch.setattr(isolated_store.time, "time", lambda: next(clock))
    p.set_avatar(b"first")
    first = p.avatar_path()
    p.set_avatar(b"second")
    assert p.avatar_path().read_bytes() == b"second" and not first.exists()     # the old file doesn't pile up
    assert isolated_store.Profiles().avatar_path().read_bytes() == b"second"     # saved with the character
