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


def test_the_issue_lists_every_os_drift_once(tmp_path):
    for os_name, drift in (("windows", ["t::a"]), ("macos", ["t::a", "t::b"])):
        (tmp_path / f"gate-report-{os_name}.json").write_text(json.dumps(
            {"verdict": "pass with data changes to review", "drift": drift, "known": []}), encoding="utf-8")
    body = nightly_gate.issue_body(str(tmp_path), "success")
    assert body.count("- [ ] `t::a`") == 1 and "- [ ] `t::b`" in body


def test_no_issue_on_a_clean_night_but_one_when_the_tests_never_reported(tmp_path):
    (tmp_path / "gate-report-windows.json").write_text(json.dumps({"verdict": "pass", "drift": [], "known": []}),
                                                       encoding="utf-8")
    assert nightly_gate.issue_body(str(tmp_path), "success") is None
    assert "No gate report" in nightly_gate.issue_body(str(tmp_path / "missing"), "failure")
