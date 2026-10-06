"""Performance fixes from the launch audit: stale tables are flagged before a release and a question doesn't wait
long for them, the Town tab's map lookups are memoized, the first question's caches are warmed in the background,
settings.json isn't rewritten for an unchanged model, and the picture zoom stops polling while hidden."""
import os
import sys
from pathlib import Path

import pytest

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
