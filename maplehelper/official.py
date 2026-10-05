"""Official game facts (assets/official/facts.json), stated by the app's owner.

They outrank the knowledge base and every other source: the AI is told so, and code that holds the same value
(jobs.JOBS) is checked against them in tests/test_official.py.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

from .store import ASSETS

log = logging.getLogger(__name__)
PATH = ASSETS / "official" / "facts.json"


@lru_cache(maxsize=1)
def facts() -> tuple[dict, ...]:
    try:
        return tuple(json.loads(PATH.read_text(encoding="utf-8")).get("facts", []))
    except (OSError, ValueError) as e:
        log.warning("official facts unreadable: %s", e)
        return ()


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
