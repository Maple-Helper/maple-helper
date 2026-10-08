"""The 'last session' summary: what changed for each character while playing."""
from types import SimpleNamespace

from maplehelper import session
from maplehelper.i18n import I18n


def char(cid="a", name="Kiwi", level=30, job="Assassin"):
    return SimpleNamespace(id=cid, name=name, level=level, job=job)


def test_summary_tracks_levels_quests_and_questions():
    kiwi = char()
    s = session.SessionStats(now=0)
    s.touch(kiwi)
    s.question(kiwi, now=60)
    s.question(kiwi, now=40 * 60)
    s.change(kiwi, "quest+", "Mai's Training")
    s.change(kiwi, "quest-", "Mai's Training")
    kiwi.level = 33
    out = s.summary(SimpleNamespace(characters=[kiwi]))
    assert out["minutes"] == 40
    row = out["chars"][0]
    assert (row["start_level"], row["end_level"], row["questions"]) == (30, 33, 2)
    assert row["quests_done"] == ["Mai's Training"]
    text = "\n".join(session.lines(out, I18n("en")))
    assert "30 → 33" in text and "Mai's Training" in text and "2 questions" in text


def test_hebrew_level_change_reads_old_to_new_like_the_other_changes():
    # COPY-12: "רמה 12 ← 15" pointed the other way from a skill change's "35% → 50%" (sitedata.change_text)
    from maplehelper import bidi
    kiwi = char(level=12)
    s = session.SessionStats(now=0)
    s.touch(kiwi)
    kiwi.level = 15
    out = s.summary(SimpleNamespace(characters=[kiwi]))
    he = I18n("he")
    for line in (session.lines(out, he)[0], session.blocks(out, he)[0]["lines"][0]):
        assert "←" not in line and "12 → 15" in line
        assert f"{bidi.LRI}12 → 15{bidi.PDI}" in bidi.plain(line, True)


def test_nothing_happened_means_no_summary():
    kiwi = char()
    s = session.SessionStats(now=0)
    s.touch(kiwi)
    assert s.summary(SimpleNamespace(characters=[kiwi])) is None


def test_deleted_character_is_left_out():
    s = session.SessionStats(now=0)
    s.question(char(cid="gone"))
    assert s.summary(SimpleNamespace(characters=[])) is None


def test_the_summary_brings_back_that_sessions_chat_per_character():
    kiwi = char()
    s = session.SessionStats(now=100)
    s.question(kiwi, now=200)
    summary = s.summary(SimpleNamespace(characters=[kiwi]))
    log = [{"role": "user", "text": "before", "t": 50}, {"role": "user", "text": "where is Mano?", "t": 150},
           {"role": "assistant", "text": "In ...", "t": 160}, {"role": "user", "text": "after", "t": 999}]
    got = session.records(summary, kiwi.id, lambda cid: SimpleNamespace(recent=lambda n: log))
    assert [m["text"] for m in got] == ["where is Mano?", "In ..."]


def test_blocks_keep_each_character_apart():
    from maplehelper.i18n import I18n
    summary = {"chars": [
        {"id": "a", "name": "Kalimero", "start_level": 30, "end_level": 31, "start_job": "Assassin",
         "end_job": "Assassin", "questions": 0, "quests_done": [], "quests_started": []},
        {"id": "b", "name": "Kalimeroz", "start_level": 1, "end_level": 1, "start_job": "Beginner",
         "end_job": "Beginner", "questions": 2, "quests_done": [], "quests_started": []}]}
    b = session.blocks(summary, I18n("en"))
    assert [x["name"] for x in b] == ["Kalimero", "Kalimeroz"]
    assert b[0]["lines"] == ["Level 30 → 31"] and b[1]["lines"] == ["Level 1", "2 questions"]
