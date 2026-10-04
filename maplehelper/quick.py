"""Instant answers straight from the knowledge base, for simple factual questions.

"How much HP does Blue Snail have?", "What does Mano drop?", "Who drops Snail Shell?",
"Where is Red Snail?" are answered in a blink, without Claude (faster, and it saves the
player's plan usage). Anything else, or anything ambiguous, goes to Claude as before,
and every instant answer offers "Ask Claude anyway".
"""
from __future__ import annotations

import re

from . import availability, combat, market, sources
from .brain import Answer
from .kb import KnowledgeBase, _norm, fold_quotes

HE = "֐-׿"


def _he(words: str, the: bool = False) -> str:
    """Whole Hebrew words: not a letter on either side (so "מי" doesn't match inside another word).
    the=True also accepts the definite article glued on: "מה הלבל של..." is the usual way to ask."""
    return rf"(?<![{HE}]){'ה?' if the else ''}({words})(?![{HE}])"


# questions that need judgement, the screenshot or the player's situation: always Claude
# ("how much / how many" is a plain number question, not a "how do I")
NEEDS_CLAUDE = re.compile(
    r"\b(why|how(?!\s+(?:much|many)\b)|should|best|better|worth|recommend|my|me|i|here|this|that)\b|"
    + _he("למה|איך|כדאי|הכי|עדיף|שווה|מומלץ|שלי|אני|פה|כאן|הזה|הזאת|זה|במסך|תמליץ|לי"), re.I)
DROPS = re.compile(r"\b(drops?|loot)\b|(מפיל|מפילה|מפילים|דרופ|דרופים|נופל)", re.I)
WHO = re.compile(r"\b(who|which (monster|mob)s?)\b|" + _he("מי|מאיפה") + "|איזה מפלצ|איפה משיגים", re.I)
WHERE = re.compile(r"\b(where|location|spawn)\b|(איפה|באיזו מפה|באיזה מפה|מיקום)", re.I)
# an item's shop: "who sells red potion", "where to buy", "מי מוכר", "איפה קונים", "כמה עולה"
SELLS = re.compile(r"\b(sells?|sold|buy|price|cost)\b|" + _he("מוכר|מוכרים|קונים|לקנות|עולה|מחיר"), re.I)
# the ACC a player needs ("accuracy needed for lupin", "כמה דיוק צריך לחילזון"): from the player's level, not the
# monster's own Accuracy stat
ACC_NEEDED = re.compile(r"\b(acc|accuracy)\b.*\b(need|needed|required|to hit)\b|\b(need|needed|required)\b.*\b(acc|accuracy)\b|"
                        r"(צריך|צריכים|נדרש|דרוש|כדי לפגוע|כדי להכות).*(דיוק|acc|אקיורסי)|"
                        r"(דיוק|acc|אקיורסי).*(צריך|צריכים|נדרש|דרוש|לפגוע|להכות)", re.I)
