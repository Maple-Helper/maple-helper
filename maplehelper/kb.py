"""Local knowledge base: entity index, name/alias lookup and pre-retrieval for questions.

Pre-retrieval matters for speed: the app hands Claude the pages it will most
likely need (entities named in the question, monsters around the player's
level), so most answers need no tool round-trips at all.
"""
from __future__ import annotations

import json
import re
from functools import cached_property
from pathlib import Path

from . import bidi, sources
from .store import ASSETS, kb_dir

FALLBACK_DIR = ASSETS / "fallback"
CLASS_PICTURE_FALLBACK = {
    "crusader": "fighter", "white-knight": "page", "dragon-knight": "spearman", "f-p-mage": "f-p-wizard",
    "i-l-mage": "i-l-wizard", "priest": "cleric", "ranger": "hunter", "sniper": "crossbowman",
    "hermit": "assassin", "chief-bandit": "bandit",
}

HEBREW = re.compile(r"[֐-׿]")


_FINALS = str.maketrans("ךםןףץ", "כמנפצ")
# Hebrew geresh / gershayim and the other look-alikes a phone or a keyboard types: "ג׳וניור" = "ג'וניור"
_QUOTES = str.maketrans({"׳": "'", "`": "'", "´": "'", "’": "'", "‘": "'", "״": '"', "“": '"', "”": '"'})


def fold_quotes(s: str) -> str:
    return s.translate(_QUOTES)


def _heb_letters(s: str) -> int:
    return len(re.findall(r"[א-ת]", s))


def _heb_loose(s: str) -> str:
    """Spelling-tolerant Hebrew: final letters, doubled yod/vav and a word-final he/alef don't matter
    ("אלינייה" = "אליניה", "הנסיס" = "הניסיס" is left to the alias list)."""
    s = s.translate(_FINALS)
    s = re.sub(r"יי+", "י", s)
    s = re.sub(r"וו+", "ו", s)
    return re.sub(r"(?<=[א-ת])[הא](?=\s|$)", "", s)


def _no_article(s: str) -> str:
    """The definite article off every word: "החילזון האדום" = "חילזון אדום" (used only for long aliases:
    "הנהר" is "the river", not River)."""
    return re.sub(r"(?:(?<=\s)|^)ה(?=[א-ת]{3,})", "", s)


