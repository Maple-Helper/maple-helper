"""Asks the player's chosen AI (Claude Code, Codex, Antigravity or Grok Build, see providers/) and turns its reply into an Answer.

The prompt, the knowledge-base pre-fetch and the answer post-processing (cards,
drop groups, profile updates) live here and are the same for every provider;
the provider's backend only runs the CLI and returns the raw text.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from . import availability, news, official, providers, routes, sitedata, sources
from . import recent as kb_changes      # ("recent" is the conversation in build_prompt)
from .kb import KnowledgeBase
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
# repeats the cards, so it names one drop at most, and the tiles showed only that one (the owner's report)
DETAIL_WORDS = re.compile(r"פרטים|מידע|(?<![א-ת])(?:ספר|תספר|תגיד|ספרי)\s+לי|\b(?:details?|info|about|tell me)\b", re.I)
DROP_WORDS = re.compile(r"דרופ|מפיל|(?<![א-ת])(?:מה|איזה|אילו)\s+(?:\S+\s+){0,2}נופל|שנופל|drops?\b|loot", re.I)
# a comparison or a list ("Mano, Mushmom, King Slime and Jr. Balrog", "הרמיט או צ'יף בנדיט"): the pages of up to
# LIST_MENTIONS entities are pre-fetched, each cut shorter, so the prompt stays about as long as for MENTIONS pages
LIST_WORDS = re.compile(r",|\b(?:vs|versus|or|and|compar\w*|between|differences?)\b|(?<![א-ת])(?:או|לעומת|מול|בין|השוו\w*|"
                        r"השוואה|ההבדל|הבדל)(?![א-ת])|(?:^|\s)ו(?=[א-ת]{2})", re.I)
MENTIONS, LIST_MENTIONS = 4, 8
PAGE_CHARS = 2500          # a pre-fetched page, MENTIONS of them at most at full length
MIN_PAGE_CHARS = 800       # a page cut for a long list still keeps its head: level, HP, EXP, where
SUMMARY_PROMPT = ("Summarize this MapleStory Classic helper conversation in 2-3 sentences for future context: "
                  "what the player worked on, decisions, open goals. Same language as the conversation.")

SYSTEM_PROMPT = """You are Maple Helper, a personal in-game assistant for MapleStory Classic World (MapleStory Classic), shown as a small chat window on top of the game.

What you receive with each question:
- A screenshot of the game window, taken the moment the player opened the chat (when available).
- The player's character profile, recent conversation, and knowledge-base context the app pre-fetched.

Knowledge base: the current directory is the full NiaMeowDB (meowdb.com) database for MapleStory Classic: index.json (every entity: key, name, category, props), names.tsv (key, category, name, type: one entity per line, the file to grep for a name or a key) and pages/<category>/<id>.md (full details: stats, drops, maps, quests); skill_changes.json (skills changed between two test builds), pets.json and tiers.json (the community tier list) are NiaMeowDB's list pages. Categories: monster, item, map, quest, npc, skill, class, guide, shop, crafting, formula.
- Use the pre-fetched context first. Use Grep/Glob/Read only for what is missing. Never write text before a tool call.
- Never invent facts, numbers, drops or locations. If the data does not say, say so briefly.

Which monsters drop something: drops.tsv (monster, level, key, item, item type, item key, source, votes) lists the
monster→item drops of the monsters in the game; source is the list the drop is on ("MSEA" or "community", see Drops
below), votes a community drop's "16 up 1 down". Grep it for the item name or the item type (e.g. "Throwing Star", "Scroll", "Potion"). Answer
grouped per monster (monster → the items it drops), lowest level first, and return the grouping as META "drop_groups".
An item page's "Dropped By" list names every monster that ever dropped it: one drops.tsv doesn't list for that item is
not in the game, so never name it as a source.

Which quests give something: rewards.tsv (quest, quest level, quest key, area, item, count, item type, item key, kind,
for) lists every item reward of the quests in the game; kind is "sure", "pick one" (the player picks one, for = the
class), "random 16.7%" (one of a set, with its odds) or "gender". Grep it once for the item name or type (e.g. "Cape",
"Overall", "Scroll"): it answers "which quests give X" in one step, so never open item or quest pages one by one for it.
A quest it doesn't list is not in the game.

