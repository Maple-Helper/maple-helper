"""The test suite's own guards (tests/conftest.py): CI fails instead of skipping a real-KB test, no network, no
machine settings leaking into the results."""
import ast
import socket
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent


def test_every_real_kb_skip_says_knowledge_base():
    """conftest turns a skip into a failure under MAPLEHELPER_REQUIRE_REAL_KB only when its reason names the
    knowledge base: "no routes.json in data/kb" slipped through, and every route test skipped green."""
    quiet = []
    for path in sorted(TESTS.glob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "skipif" and node.args):
                continue
            cond = ast.get_source_segment(src, node.args[0]) or ""
            if "REAL_KB" not in cond and "BUNDLED_KB" not in cond:
                continue
            reason = next((k.value for k in node.keywords if k.arg == "reason"), None)
            text = reason.value if isinstance(reason, ast.Constant) else ""
            if "knowledge base" not in str(text):
                quiet.append(f"{path.name}:{node.lineno} {text!r}")
    assert quiet == []


def test_no_name_lookup_leaves_the_machine():
    with pytest.raises(OSError):
        socket.getaddrinfo("meowdb.com", 443)
    assert socket.getaddrinfo("127.0.0.1", 80)          # loopback and plain addresses still work
    assert socket.getaddrinfo("localhost", 80)


def test_windows_high_contrast_stays_out_of_the_tests():
    from maplehelper.ui import theme
    theme.set_mode("light")
    try:
        assert theme.high_contrast() is None and theme.MODE == "light"
    finally:
        theme.set_mode("dark")


def test_no_lost_line_continuation_in_the_code():
    """A backslash + newline lost in an edit leaves "and             name..." mid-line (quests.py, review CORE-8)."""
    import re
    code = TESTS.parent / "maplehelper"
    lost = [f"{p.relative_to(code)}:{n}" for p in sorted(code.rglob("*.py"))
            for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"\b(?:and|or|else|not|if|in)\s{8,}[^\s#]", ln.split("#", 1)[0])]
    assert lost == []