# a level the question names ("acc needed for lupin at level 25", "כמה דיוק צריך ללופין בלבל 25"): the ACC is for it
ASKED_LEVEL = re.compile(r"\b(?:level|lvl|lv)\.?\s*(\d{1,3})\b|" + rf"(?<![{HE}])[בל]?(?:לבל|רמה)\s*-?\s*(\d{{1,3}})(?!\d)", re.I)
STATS = [  # (pattern, props key, label); Hebrew as whole words: "לבלו סנייל" (Blue Snail) is not "לבל"
    (re.compile(r"\bhp\b|" + _he("חיים|אייץ' פי", the=True), re.I), "HP", "HP"),
    (re.compile(r"\bmp\b|" + _he("מאנה|מנה", the=True), re.I), "MP", "MP"),
    (re.compile(r"\bexp\b|\bxp\b|" + _he("אקספי|נסיון|ניסיון", the=True), re.I), "EXP", "EXP"),
    (re.compile(r"\blevel\b|\blv\b|" + _he("לבל|רמה", the=True), re.I), "Level", "Level"),
    # the knowledge base has no plain "Defense": monsters carry "Physical Defense" and "Magic Defense"
    (re.compile(r"(?<!magic )(?<!\bm\.)(?<!\bm )\b(?:p\.?\s?|physical\s)?def(?:ense|ence)?\b|"
                rf"(?<![{HE}])ה?(הגנה)(?![{HE}])(?!\s+(?:מ?קסם|מגית))", re.I), "Physical Defense", "P.DEF"),
    (re.compile(r"\bm\.?\s?def\b|\bmagic\s+def(?:ense|ence)?\b|" + _he("הגנת קסם|הגנה מקסם|הגנה מגית", the=True), re.I),
     "Magic Defense", "M.DEF"),
    (re.compile(r"\bavoid(?:ability)?\b|" + _he("התחמקות|אבויד|אוויד", the=True), re.I), "Avoidability", "Avoid"),
    (re.compile(r"\bacc(uracy)?\b|" + _he("דיוק", the=True), re.I), "Accuracy", "Accuracy"),
    (re.compile(r"\b(att|attack|damage)\b|" + _he("נזק|התקפה", the=True), re.I), "Physical Damage", "Damage"),
]
MAX_WORDS = 9
# the words a short question puts right before a name; anything else ("Jr Boogie", "Mossy Snail", "Ghost Stump")
# may be part of a name the knowledge base doesn't have, so the matched shorter name isn't a sure answer
QUESTION_WORDS = set("""
what whats what's where's wheres who's whos it's its is are was the a an of does do did much many where who which can
could find found to for from in at
on by about drop drops dropped loot loots hp mp exp xp level lv lvl def defense defence acc accuracy avoid avoidability
att attack damage has have give gives spawn spawns location located monster mob monsters mobs sell sells sold buy
price cost needed need required and with get gets kill m p magic physical
כמה יש מה של איפה מי מפיל מפילה מפילים נופל נופלים דרופ דרופים באיזה באיזו מפה לבל רמה חיים אקספי ניסיון נסיון
נותן נותנת נותנים עושה הגנה הגנת דיוק נזק התקפה מאנה מנה מיקום נמצא נמצאת נמצאים צריך צריכים בשביל את עם על
אייץ' פי אמ קסם התחמקות מוכר מוכרים קונים לקנות עולה מחיר משיגים מאיפה הוא היא ומה ואיפה
""".split())


def _known_word(w: str) -> bool:
    """A question word, also with a glued Hebrew prefix or article ("והלבל", "בשביל"), or a prefix written apart
    ("כמה HP יש ל-Snail")."""
    return w in QUESTION_WORDS or w.strip("ובלמהשכ") == "" or any(w[i:] in QUESTION_WORDS for i in (1, 2) if len(w) > i and w[:i].strip("ובלמהשכ") == "")


def _maps(kb: KnowledgeBase, key: str, n: int = 3) -> list[str]:
    """Where a monster lives: maps a player can reach now (no El Nath before it opens, no PQ stage), labelled."""
    return [kb.map_label(m) for m in kb._top_maps(key, 12) if combat.reachable_map(kb, m)][:n]


def drops_note(t, srcs) -> str:
    """The line under a drop answer: which list the drops are from (the MSEA reference list, players' Classic
    sightings, or both)."""
    kinds = set(srcs)
    if kinds == {sources.COMMUNITY}:
        return t("quick_drops_note_community")
    return t("quick_drops_note_both") if sources.COMMUNITY in kinds else t("quick_drops_note")


