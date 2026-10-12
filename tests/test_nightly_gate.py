"""tools/nightly_gate.py: which test results stop the nightly KB and which only go to the kb-drift issue."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import nightly_gate  # noqa: E402


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """nightly_gate with pytest faked: returns (run, report) — run(exit code, failed tests, still failing on release)."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(nightly_gate, "LASTFAILED", tmp_path / "lastfailed")
    report = tmp_path / "report.json"

    def run(rc, failed=(), on_release=()):
        monkeypatch.setattr(nightly_gate, "pytest", lambda *a: rc)
        monkeypatch.setattr(nightly_gate, "last_failed", lambda: sorted(failed))
        monkeypatch.setattr(nightly_gate, "classify",
                            lambda f: ([t for t in f if t not in on_release], [t for t in f if t in on_release]))
        code = nightly_gate.main(["--report", str(report), "--max-failures", "3"])
        return code, json.loads(report.read_text(encoding="utf-8"))
    return run


def test_all_green_publishes(gate):
    assert gate(0) == (0, {"verdict": "pass", "drift": [], "known": []})


def test_a_few_pinned_values_the_site_changed_publish_and_are_listed(gate):
    code, rep = gate(1, ["t::a", "t::b"], on_release=["t::b"])
    assert code == 0 and rep["drift"] == ["t::a"] and rep["known"] == ["t::b"]


def test_many_failures_look_like_a_broken_scrape_and_stop_the_night(gate):
    code, rep = gate(1, ["t::a", "t::b", "t::c", "t::d"])
    assert code == 1 and rep["verdict"].startswith("blocked")


@pytest.mark.parametrize("rc", [2, 3, 4, 5])
def test_a_run_that_is_not_test_failures_stops_the_night(gate, rc):
    assert gate(rc)[0] == 1


def test_a_failed_run_with_no_failed_test_recorded_stops_the_night(gate):
    assert gate(1, [])[0] == 1
