"""Asks the player's chosen AI (Claude Code, Codex, Antigravity or Grok Build, see providers/) and turns its reply into an Answer.

The prompt, the knowledge-base pre-fetch and the answer post-processing (cards,
drop groups, profile updates) live here and are the same for every provider;
the provider's backend only runs the CLI and returns the raw text.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

from . import availability, live, mesowatch, news, official, planner, providers, routes, sitedata, sources, tables
from . import recent as kb_changes      # ("recent" is the conversation in build_prompt)
from .kb import KnowledgeBase, _norm
from .store import Character, History

log = logging.getLogger(__name__)

META = "@@META@@"
_FOCUS_TAG = re.compile(r"^\s*\[about [^\]]*\]\s*")      # "[about Mano] " the chat puts before a tagged question
REVERSE_WORDS = re.compile(r"(מאיז[הו]|מאילו|איזה|אילו)\s+מפלצ|מי\s+מפיל|which\s+monsters?|who\s+drops|what\s+drops", re.I)
# "נופל" alone is also falling ("למה אני נופל מהחבל ליד Mano" showed Mano's whole drop list): only as "what drops"
# questions the list pages answer (sitedata.py): pets, which job is strongest, a build's skills
PET_WORDS = re.compile(r"(?<![א-ת])(?:ה|ל|ב)?(?:חיית|חיות|חיה|פט|פטים)(?![א-ת])|\bpets?\b", re.I)
TIER_WORDS = re.compile(r"טייר|דירוג|(?:איזה|איזו)\s+(?:ג'וב|מקצוע|קלאס)|ג'וב\s+הכי|\btier|\bbest\s+(?:class|job)|"
                        r"\bwhich\s+(?:class|job)|\bstrongest\s+(?:class|job)", re.I)
BUILD_WORDS = re.compile(r"סקיל|בילד|(?<![A-Za-z])SP(?![A-Za-z])|\bskills?\b|\bbuild\b", re.I)
# "tell me about Blue Snail": the monster's card and every drop as tiles, as for a drops question. The answer no longer
# repeats the cards, so it names one drop at most, and the tiles showed only that one (the owner's report). Never a
# bare "about": "what about Lupin vs Ligator" buried Ligator under Lupin's 23 drops (audit AI-8)
DETAIL_WORDS = re.compile(r"פרטים|מידע|(?<![א-ת])(?:ספר|תספר|תגיד|ספרי)\s+לי|\b(?:details?|info|tell me)\b", re.I)
DROP_WORDS = re.compile(r"דרופ|מפיל|(?<![א-ת])(?:מה|איזה|אילו)\s+(?:\S+\s+){0,2}נופל|שנופל|drops?\b|loot", re.I)
# a comparison or a list ("Mano, Mushmom, King Slime and Jr. Balrog", "הרמיט או צ'יף בנדיט"): the pages of up to
# LIST_MENTIONS entities are pre-fetched, each cut shorter, so the prompt stays about as long as for MENTIONS pages.
# A list's commas (two: "hi, where is mano" is none) and a glued "and" after a word ("ויזארד אש" is no list)
LIST_WORDS = re.compile(r",[^,]*,|\b(?:vs|versus|or|and|compar\w*|between|differences?)\b|(?<![א-ת])(?:או|לעומת|מול|"
                        r"בין|השוו\w*|השוואה|ההבדל|הבדל)(?![א-ת])|(?<=\S\s)ו(?=[א-ת]{2})", re.I)
MENTIONS, LIST_MENTIONS = 4, 8
PAGE_CHARS = 2500          # a pre-fetched page, MENTIONS of them at most at full length
MIN_PAGE_CHARS = 800       # a page cut for a long list still keeps its head: level, HP, EXP, where
# The AI's instructions are content the owner changes with no app release (live.py): assets/ai/system.md (the system
# prompt, a str.format template: {length} is the answer length, literal braces are doubled, @@TABLES_NOTE@@ is where
# tables.prompt_note() goes), assets/ai/reply_rules.md (the rules repeated at the end of every question, {length} =
# the most lines) and assets/ai/rules.json (shorter rules: the HUD rule, the "not in the game" note, the summary
# prompt, the length lines). A live copy that doesn't fit the format falls back to the shipped one. A release that
# changes what the app parses from an answer (the META block) bumps live.CONTRACTS["ai"], so older apps keep theirs.
# Why some rules are as they are: HUD_RULE, the HUD is what the player is now (Claude answered "Lv. 31" from the saved
# profile with "LV. 13 ... KalimeroZ" on screen, while Codex and Gemini read the HUD); NOT_OUT, "if asked" let a
# stats question through ("how much EXP does Crimson Balrog give" got its EXP as a live boss's).


def _ai_text(path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")      # a Windows checkout has CRLF


def _load_ai(get) -> tuple[str, str, dict]:
    """(system template, reply rules, rules.json) from get(relpath) -> Path; raises when one doesn't fit."""
    system = _ai_text(get("ai/system.md")).rstrip("\n") + "\n"
    reply = _ai_text(get("ai/reply_rules.md")).rstrip("\n")
    rules = json.loads(_ai_text(get("ai/rules.json")))
    note = tables.prompt_note().replace("{", "{{").replace("}", "}}")
    if "@@TABLES_NOTE@@" not in system or META not in system or META not in reply:
        raise ValueError("a marker is missing")
    system = system.replace("@@TABLES_NOTE@@", note)
    length = rules["length"]
    lines = rules["length_lines"]
    for k in ("short", "detailed"):
        if not (isinstance(length.get(k), str) and isinstance(lines.get(k), int)):
            raise ValueError(f"length {k} is missing")
        system.format(length=length[k])
        reply.format(length=lines[k])
    for k in ("hud_rule", "not_out", "summary"):
        if not (isinstance(rules.get(k), str) and rules[k].strip()):
            raise ValueError(f"{k} is missing")
    return system, reply, rules


def load_prompts() -> None:
    """Read the AI's instructions again (at import, and when live.py brings new ones)."""
    global SYSTEM_PROMPT, REPLY_RULES, HUD_RULE, NOT_OUT, SUMMARY_PROMPT, LENGTH, LENGTH_LINES
    try:
        system, reply, rules = _load_ai(live.asset)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as e:
        log.warning("the live AI instructions don't fit (%s): the shipped ones are used", e)
        system, reply, rules = _load_ai(live.bundled)
    SYSTEM_PROMPT, REPLY_RULES = system, reply
    HUD_RULE, NOT_OUT, SUMMARY_PROMPT = rules["hud_rule"], rules["not_out"], rules["summary"]
    LENGTH = {k: rules["length"][k] for k in ("short", "detailed")}
    LENGTH_LINES = {k: rules["length_lines"][k] for k in ("short", "detailed")}


