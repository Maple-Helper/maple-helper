"""Answers the player pinned, per character, so a quest route or a build stays one tap away."""
from __future__ import annotations

import re
import time

MAX_PINS = 12
_FOCUS_TAG = re.compile(r"^\s*\[about [^\]]*\]\s*")


def shown_question(question: str) -> str:
    """A stored question as the player asked it: the history keeps "[about Mano] what does it drop?" (the card the
    question was about, for the AI), the player only wrote "what does it drop?"."""
    return _FOCUS_TAG.sub("", question or "", count=1) or question or ""


def items(settings, cid: str | None) -> list[dict]:
    return list((settings["pins"] or {}).get(cid or "", []))


def add(settings, cid: str | None, question: str, answer: str, now: float | None = None) -> bool:
    if not cid or not answer.strip():
        return False
    data = dict(settings["pins"] or {})
    pins = [p for p in data.get(cid, []) if p.get("a") != answer]
    pins.insert(0, {"q": question, "a": answer, "t": now if now is not None else time.time()})
    data[cid] = pins[:MAX_PINS]
    settings["pins"] = data
    return True


def remove(settings, cid: str | None, answer: str) -> None:
    if not cid:          # as add: no character, nothing pinned (it wrote a None key into settings, audit AI-25)
        return
    data = dict(settings["pins"] or {})
    data[cid] = [p for p in data.get(cid or "", []) if p.get("a") != answer]
    settings["pins"] = data


def conversations(records: list[dict]) -> list[dict]:
    """History records -> [{q, a, t}] question/answer pairs, oldest first. Two answers in a row answer the same
    question (an instant answer, then "Ask Claude anyway")."""
    out, q = [], None
    for r in records:
        if r.get("role") == "user":
            q = r
        elif r.get("role") == "assistant" and q is not None:
            out.append({"q": q.get("text", ""), "a": r.get("text", ""), "t": r.get("t", q.get("t", 0)),
                        "entities": r.get("entities") or []})
    return out


def search(pairs: list[dict], query: str) -> list[dict]:
    """Newest first; every word of the query must appear in the question or the answer, as the player sees them:
    not the stored "[about Mano]" tag (an "about" search matched every question asked from a card) nor the
    answer's "**" bold marks."""
    words = [w for w in query.lower().split() if w]
    hits = [p for p in pairs
            if all(w in (shown_question(p["q"]) + " " + p["a"].replace("**", "")).lower() for w in words)]
    return list(reversed(hits))
