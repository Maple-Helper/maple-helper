"""Performance fixes from the launch audit: stale tables are flagged before a release and a question doesn't wait
long for them, the Town tab's map lookups are memoized, the first question's caches are warmed in the background,
settings.json isn't rewritten for an unchanged model, and the picture zoom stops polling while hidden."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


# --- PRF-5: stale tables -------------------------------------------------------------------------------------------

def test_a_question_waits_only_briefly_for_a_tables_build():
    from maplehelper import tables
    assert tables.ASK_WAIT <= 5


def test_the_release_warns_when_the_kb_tables_are_stale(kb_copy, capsys):
    import kb_release
    from maplehelper import tables
    (kb_copy / tables.MARK_FILE).write_text("tables 0\n", encoding="utf-8")       # an older app's mark
    assert kb_release.main(["tables-check", str(kb_copy)]) == 0                   # a warning, never a failed release
    assert "::warning::" in capsys.readouterr().out
    assert kb_release.refresh_tables(kb_copy)
    assert kb_release.main(["tables-check", str(kb_copy)]) == 0
    assert "KB tables: current" in capsys.readouterr().out


# --- PRF-6: the Town tab's map lookups ------------------------------------------------------------------------------

def test_a_map_lookup_by_text_is_remembered(kb, monkeypatch):
    from maplehelper import routes
    g = routes.Graph(kb)
    calls = []
    real = type(kb).find_mentions

    def counting(self, text, *a, **k):
        calls.append(text)
        return real(self, text, *a, **k)
    monkeypatch.setattr(type(kb), "find_mentions", counting)
    first = g.find("somewhere not a map name")
    n = len(calls)
    assert g.find("somewhere not a map name") == first and len(calls) == n       # the mention matcher ran once
    assert n == 1 and g.find("  ") is None


# --- PRF-7: the first question's caches are built in the background ---------------------------------------------------

def test_the_tables_thread_then_warms_what_the_first_question_builds(kb_copy):
    from maplehelper import tables
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(kb_copy)
    assert "_hebrew_words" not in kb.__dict__
    tables.ensure_async(kb, then=kb.warm).join(timeout=120)
    assert tables.current(kb_copy)
    assert {"_question_names", "_hebrew_words"} <= set(kb.__dict__)      # the mention matcher's lazy name lists


def test_a_failing_warm_up_never_takes_the_app_down(kb_copy):
    from maplehelper import tables
    from maplehelper.kb import KnowledgeBase

    def boom():
        raise RuntimeError("x")
    th = tables.ensure_async(KnowledgeBase(kb_copy), then=boom)
    th.join(timeout=120)
    assert not th.is_alive()


# --- PRF-9: an unchanged model isn't written again -------------------------------------------------------------------

from test_chat_audit import overlay as chat  # noqa: E402,F401 - the chat window, offscreen


def test_an_answer_from_the_same_model_doesnt_rewrite_settings(chat, monkeypatch):  # noqa: F811 - the fixture
    from maplehelper.brain import Answer
    ov = chat
    saves = []
    real = type(ov.settings).save
    monkeypatch.setattr(type(ov.settings), "save", lambda self: (saves.append(1), real(self)))
    ov.settings["provider"] = "claude"
    saves.clear()
    ov._on_done(Answer(text="one", model="claude-sonnet-5"), None)
    assert ov.settings["last_model"]["claude"] == "claude-sonnet-5" and len(saves) == 1
    ov._on_done(Answer(text="two", model="claude-sonnet-5"), None)
    assert len(saves) == 1                                                    # nothing changed: not written again
    ov._on_done(Answer(text="three", model="claude-opus-5"), None)
    assert ov.settings["last_model"]["claude"] == "claude-opus-5" and len(saves) == 2