SYSTEM_PROMPT = REPLY_RULES = HUD_RULE = NOT_OUT = SUMMARY_PROMPT = ""
LENGTH: dict[str, str] = {}
LENGTH_LINES: dict[str, int] = {}
load_prompts()


@dataclass
class Answer:
    text: str = ""
    entities: list[str] = field(default_factory=list)
    profile_update: dict = field(default_factory=dict)
    avatar_box: list | None = None
    drop_groups: list = field(default_factory=list)
    grind: dict = field(default_factory=dict)       # a grind tracker read: map, monster, mesos, potions (grind.py)
    # where an instant answer's data comes from (sources.py: "COT2", "MSEA", "community", "MeowDB", ...): chips
    # beside its badge
    sources: list = field(default_factory=list)
    error: str | None = None
    cost_usd: float | None = None
    limits: dict | None = None          # Claude plan usage (usage.parse of Claude Code's rate_limit_event)
    model: str | None = None            # the model that answered, when the CLI says
    tool_calls: int | None = None       # tools the AI ran for it, where its CLI tells (RawResult): evals only
    turns: int | None = None


_SECTION_END = ("Associated Quests", "Map Locations", "Respawn Timer", "Change history", "Similar monsters",
                "Similar items", "Dropped By")


def _cut(body: str, head: str) -> str:
    """The page without the section that starts at the line `head`, up to the next section."""
    m = re.search(rf"^{re.escape(head)}\b.*$", body, re.M)
    if not m:
        return body
    ends = [j for h in _SECTION_END if h != head and (j := body.find("\n" + h, m.end())) > 0]
    return body[:m.start()] + (body[min(ends) + 1:] if ends else "")


def without_superseded(kb: KnowledgeBase, key: str, body: str) -> str:
    """What the game's official data replaces, out of a page for the AI (the owner's rule, 2026-10-04: once an
    official value is out, only it and the community's data count): the test builds' change table once a page's
    values are from the released game."""
    stamp = sources.stat_source(kb, key)
    if stamp and not sources.test_build(stamp.source):
        body = _cut(body, "Change history")
    return body


def mention_cap(question: str) -> int:
    """How many entities a question's pre-fetch may name: more for a comparison or a list (a 4 cap left the fifth
    of "compare Mano, Mushmom, King Slime, Jr. Balrog and Crimson Balrog" for the AI to look up, ~20 s more)."""
    return LIST_MENTIONS if LIST_WORDS.search(question) else MENTIONS


def page_chars(n: int) -> int:
    """The length of each of n pre-fetched pages: PAGE_CHARS up to MENTIONS pages, then shorter, the same total."""
    return PAGE_CHARS if n <= MENTIONS else PAGE_CHARS * MENTIONS // n


def _page(kb: KnowledgeBase, key: str, limit: int) -> str:
    """A pre-fetched page, marked when the KB says it isn't in the game: the pages of Orbis, El Nath and the rest
    read like any town's ("El Nath is a town in El Nath, Ossyria"), and the AI sent players there."""
    body = kb.page_body(key, limit=limit)
    body = without_superseded(kb, key, body) if body else body
    if body and key.startswith("item/"):
        body = _mark_droppers(kb, body)
    if body:
        # what each kind of data on the page is (its build, drop list, ...), so the answer can name it
        note = sources.page_note(kb, key)
        body = f"{note}\n{body}" if note else body
    if body and not availability.of(kb).entity_open(key):
        return f"[{key}] ({NOT_OUT})\n{body}"
    return f"[{key}]\n{body}" if body else ""


def _site_context(kb: KnowledgeBase, question: str, character: Character | None, shown: list[str]) -> list[str]:
    """What NiaMeowDB's list pages add (sitedata.py): the build changes of the skills in the context (and of the
    player's job line when the question is about skills or a build), the pets when one is named or pets are asked
    about, and the community tier list when the question compares jobs."""
    out = []
    skills = [k for k in shown if k.startswith("skill/")]
    if character and getattr(character, "job", None) and BUILD_WORDS.search(question):
        skills += [c.key for c in sitedata.changes_for(kb, character.base_class, character.job)]
    lines = sitedata.ai_skill_lines(kb, skills)
    if lines:
        out.append("\n".join(lines))
    named = [k for k in shown if k.startswith("item/") and sitedata.pet(kb, k)]
    lines = sitedata.ai_pet_lines(kb, None if PET_WORDS.search(question) else named) if named or PET_WORDS.search(question) else []
    if lines:
        out.append("\n".join(lines))
    if TIER_WORDS.search(question):
        lines = sitedata.ai_tier_lines(kb)
    elif character and BUILD_WORDS.search(question):
        lines = sitedata.ai_tier_lines(kb, sitedata.tiers_for(kb, character.base_class, character.job))
    else:
        lines = []
    if lines:
        out.append("\n".join(lines))
    return out


def _mark_droppers(kb: KnowledgeBase, body: str) -> str:
    """An item page's "Dropped By" list ("Mano / Lv 20 / Trixter / Lv 25 / Jr. Sentinel / Lv 26") with the
    monsters the KB doesn't confirm in the game marked: the AI named Jr. Sentinel (Orbis) as a source, live."""
    i = body.find("Dropped By")
    if i < 0:
        return body
    open_ = availability.of(kb)
    lines = body[i:].split("\n")
    for n in range(len(lines) - 1):
        if re.fullmatch(r"Lv \d+", lines[n + 1].strip()):
            keys = kb.monster_keys(lines[n].strip())
            if keys and not any(open_.monster_key_open(k) for k in keys):
                lines[n] += " (not in the game)"
    return body[:i] + "\n".join(lines)


ENGLISH_WORDS = {"which", "what", "where", "who", "how", "why", "when", "whats", "what's", "is", "are", "does", "do",
                 "can", "should", "give", "gives", "sells", "sell", "drop", "drops", "best", "the", "for", "to", "in",
                 "of", "my", "me", "i",
                 # "who's Grendel", "can't find Mano", "2nd job warrior level?" went Hebrew (audit AI-17)
                 "who's", "whos", "where's", "wheres", "how's", "find", "stats", "level", "job", "vs", "with", "get",
                 "need", "can't", "cant", "thanks", "nd", "st", "rd", "th"}


