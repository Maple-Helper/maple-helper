"""The answer evals: the case file is always valid, and the instant answers pass against the real KB when it's here.

Claude mode is never run from tests: it spends a real player's plan usage.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import eval_answers

REAL_KB = eval_answers.KB
needs_real_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(),
                                   reason="real knowledge base not present (data/kb)")


def ans(text="", entities=(), drop_groups=()):
    return SimpleNamespace(text=text, entities=list(entities), drop_groups=list(drop_groups))


def test_case_file_is_valid():
    cases = eval_answers.load_cases()
    assert eval_answers.validate_cases(cases) == []
    assert len(cases) >= 40
    he = sum(c["lang"] == "he" for c in cases)
    assert 0.5 <= he / len(cases) <= 0.75                # mostly Hebrew, like the players
    assert any(c["checks"].get("instant") is False for c in cases)


@pytest.mark.parametrize("case, problem", [
    ({"id": "Bad Id", "question": "q", "lang": "he", "kind": "stats", "checks": {"instant": False}}, "lowercase"),
    ({"id": "a", "question": " ", "lang": "he", "kind": "stats", "checks": {"instant": False}}, "empty question"),
    ({"id": "a", "question": "q", "lang": "fr", "kind": "stats", "checks": {"instant": False}}, "lang"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "nope", "checks": {"instant": False}}, "kind"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "stats", "checks": {"must_mentoin": ["x"]}}, "unknown checks"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "stats", "checks": {"must_mention": "x"}}, "list of strings"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "stats", "checks": {"instant": "yes"}}, "true or false"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "stats", "checks": {"instant": True}}, "something to check"),
])
def test_validation_catches_typos(case, problem):
    assert any(problem in p for p in eval_answers.validate_cases([case]))


def test_duplicate_ids_are_rejected():
    c = {"id": "a", "question": "q", "lang": "he", "kind": "stats", "checks": {"instant": False}}
    assert any("duplicate" in p for p in eval_answers.validate_cases([c, dict(c)]))


def test_score_reads_text_and_card_names(kb):
    # an instant drops answer names the items only on its cards; numbers match with or without separators
    a = ans("Red Snail · HP: 7,420", entities=["monster/130101"],
            drop_groups=[{"monster": "monster/100101", "items": ["item/2000000"]}])
    checks = {"must_mention": ["7420", "red potion", "Blue Snail"], "must_not_mention": ["P.DMG"],
              "entities_include": ["monster/130101", "item/2000000"]}
    assert eval_answers.score(checks, a, kb) == []


def test_score_reports_every_problem(kb):
    a = ans("Snail drops P.DMG", entities=["monster/100100"])
    checks = {"must_mention": ["Red Potion"], "must_mention_any": ["Henesys", "Snail Garden"],
              "must_not_mention": ["p.dmg"], "entities_include": ["item/2000000"]}
    assert eval_answers.score(checks, a, kb) == [
        "missing 'Red Potion'", "none of 'Henesys', 'Snail Garden'", "mentions 'p.dmg'", "no card for item/2000000"]


def test_quick_mode_on_the_fixture_kb(kb):
    cases = [
        {"id": "hp", "question": "Red Snail hp", "lang": "en", "kind": "stats",
         "checks": {"must_mention": ["45"], "entities_include": ["monster/130101"], "instant": True}},
        {"id": "judge", "question": "should I hunt Red Snail?", "lang": "en", "kind": "judgement",
         "checks": {"instant": False}},
        {"id": "wrongly-claude", "question": "Red Snail hp", "lang": "en", "kind": "stats", "checks": {"instant": False}},
        {"id": "claude-only", "question": "where is Athena Pierce", "lang": "en", "kind": "npc",
         "checks": {"must_mention": ["Henesys"]}},
    ]
    results = {r["id"]: r for r in eval_answers.run_quick(cases, kb)}
    assert set(results) == {"hp", "judge", "wrongly-claude"}       # no "instant": not a quick-mode case
    assert results["hp"]["ok"] and results["judge"]["ok"]
    assert not results["wrongly-claude"]["ok"] and "answered instantly" in results["wrongly-claude"]["problems"][0]


def test_reports_compare_with_the_previous_one(tmp_path):
    old = [{"id": "a", "ok": True}, {"id": "b", "ok": False}, {"id": "c", "ok": True}]
    # dated in the past: a report written "now" must count as newer whatever the time of day
    (tmp_path / "20000101-000000.json").write_text(json.dumps({"results": old}), encoding="utf-8")
    new = [{"id": "a", "lang": "he", "kind": "stats", "ok": False, "problems": ["missing 'x'"]},
           {"id": "b", "lang": "he", "kind": "stats", "ok": True, "problems": []},
           {"id": "d", "lang": "en", "kind": "guide", "ok": False, "problems": ["missing 'y'"]}]
    path = eval_answers.save_report(new, "sonnet", folder=tmp_path)
    prev = eval_answers.previous_report(tmp_path, before=path)
    assert eval_answers.compare(prev, new) == (["a"], ["b"])      # "d" is new: nothing to compare with
    assert eval_answers.compare(None, new) == ([], [])


def test_claude_mode_refuses_to_run_under_pytest(capsys):
    fixture_kb = Path(__file__).parent / "fixtures" / "kb"
    assert eval_answers.main(["--mode", "claude", "--yes", "--kb", str(fixture_kb)]) == 2
    assert "refusing" in capsys.readouterr().out


@pytest.fixture(scope="module")
def real_kb():
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@needs_real_kb
def test_case_keys_exist_in_the_real_kb(real_kb):
    missing = [(c["id"], k) for c in eval_answers.load_cases() for k in c["checks"].get("entities_include", [])
               if not real_kb.get(k)]
    assert missing == []


@needs_real_kb
def test_instant_answers_pass_on_the_real_kb(real_kb):
    results = eval_answers.run_quick(eval_answers.load_cases(), real_kb)
    failed = {r["id"]: r["problems"] for r in results if not r["ok"]}
    assert results and failed == {}


def test_claude_runner_scores_and_cleans_up(kb, monkeypatch):
    # a stand-in Brain: the real one would spend plan usage
    from maplehelper import brain
    from maplehelper.brain import Answer

    class FakeBrain:
        shut = False

        def __init__(self, kb, provider=None, model=None):
            pass

        def available(self):
            return True

        def ask(self, question, character, history, screenshot_jpeg, on_delta=None):
            if "fail" in question:
                return Answer(error="usage_limit")
            return Answer(text="Red Snail lives in Snail Garden", entities=["monster/130101"], cost_usd=0.01)

        def shutdown(self):
            FakeBrain.shut = True

    monkeypatch.setattr(brain, "Brain", FakeBrain)
    cases = [{"id": "ok", "question": "where is Red Snail", "lang": "en", "kind": "where",
              "checks": {"must_mention": ["Snail Garden"], "instant": True}},
             {"id": "err", "question": "fail", "lang": "en", "kind": "where", "checks": {"must_mention": ["x"]}}]
    results = eval_answers.run_claude(cases, kb)
    assert [r["ok"] for r in results] == [True, False]
    assert results[1]["problems"] == ["error: usage_limit"] and results[0]["cost_usd"] == 0.01
    assert FakeBrain.shut


# ------------------------------------------------------------------ list recall, filters, profiles

def test_must_list_scores_recall_and_passes_only_complete(kb):
    checks = {"must_list": ["Red Snail", "Blue Snail", "Mano", "Stump"], "must_not_list": ["El Nath"]}
    half = ans("Red Snail and Blue Snail drop it")
    assert eval_answers.recall(checks, half, kb) == 0.5
    assert eval_answers.score(checks, half, kb) == ["list misses 2/4: 'Mano', 'Stump'"]
    # a name on a card counts as listed, like any check
    full = ans("Blue Snail, Mano and Stump", entities=["monster/130101"])
    assert eval_answers.recall(checks, full, kb) == 1.0 and eval_answers.score(checks, full, kb) == []
    out = ans("Red Snail, Blue Snail, Mano, Stump, and some in El Nath")
    assert eval_answers.score(checks, out, kb) == ["lists 'El Nath'"]
    assert eval_answers.recall({"must_mention": ["x"]}, out, kb) is None      # not a list case


@pytest.mark.parametrize("case, problem", [
    ({"id": "a", "question": "q", "lang": "he", "kind": "list", "checks": {"must_list": ["x"]}}, "needs a"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "list", "why": "w", "checks": {"must_list": []}},
     "list of strings"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "list", "tags": ["Bad Tag"], "checks": {"instant": False}},
     "tags"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "list", "profile": {"level": "31"},
      "checks": {"instant": False}}, "profile level"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "list", "profile": {"lvl": 31}, "checks": {"instant": False}},
     "unknown profile fields"),
    ({"id": "a", "question": "q", "lang": "he", "kind": "list", "profile": {"level": 400},
      "checks": {"instant": False}}, "1-250"),
])
def test_validation_of_the_new_fields(case, problem):
    assert any(problem in p for p in eval_answers.validate_cases([case]))


def test_the_new_list_cases_are_there():
    cases = eval_answers.load_cases()
    hard = [c for c in cases if "hard" in (c.get("tags") or [])]
    assert len(hard) >= 25 and sum(c["lang"] == "he" for c in hard) >= 10
    assert sum(bool(c["checks"].get("must_list")) for c in hard) >= 15
    capes = next(c for c in cases if c["id"] == "list-cape-quests-en")["checks"]
    assert capes["must_list"] == ["Stranger's Identity", "Delivering the Flying Medicine", "Maya's Last Collection"]


def test_case_filter_by_id_tag_kind_and_pattern():
    cases = [{"id": "list-a", "kind": "list", "tags": ["quest"]}, {"id": "shop-b", "kind": "shop"},
             {"id": "stats-c", "kind": "stats", "tags": ["quest", "hard"]}]

    def ids(spec):
        return [c["id"] for c in eval_answers.select_cases(cases, spec)]
    assert ids(None) == ["list-a", "shop-b", "stats-c"]
    assert ids("quest") == ["list-a", "stats-c"] and ids("shop") == ["shop-b"]
    assert ids("list-*, stats-c") == ["list-a", "stats-c"]
    with pytest.raises(SystemExit, match="nope"):
        eval_answers.select_cases(cases, "list-a,nope")


def test_profiles_default_case_and_override():
    hunter = {"level": 35, "job": "Hunter", "base_class": "Bowman"}
    assert eval_answers.case_profile({}) == eval_answers.DEFAULT_PROFILE
    assert eval_answers.case_profile({"profile": {"level": 35}})["level"] == 35
    assert eval_answers.case_profile({"profile": {"level": 35}})["job"] == "Assassin"
    assert eval_answers.case_profile({"profile": hunter}, override={"level": 50})["job"] == "Assassin"
    assert eval_answers.case_profile({"profile": hunter}, override={}) == {}
    assert eval_answers.parse_profile("level=35, job=Hunter,base_class=Bowman") == hunter
    assert eval_answers.parse_profile("none") == {} and eval_answers.parse_profile(None) is None
    with pytest.raises(SystemExit):
        eval_answers.parse_profile("lvl=3")
    ch = eval_answers.character(eval_answers.case_profile({"profile": hunter}))
    assert (ch.job, ch.base_class, ch.level) == ("Hunter", "Bowman", 35)
    assert eval_answers.character({}) is None


@needs_real_kb
def test_list_names_are_real_kb_names(real_kb):
    """Every name a list check expects (or forbids) is part of a real KB name: a typo would fail every run."""
    names = [eval_answers._norm(e["name"]) for e in real_kb.entities.values()]
    unknown = [(c["id"], n) for c in eval_answers.load_cases() for k in ("must_list", "must_not_list")
               for n in c["checks"].get(k, []) if not any(eval_answers._norm(n) in m for m in names)]
    assert unknown == []


# ------------------------------------------------------------------ live runner: metrics, workers, repeats

def test_live_runner_measures_each_answer(kb, monkeypatch):
    from maplehelper import brain
    from maplehelper.brain import Answer

    made, asked = [], []

    class FakeBrain:
        def __init__(self, kb, provider=None, model=None):
            self.provider, self.model, self.down = provider, model, False
            made.append(self)

        def available(self):
            return True

        def prewarm(self):
            pass

        def ask(self, question, character, history, screenshot_jpeg, on_delta=None):
            asked.append((question, character.job if character else None, character.level if character else None))
            on_delta("")                 # nothing visible yet: no first text
            on_delta("Red")
            return Answer(text="Red Snail and Blue Snail", tool_calls=3, turns=4, model="m1")

        def shutdown(self):
            self.down = True

    monkeypatch.setattr(brain, "Brain", FakeBrain)
    cases = [{"id": "l", "question": "snails?", "lang": "en", "kind": "list", "why": "w",
              "checks": {"must_list": ["Red Snail", "Blue Snail", "Mano"]}},
             {"id": "h", "question": "bow?", "lang": "en", "kind": "equip", "profile": {"job": "Hunter", "level": 35},
              "checks": {"must_mention": ["Red Snail"]}}]
    results = eval_answers.run_live(cases, kb, "codex", "gpt-x", workers=2, repeat=2)
    assert len(made) == 2 and all(b.down and b.provider == "codex" and b.model == "gpt-x" for b in made)
    assert [r["id"] for r in results] == ["l", "h", "l", "h"] and [r["run"] for r in results] == [0, 0, 1, 1]
    r = results[0]
    assert r["recall"] == 0.667 and not r["ok"] and r["provider"] == "codex"
    assert r["tool_calls"] == 3 and r["turns"] == 4 and r["model"] == "m1"
    assert r["first_text_s"] is not None and r["first_text_s"] <= r["seconds"]
    assert results[1]["ok"] and results[1]["recall"] is None
    assert sorted(set(asked)) == [("bow?", "Hunter", 35), ("snails?", "Assassin", 31)]
    asked.clear()
    eval_answers.run_live(cases[:1], kb, "claude", profile={})
    assert asked == [("snails?", None, None)]          # --profile none: asked with no profile


def test_live_runner_survives_a_crash(kb, monkeypatch):
    from maplehelper import brain

    class Crashing:
        def __init__(self, *a, **k):
            pass

        def available(self):
            return True

        def ask(self, *a, **k):
            raise RuntimeError("boom")

        def shutdown(self):
            pass

    monkeypatch.setattr(brain, "Brain", Crashing)
    case = {"id": "x", "question": "q", "lang": "en", "kind": "stats", "checks": {"must_mention": ["y"]}}
    [r] = eval_answers.run_live([case], kb)
    assert not r["ok"] and r["problems"] == ["error: RuntimeError: boom"] and r["tool_calls"] is None


def _r(cid, ok, secs, tools=None, recall=None, problems=None):
    return {"id": cid, "lang": "en", "kind": "list", "ok": ok, "seconds": secs, "tool_calls": tools,
            "recall": recall, "problems": problems if problems is not None else ([] if ok else ["missing 'x'"])}


def test_summary_numbers_and_worst_cases():
    results = [_r(f"c{i}", True, float(i), tools=i) for i in range(1, 11)]
    results += [_r("bad", False, 50.0, 9, 0.5), _r("err", False, 1.0, problems=["error: timeout"])]
    s = eval_answers.summarize(results)
    assert s["passed"] == 10 and s["runs"] == 12 and s["pass_rate"] == 0.833
    assert s["mean_recall"] == 0.5 and s["errors"] == 1
    assert s["median_s"] == 5.5 and s["p90_s"] == 10.0 and s["median_tool_calls"] == 6
    assert [w["id"] for w in s["worst"]][:2] == ["bad", "err"] and len(s["worst"]) == 5


def test_repeats_fold_into_one_row_per_case():
    rows = eval_answers.per_case([_r("a", True, 2.0, 1, 1.0), _r("a", False, 4.0, 3, 0.5),
                                  _r("a", True, 9.0, 5, 1.0)])
    a = rows["a"]
    assert a["runs"] == 3 and a["pass"] == 0.667 and a["recall"] == 0.833
    assert a["seconds"] == 4.0 and a["tool_calls"] == 3 and a["problems"] == ["missing 'x'"]


def test_reports_are_per_provider(tmp_path):
    p1 = eval_answers.save_report([_r("a", True, 1.0)], None, folder=tmp_path, provider="claude")
    p2 = eval_answers.save_report([_r("a", False, 1.0)], None, folder=tmp_path, provider="codex")
    p3 = eval_answers.save_report([_r("a", True, 2.0)], None, folder=tmp_path, provider="claude")
    assert len({p1, p2, p3}) == 3 and p2.name.endswith("-codex.json")
    assert eval_answers.report_provider(p2) == "codex"
    assert eval_answers.report_provider(Path("20000101-000000.json")) == "claude"      # the older names
    prev = eval_answers.previous_report(tmp_path, before=p3, provider="claude")
    assert prev["provider"] == "claude" and prev["results"][0]["seconds"] == 1.0
    assert eval_answers.previous_report(tmp_path, before=p3, provider="codex")["results"][0]["ok"] is False
    data = json.loads(p3.read_text(encoding="utf-8"))
    assert data["summary"]["median_s"] == 2.0 and data["total"] == 1


def test_compare_mode_shows_per_case_deltas(tmp_path, capsys):
    before = {"results": [_r("a", True, 10.0, 8, 1.0), _r("b", False, 30.0, 20, 0.5), _r("c", True, 5.0, 2),
                          _r("old", True, 1.0)]}
    after = {"results": [_r("a", True, 4.0, 2, 1.0), _r("b", True, 12.0, 6, 1.0), _r("c", False, 6.0, 3),
                         _r("new", True, 1.0)]}
    cmp = eval_answers.compare_reports(before, after)
    rows = {r["id"]: r for r in cmp["rows"]}
    assert set(rows) == {"a", "b", "c"} and cmp["only_before"] == ["old"] and cmp["only_after"] == ["new"]
    assert rows["a"]["d_seconds"] == -6.0 and rows["a"]["d_tool_calls"] == -6 and not rows["a"]["regressed"]
    assert rows["b"]["fixed"] and rows["b"]["recall"] == (0.5, 1.0)
    assert rows["c"]["regressed"]
    assert cmp["before"]["passed"] == 2 and cmp["after"]["passed"] == 2
    # a recall drop is a regression even when neither run passed
    worse = eval_answers.compare_reports({"results": [_r("l", False, 1.0, recall=0.8)]},
                                         {"results": [_r("l", False, 1.0, recall=0.4)]})
    assert worse["rows"][0]["regressed"]

    a, b = tmp_path / "A.json", tmp_path / "B.json"
    a.write_text(json.dumps(before), encoding="utf-8")
    b.write_text(json.dumps(after), encoding="utf-8")
    assert eval_answers.main(["--compare", str(a), str(b)]) == 1       # c regressed
    out = capsys.readouterr().out
    assert "REGRESSED" in out and "fixed" in out and "-6" in out
    b.write_text(json.dumps(before), encoding="utf-8")
    assert eval_answers.main(["--compare", str(a), str(b)]) == 0


def test_signed_in_providers_reads_each_account(monkeypatch):
    from maplehelper import providers

    states = {"claude": "ok", "codex": "logged_out", "gemini": "not_installed", "grok": "ok"}
    for name, p in providers.PROVIDERS.items():
        def account(name=name):
            if name == "gemini":
                raise OSError("broken")
            return {"status": states[name]}
        monkeypatch.setattr(p, "account", account)
    assert eval_answers.signed_in_providers() == ["claude", "grok"]


def test_live_mode_refuses_any_provider_under_pytest(capsys):
    fixture_kb = Path(__file__).parent / "fixtures" / "kb"
    assert eval_answers.main(["--provider", "all-signed-in", "--yes", "--kb", str(fixture_kb)]) == 2
    assert "refusing" in capsys.readouterr().out


def test_unknown_case_filter_fails_before_anything_runs(capsys):
    fixture_kb = Path(__file__).parent / "fixtures" / "kb"
    assert eval_answers.main(["--cases", "no-such-case", "--kb", str(fixture_kb)]) == 2
    assert "no-such-case" in capsys.readouterr().out


# ------------------------------------------------------------------ the backends' tool-call counts

def test_claude_format_streams_count_each_tool_once():
    from maplehelper.providers.base import note_tool_use
    ids: set = set()
    start = {"type": "stream_event", "event": {"type": "content_block_start",
                                               "content_block": {"type": "tool_use", "id": "t1", "name": "Grep"}}}
    whole = {"type": "assistant", "message": {"content": [{"type": "text", "text": "x"},
                                                          {"type": "tool_use", "id": "t1"},
                                                          {"type": "tool_use", "id": "t2"}]}}
    assert note_tool_use(start, ids) and note_tool_use(whole, ids) and ids == {"t1", "t2"}
    assert not note_tool_use({"type": "result"}, ids)


def test_grok_stream_reports_tools_and_turns():
    from maplehelper.providers import grok
    lines = [json.dumps(e) + "\n" for e in (
        {"type": "stream_event", "event": {"type": "message_start", "message": {}}},
        {"type": "stream_event", "event": {"type": "content_block_start",
                                           "content_block": {"type": "tool_use", "id": "a"}}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a"}]}},
        {"type": "stream_event", "event": {"type": "content_block_delta",
                                           "delta": {"type": "text_delta", "text": "Hunt"}}},
        {"type": "result", "subtype": "success", "num_turns": 2})]
    stats: dict = {}
    text, result, _ = grok.parse_stream(lines, None, stats)
    assert text == "Hunt" and stats == {"tool_calls": 1, "turns": 2}
    stats = {}
    grok.parse_stream([json.dumps({"type": "result", "subtype": "success"})], None, stats)
    assert stats == {"tool_calls": None, "turns": None}         # no content blocks: the stream can't tell


def test_codex_events_count_commands():
    from maplehelper.providers import codex
    lines = [json.dumps(e) for e in (
        {"type": "item.started", "item": {"id": "i1", "type": "command_execution"}},
        {"type": "item.completed", "item": {"id": "i1", "type": "command_execution"}},
        {"type": "item.completed", "item": {"id": "i2", "type": "command_execution"}},
        {"type": "item.completed", "item": {"id": "i3", "type": "reasoning"}},
        {"type": "item.completed", "item": {"id": "i4", "type": "agent_message", "text": "Hunt"}},
        {"type": "turn.completed"})]
    r = codex.parse_events(lines)
    assert r.text == "Hunt" and r.tool_calls == 2 and r.turns is None


def test_gemini_events_count_tool_steps():
    from maplehelper.providers import gemini

    def step(**kw):
        return json.dumps({"event": "step_update", "step_update": kw})
    lines = [step(step_type="tool", state="ACTIVE"), step(step_type="tool", state="ACTIVE"),
             step(step_type="tool", state="DONE"), step(step_type="tool", state="ACTIVE"),
             step(step_type="tool", state="DONE"), step(step_type="agent_response", text_delta="Hunt"),
             json.dumps({"event": "result", "result": {"status": "SUCCESS"}})]
    stats: dict = {}
    text, *_ = gemini.parse_events(lines, None, stats)
    assert text == "Hunt" and stats == {"tool_calls": 2}


def test_brain_passes_the_counts_on(kb_copy):
    from maplehelper.brain import Brain
    from maplehelper.kb import KnowledgeBase
    from maplehelper.providers.base import RawResult

    class Backend:
        exe = "fake"

        def run(self, *a, **k):
            return RawResult(text="Hunt Red Snail.\n@@META@@\n{}", tool_calls=5, turns=6)

        def prewarm(self):
            pass

    b = Brain(KnowledgeBase(kb_copy))
    b.backend = Backend()
    a = b.ask("where to hunt?", None, None, None)
    assert (a.tool_calls, a.turns) == (5, 6)