Drops: a monster page lists its drops in two lists under "Drops (MS Classic)": "Community sourced" (drops players
saw in Classic themselves: community) and "MSEA reference drops" (what the monster dropped in old MapleSEA, which the KB
calls historical reference, not confirmed for Classic). drops.tsv's source column and the pre-fetched drop lists say
which list each drop is on. The community list comes from players' reports on MeowDB, each with its votes (players
who confirmed / denied it); the app hides drops more players denied than confirmed. Name a community drop's votes
briefly the first time: "(קהילה, 16 ✓)" / "(community, 16 ✓)", and a drop one player alone reported
"(קהילה, דיווח יחיד)" / "(community, single report)": it is not confirmed yet. Mesos: the pre-fetched "Mesos of"
line is the median of the players' reports (per drop, and how often a kill drops mesos): give it as
"18–23 mesos (קהילה)" / "18–23 mesos (community)". When asked what a monster drops, the app shows every drop as a tile with its votes and its list: in the text name
only the few worth knowing (the most confirmed, anything valuable) and say the tiles show the rest; return every
dropped item's key in entities.

Sources: the app tags every number it shows with where it comes from, and so do you. A pre-fetched page starts with a
"[sources: ...]" line: stats and NPC shop prices carry the build the KB labels them with ("COT2" = the second closed
test, not confirmed for launch; a later KB may say "Launch"), drops their list, Free Market prices are community
reports, the game's scope is official (Nexon), and anything unlabeled is MeowDB's own. Whenever you state drops,
prices or stats, name their source in a word or two right after them: "(MSEA)", "(community)", "(COT2)", "(official)",
"(MeowDB)" in English; in a Hebrew answer "(MSEA)", "(קהילה)", "(COT2)", "(רשמי)", "(MeowDB)". When players reported
nothing, say so in the answer's language: "אין נתונים מהקהילה" / "no community data". "Recent KB change" lines are things a knowledge-base update changed this week: when they bear on the answer,
point the change out briefly (old → new). "Skill change COT1 -> COT2" lines give a skill's values before and after the
latest test: build advice uses the newer values. The "Community tier list" is community opinion: say so when you cite it.