def reply_language(question: str, ui_lang: str = "he", kb: KnowledgeBase | None = None) -> str:
    """The answer's language: the question's (Hebrew letters: Hebrew, Latin ones: English), else the app's.
    Hebrew in the context (earlier session summaries, profile notes) made an English player's answer Hebrew."""
    if re.search(r"[\u0590-\u05FF]", question):
        return "Hebrew"
    bare = _FOCUS_TAG.sub("", question)
    # a name alone ("SAUNA ROB") is no English sentence: the app's language (it answered a Hebrew player in English);
    # a short question with an English question or function word is one ("which quests reward scrolls?" got Hebrew)
    words = re.findall(r"[A-Za-z']+", bare)
    if kb is not None:
        # the words of the game names it holds don't count: "Valley of Death", "Return Scroll to Henesys" typed alone
        # are names (their "of", "to" made 256 KB names an English sentence); "where is Valley of Death" isn't
        spans = kb.mention_spans(bare, LIST_MENTIONS)
        if spans:
            inside = {i for _, a, b in spans for i in range(a, b)}
            # (letters only, as above: "2nd" is "nd", one of AI-17's words)
            words = [x for i, w in enumerate(_norm(bare).split()) if i not in inside for x in re.findall(r"[a-z']+", w)]
            return "English" if len(words) > 4 or any(w in ENGLISH_WORDS for w in words) else \
                "Hebrew" if ui_lang == "he" else "English"
    if len(words) > 4 or (len(words) >= 2 and any(w.lower() in ENGLISH_WORDS for w in words)):
        return "English"
    return "Hebrew" if ui_lang == "he" else "English"


def build_prompt(question: str, character: Character | None, history: History | None, kb: KnowledgeBase,
                 has_screenshot: bool, length: str = "short", focus=None, extra: str | None = None,
                 kb_context: bool = True, ui_lang: str = "he") -> str:
    """kb_context=False: no knowledge-base pre-fetch (a screenshot read needs only the profile and the picture).
    ui_lang: the app's language, for a question with no words to tell by."""
    language = f"Reply in {reply_language(question, ui_lang, kb)}, whatever language the context above is in."
    parts = []
    if character:
        parts.append(f"<player_profile>\n{character.summary()}\n</player_profile>")
    else:
        parts.append("<player_profile>unknown</player_profile>")
    if history:
        summ = history.summaries()
        if summ:
            # older than the profile: a summary's "level 25 Thief" never outranks it (audit AI-20)
            parts.append("<earlier_sessions> (older than <player_profile>: the profile wins)\n" + "\n".join(summ[-3:])
                         + "\n</earlier_sessions>")
        recent = history.conversation()        # since the chat's clear button, when it was pressed
        # the chat writes the question to the history before the AI runs: it goes once, in <question>
        if recent and recent[-1].get("role") == "user" and _FOCUS_TAG.sub("", recent[-1]["text"]).strip() == question.strip():
            recent = recent[:-1]
        if recent:
            convo = "\n".join(f"{'Player' if r['role'] == 'user' else 'Helper'}: {r['text'][:600]}" for r in recent)
            parts.append(f"<recent_conversation>\n{convo}\n</recent_conversation>")
    ctx = []
    if not kb_context:
        question_parts = [f"<screenshot>{'attached above' if has_screenshot else 'not available'}</screenshot>",
                          *([HUD_RULE] if has_screenshot else []),
                          f"<question>\n{question}\n</question>", language,
                          REPLY_RULES.format(length=LENGTH_LINES["short"])]
        return "\n\n".join(parts + question_parts)
    reverse = is_reverse(question, kb)
    # a list / filter question ("which quests give capes", "gloves for a Lv. 30 Thief"): its table rows, all of them
    rows_block, plan = planner.context(question, kb, character, reverse=reverse)
    # "where to grind at level 45" is about level 45, whatever the profile says
    level = plan.level if plan and plan.level else character.level if character else None
    if level:
        digest = kb.level_digest(level)
        if digest:
            ctx.append(digest)
    tagged = [k for k in ([focus] if isinstance(focus, str) else (focus or [])) if k and kb.get(k)]
    if tagged:
        names = ", ".join(f"{kb.get(k)['name']} [{k}]" for k in tagged)
        sel = [f"The player tagged these cards; the question is about them unless they say otherwise: {names}"]
        per = 4000 if len(tagged) == 1 else 2000
        for k in tagged:
            sel.append(_page(kb, k, per))
            if k.startswith("monster/"):
                sel.append(kb.drops_digest(k))
            elif k.startswith("item/"):
                sel.append(kb.droppers_digest(k))
        ctx.append("<selected>\n" + "\n".join(x for x in sel if x) + "\n</selected>")
    # "how do I get to Sleepywood?": the way there, worked out from the KB's map connections (no tool call for it)
    way = routes.ai_context(kb, question, character, tagged)
    if way:
        ctx.append(way)
    if reverse:
        items = item_keys_for_question(question, kb)
        groups = kb.drop_groups(items, limit=10)
        if groups:
            lines = ["Which monsters drop it (from drops.tsv; each drop with its list: MSEA = the reference list, not "
                     "confirmed for Classic, community = players saw it in Classic; lowest level first; names and keys "
                     "as in game):"]
            for g in groups:
                m = kb.get(g["monster"])
                lv = (m.get("props") or {}).get("Level", "?")
                lines.append(f"- {m['name']} (Lv {lv}) [{g['monster']}]: "
                             + ", ".join(f"{kb.get(i)['name']} [{i}] ({g['sources'].get(i, sources.MSEA)}"
                                         + (f", {g['votes'][i][0]} confirmed {g['votes'][i][1]} denied"
                                            if i in g.get("votes", {}) else "") + ")"
                                         for i in g["items"]))
            ctx.append("\n".join(lines))
    named = kb.find_mentions(question, max_results=mention_cap(question))
    for key in named:
        # a monster's drops and mesos; an item's droppers by the players' reports, with votes (its page's
        # "Dropped By" doesn't have them)
        digest = (kb.drops_digest(key) if key.startswith("monster/")
                  else kb.droppers_digest(key) if key.startswith("item/") else "")
        limit = page_chars(len(named))
        if len(named) > MENTIONS:      # past MENTIONS entities their digests come out of the pages' share too
            limit = max(MIN_PAGE_CHARS, limit - len(digest))
        body = _page(kb, key, limit)
        ctx += [x for x in (body, digest) if x]
    # what a KB update changed this week in the entities above (and the level digest's monsters)
    shown = re.findall(r"\[((?:monster|item|npc|map|quest|skill)/[^\]\s]+)\]", "\n".join(ctx))
    changes = kb_changes.ai_lines(kb, shown)
    if changes:
        ctx.append("\n".join(changes))
    ctx += _site_context(kb, question, character, shown)
    # what the items in the context really sell for on the Free Market (MesoWatch), from the copy on hand: the chat
    # never waits for the site
    sold = mesowatch.ai_lines(kb, tagged + named)
    if sold:
        ctx.append("\n".join(sold))

    # the KB's news (Nexon's own announcements) for a question about news, launch or maintenance: what was
    # announced, never what is released (the game scope says that)
    if news.asks_news(question) and (announced := news.ai_lines(kb)):
        ctx.append("\n".join(announced))
    # the KB's guide on what the question names ("what is Forgotten Hollow": its endgame-area guide; "when does it
    # release": the release-date guide, the grand launch beside Founder's Access in the news)
    for key in guides_for(question, kb, named):
        ctx.append(_page(kb, key, GUIDE_CHARS))
    # last, nearest the question: the answer read off the tables. Before the pages, the model followed a page
    # ("Steel Guards ... Needed By 2 recipes": "it isn't crafted") over the complete recipe rows
    if rows_block:
        ctx.append(rows_block)
    if ctx:
        parts.append("<kb_context>\n" + "\n\n".join(ctx) + "\n</kb_context>")
    if has_screenshot is True:
        shot = "attached above"
    elif not has_screenshot:
        shot = "not available"
    else:      # the number of full-resolution tiles that follow it
        shot = (f"attached above, followed by {has_screenshot} full-resolution parts of the same screenshot "
                "(left to right) for reading small icons and text")
    parts.append(f"<screenshot>{shot}</screenshot>")
    if has_screenshot:
        parts.append(HUD_RULE)
    if extra:
        parts.append(extra)          # app-made context (inventory read, a picked-up conversation): prompt only
    parts.append(f"<question>\n{question}\n</question>")
    # a Hebrew question slipped into English once after a screenshot-heavy turn (live), and an English one into
    # Hebrew with Hebrew in the context: the language is said outright, after the question
    parts.append(language)
    parts.append(REPLY_RULES.format(length=LENGTH_LINES.get(length, 6)))
    return "\n\n".join(parts)


