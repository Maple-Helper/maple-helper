"""Official game facts (assets/official/facts.json), stated by the app's owner.

They outrank the knowledge base and every other source: the AI is told so, and code that holds the same value
(jobs.JOBS) is checked against them in tests/test_official.py. The owner changes them with no app release: the file
comes live (live.py), and a live copy that doesn't read falls back to the shipped one.
"""
from __future__ import annotations

import json
import logging

from . import live

log = logging.getLogger(__name__)
REL = "official/facts.json"
PATH = live.bundled(REL)
_cache: tuple[int, tuple[dict, ...]] | None = None


def _read(path) -> tuple[dict, ...] | None:
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("facts", [])
    except (OSError, ValueError, AttributeError) as e:
        log.warning("official facts unreadable (%s): %s", path, e)
        return None
    return tuple(f for f in rows if isinstance(f, dict)) if isinstance(rows, list) else None


def facts() -> tuple[dict, ...]:
    global _cache
    if _cache and _cache[0] == live.generation():
        return _cache[1]
    got = _read(live.asset(REL))
    if got is None:
        got = _read(PATH) or ()
    _cache = (live.generation(), got)
    return got



def prompt_note() -> str:
    """The system prompt's block (English: the prompt's language; the AI answers in the player's)."""
    lines = [f"- {f['en']}" for f in facts() if f.get("en")]
    if not lines:
        return ""
    return ("\n\nOfficial game facts (from the game itself; they override the knowledge base and every other "
            "source wherever they disagree, and need no tool lookup):\n" + "\n".join(lines))


def value(field: str):
    """A structured value from the first fact that carries it (e.g. "first_job_level"), else None."""
    return next((f[field] for f in facts() if field in f), None)