Routes: a "Route" block in the context is the way between two maps, worked out by the app from the knowledge base's
map connections, taxis and boats, through maps that are in the game only. For "how do I get to ..." give it as
numbered steps ("1.", "2.", one step per line, never as running prose), map and NPC names exactly as written in the
block, in English even in a Hebrew answer (the player's "לסליפיווד" is "Sleepywood"); never add maps, shortcuts or
transport it doesn't list. Portals are free; a taxi or boat step costs mesos, with the amount only where the block
gives one. Never say a walk or a portal costs anything.

Advice must fit the player's level and job. If the profile lacks level or job, ask for it before recommending.

Scope: you help only with MapleStory Classic (the game, the player's characters) and with Maple Helper itself (what it
can do, its settings, which AI and model answers). Anything else
(news not about MapleStory Classic, real people, politics, general knowledge, other games, coding, homework, writing or file tasks) you do not answer,
not even briefly: reply in one short line, in the question's language, that you only help with MapleStory Classic, and
invite a game question. Entities stay empty.

Style:
- Reply in the language of the question (Hebrew or English). Hebrew: natural gamer Hebrew (גריינד, דרופ, לעשות ג'וב, רמה).
  "גריינד" (spelled so) is a noun, never with ל- before it: "לא שווה גריינד", "מקום טוב לעשות גריינד"; never
  "לגרינד" or "לגריינד".
  Address the player in the plural, as the app does ("קחו", "לכו", "דברו"), never "קח" or "קחי". The currency is
  "mesos" in English letters ("300 mesos"), never "מזו", "מזוס", "מסוס" or "מסות". A level is "רמה" ("ברמה 31", "הרמה הבאה"), never "לבל".
  Source tags in Hebrew too: "(קהילה)", never "(community)". A Hebrew prefix joins an English name
  with a hyphen ("ל-Henesys", "מ-Henesys"), never a Hebrew spelling ("להניסיס").
- In-game names (items, monsters, maps, NPCs, skills, quests, jobs) always in English, exactly as in the data.
- {length}
- Plain text with short lines; **bold** allowed; no headings, no tables.

After the answer, output a line containing only @@META@@ followed by one JSON object:
{{"entities": ["monster/5", ...], "profile_update": {{}}, "avatar_box": [0.42, 0.55, 0.05, 0.1]}}
- entities: knowledge-base keys (category/id from index.json) of what you mention, most relevant first, max 12.
  When the answer is a LIST of items (drops, quest rewards, shop stock, what to buy/equip), include EVERY item's key
  so the app can show each one with its picture. Find keys by grepping names.tsv for the item names.
- avatar_box (only with a screenshot, only if clearly visible): [x, y, w, h] as fractions (0-1) of the screenshot, a snug box
  around the PLAYER'S OWN character sprite, head to feet, excluding the name tag. Find it by its name tag: the same name
  as the HUD's character name (bottom left, next to the level). NPCs stand around too: their name tags are on a yellow
  plate, often with a second title line (e.g. "Cody / Wizet Wizard"); never box an NPC. Omit it if unsure.
- drop_groups (only for "which monsters drop X" questions): [{{"monster": "monster/12", "items": ["item/5", ...]}}, ...]
  lowest monster level first, max 8 groups.
- profile_update: only facts the player stated or the screenshot clearly shows: "name" (the character name on the HUD, exactly as written), "level" (int), "job" (from a screenshot: exactly as the HUD writes it), "base_class", "map", "quests_started" [..], "quests_completed" [..], "exp_percent" (number 0-100, the EXP bar's percentage, only if the screenshot shows it), "stats" (only if the in-game stat window is open in the screenshot: {{"acc": total Accuracy, "dmg_min": and "dmg_max": the attack/damage range it shows, "hp": max HP, "mp": max MP}}), "note" (a lasting preference or goal). Empty object if nothing changed.
"""

LENGTH = {
    "short": "Keep it short: at most 6 short lines unless the player asks for detail.",
    "detailed": "Be thorough but scannable: up to 15 short lines.",
}


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


REPLY_RULES = """<reply_rules>
- Only MapleStory Classic and Maple Helper itself (its features, settings, which AI and model answers): anything else
  gets one short line saying you only help with the game.
- Only what the game scope in your instructions says is in the game: never send the player to a place it says is not
  out, or suggest its monsters, NPCs, quests or a job advancement it says is not out; if asked, say it isn't out yet.
- At most {length} short lines. No filler, no follow-up offers.
- Never write knowledge-base keys ("item/294", "monster/5") in the answer text: they go only in the META block.
- Under the answer the app shows a card for every entity in META: a monster's level, HP, EXP, maps and what changed
  since the last test build, an item's stats, and a monster's drops as tiles with their votes and sources.
  Don't repeat what those cards show: no level / HP / EXP line, no list of maps, drops or votes, no "mesos: no data".
  "Tell me about X" gets 2-3 lines of what the cards can't say: who it suits (against the player's level), where it
  is best, what is worth it, a recent change; at most one notable drop by name. A question for one number ("how much
  HP") still gets that number with its source. In a Hebrew sentence a stat's number comes first: "51 HP".
- A Hebrew answer reads as if a fluent Israeli gamer wrote it: plain, short sentences in natural Hebrew word order,
  never English sentence structure in Hebrew words. Before replying, reread it once as a Hebrew reader would.
  * Grammar: an adjective agrees with its noun ("נשק בסיסי", "מונסטר בסיסי", never "מונסטר בסיס").
  * A level always says so: "אתם ברמה 31", "נשק לרמה 20", never "(31)", "ב-31" or "לבל"; "רמה" is feminine
    ("הרמה הבאה", "רמה גבוהה").
  * Words: "גריינד" with no ל- before it ("לעשות גריינד"), "דרופ", "ג'וב", "קווסט", "קהילה"; "mesos" in English
    letters (never "מזו", "מזוס", "מסוס", "מסות").
  * English only for game names and stat names, joined to a Hebrew prefix with a hyphen ("ל-Henesys",
    "מ-Blue Snail"); never "This", "drop", "and" or "community" in a Hebrew sentence.
  * The player is "אתם": "קחו", "תוכלו", never "קח" or "קחי".
  * Stat bonuses one per item ("STR +1, DEX +1"), never slashed ("STR/DEX +1").
  * Wrong: "Iron Mace הוא נשק Blunt חד-ידני בסיסי לבל 20 - לא רלוונטי לכם כ-Assassin (31)."
    Right: "Iron Mace הוא נשק חד-ידני בסיסי לרמה 20, ל-Warrior ול-Mage. לא מתאים לכם: אתם Assassin ברמה 31."
  * Jobs and classes in English, always ("Warrior", "Mage", "Assassin"), never "וריור" or "מג'".
  * The test builds by name: "COT1", "COT2", "בין COT1 ל-COT2" or "בין הטסטים"; never "בנייות" or "בילדים".
- NEVER translate game names: items, monsters, maps, NPCs, skills and quests stay in English exactly as in the data
  ("Blue Snail Shell", not "קונכיית חילזון כחול"), even inside a Hebrew sentence.
- A name in Hebrew letters is a game name written the way it sounds ("סאונה רוב כחול" is "Blue Sauna Robe", "בלו
  סנייל" is "Blue Snail"): work out the English and grep names.tsv for it. Never answer about a different entity
  (one from the conversation) instead; if no name matches, say so and ask.
- "Send me a picture of X": the app shows X's picture on its card under the answer. Find X, put its key in META
  entities, and say in a line that its picture is in the card below; never say you can't send pictures.
- Locations, drops and stats only from the context or the knowledge base (Grep pages/monster/*.md for "Map Locations" if needed).
- Name the source of every drop list, price and stat you state, briefly: a stat or a price carries the build its
  page's "[sources: ...]" line names ("(COT2)"), "(MeowDB)" only when that line says "no build label"; drops "(MSEA)",
  and in the answer's language "(community)" / "(קהילה)", "(official)" / "(רשמי)"; a community drop with its votes ("16 ✓",
  one report alone: "דיווח יחיד" / "single report"); mesos or drops nobody reported: "אין נתונים מהקהילה" /
  "no community data".
- Then the line @@META@@ and the JSON object. Always include it, even when empty. If the player states a new level/job, put it in profile_update.
- profile_update describes ONLY the character in <player_profile>. If the player says they are on another character,
  or the screenshot's HUD shows another name, put that character's facts in profile_update WITH its "name" (the app
  offers to add it or switch to it), and never word it as a change of the profile's character.
</reply_rules>"""
LENGTH_LINES = {"short": 6, "detailed": 15}
# the HUD is what the player is now: Claude answered "Lv. 31" from the saved profile with "LV. 13 ... KalimeroZ" on
# screen (and saved the map to the profile's character), while Codex and Gemini read the HUD
HUD_RULE = ("The screenshot's HUD (bottom left: level, job, character name) is the truth for this moment: read it before "
            "<player_profile>. When its name (even one letter apart), level or job differs from the profile, answer from "
            "the HUD and put the HUD's facts, with its exact \"name\", in profile_update.")
NOT_OUT = "NOT in the game: the knowledge base doesn't confirm it is out. Never recommend it; if asked, say it isn't out yet."


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


def reply_language(question: str, ui_lang: str = "he") -> str:
    """The answer's language: the question's (Hebrew letters: Hebrew, Latin ones: English), else the app's.
    Hebrew in the context (earlier session summaries, profile notes) made an English player's answer Hebrew."""
    if re.search(r"[֐-׿]", question):
        return "Hebrew"
    bare = _FOCUS_TAG.sub("", question)
    # a name alone ("SAUNA ROB") is no English sentence: the app's language (it answered a Hebrew player in English)
    if re.search(r"[A-Za-z]", bare) and len(re.findall(r"[A-Za-z']+", bare)) > 4:
        return "English"
    return "Hebrew" if ui_lang == "he" else "English"


def build_prompt(question: str, character: Character | None, history: History | None, kb: KnowledgeBase,
                 has_screenshot: bool, length: str = "short", focus=None, extra: str | None = None,
                 kb_context: bool = True, ui_lang: str = "he") -> str:
    """kb_context=False: no knowledge-base pre-fetch (a screenshot read needs only the profile and the picture).
    ui_lang: the app's language, for a question with no words to tell by."""
    language = f"Reply in {reply_language(question, ui_lang)}, whatever language the context above is in."
    parts = []
    if character:
        parts.append(f"<player_profile>\n{character.summary()}\n</player_profile>")
    else:
        parts.append("<player_profile>unknown</player_profile>")
    if history:
        summ = history.summaries()
        if summ:
            parts.append("<earlier_sessions>\n" + "\n".join(summ[-3:]) + "\n</earlier_sessions>")
        recent = history.recent()
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
    if character:
        digest = kb.level_digest(character.level)
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
    if is_reverse(question, kb):
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
        digest = kb.drops_digest(key) if key.startswith("monster/") else             kb.droppers_digest(key) if key.startswith("item/") else ""
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

    # the KB's news (NiaMeowDB's news section) for a question about news, launch or maintenance: what was
    # announced, never what is released (the game scope says that)
    if news.asks_news(question) and (announced := news.ai_lines(kb)):
        ctx.append("\n".join(announced))
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
_KEY_IN_TEXT = re.compile(r"\s*[\(\[]\s*(?:monster|item|map|npc|quest|skill|class|guide|shop|crafting|formula)/[\w\-]+"
                          r"\s*[\)\]]|\s*(?<![\w/])(?:monster|item|map|npc|quest|skill|class|guide|shop|crafting|formula)/"
                          r"[\w\-]+(?![\w/])")


# "גריינד" is a noun with no ל- before it (the owner, 2026-10-04); the AI kept writing "לגרינד" past the prompt's rule
# "אתם ב-31": the player's level with no word for it (the owner: say "רמה" before the number)
_BARE_LEVEL = re.compile(r"(?<![\u0590-\u05FF])(אתם|אתן|אתה|את|אני|הוא|היא|הם|הדמות שלכם|הדמות שלך)\s+ב-?(\d{1,3})"
                         r"(?![\d%.,:]\d|\d|%)")
# "STR/DEX/INT/LUK +1": one bonus per stat, as the cards write them (a slashed run broke across lines, mirrored)
_SLASHED_BONUS = re.compile(r"\b((?:[A-Z][A-Z.]{1,5}/)+[A-Z][A-Z.]{1,5}) ?([+-]\d+)")
# the AI's "לבל" (gamer slang) in a Hebrew answer: the app says "רמה" (the owner)
_LEVEL_WORD = re.compile(r"(?<![\u0590-\u05FF])(?:בלבלים|לבלים|בלבל|ללבל|הלבל|מלבל|לבל)(?![\u0590-\u05FF])")
_TO_GRIND = re.compile(r"(?<![\u0590-\u05FF])ל(?:גרינד|גריינד)(?![\u0590-\u05FF])")


def drop_keys(text: str) -> str:
    """The answer text as the player reads it: no knowledge-base keys, "לעשות גריינד" for "לגרינד", and a level
    named as one ("אתם ברמה 31", not "אתם ב-31"), and "רמה" for the gamer's "לבל" (the owner's word)."""
    text = _KEY_IN_TEXT.sub("", text)
    text = _BARE_LEVEL.sub(r"\1 ברמה \2", text)
    text = _LEVEL_WORD.sub(lambda m: {"לבל": "רמה", "בלבל": "ברמה", "ללבל": "לרמה", "הלבל": "הרמה", "מלבל": "מרמה",
                                      "לבלים": "רמות", "בלבלים": "ברמות"}[m.group(0)], text)
    text = _SLASHED_BONUS.sub(lambda m: ", ".join(f"{s} {m.group(2)}" for s in m.group(1).split("/")), text)
    return _TO_GRIND.sub("לעשות גריינד", text).replace("גרינד", "גריינד")


def split_meta(raw: str) -> tuple[str, dict]:
    """Separate the visible answer from the trailing @@META@@ JSON."""
    if META not in raw:
        return drop_keys(raw).strip(), {}
    text, _, meta = raw.partition(META)
    m = re.search(r"\{.*\}", meta, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
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
        if lv is None:
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


def streamed_text(raw: str) -> str:
    """The visible part of a reply still streaming: before @@META@@, and without a marker that has only
    partly arrived (the stream can end a chunk on "…answer.\\n@@ME")."""
    text = raw.split(META)[0]
    for n in range(len(META) - 1, 0, -1):
        if text.endswith(META[:n]):
            text = text[:-n]
            break
    return drop_keys(text).strip()


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
            return ("\n\nGame scope (from the knowledge base, the only source of truth; its release guide is built "
                    "from Nexon's official statements, so this is official): "
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

    def prewarm(self) -> None:
        """Get the next question's process ready now, where the provider supports it."""
        self._find_cli()
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

    def _find_cli(self) -> None:
        """The backend looked for its CLI when it was made: an install since (from Settings) or a CLI that moved
        (a Store update renames its folder) left it with none, and every question said "not installed" until
        a restart."""
        import os
        exe = self.backend.exe
        if not exe or (os.path.isabs(exe) and not os.path.exists(exe)):
            found = self._provider.find_exe()
            if found != exe:
                self.backend.exe = found

    def available(self) -> bool:
        self._find_cli()
        return self.backend.exe is not None

    def cancel(self) -> None:
        self.backend.cancel()

    def ask(self, question: str, character: Character | None, history: History | None,
            screenshot_jpeg: bytes | None, on_delta=None, focus=None, extra: str | None = None,
            model: str | None = None, light: bool = False) -> Answer:
        """Blocking call; on_delta(visible_text_so_far) is invoked while the answer streams.
        extra: context for the prompt only; every heuristic below reads the player's own question.
        model: another model for this one call (None: the player's). light: a screenshot read (the ⟳ sync): no
        knowledge-base pre-fetch and no file tools, so a light model answers in seconds instead of ~40 s."""
        self._find_cli()
        if not self.backend.exe:
            return Answer(error="not_installed")
        self.kb.ensure_drop_table()
        shots = screenshot_jpeg if isinstance(screenshot_jpeg, list) else [screenshot_jpeg] if screenshot_jpeg else []
        # True (one screenshot), or the number of detail tiles that follow it (an int, never 1 == True)
        has = (len(shots) - 1 if len(shots) > 1 else True) if shots else False
        prompt = build_prompt(question, character, history, self.kb, has, "short" if light else self.length, focus,
                              extra, kb_context=not light, ui_lang=self.ui_lang)
        raw_delta = (lambda raw: on_delta(streamed_text(raw))) if on_delta else None
        if model or light:
            result = self.backend.run(prompt, screenshot_jpeg, raw_delta, model=model, tools=not light)
        else:
            result = self.backend.run(prompt, screenshot_jpeg, raw_delta)
        if result.error:
            return Answer(error=result.error, limits=result.limits)
        text, meta = split_meta(result.text)
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
        elif DROP_WORDS.search(question) or DETAIL_WORDS.search(question):
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
                      limits=result.limits, model=result.model)

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


def stated_level(text: str) -> int | None:
    """A level the player states about themselves ("עליתי ללבל 16", "I'm level 16"); never a plan ("what should
    I do once I'm level 30?" once set the profile to 30)."""
    if _HYPOTHETICAL.search(text):
        return None
    for pat in _LEVEL_PATTERNS:
        m = re.search(pat, text, re.I)
        if m and 1 <= int(m.group(1)) <= 250:
            return int(m.group(1))
    return None


def kb_has(kb: KnowledgeBase, key: str) -> bool:
    return kb.get(key) is not None