# Hebrew (and English) words for item families → the item "type" text in the database
# (English as whole words: "hats?" matched the end of "What", "that" and "chat", and every "which monsters should I
# grind?" got 369 hats as its drop context and its card panel)
ITEM_FAMILIES = [
    (r"כוכב|שוריקן|\bthrowing\s*stars?\b|\bstars?\b", "Throwing Star"),
    (r"חיצ(ים|י)|\barrows?\b", "Arrow"),
    (r"שיקוי|שיקויים|פוטיון|\bpotions?\b", "Potion"),
    (r"מגיל(ה|ות)|סקרול|\bscrolls?\b", "Scroll"),
    (r"כפפ(ה|ות)|\bgloves?\b", "Glove"),
    (r"נעל(יים)?|\bboots?\b|\bshoes?\b", "Shoes"),
    (r"כוב(ע|עים)|\bhats?\b|\bhelm", "Hat"),
    (r"מגן|\bshields?\b", "Shield"),
    (r"עגיל|\bearrings?\b", "Earring"),
    (r"גלימ(ה|ות)|\bcapes?\b", "Cape"),
]


GUIDE_CHARS = 2000
# "when does it release / launch", "מתי המשחק יוצא": the release-date guide (the news alone gave Founder's Access)
RELEASE_WORDS = re.compile(r"\b(?:release|launch)\w*|(?<![א-ת])(?:יוצא|ייצא|יצא|השקה|ההשקה|שחרור)(?![א-ת])", re.I)


def guides_for(question: str, kb: KnowledgeBase, named: list[str]) -> list[str]:
    """The KB guides about what the question names: a place's own guide ("Forgotten Hollow" -> "Forgotten Hollow
    Guide", which tells what the Hollow is while its map page says only "a town"), and the release-date guide for a
    question on when the game comes out."""
    names = {str((kb.get(k) or {}).get("name", "")).lower() for k in named if k.startswith("map/")}
    release = bool(RELEASE_WORDS.search(question) and news.asks_news(question))
    if not names and not release:
        return []
    out = []
    for key, e in kb.entities.items():
        if e.get("category") != "guide" or key in named:
            continue
        title = str(e.get("name", "")).lower()
        if (title.endswith(" guide") and title[:-len(" guide")] in names) or (release and "release date" in title):
            out.append(key)
    return out[:2]


def is_reverse(question: str, kb: KnowledgeBase) -> bool:
    """A "which monsters drop X" question. "what drops does Mano have?" names a monster: it asks Mano's drops.
    "Which monsters" alone is no drops question ("which monsters should I grind?"): it needs a drop word or an item."""
    return bool(REVERSE_WORDS.search(question)) and \
        not any(k.startswith("monster/") for k in kb.find_mentions(question, max_results=4)) and \
        (bool(DROP_WORDS.search(question)) or bool(item_keys_for_question(question, kb)))


def item_keys_for_question(question: str, kb: KnowledgeBase) -> list[str]:
    """Items a 'which monsters drop …' question is about: named items, or a whole item family."""
    named = [k for k in kb.find_mentions(question, 12) if k.startswith("item/")]
    if named:
        return named
    for pattern, family in ITEM_FAMILIES:
        if re.search(pattern, question, re.I):
            return [k for k, e in kb.entities.items()
                    if e["category"] == "item" and family.lower() in (e.get("type") or "").lower()]
    return []


# a knowledge-base key the AI wrote into its prose ("Subi Throwing Stars (item/294)"): keys are for the META block
# and the app's cards, a player reads them as noise. Removed with the brackets around it, or alone.
# (with a Hebrew prefix glued to it: "ו-(monster/5)" left a stray "ו-", audit AI-23)
_KEY_IN_TEXT = re.compile(r"\s*(?:[ובלמהשכ]-)?[\(\[]\s*(?:monster|item|map|npc|quest|skill|class|guide|shop|crafting|"
                          r"formula)/[\w\-]+\s*[\)\]]|\s*(?:(?<![\w/])[ובלמהשכ]-)?(?<![\w/])(?:monster|item|map|npc|"
                          r"quest|skill|class|guide|shop|crafting|formula)/[\w\-]+(?![\w/])")


# "גריינד" is a noun with no ל- before it (the owner, 2026-10-04); the AI kept writing "לגרינד" past the prompt's rule
# "אתם ב-31": the player's level with no word for it (the owner: say "רמה" before the number). Never a count:
# "Stirge הוא ב-5 מפות" became "הוא ברמה 5 מפות" (audit AI-7). Said of the player ("אתם", "אני", "הדמות שלכם") it is
# a level whatever follows ("אתם ב-31 כבר", review CORE-5), but for a counted noun; of "הוא/היא/הם", a Hebrew word
# after the number keeps it as written, but for "ו...", "עכשיו", "כרגע" and "אז"
_BARE_LEVEL = re.compile(r"(?<![\u0590-\u05FF])(?:(?P<me>אתם|אתן|אתה|את|אני|הדמות שלכם|הדמות שלך)\s+ב-?(?P<n>\d{1,3})"
                         r"(?![\d%.,:]\d|\d|%)(?!\s*ה?(?:מפות|מקומות|ערוצים|חלקים|שלבים|קבוצות|דקות|שעות|ימים|פעמים|אחוז|אלף"
                         r"|משימות|קווסטים|מפלצות|דמויות|פריטים)"
                         r"(?![\u0590-\u05FF]))|(?P<he>הוא|היא|הם)\s+ב-?(?P<n2>\d{1,3})"
                         r"(?![\d%.,:]\d|\d|%)(?!\s*(?!ו|עכשיו|כרגע|אז(?![\u0590-\u05FF]))[\u0590-\u05FF]))")