def answer(question: str, kb: KnowledgeBase, t, char=None) -> Answer | None:
    """An Answer from the KB alone, or None when Claude should answer. char: the active character (its level
    for "how much ACC do I need" questions), or None."""
    q = fold_quotes(question.strip())
    if not q or len(q.split()) > MAX_WORDS or NEEDS_CLAUDE.search(q):
        return None
    spans = kb.mention_spans(q, max_results=3)
    if len(spans) != 1:
        return None          # nothing named, or several things: a judgement call
    key, w0, _ = spans[0]
    words = _norm(q).split()
    if w0 > 0 and not _known_word(words[w0 - 1]):
        return None          # "Jr Boogie hp": an unknown word glued before the name, maybe a longer name
    e = kb.get(key) or {}
    cat, name = e.get("category"), e.get("name", key)

    if cat == "item" and SELLS.search(q) and not DROPS.search(q):
        prices = market.npc_prices(kb, key)
        shops = [s for s in prices.shops if combat.released(kb, s[1])]
        if not shops:
            return None
        # a Town Hall shop's price is for a citizen grade and up: said, or a player without it is told to buy there
        # and a price the KB labels as the COT2 test's is said to be one, not given as the launch price
        lines = [f"• {npc} · {where} · {price:,} mesos"
                 + (" " + t("price_rank", rank=prices.ranks[(npc, where)]) if (npc, where) in prices.ranks else "")
                 + (" " + sources.price_note(t, prices.labels[(npc, where)]) if prices.test_price((npc, where)) else "")
                 for npc, where, price in shops[:3]]
        return Answer(text=t("quick_sells", name=name) + "\n" + "\n".join(lines), entities=[key],
                      sources=[prices.source(s) for s in shops[:3]])
    if cat == "item" and (WHO.search(q) or DROPS.search(q)):
        groups = kb.drop_groups([key], limit=6)
        if not groups:
            return None
        srcs = [s for g in groups for s in g["sources"].values()]
        return Answer(text=t("quick_who_drops", name=name, n=len(groups)) + "\n" + drops_note(t, srcs),
                      entities=[key], drop_groups=groups, sources=srcs)
    if cat != "monster":
        return None
    if not availability.of(kb).monster_key_open(key):
        # the KB doesn't confirm it in the game (Ossyria, no map at all): say so, never its stats as if it were
        # (what is out comes from the release guide's official statements and the map pages: availability.py)
        return Answer(text=t("quick_not_in_game", name=name), entities=[], sources=[sources.OFFICIAL])
    acc = bool(ACC_NEEDED.search(q))
    asks = [bool(DROPS.search(q) and not WHO.search(q)), bool(WHERE.search(q)),
            acc or any(rx.search(q) for rx, _, _ in STATS)]
    if sum(asks) > 1:
        return None          # "Mano's level and drops": answering only half would look like the whole answer
    if DROPS.search(q) and not WHO.search(q):
        lists = kb.drop_lists(key)
        drops = [k for ks in lists.values() for k in ks]
        if not drops:
            return None
        srcs = [s for s, ks in lists.items() if ks]
        text = t("quick_drops", name=name, n=len(drops)) + "\n" + drops_note(t, srcs)
        mesos = kb.community_mesos(key)
        if mesos:
            # the mesos players reported too ("מזו 18–23 (קהילה)"): part of what a monster drops
            text += "\n" + sources.mesos_line(t, mesos)
        return Answer(text=text, entities=[key] + drops, sources=srcs)
    if WHERE.search(q):
        maps = _maps(kb, key)
        if not maps:
            return None      # only unreachable maps (or none): Claude can explain
        return Answer(text=t("quick_where", name=name) + "\n" + "\n".join(f"• {m}" for m in maps), entities=[key],
                      sources=[sources.MEOWDB])
    if acc:
        m = combat.monster(kb, key)
        if not m:
            return None
        asked = ASKED_LEVEL.search(q)
        # the level the question names, else the character's, else (no character) the monster's own
        lv = int(next(g for g in asked.groups() if g)) if asked else int(getattr(char, "level", 0) or 0) or m.level
        if not 1 <= lv <= 250:
            return None
        # worked out from the monster's own level and Avoid: those carry its page's source
        if m.avoid <= 0:
            return Answer(text=t("quick_acc_none", name=name), entities=[key], sources=[sources.source_of(kb, key)])
        return Answer(text=t("quick_acc", name=name, lv=lv, n=combat.acc_needed(lv, m.level, m.avoid),
                             n90=combat.acc_needed(lv, m.level, m.avoid, 0.9)), entities=[key],
                      sources=[sources.source_of(kb, key)])
    props = e.get("props") or {}
    asked = [(k, label) for rx, k, label in STATS if rx.search(q) and props.get(k) not in (None, "")]
    if asked:
        return Answer(text="\n".join(f"{name} · {label}: {props[k]}" for k, label in asked), entities=[key],
                      sources=[sources.source_of(kb, key)])
    return None