def _norm(s: str) -> str:
    s = fold_quotes(s.lower())
    # gershayim inside a Hebrew word stays ("צה"ל"); any other double quote is punctuation
    s = re.sub(r'(?<![א-ת])"|"(?![א-ת])', " ", s)
    s = re.sub(r"[^\w֐-׿'\" ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# aliases.json ships with the knowledge base release: its few bad entries are corrected here, in code.
# Common Hebrew words and generic nouns an alias must never be ("מאי" is May, "פסל" any statue, "השף" any chef):
ALIAS_DROP = {
    "בין", "אלי", "אליי", "מאי", "פי", "סר", "מקס", "אוק", "פיל", "הפיל",
    "נהר", "הנהר", "פסל", "סדן", "צור", "הצור", "השף", "שף", "רוח רפאים", "טוויטר", "טיק טוק", "שוער", "ליצן",
    "תיבת אוצר",
    "מיין",        # Myen's alias is the verb "to sort" ("למיין את האינבנטורי" made a Myen card, and voice "ל-Myen")
}
# alias -> entity key, over aliases.json (keys and names as in the KB's index.json)
ALIAS_SET = {
    "פטרייה רקובה": "monster/62",        # Rotten Mushroom (aliases.json gave this spelling to Rotten Mushmom)
    "לטי": "monster/1011",               # Leatty (aliases.json: Dark Leatty, which keeps "דארק ליטי")
    "ליטי": "monster/1011",
    "ג'וניור סנטינל": "monster/1001",     # Jr. Sentinel, not the Maple Island "Tutorial Jr. Sentinel"
    # the KB has "Jr. Boogie 1" and "Jr. Boogie 2" (the same stats and maps), no plain "Jr. Boogie"
    "jr boogie": "monster/33", "ג'וניור בוגי": "monster/33",
}
# NPCs of the KB named like an everyday English word: an answer saying "Max HP" or "River" at a sentence start
# names no NPC (the AI lists the NPCs it means in its META entities, those still get a card)
COMMON_WORD_NPCS = {"Max", "River", "Anvil", "Oak", "Jack", "Pan", "Chef", "Statue", "Flint", "Rain", "Exit", "Silver"}
NO_LOOSE_UNDER = 5    # Hebrew letters an alias needs for its spelling-tolerant form ("פיה" -> "פי" is no name)
# the part of a name that marks one variant of an entity: "Nella (KPQ 1st Stage)", "Forgotten Hollow Instance 080003500"
_VARIANT = re.compile(r"\s*\(.*?\)|\s+Instance \d+$")
PREFIX_FROM = 4        # Hebrew letters a name needs before a glued prefix counts ("לאן" is not ל + "אן")
_PREFIX = "[בלמהושכ]{1,2}"
DROPS_MARK = ("drops.tsv lists only monsters the KB confirms are in the game (availability.py), with a source column\n"
              "and the players' votes on community drops\n"
              "rewards.tsv lists the item rewards of the quests the KB confirms are in the game\n")
NAMES_TABLE = "names.tsv"     # key, category, name, type: one line per entity, for the AI to grep (see ensure_drop_table)
REWARDS_TABLE = "rewards.tsv"  # quest -> item rewards, one line per reward (see ensure_drop_table)
COMMUNITY_FILE = "community.json"     # players' drop and mesos reports per monster (tools/scrape_community.py)
# a community drop is shown when more players confirmed it than denied it (score = up - down); one with a single
# vote is shown marked "single report" (tools/kb_release.py repeats the rule for the patch notes)
COMMUNITY_MIN_SCORE = 1


class KnowledgeBase:
    def __init__(self, root: Path | None = None):
        self.root = root or kb_dir()
        self.entities: dict[str, dict] = {}
        idx = self.root / "index.json"
        if idx.exists():
            for e in json.loads(idx.read_text(encoding="utf-8")):
                self.entities[e["key"]] = e
        self.aliases: dict[str, str] = {}   # normalized alias -> key
        alias_file = self.root / "aliases.json"
        if alias_file.exists():
            shared: dict[str, list[str]] = {}
            for key, names in json.loads(alias_file.read_text(encoding="utf-8")).items():
                for n in names:
                    if _norm(n) not in ALIAS_DROP and key in self.entities:
                        shared.setdefault(_norm(n), [])
                        if key not in shared[_norm(n)]:
                            shared[_norm(n)].append(key)
            for alias, keys in shared.items():
                key = keys[0] if len(keys) == 1 else self._base_entity(keys)
                if key:
                    self.aliases[alias] = key
        self.aliases.update({_norm(a): k for a, k in ALIAS_SET.items() if k in self.entities})
        # names with ", " ": " or "[ ]" ("Tree Dungeon, Monkey Forest I") stay one block in a Hebrew answer
        bidi.set_names(e.get("name", "") for e in self.entities.values())

    def _base_entity(self, keys: list[str]) -> str | None:
        """The one entity an alias several entities share means: the plain one ("Forgotten Hollow", not
        "Forgotten Hollow Instance 080003500"; "Zelya", not "Zelya (Free Market)"). aliases.json gives some
        Hebrew names to every variant, and the last one used to win (an empty instance arena, a PQ stage NPC).
        Variants alone ("VIP Cab (Ellinia)", "VIP Cab (Sleepywood)") mean their plain name's entity when the KB
        has it; two plain names ("Pason", "Pison") are a real tie: no alias then, rather than a wrong card."""
        def rank(k: str) -> tuple[bool, int]:
            name = self.entities[k].get("name", "")
            return bool(_VARIANT.search(name)), len(name)
        ranked = sorted(keys, key=rank)
        if rank(ranked[0]) != rank(ranked[1]):
            return ranked[0]
        bases = {_VARIANT.sub("", self.entities[k].get("name", "")).strip() for k in keys}
        if len(bases) == 1:
            base, cat = bases.pop(), keys[0].partition("/")[0]
            return next((k for k, e in self.entities.items()
                         if e.get("name") == base and k.startswith(cat + "/")), None)
        return None

    # ------------------------------------------------------------ basic access

    def get(self, key: str) -> dict | None:
        return self.entities.get(key)

    def image_path(self, key: str) -> Path | None:
        e = self.get(key)
        if e and e.get("image"):
            p = self.root / e["image"]
            return p if p.exists() else None
        if e and e["category"] == "quest":
            # a quest shows the NPC who gives it
            giver = str((e.get("props") or {}).get("NPC") or "").lower()
            npc = self._npc_by_name.get(giver) or self._npc_by_name.get(re.sub(r"\s*\(.*?\)", "", giver))
            if npc and self.image_path(npc):
                return self.image_path(npc)
        if e and e["category"] == "class":
            # 3rd jobs have no picture: use the 2nd job they come from
            second = CLASS_PICTURE_FALLBACK.get(key.partition("/")[2])
            if second and self.get(f"class/{second}"):
                return self.image_path(f"class/{second}")
        return None

    def picture(self, key: str) -> Path | None:
        """Always a picture for a card: the entity's own, a related one, or its category icon."""
        own = self.image_path(key)
        if own:
            return own
        cat = key.partition("/")[0]
        fb = FALLBACK_DIR / f"{cat}.png"
        return fb if fb.exists() else (FALLBACK_DIR / "default.png")

    @cached_property
    def _npc_by_name(self) -> dict[str, str]:
        out = {}
        for k, e in self.entities.items():
            if e["category"] == "npc":
                n = e["name"].lower()
                out.setdefault(n, k)
                out.setdefault(re.sub(r"\s*\(.*?\)", "", n), k)
        return out

    def npc_key(self, name: str) -> str | None:
        """The NPC page for a name as quests write it ("Arwen the Fairy", "Jake (Subway)")."""
        n = (name or "").strip().lower()
        return self._npc_by_name.get(n) or self._npc_by_name.get(re.sub(r"\s*\(.*?\)", "", n)) if n else None

    def all_maps(self, key: str) -> list[str]:
        """Every map cell of a monster page's "Map Locations" table ("Snail Hunting Ground I Maple Road")."""
        return self._top_maps(key, 999)

    def page(self, key: str) -> str:
        e = self.get(key)
        if not e:
            return ""
        cat, _, slug = key.partition("/")
        p = self.root / "pages" / cat / f"{slug}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def page_body(self, key: str, limit: int = 2500) -> str:
        text = self.page(key)
        if text.startswith("---"):
            end = text.find("\n---", 3)
            text = text[end + 4:] if end > 0 else text
        return text.strip()[:limit]

    # ------------------------------------------------------------ name lookup

    @cached_property
    def _names(self) -> list[tuple[str, str, bool]]:
        """(normalized name, key, loose) sorted longest-first, so 'Red Snail' wins over 'Snail'.

        loose=True is a long Hebrew alias's spelling-tolerant form, looked up in the question's loose copies;
        a loose form two entities share, or that is another entity's exact name, is dropped (ambiguous)."""
        pairs = []
        for key, e in self.entities.items():
            n = _norm(e["name"])
            if len(n) >= 3:
                pairs.append((n, key, False))
        pairs += [(a, k, False) for a, k in self.aliases.items() if len(a) >= 2]
        exact = {n: k for n, k, _ in pairs}
        loose: dict[str, set[str]] = {}
        for a, k in self.aliases.items():
            if HEBREW.search(a) and _heb_letters(a) >= NO_LOOSE_UNDER:
                form = _heb_loose(a)
                if _heb_letters(form) >= 3 and form not in ALIAS_DROP:
                    loose.setdefault(form, set()).add(k)
        pairs += [(f, next(iter(ks)), True) for f, ks in loose.items()
                  if len(ks) == 1 and exact.get(f, next(iter(ks))) in ks]
        # a dropped alias names nothing, and neither does a shorter name inside it: key "" takes its words
        # ("טיק טוק" is TikTok, and its "טיק" answered with Tick's stats)
        pairs += [(d, "", False) for d in ALIAS_DROP if HEBREW.search(d)]
        return sorted(pairs, key=lambda p: -len(p[0]))

    def _occurrence(self, hay: str, name: str, key: str, taken: list[tuple[int, int]]) -> tuple[int, int] | None:
        """The first place `name` stands in `hay` (" word word ... ") outside the spans already taken, as a
        (first word, last word + 1) range. Hebrew prefixes glue on to a long enough name ("לחילזון", "בהנסיס"),
        and a monster's name may be plural ("fire boars", "תמנונים")."""
        if name not in hay:
            return None          # cheap: most names aren't in the question at all
        heb = bool(HEBREW.search(name))
        pre = f"(?:{_PREFIX})?" if heb and _heb_letters(name) >= PREFIX_FROM else ""
        plural = ""
        if key.startswith("monster/"):
            plural = "(?:ימ|ות|ים)?" if heb else "(?:e?s)?"
        for m in re.finditer(f"(?<= ){pre}{re.escape(name)}{plural}(?= )", hay):
            w0 = hay.count(" ", 0, m.start()) - 1
            span = (w0, w0 + name.count(" ") + 1)
            if not any(a < span[1] and span[0] < b for a, b in taken):
                return span
        return None

    def mention_spans(self, text: str, max_results: int = 5, answer: bool = False) -> list[tuple[str, int, int]]:
        """(key, first word, last word + 1) of the entities named in free text, by the words of _norm(text).

        The text is searched as typed, and (Hebrew) in two loose copies: spelling-tolerant, and that without the
        definite article. The copies keep the words in place, so a name matched in one copy can't be counted
        again from another, nor a shorter name inside it ("Red Snail" is not also "Snail").

        answer=True reads an AI answer, where game names are written exactly: English names must match their
        case ("your max HP" is not Max, "the river" not River), no loose Hebrew forms, no English aliases, and
        an NPC named like an everyday word (COMMON_WORD_NPCS) never counts."""
        norm = _norm(text)
        copies = [f" {norm} "]
        if HEBREW.search(norm) and not answer:
            loose = _heb_loose(norm)
            copies += [f" {loose} ", f" {_no_article(loose)} "]
        out: list[tuple[str, int, int]] = []
        taken: list[tuple[int, int]] = []
        for name, key, is_loose in self._names:
            for i, hay in enumerate(copies):
                if (i > 0) != is_loose:
                    continue      # exact names in the text as typed; loose forms in the loose copies
                span = self._occurrence(hay, name, key, taken)
                if span and not HEBREW.search(name) and (answer or key in self._common_npcs) \
                        and not self._written(text, key, name, answer):
                    span = None       # a question's "max level" is no Max either; "where is Max" is
                if span:
                    taken.append(span)
                    if key and key not in [k for k, _, _ in out]:
                        out.append((key, *span))
                    break
            if len(out) >= max_results:
                break
        return out

    @cached_property
    def _common_npcs(self) -> set[str]:
        return {k for k, e in self.entities.items() if e.get("category") == "npc" and e.get("name") in COMMON_WORD_NPCS}

    def _written(self, text: str, key: str, name: str, answer: bool = True) -> bool:
        """An English entity name written as the game writes it (its own case). In an answer an NPC named like an
        everyday word never counts (the AI lists the ones it means)."""
        e = self.get(key) or {}
        if _norm(e.get("name", "")) != name or answer and key in self._common_npcs:
            return False          # an English alias, or an everyday word
        words = re.escape(fold_quotes(e["name"])).replace(r"\ ", r"\s+")
        plural = "(?:e?s)?" if key.startswith("monster/") else ""
        return re.search(rf"(?<![\w]){words}{plural}(?![\w])", fold_quotes(text)) is not None

    def find_mentions(self, text: str, max_results: int = 5, answer: bool = False) -> list[str]:
        """Entities named in free text (English names, Hebrew aliases, transliterations); answer=True for an AI
        answer's text (exact names only, see mention_spans)."""
        return [k for k, _, _ in self.mention_spans(text, max_results, answer)]

    @cached_property
    def _resolve_pattern(self) -> re.Pattern | None:
        """One pattern for every Hebrew alias, longest first (compiling one per alias took ~140 ms a call).
        A glued prefix only before a long alias: "כאן" is not כ + "אן" (Anne), "מפיל" not מ + "פיל"."""
        heb = sorted({a for a, k in self.aliases.items() if HEBREW.search(a) and self.get(k)}
                     | {d for d in ALIAS_DROP if HEBREW.search(d)}, key=len, reverse=True)
        if not heb:
            return None
        long = [a for a in heb if _heb_letters(a) >= PREFIX_FROM]
        alt = "|".join(map(re.escape, heb))
        pre = rf"(?P<pre>[ובלמהשכ]{{1,2}}(?=(?:{'|'.join(map(re.escape, long))})(?![֐-׿])))?" if long else ""
        return re.compile(rf"(?<![֐-׿]){pre}(?P<name>{alt})(?![֐-׿])")

    def resolve_names(self, text: str) -> str:
        """Replace Hebrew aliases/transliterations with official English names (used after speech-to-text).
        The same safety as find_mentions: no dropped alias (ALIAS_DROP), a glued prefix only on a long name."""
        out = fold_quotes(text)
        if not HEBREW.search(out) or self._resolve_pattern is None:
            return out

        def name(m: re.Match) -> str:
            if m.group("name") not in self.aliases:
                return m.group(0)      # a dropped alias (in the pattern so no shorter alias inside it matches)
            # keep a glued Hebrew prefix: "ובלו סנייל" → "ו-Blue Snail"
            en = self.get(self.aliases[m.group("name")])["name"]
            pre = m.groupdict().get("pre")
            return f"{pre}-{en}" if pre else en

        return self._resolve_pattern.sub(name, out)

    # ------------------------------------------------------------ drops

    @cached_property
    def _item_by_name(self) -> dict[str, str]:
        out = {}
        for k, e in self.entities.items():
            if e["category"] == "item":
                out.setdefault(e["name"].strip().lower(), k)
        return out

    def drop_lists(self, key: str) -> dict[str, list[str]]:
        """A monster's drops by the list its page puts them in: {sources.COMMUNITY: [...], sources.MSEA: [...]}.

        The page's "Drops (MS Classic)" block holds two lists: "Community sourced", the drops players have seen
        in Classic themselves, then "MSEA reference drops", old MapleSEA's table that the KB calls historical
        reference. Read as one list, an MSEA drop was shown as if confirmed for Classic (and the other way).
        The community list is community.json's reports first (community_drops: best confirmed first), then the
        page's own; a drop on both lists is shown once, on the community one. (Players' reports don't replace the
        MSEA list: only the game's official data would, the owner's rule.)"""
        memo = self.__dict__.setdefault("_drop_lists", {})
        if key in memo:
            return memo[key]
        body = self.page(key)
        # (the site's script loads the community list into the page, so the scraped page text rarely has it)
        out: dict[str, list[str]] = {sources.COMMUNITY: [d["item"] for d in self.community_drops(key)],
                                     sources.MSEA: []}
        i = body.find("Drops (MS Classic)")
        if i >= 0:
            end = len(body)
            for marker in ("Associated Quests", "Map Locations", "Respawn Timer", "Change history"):
                j = body.find(marker, i)
                if 0 < j < end:
                    end = j
            block = body[i:end]
            m = re.search(r"^MSEA reference drops\s*$", block, re.M | re.I)
            parts = {sources.COMMUNITY: block[:m.start()] if m else block, sources.MSEA: block[m.end():] if m else ""}
            name = self.get(key)["name"]
            for src, text in parts.items():
                lines = text.split("\n")
                for n, line in enumerate(lines):
                    k = self._drop_item(line.strip().lower(), lines[n + 1].strip() if n + 1 < len(lines) else "", name)
                    if k and k not in out[src] and not (src == sources.MSEA and k in out[sources.COMMUNITY]):
                        out[src].append(k)
        memo[key] = out
        return out

    # ------------------------------------------------------------ community reports

    @cached_property
    def _community(self) -> dict[str, dict]:
        """community.json's monsters (monster key -> {"drops", "mesos", "fetched"}); {} without the file. A KB
        update brings a new file with a new KnowledgeBase object, so it is read once."""
        try:
            data = json.loads((self.root / COMMUNITY_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        monsters = data.get("monsters") if isinstance(data, dict) else None
        return monsters if isinstance(monsters, dict) else {}

    def _community_open(self, key: str) -> dict | None:
        """A monster's community entry, only for a monster the KB confirms is in the game."""
        entry = self._community.get(key)
        if not isinstance(entry, dict) or not self.get(key):
            return None
        from . import availability
        return entry if availability.of(self).monster_key_open(key) else None

    def community_drops(self, monster_key: str) -> list[dict]:
        """What players reported a monster drops, best confirmed first: [{"item", "up", "down", "score", "reqJob",
        "single"}]. A drop more players denied than confirmed (score under COMMUNITY_MIN_SCORE) isn't shown;
        "single": one player's report alone (shown as "single report"). Empty for a monster not in the game."""
        memo = self.__dict__.setdefault("_community_drops", {})
        if monster_key in memo:
            return memo[monster_key]
        out = []
        for d in (self._community_open(monster_key) or {}).get("drops") or []:
            if not isinstance(d, dict) or not self.get(str(d.get("item"))):
                continue
            up, down = int(d.get("up") or 0), int(d.get("down") or 0)
            score = int(d["score"]) if isinstance(d.get("score"), (int, float)) else up - down
            if score >= COMMUNITY_MIN_SCORE:
                out.append({"item": d["item"], "up": up, "down": down, "score": score, "reqJob": d.get("reqJob"),
                            "single": up + down <= 1})
        memo[monster_key] = sorted(out, key=lambda d: (-d["score"], -d["up"]))
        return memo[monster_key]

    def community_vote(self, monster_key: str, item_key: str) -> dict | None:
        """The players' votes on one community drop (as community_drops gives it), or None."""
        return next((d for d in self.community_drops(monster_key) if d["item"] == item_key), None)

    def community_mesos(self, monster_key: str) -> tuple[int, int, float | None, int] | None:
        """(min, max, drop chance %, reports): the mesos players reported for a monster (the reports' medians), or
        None (no reports, or a monster not in the game)."""
        m = (self._community_open(monster_key) or {}).get("mesos")
        if not isinstance(m, dict) or not isinstance(m.get("min"), (int, float)) or not m.get("count"):
            return None
        chance = m.get("chance")
        return (int(m["min"]), int(m.get("max") or m["min"]),
                float(chance) if isinstance(chance, (int, float)) else None, int(m["count"]))

    def mesos_per_kill(self, monster_key: str) -> float | None:
        """The mesos a kill brings on average by the players' reports: the range's middle times its drop chance."""
        m = self.community_mesos(monster_key)
        if not m:
            return None
        lo, hi, chance, _ = m
        return (lo + hi) / 2 * (100 if chance is None else chance) / 100

    def monster_drops(self, key: str) -> list[str]:
        """Item keys a monster drops, both lists: the community's Classic drops first, then the MSEA reference
        list (drop_lists / drop_source say which list each comes from)."""
        lists = self.drop_lists(key)
        return lists[sources.COMMUNITY] + lists[sources.MSEA]

    def drop_source(self, monster: str, item: str) -> str | None:
        """The list a monster's drop is on (sources.COMMUNITY or sources.MSEA), or None when it isn't."""
        for src, keys in self.drop_lists(monster).items():
            if item in keys:
                return src
        return None

    @cached_property
    def _monsters_by_name(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for k, e in self.entities.items():
            if e["category"] == "monster":
                out.setdefault(e["name"].strip().lower(), []).append(k)
        return out

    def monster_keys(self, name: str) -> list[str]:
        """Every monster entry of a name: the KB lists some twice ("Mano" in its map and a map-less copy)."""
        return self._monsters_by_name.get((name or "").strip().lower(), [])

    @cached_property
    def _items_by_name(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for k, e in self.entities.items():
            if e["category"] == "item":
                out.setdefault(e["name"].strip().lower(), []).append(k)
        return out

    def _drop_item(self, name: str, hint: str, monster: str) -> str | None:
        """The item a drop line names. Some items share a name (the Lv 40 earring "Blue Moon" and the Lv 50 Thief
        top "Blue Moon"): the line under the name tells them apart ("Lv 50 · Thief" for equipment, the type
        word, "Potion" or "Monster Drop", otherwise), and when it can't (two "Dark Shadow" tops, Lv 40 · Thief),
        the item page whose own "Dropped By" list names this monster. Still a tie: the first one, as before."""
        keys = self._items_by_name.get(name)
        if not keys or len(keys) == 1:
            return keys[0] if keys else None
        lv = re.match(r"Lv (\d+)\b", hint)
        if lv:
            fit = [k for k in keys if str(self.get(k).get("type") or "").startswith("Equip")
                   and str((self.get(k).get("props") or {}).get("Level Requirement", "")) == lv.group(1)]
        else:
            fit = [k for k in keys if hint and hint.lower() in str(self.get(k).get("type") or "").lower()]
        fit = fit or keys
        if len(fit) > 1:
            fit = [k for k in fit if self._dropped_by(k, monster)] or fit
        return fit[0]

    def _dropped_by(self, item: str, monster: str) -> bool:
        page = self.page(item)
        i = page.find("Dropped By")
        return i >= 0 and re.search(rf"^{re.escape(monster)}$", page[i:], re.M) is not None

    @cached_property
    def droppers(self) -> dict[str, list[str]]:
        """item key → monster keys that drop it, the ones players saw drop it first, then lowest level first: only
        monsters the KB confirms are in the game (availability.py), so no Orbis/El Nath or map-less monster is ever
        named as a source."""
        from . import availability
        open_ = availability.of(self)
        out: dict[str, list[str]] = {}
        for mkey, e in self.entities.items():
            if e["category"] == "monster" and open_.monster_key_open(mkey):
                for ikey in self.monster_drops(mkey):
                    out.setdefault(ikey, []).append(mkey)
        lvl = lambda k: (self.get(k).get("props") or {}).get("Level") or 999  # noqa: E731
        return {i: sorted(ms, key=lambda m: (self.drop_source(m, i) != sources.COMMUNITY, lvl(m)))
                for i, ms in out.items()}

    def drop_groups(self, item_keys: list[str], limit: int = 8) -> list[dict]:
        """Group items by the monsters that drop them: [{"monster": key, "items": [keys]}], the monsters players saw
        drop one of them first, then by monster level."""
        groups: dict[str, list[str]] = {}
        for i in item_keys:
            for m in self.droppers.get(i, []):
                groups.setdefault(m, [])
                if i not in groups[m]:
                    groups[m].append(i)
        lvl = lambda k: (self.get(k).get("props") or {}).get("Level") or 999  # noqa: E731
        seen = lambda m: any(self.drop_source(m, i) == sources.COMMUNITY for i in groups[m])  # noqa: E731
        ordered = sorted(groups, key=lambda m: (not seen(m), lvl(m)))[:limit]
        return [self.drop_group(m, groups[m]) for m in ordered]

    def drop_group(self, monster: str, items: list[str]) -> dict:
        """{"monster", "items", "sources": {item: its list}, "votes": {item: (up, down)}}: every drop shown says
        which list it comes from, and a community drop how many players confirmed and denied it."""
        return {"monster": monster, "items": items,
                "sources": {i: self.drop_source(monster, i) or sources.MSEA for i in items},
                "votes": {i: (v["up"], v["down"]) for i in items if (v := self.community_vote(monster, i))}}

    def ensure_drop_table(self) -> None:
        """Write drops.tsv next to index.json so Claude can grep 'which monsters drop X' in one step: only monsters
        the KB confirms are in the game, each drop with the list it is on. A table from before those rules (no
        drops.ingame mark beside it, or an older mark) is redone.
        names.tsv beside it is the index's names and keys, one entity per line, and rewards.tsv the quests'
        item rewards ("which quests give a cape" was 35 tool calls over the item pages, ~2-4 min)."""
        path = self.root / "drops.tsv"
        mark = self.root / "drops.ingame"
        names = self.root / NAMES_TABLE
        rewards = self.root / REWARDS_TABLE
        idx = self.root / "index.json"
        try:
            community = self.root / COMMUNITY_FILE      # (a newer community.json redoes the table too)
            if (path.exists() and mark.exists() and names.exists() and rewards.exists() and idx.exists()
                    and path.stat().st_mtime >= idx.stat().st_mtime
                    and (not community.exists() or path.stat().st_mtime >= community.stat().st_mtime)
                    and mark.read_text(encoding="utf-8") == DROPS_MARK):
                return
            mark.write_text(DROPS_MARK, encoding="utf-8")
            # index.json is one 1.3 MB line: Gemini's grep can't read a line that long ("bufio.Scanner: token too
            # long", answers took 30-140 s) and any other grep hit returns all of it. One entity per line instead.
            rows = ["key\tcategory\tname\ttype"]
            rows += [f"{k}\t{e.get('category', '')}\t{e.get('name', '')}\t{e.get('type') or ''}" for k, e in self.entities.items()]
            names.write_text("\n".join(rows), encoding="utf-8")
            # source: the list the drop is on, "MSEA" (reference) or "community" (players saw it in Classic);
            # votes: how many players confirmed / denied a community drop ("16 up 1 down"), empty on the MSEA list
            lines = ["monster\tmonster_level\tmonster_key\titem\titem_type\titem_key\tsource\tvotes"]
            for ikey, monsters in self.droppers.items():
                it = self.get(ikey)
                for m in monsters:
                    me = self.get(m)
                    lv = (me.get("props") or {}).get("Level", "")
                    v = self.community_vote(m, ikey)
                    lines.append(f"{me['name']}\t{lv}\t{m}\t{it['name']}\t{it.get('type') or ''}\t{ikey}\t"
                                 f"{self.drop_source(m, ikey) or sources.MSEA}\t"
                                 + (f"{v['up']} up {v['down']} down" if v else ""))
            path.write_text("\n".join(lines), encoding="utf-8")
            rewards.write_text("\n".join(self._reward_rows()), encoding="utf-8")
        except OSError:
            pass

    def _reward_rows(self) -> list[str]:
        """rewards.tsv: every item a quest in the game gives, one per line. kind: "sure" (always given), "pick one"
        (the player chooses one of the class's), "random" (one of the set, with its odds), "gender"."""
        from . import availability, quests
        open_ = availability.of(self)
        rows = ["quest\tquest_level\tquest_key\tarea\titem\tcount\titem_type\titem_key\tkind\tfor"]
        for k, e in self.entities.items():
            if e.get("category") != "quest" or not open_.quest_open(k):
                continue
            q = quests.quest(self, k)
            if not q:
                continue
            given = [(r, "sure", "") for r in q.rewards]
            given += [(r, "pick one", c) for c, rs in q.class_rewards.items() for r in rs]
            given += [(r, "random", c) for c, rs in q.random_rewards.items() for r in rs]
            given += [(r, "gender", g) for g, rs in q.gender_rewards.items() for r in rs]
            for r, kind, who in given:
                m = re.fullmatch(r"(.+?) x ([\d,]+)(?: \(([\d.]+)%\))?", r)
                name, n, odds = (m.group(1), m.group(2), m.group(3)) if m else (r, "", None)
                ikey = self._item_by_name.get(name.strip().lower(), "")
                it = (self.get(ikey) if ikey else None) or {}
                rows.append(f"{q.name}\t{q.opens_at()}\t{k}\t{q.area}\t{name}\t{n}\t{it.get('type') or ''}\t{ikey}\t"
                            f"{kind + (f' {odds}%' if odds else '')}\t{who}")
        return rows

    def drops_digest(self, key: str) -> str:
        """A monster's drops and mesos for the AI, list by list, each said for what it is: a community drop with its
        players' votes, and "no community data" when players reported nothing."""
        lists = self.drop_lists(key)
        e = self.get(key)
        out = []
        for src, head in ((sources.COMMUNITY, "community-confirmed in Classic (players saw them drop; the votes are "
                                              "how many players confirmed / denied each)"),
                          (sources.MSEA, "MSEA reference list: old MapleSEA, not confirmed for Classic")):
            if lists[src]:
                names = ", ".join(f"{self.get(k)['name']} [{k}]{self._votes_note(key, k)}" for k in lists[src])
                out.append(f"Drops of {e['name']}, {head}; names and keys exactly as in the game: {names}")
        if not lists[sources.COMMUNITY]:
            out.append(f"Community drops of {e['name']}: no community data (no player reports)")
        out.append(self.mesos_digest(key))
        return "\n".join(out)

    def droppers_digest(self, item: str) -> str:
        """Who drops an item, by the players' reports (with votes), for the AI: an item page's own "Dropped By"
        list doesn't have them (the site loads them in the browser)."""
        name = (self.get(item) or {}).get("name", item)
        seen = [m for m in self.droppers.get(item, []) if self.drop_source(m, item) == sources.COMMUNITY]
        if not seen:
            return f"Community reports of monsters dropping {name}: no community data (no player reports)"
        rows = [f"{self.get(m)['name']} (Lv {(self.get(m).get('props') or {}).get('Level', '?')}) [{m}]"
                f"{self._votes_note(m, item)}" for m in seen[:12]]
        return f"Community reports of monsters dropping {name} (players saw it drop in Classic): {', '.join(rows)}"

    def _votes_note(self, monster: str, item: str) -> str:
        v = self.community_vote(monster, item)
        if not v:
            return ""
        return " (single report)" if v["single"] else f" ({v['up']} confirmed, {v['down']} denied)"

    def mesos_digest(self, key: str) -> str:
        """The mesos players reported for a monster, one line for the AI."""
        name = (self.get(key) or {}).get("name", key)
        m = self.community_mesos(key)
        if not m:
            return f"Mesos of {name}: no community data (no player reports)"
        lo, hi, chance, n = m
        odds = f", dropped on {chance:g}% of kills" if chance is not None else ""
        return (f"Mesos of {name} (community, median of {n} player report{'' if n == 1 else 's'}): {lo}-{hi} "
                f"per drop{odds}")

    # ------------------------------------------------------------ level digest

    @cached_property
    def _monsters(self) -> list[dict]:
        """The monsters the KB confirms are in the game, with the maps a player can go to (the AI's level digest).
        No tutorial/job-test/PQ copies ("Fairy 2", "Tutorial Jr. Sentinel", the Tools' training spots skip them
        too), and a boss the KB also lists as a map-less copy (King Slime, Mushmom's 800xxx entries) only once:
        those rows took the place of monsters a player can train on under the 40-row cap."""
        from . import availability, combat
        open_ = availability.of(self)
        rows = []
        for key, e in self.entities.items():
            if e["category"] != "monster" or combat.special_monster(e["name"]) or not open_.monster_key_open(key):
                continue
            p = e.get("props", {})
            lvl = p.get("Level")
            if not isinstance(lvl, (int, float)):
                continue
            mesos = self.community_mesos(key)
            rows.append({"key": key, "name": e["name"], "level": int(lvl), "hp": p.get("HP"),
                         "exp": p.get("EXP"), "mesos": f"{mesos[0]}-{mesos[1]}" if mesos else "-", "maps": [m for m in self.all_maps(key) if combat.reachable_map(self, m)][:3]})
        mapped = {r["name"] for r in rows if r["maps"]}
        seen: set[str] = set()
        out = []
        for r in sorted(rows, key=lambda r: (r["level"], not r["maps"])):
            if r["maps"] or (r["name"] not in mapped and r["name"] not in seen):
                out.append(r)
                seen.add(r["name"])
        return out

    def _top_maps(self, key: str, n: int = 3) -> list[str]:
        body = self.page(key)
        i = body.find("Map Locations")
        if i < 0:
            return []
        maps = []
        # table rows: "Map Region | Count | Share | Types | Mob Rate | Respawn"
        for line in body[i:].split("\n")[2:2 + n * 3]:
            if " | " not in line:
                break  # end of the table: the "Change history" table below it has numeric rows too ("HP | 7,560 | ...")
            cols = [c.strip() for c in line.split(" | ")]
            if len(cols) >= 3 and cols[1].isdigit():
                maps.append(cols[0])
            if len(maps) >= n:
                break
        return maps

    @cached_property
    def _regions(self) -> list[str]:
        """The regions the map pages name ("Location Maple Road / Maple Island" → "Maple Road"), longest first."""
        found = set()
        for p in (self.root / "pages" / "map").glob("*.md"):
            m = re.search(r"^Location (.+?) / ", p.read_text(encoding="utf-8"), re.M)
            if m:
                found.add(m.group(1).strip())
        return sorted(found, key=len, reverse=True)

    def map_label(self, raw: str) -> str:
        """A monster page's map cell glues the region to the map's name ("Snail Hunting Ground I Maple Road"):
        "Snail Hunting Ground I · Maple Road" when it ends with a known region, else as it is."""
        for region in self._regions:
            name = raw[:-len(region)].rstrip()
            if raw.endswith(" " + region) and name:
                return f"{name} · {region}"
        return raw

    def level_digest(self, level: int, below: int = 5, above: int = 8) -> str:
        rows = [r for r in self._monsters if level - below <= r["level"] <= level + above]
        if not rows:
            return ""
        lines = ["Monsters near the player's level (name | level | HP | EXP | mesos per drop by community reports, "
                 "- = no community data | top maps | key):"]
        for r in rows[:40]:
            lines.append(f"{r['name']} | {r['level']} | {r['hp']} | {r['exp']} | {r['mesos']} | "
                         f"{', '.join(r['maps'])} | {r['key']}")
        return "\n".join(lines)