# "STR/DEX/INT/LUK +1": one bonus per stat, as the cards write them (a slashed run broke across lines, mirrored)
_SLASHED_BONUS = re.compile(r"\b((?:[A-Z][A-Z.]{1,5}/)+[A-Z][A-Z.]{1,5}) ?([+-]\d+)")
# the AI's "לבל" (gamer slang) in a Hebrew answer: the app says "רמה" (the owner). Any prefix ("ולבל", "מהלבל",
# "כשהלבל"); a bare "בלבל" only before a number, since it is also the verb "confused" ("זה בלבל אותי")
_LEVEL_WORD = re.compile(r"(?<![\u0590-\u05FF])((?:ו|ש|כש|וכש)?(?:ה|מה|לה|בה|מ|ל)?|(?:ו|ש|כש|וכש)?ב(?=לבל\s*-?\d|"
                         r"לבלים|לבלינג))(לבלים|לבלינג|לבל)([- ]?אפ)?(?![\u0590-\u05FF])")
_TO_GRIND = re.compile(r"(?<![\u0590-\u05FF])ל(?:גרינד|גריינד)(?![\u0590-\u05FF])")
# a scroll is "סקרול" / "סקרולים" in the app's Hebrew, never "מגילה" (the owner, 2026-10-11). Any prefix
# ("המגילה", "למגילת", "ומגילות"); the construct "מגילת X" is plain "סקרול X". The word only: agreement around it
# is the prompt's job, as with "לבל"
_SCROLL_WORD = re.compile(r"(?<![\u0590-\u05FF])((?:ו|ש|כש|וכש)?(?:ה|מה|לה|בה|מ|ל|ב|כ)?)(מגילות|מגילת|מגילה)"
                          r"(?![\u0590-\u05FF])")
# an English name given a Hebrew plural ending, seen live: "Warrior-ים". The plural stays English: "Warriors"
_HEBREW_PLURAL = re.compile(r"\b([A-Za-z][A-Za-z']*)-(?:ים|ות)(?![\u0590-\u05FF])")


def _english_plural(word: str) -> str:
    low = word.lower()
    if low.endswith("man"):
        return word[:-3] + "men"                 # Bowman -> Bowmen
    if low.endswith("ief"):
        return word[:-1] + "ves"                 # Thief -> Thieves
    if low.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    if low.endswith("y") and low[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def _level_word(m: re.Match) -> str:
    pre, word, up = m.groups()
    if up or word == "לבלינג":                          # "לבל אפ" / "לבלינג": the climb, not one level
        rest = "רמה" if up else "רמות"
        return (pre[:-1] + "עליית ה" + rest) if pre.endswith("ה") else pre + "עליית " + rest
    return pre + ("רמות" if word == "לבלים" else "רמה")


def drop_keys(text: str) -> str:
    """The answer text as the player reads it: no knowledge-base keys, "לעשות גריינד" for "לגרינד", and a level
    named as one ("אתם ברמה 31", not "אתם ב-31"), "רמה" for the gamer's "לבל" (the owner's word), "סקרול" for
    "מגילה", and an English
    plural for a Hebrew ending on an English name ("Warriors", not "Warrior-ים")."""
    text = _KEY_IN_TEXT.sub("", text)
    text = _BARE_LEVEL.sub(lambda m: f"{m['me'] or m['he']} ברמה {m['n'] or m['n2']}", text)
    text = _LEVEL_WORD.sub(_level_word, text)
    text = _SCROLL_WORD.sub(lambda m: m.group(1) + ("סקרולים" if m.group(2) == "מגילות" else "סקרול"), text)
    text = _SLASHED_BONUS.sub(lambda m: ", ".join(f"{s} {m.group(2)}" for s in m.group(1).split("/")), text)
    text = _HEBREW_PLURAL.sub(lambda m: _english_plural(m.group(1)), text)
    return _TO_GRIND.sub("לעשות גריינד", text).replace("גרינד", "גריינד")


_HEBREW = re.compile(r"[\u0590-\u05FF]")
# the model talking to itself after its tool calls, seen live at the start of a Hebrew answer: "This quest is in Kerning
# City (Victoria Island) - good, in game. Now for answer, I'll mention Stranger's Identity as doable now, ..."
_NARRATION = re.compile(r"\b(?:I'll|I will|I'm going to|I am going to|I need to|I should|I can see|Let me|Let's|"
                        r"Now (?:for|I|to|let)|for (?:the |my )?(?:answer|reply)|in (?:the )?answer,|"
                        r"the (?:player|user)(?:'s)? (?:is|asks|asked|wants|question)|(?:good|ok|okay|great)\s*[,-]\s+"
                        r"(?:it'?s |that'?s |in )|(?:is|it's|that's) in (?:the )?game\b|the (?:data|kb|knowledge base|"
                        r"page|grep|search) (?:says|shows|lists|confirms|returned)|I'?ve (?:got|found|checked))", re.I)
# a line that is part of an answer's layout, never a monologue: a list item, a bold name, a heading, a table row
_LAYOUT = re.compile(r"\s*(?:[-*•#|>]|\d+[.)]|\*\*)")
_PLAN_START = re.compile(r"\s*(?:Let me|I'll|I will|Now)\b", re.I)


def _narration(lines: list[str]) -> bool:
    """English lines that read as the model's own planning: prose (no list or bold layout), at least a short
    sentence, and in its words ("I'll", "Now for the answer", "good, in game")."""
    lines = [s for s in lines if s.strip()]
    if not lines or any(_LAYOUT.match(s) for s in lines):
        return False
    head = " ".join(lines)
    # a short one too when it opens as planning ("Let me check the data." stayed above the Hebrew answer)
    return (len(head.split()) >= 6 or bool(_PLAN_START.match(head))) and bool(_NARRATION.search(head))


# an English answer's own planning first line: "Let me check the data for the player first." (audit AI-19); narrower
# than _NARRATION, which "Let's head to Perion" would match
_EN_PLANNING = re.compile(r"^\s*(?:Let me|I'll|I will|I need to|Now (?:for|let me|I'll)|I'?ve (?:got|found|checked))\b"
                          r".*\b(?:check|look|grep|search|read|find|answer|data|kb|knowledge base|pages?)\b", re.I)


def strip_lead_in(text: str) -> str:
    """A Hebrew answer without the English planning paragraph the model sometimes starts it with. Only English prose
    before the first Hebrew line goes, and only when it reads as planning: an English answer, or an English game
    name or list line before the Hebrew, stays. An English answer loses a first line of planning only."""
    lines = text.split("\n")
    first = next((i for i, s in enumerate(lines) if _HEBREW.search(s)), None)
    if first is None:
        return strip_english_planning(text)
    if not first or not _narration(lines[:first]):
        return text
    return "\n".join(lines[first:])


def strip_english_planning(text: str) -> str:
    """An answer without a first line of the model's own planning in English ("Let me check the data for the player
    first."): that line only, and only when more follows (audit AI-19). An English answer gets this alone (P84B-4)."""
    lines = text.split("\n")
    rest = "\n".join(lines[1:]).strip()
    if rest and _EN_PLANNING.search(lines[0]) and _narration(lines[:1]):
        return rest
    return text


def _without_partial_marker(text: str) -> str:
    """The text without a marker that has only partly arrived at its end ("…answer.\\n@@ME")."""
    for n in range(len(META) - 1, 0, -1):
        if text.endswith(META[:n]):
            return text[:-n]
    return text


def split_meta(raw: str, hebrew: bool | None = None) -> tuple[str, dict]:
    """Separate the visible answer from the trailing @@META@@ JSON. The last marker with JSON after it is the META
    (prose that mentions the marker cut the answer there), parsed up to its own closing brace (a second "{...}"
    after it lost the whole META: entities and profile_update; audit AI-14).
    hebrew: the answer should be Hebrew; False keeps an English answer whole but for a first line of planning (its
    "Let me explain: ..." first paragraph went when a later line quoted a Hebrew name, P84B-4). None: not known,
    the lead-in rule decides alone."""
    lead_in = strip_lead_in if hebrew is not False else strip_english_planning
    if META not in raw:
        return drop_keys(lead_in(_without_partial_marker(raw.rstrip()))).strip(), {}
    text, _, meta = raw.rpartition(META)
    if "{" not in meta:
        text, _, meta = raw.partition(META)
    # an earlier META block (the model wrote two): out of the text
    text = lead_in(re.sub(re.escape(META) + r"\s*\{[\s\S]*$", "", text))
    start = meta.find("{")
    try:
        data = json.JSONDecoder().raw_decode(meta[start:])[0] if start >= 0 else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    # every field to the type the app expects: a malformed reply must never replace a good answer with an error
    for key, typ in (("profile_update", dict), ("entities", list), ("drop_groups", list), ("grind", dict)):
        if key in data and not isinstance(data[key], typ):
            del data[key]
    _numbers(data.get("profile_update"))
    return drop_keys(text).strip(), data


def _whole(v) -> int | None:
    """12, 12.0 and "12" (or "1,234") as an int; never a bool (True is no level 1), a fraction or a word."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    if isinstance(v, str) and re.fullmatch(r"\s*\d{1,3}(?:,\d{3})*\s*|\s*\d+\s*", v):
        return int(v.replace(",", ""))
    return None


def _numbers(update) -> None:
    """profile_update's level and stats as ints (the AI writes "12" or 12.0 too): the chat compares the level with
    the saved one (a lower level asks first), which a string slipped past."""
    if not isinstance(update, dict):
        return
    if "level" in update:
        lv = _whole(update["level"])
        if lv is None or not 1 <= lv <= 250:         # "Lv. 0" asked to lower the level for nothing (audit AI-15)
            del update["level"]
        else:
            update["level"] = lv
    stats = update.get("stats")
    if "stats" in update:
        clean = {k: n for k, v in stats.items() if (n := _whole(v)) is not None} if isinstance(stats, dict) else {}
        if clean:
            update["stats"] = clean
        else:
            del update["stats"]


def streamed_text(raw: str, hebrew: bool = False) -> str:
    """The visible part of a reply still streaming: before @@META@@, and without a marker that has only
    partly arrived (the stream can end a chunk on "…answer.\\n@@ME").
    hebrew: the answer should be Hebrew. Until its first Hebrew letter arrives, English text waits for its first
    line to end, and English planning (strip_lead_in) waits for the Hebrew after it: it never flashes up."""
    text = _without_partial_marker(raw.split(META)[0])
    if hebrew and not _HEBREW.search(text):
        # English prose waits for the Hebrew (or the end): a short "Let me check." showed, then went once the rest
        # of the planning arrived. A list or bold line (a name, a route) shows at once
        lines = text.split("\n")
        if len(lines) == 1 or not any(_LAYOUT.match(s) for s in lines[:-1] if s.strip()):
            return ""
    return drop_keys(strip_lead_in(text) if hebrew else text).strip()


class Stream:
    """The callback a provider streams the raw reply to: the chat gets the visible text (streamed_text), and stage()
    passes on what the AI is doing before its first words ("tools", "hedge": base.Race, the providers' stream
    readers), for the waiting bubble. A provider that knows nothing of stages just calls it with the text."""

    def __init__(self, on_delta, on_stage, hebrew: bool):
        self.on_delta, self.on_stage, self.hebrew = on_delta, on_stage, hebrew

    def __call__(self, raw: str) -> None:
        if self.on_delta:
            self.on_delta(streamed_text(raw, self.hebrew))

    def stage(self, kind: str) -> None:
        if self.on_stage:
            self.on_stage(kind)


WARM_IDLE_S = 60 * 60     # the chat closed, or no question asked, this long: no process is kept waiting (audit PRF-1)


class Brain:
    def __init__(self, kb: KnowledgeBase, provider: str = providers.DEFAULT, model: str | None = None,
                 length: str = "short", api_key: str | None = None):
        self.kb = kb
        self.model = model
        self.length = length
        self.api_key = api_key
        self.last_model = None         # the model that answered last (the CLI's default has no name until then)
        self._defaults: dict[str, str | None] = {}     # provider -> its CLI's default model name, read once
        self.ui_lang = "he"            # the app's language (set by the app): for questions with no words to tell by
        self._provider = providers.get(provider)
        self.backend = self._provider.backend(self)
        self._cancels = 0              # cancel() calls so far: a question stopped before its AI run began skips it
        self._chat_open = False        # the chat window is on screen (Overlay tells, see chat_shown)
        self._chat_left: float | None = None     # when it was last closed (time.monotonic); None: never opened yet
        self._last_use: float | None = None      # the last open, typing or question (time.monotonic); None: never

    @property
    def provider(self) -> str:
        return self._provider.name

    @provider.setter
    def provider(self, name: str) -> None:
        """Switch AI: the old backend's processes are stopped, the new one starts cold."""
        new = providers.get(name)
        if new.name != self._provider.name:
            self.backend.shutdown()
            self._provider, self.backend = new, new.backend(self)
            self.last_model = None          # the other AI's model

    def system_prompt(self) -> str:
        return (SYSTEM_PROMPT.format(length=LENGTH.get(self.length, LENGTH["short"])) + self._scope()
                + official.prompt_note() + self._running_on())

    def _scope(self) -> str:
        """What is in the game, as the KB states it (availability.py): the AI never sends a player to Orbis or
        El Nath, or offers a 3rd job, while the KB says they aren't out."""
        from . import availability
        try:
            # MeowDB's guide and the app's reading of the map pages, never "(official)": only the official facts
            # below are (the owner's rule; audit AI-16)
            return ("\n\nGame scope (from the knowledge base's release guide on MeowDB and its map pages, the only "
                    "source of truth on what is out; cite it as \"(MeowDB)\", never \"(official)\"): "
                    + availability.of(self.kb).scope_note())
        except Exception:      # noqa: BLE001 - a KB without the release guide: no scope line rather than no answer
            return ""

    def _running_on(self) -> str:
        """Which AI answers: our instructions replace each CLI's own, and Grok then didn't know its model
        ("which model am I talking to?" got "not shown in this session")."""
        from .providers.base import model_name
        model = self.last_model or self.model
        # Codex never reports the model it ran: with no model picked, its CLI's default (read by prewarm), so
        # "which model are you?" isn't "ChatGPT, the exact model isn't exposed to me"
        name = model_name(model) if model else (self._defaults.get(self._provider.name) or "")
        return f"\nYou run on {self._provider.label}" + (f", model {name}" if name else "") + "."

    def chat_shown(self, shown: bool) -> None:
        """The chat opened or closed: what decides whether a process is kept waiting for the next question."""
        self._chat_open = shown
        if shown:
            self._last_use = time.monotonic()
        else:
            self._chat_left = time.monotonic()

    def note_use(self) -> bool:
        """The player types in the chat. True when no process was being kept until now (the chat sat open with no
        question for WARM_IDLE_S): the caller warms one again, ready by the time the question is sent."""
        idle = not self.wants_warm()
        self._last_use = time.monotonic()
        return idle

    def wants_warm(self) -> bool:
        """Worth keeping a process ready: the chat was used (opened, typed in, asked) less than WARM_IDLE_S ago,
        and is open or was closed less than WARM_IDLE_S ago. A start in the tray at login whose chat never opens
        kept a ~360 MB Claude Code process all day, renewed every 15 minutes, and so did a chat left open next to
        the game with no question (audit PRF-1); it is warmed when the chat opens or is typed in instead (typing a
        question outlasts the CLI's start, and a question with no warm process starts its own)."""
        now = time.monotonic()
        if self._last_use is None or now - self._last_use >= WARM_IDLE_S:
            return False
        return self._chat_open or (self._chat_left is not None and now - self._chat_left < WARM_IDLE_S)

    def prewarm(self) -> None:
        """Get the next question's process ready now, where the provider supports it (only while it pays off:
        see wants_warm)."""
        self._find_cli()
        if self.wants_warm():
            self.backend.prewarm()
        if not self.model and self._provider.name not in self._defaults:
            try:
                self._defaults[self._provider.name] = self._provider.default_model()
            except Exception:      # noqa: BLE001 - only the model's name in the prompt depends on it
                log.warning("default model of %s not read", self._provider.name, exc_info=True)

    def shutdown(self) -> None:
        self.backend.shutdown()

    def drop_warm(self) -> None:
        """Stop the waiting process only, never an answer the player is reading (the KB swap needs the folder)."""
        drop = getattr(self.backend, "drop_warm", None)
        if drop:
            drop()

    def _find_cli(self, again: bool = False) -> None:
        """The backend looked for its CLI when it was made: an install since (from Settings) or a CLI that moved
        (a Store update renames its folder) left it with none, and every question said "not installed" until
        a restart. again: look even when the old one is still there (an update for a CLI that was too old can
        install the new one in another folder, and the old one kept answering "too old")."""
        import os
        exe = self.backend.exe
        if again or not exe or (os.path.isabs(exe) and not os.path.exists(exe)):
            found = self._provider.find_exe()
            if found != exe:
                self.backend.exe = found

    def refind_cli(self) -> None:
        self._find_cli(again=True)

    def available(self) -> bool:
        self._find_cli()
        return self.backend.exe is not None

    def cancel(self) -> None:
        """Stop the question being answered now (the chat's Stop button, a sync that timed out, a quit)."""
        self._cancels += 1
        self.backend.cancel()

    def ask(self, question: str, character: Character | None, history: History | None,
            screenshot_jpeg: bytes | None, on_delta=None, focus=None, extra: str | None = None,
            model: str | None = None, light: bool = False, on_stage=None) -> Answer:
        """Blocking call; on_delta(visible_text_so_far) is invoked while the answer streams, on_stage(kind) when
        the AI reports what it is doing before that (Stream).
        extra: context for the prompt only; every heuristic below reads the player's own question.
        model: another model for this one call (None: the player's). light: a screenshot read (the ⟳ sync): no
        knowledge-base pre-fetch and no file tools, so a light model answers in seconds instead of ~40 s."""
        cancels = self._cancels
        self._last_use = time.monotonic()      # a question: the next one's process is kept ready again
        self._find_cli()
        if not live.enabled(f"provider_{self.provider}"):
            return Answer(error="live_off")        # the owner's switch (live.py): this AI is broken for everyone now
        if not self.backend.exe:
            return Answer(error="not_installed")
        self.kb.ensure_drop_table()
        shots = screenshot_jpeg if isinstance(screenshot_jpeg, list) else [screenshot_jpeg] if screenshot_jpeg else []
        # True (one screenshot), or the number of detail tiles that follow it (an int, never 1 == True)
        has = (len(shots) - 1 if len(shots) > 1 else True) if shots else False
        prompt = build_prompt(question, character, history, self.kb, has, "short" if light else self.length, focus,
                              extra, kb_context=not light, ui_lang=self.ui_lang)
        hebrew = reply_language(question, self.ui_lang, self.kb) == "Hebrew"
        raw_delta = Stream(on_delta, on_stage, hebrew) if on_delta or on_stage else None
        if cancels != self._cancels:
            return Answer(error="cancelled")      # Stop came while the prompt was built: no AI run starts at all
        if model or light:
            result = self.backend.run(prompt, screenshot_jpeg, raw_delta, model=model, tools=not light)
        else:
            result = self.backend.run(prompt, screenshot_jpeg, raw_delta)
        if result.error:
            if result.error == "cli_outdated":
                from .providers.base import note_outdated
                note_outdated(self._provider.name, self.backend.exe)     # Settings says "too old", not "Connected"
            return Answer(error=result.error, limits=result.limits)
        text, meta = split_meta(result.text, hebrew)
        if not text and not meta:
            return Answer(error="no_result", limits=result.limits)   # nothing at all came back: no empty bubble
        if "profile_update" not in meta:
            # only when the AI sent no update at all: it saw the question and judged "{}" (nothing changed)
            stated = stated_level(question)
            if stated:
                meta.setdefault("profile_update", {})["level"] = stated
        entities = [k for k in meta.get("entities", []) if isinstance(k, str) and kb_has(self.kb, k)][:12]
        # only cards for what the answer actually talks about (a follow-up on snails got a Mano card, live)
        low = text.lower()
        entities = [k for k in entities if str((self.kb.get(k) or {}).get("name", "")).lower() in low]
        groups = []
        every_drop = False
        for g in meta.get("drop_groups") or []:
            # a group of the wrong shape ("items": 5) is skipped: it must never turn a good answer into an error
            if isinstance(g, dict) and kb_has(self.kb, str(g.get("monster", ""))) and isinstance(g.get("items"), list):
                items = [i for i in g["items"] if isinstance(i, str) and kb_has(self.kb, i)]
                if items:
                    groups.append(self.kb.drop_group(g["monster"], items[:10]))
        if not groups and is_reverse(question, self.kb):
            # the app builds the grouping itself: the question's items, else the items the answer names
            items = item_keys_for_question(question, self.kb) or \
                [k for k in entities if k.startswith("item/")] or \
                [k for k in self.kb.find_mentions(text, 12, answer=True) if k.startswith("item/")]
            groups = self.kb.drop_groups(items)
        if groups:
            entities = []          # the grouped view replaces the flat cards
        elif DROP_WORDS.search(question) or (DETAIL_WORDS.search(question) and not LIST_WORDS.search(question)):
            # a drops question (or "tell me about" a monster): the monster card + every drop as a tile, from the KB
            monsters = [k for k in entities if k.startswith("monster/")] or \
                [k for k in self.kb.find_mentions(question, 4) if k.startswith("monster/")]
            if monsters:
                drops = self.kb.monster_drops(monsters[0])
                entities = [monsters[0]] + drops
                every_drop = True       # all of them: at 12 cards, 18 community drops left no room for the MSEA list
        if not groups:
            # cards for every in-game name the answer itself mentions, after the ones the AI listed (it listed only
            # Snail Shell for an answer naming Brown Skullcap, Green Skullcap and Snail, seen live)
            # (names as written: "your max HP" is no Max card, "בין לבל 10 ל-20" no Bain card)
            named = [k for k in self.kb.find_mentions(text, max_results=12, answer=True)
                     if k.split("/")[0] in ("monster", "item", "npc", "map", "quest")]
            entities = entities + [k for k in named if k not in entities]
        # no card for what the KB says isn't in the game: "El Nath isn't out yet" came with an El Nath map card
        # (a 3rd-job answer with Tylus' and Chief's Residence's), shown like any place the player can go
        open_ = availability.of(self.kb)
        entities = [k for k in entities if open_.entity_open(k)]
        box = meta.get("avatar_box")
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            box = None
        if result.model:
            self.last_model = result.model
        return Answer(text=text, entities=entities if every_drop else entities[:12], drop_groups=groups[:8], profile_update=meta.get("profile_update") or {},
                      grind=meta.get("grind") or {}, avatar_box=box if screenshot_jpeg else None, cost_usd=result.cost_usd,
                      limits=result.limits, model=result.model, tool_calls=result.tool_calls, turns=result.turns)

    def summarize(self, transcript: str) -> str | None:
        """One-paragraph summary of a finished session, kept as long-term context."""
        if not transcript.strip():
            return None
        return self.backend.summarize(SUMMARY_PROMPT, transcript)

    def summarize_guide(self, key: str, page: str, lang: str) -> str | None:
        """The practical takeaways of a KB guide in the player's language (a light model, one short call)."""
        language = "Hebrew" if lang == "he" else "English"
        prompt = (f"Summarize this MapleStory Classic guide for a player, in {language}: 6-10 short bullet lines "
                  "('• ...') with the most useful practical advice (builds, levels, where to go, what to buy). "
                  "Keep every game name (items, monsters, maps, skills, jobs) in English exactly as written. "
                  "No intro, no outro.")
        out = self.backend.summarize(prompt, page[:60000], timeout=120)
        if not out:
            log.warning("guide summary failed for %s", key)
        return out


_LEVEL_PATTERNS = [
    r"(?:עליתי|הגעתי)\s+(?:ל|ללבל|לרמה)\s*-?\s*(\d{1,3})",
    r"(?:אני|עכשיו)\s+(?:ב)?(?:לבל|רמה)\s*(\d{1,3})",
    # "now" only right after "I'm" ("now lv 30 quests?" asks about level 30), "hit" only as news ("just hit lvl 70",
    # not "how long to hit level 30?" or "monsters that hit level 20 players hard")
    r"(?:i'?m|i am)(?:\s+now)?\s+(?:level|lvl|lv\.?)\s*(\d{1,3})",
    r"(?:reached|(?:just|finally)\s+hit)\s+(?:level|lvl|lv\.?)\s*(\d{1,3})",
]


# a plan, not a fact; Hebrew words at a word start only ("עכשיו" contains "כש")
_HYPOTHETICAL = re.compile(r"\b(?:when|once|if|until|after|before)\b|(?:^|\s)(?:כש|אם\s|עד\sש|אחרי\sש|לפני\sש)", re.I)


# another character's level ("im lvl 15 on my other char", "my friend is level 40"): not this profile's (audit AI-15)
_SOMEONE_ELSE = re.compile(r"\b(?:alts?|other (?:char\w*|toon)|another (?:char\w*|toon)|friends?|second char\w*)\b|"
                           r"דמות (?:אחרת|נוספת|שנייה)|(?<![א-ת])ה?חבר(?:ה|ים|ות)?(?:\s+שלי)?(?![א-ת])", re.I)
# friends as company ("played with friends", "עם חבר"): the level is still the player's (review CORE-7)
_WITH_FRIEND = re.compile(r"(?:\bwith|(?<![א-ת])עם)\s+(?:my\s+|a\s+)?(?:friends?\b|ה?חבר(?:ה|ים|ות)?(?:\s+שלי)?"
                          r"(?![א-ת]))", re.I)
_CLAUSE_END = ",.;!?\n"


def stated_level(text: str) -> int | None:
    """A level the player states about themselves ("עליתי ללבל 16", "I'm level 16"); never a plan ("what should
    I do once I'm level 30?" once set the profile to 30), nor another character's in the same clause."""
    if _HYPOTHETICAL.search(text):
        return None
    for pat in _LEVEL_PATTERNS:
        m = re.search(pat, text, re.I)
        if m and 1 <= int(m.group(1)) <= 250:
            # only the clause that says it: "I'm level 30, played with friends all day" is the player's
            start = max(text.rfind(c, 0, m.start()) for c in _CLAUSE_END) + 1
            end = min((i for i in (text.find(c, m.end()) for c in _CLAUSE_END) if i >= 0), default=len(text))
            if _SOMEONE_ELSE.search(_WITH_FRIEND.sub(" ", text[start:end])):
                return None
            return int(m.group(1))
    return None


def kb_has(kb: KnowledgeBase, key: str) -> bool:
    return kb.get(key) is not None
