"""Local knowledge base: entity index, name/alias lookup and pre-retrieval for questions.

Pre-retrieval matters for speed: the app hands Claude the pages it will most
likely need (entities named in the question, monsters around the player's
level), so most answers need no tool round-trips at all.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import cached_property, lru_cache
from pathlib import Path

from . import bidi, sources, tables
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


@lru_cache(maxsize=32768)
def _name_norm(name: str) -> str:
    """_norm of a KB name: the name indexes normalize every name a few times over."""
    return _norm(name)


# aliases.json ships with the knowledge base release: its few bad entries are corrected here, in code.
# Common Hebrew words and generic nouns an alias must never be ("מאי" is May, "פסל" any statue, "השף" any chef):
ALIAS_DROP = {
    "בין", "אלי", "אליי", "מאי", "פי", "סר", "מקס", "אוק", "פיל", "הפיל",
    "נהר", "הנהר", "פסל", "סדן", "צור", "הצור", "השף", "שף", "רוח רפאים", "טוויטר", "טיק טוק", "שוער", "ליצן",
    "תיבת אוצר",
    "מיין",        # Myen's alias is the verb "to sort" ("למיין את האינבנטורי" made a Myen card, and voice "ל-Myen")
    # everyday words: "איפה אפשר להרוג פיה" (a fairy) gave Pia, "טיק" (a tick) Tick, "פול HP" Paul, "לוק" (a look)
    # Luke, "ברי לי" Bari, "אורה" (light) Aura, "יונה" (a dove) Yoona, "לין" (to sleep over) Lyn
    "פיה", "טיק", "פול", "לוק", "ברי", "אורה", "יונה", "לין",
}
# aliases that are also Israeli first names ("אלון חבר שלי" is a friend, not Oak): a question asking for the NPC
# ("איפה מאיה") still finds it, an AI answer and a dictated question never turn the name into the NPC
FIRST_NAME_ALIASES = {"אלון", "מאיה", "אלכס", "רנה"}
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
# Everyday Hebrew the app's texts no longer write but players do (_hebrew_words): the guides talk to the reader in
# the plural now (owner's rule), a player asks in the singular ("המוב שלפניך" is no Panic), and says לבל/גרינד
PLAYER_HEBREW = (
    "אתה שלך לך ממך אותך בך בשבילך עליך לפניך שלפניך בינך אליך איתך מולך אחריך לעצמך "
    "מכיר מעדיף מצפה מתכנן ממוקם מטיל ומטיל מגביל מתקזז קודמים גבוהים המאוחרים שמופיעים שהחזקת הסתיים "
    "חזור תביא תדליק תוציא תחליט תחליף תחפש תטיל תירשם תכבה תסקרל תעבור תעלה תפליג ותבנה ותמלא ותפעיל "
    "ותשווה ותתחיל לבל לבלים לבלינג הלבלינג שלבלי שלבלים גרינד בגרינד מגרינדים הושלם מתוכנן")
# the words after "Max" that make it a stat, not the NPC ("Max HP", "max level")
_STAT_WORDS = {"hp", "mp", "level", "lv", "lvl", "stat", "stats", "damage", "dmg", "exp", "str", "dex", "int", "luk"}
NO_LOOSE_UNDER = 5    # Hebrew letters an alias needs for its spelling-tolerant form ("פיה" -> "פי" is no name)
# the part of a name that marks one variant of an entity: "Nella (KPQ 1st Stage)", "Forgotten Hollow Instance 080003500"
_VARIANT = re.compile(r"\s*\(.*?\)|\s+Instance \d+$")
PREFIX_FROM = 4        # Hebrew letters a name needs before a glued prefix counts ("לאן" is not ל + "אן")
_PREFIX = "[בלמהושכ]{1,2}"
DROPS_MARK = tables.DROPS_MARK
NAMES_TABLE = "names.tsv"     # key, category, name, type: one line per entity, for the AI to grep (tables.py)
REWARDS_TABLE = "rewards.tsv"  # quest -> item rewards, one line per reward (tables.py)
COMMUNITY_FILE = "community.json"     # players' drop and mesos reports per monster (tools/scrape_community.py)
# a community drop is shown when more players confirmed it than denied it (score = up - down); one with a single
# vote is shown marked "single report" (tools/kb_release.py repeats the rule for the patch notes)
COMMUNITY_MIN_SCORE = 1


# --- the shorter forms a player writes a name in (a question only: an answer writes names exactly)
# the categories whose names have them (a quest's or a guide's name is a sentence, nobody shortens it)
SHORT_FORM_CATEGORIES = ("item", "npc", "map", "monster", "skill", "class")
SHORT_FROM = 4         # letters a one-word short form needs ("ilbi", "lith"; "jr" or "red" is no name)
# English words that are the first word of exactly one KB name: "work" is no Work Gloves, "summer" no Summer Store
# Permit. A word the KB's quest and guide pages write in lower case is an everyday word too (_prose_words), this
# list is the ones they happen not to write.
COMMON_LEAD_WORDS = {
    "aluminum", "amazon", "atmospheric", "band", "beetle", "biker", "blueberry", "bone", "bowling", "bygone",
    "camo", "camouflaged", "cardboard", "cargo", "cashier", "charged", "checkered", "cherub", "christmas",
    "collision", "composite", "construction", "copper", "cozy", "crescent", "crested", "crumbling", "cubic",
    "cyclist", "dances", "deadly", "deer", "destructive", "detective", "dilapidated", "disposed", "downstairs",
    "dried", "drumming", "eagle", "expanded", "faded", "fashionable", "fireman", "fish", "flash", "flipper",
    "frameless", "fried", "fusion", "gargoyle", "gentleman", "glowing", "granny", "grape", "grim", "gross", "guild",
    "haircutter", "halfmoon", "hardwood", "hired", "horny", "hotel", "hyper", "inkwell", "ivory", "janitor",
    "jeweled", "jousting", "keen", "kitty", "knuckle", "luminous", "lunar", "marine", "mechanical", "medical",
    "melting", "michael", "minor", "mortal", "nick", "olive", "ominous", "pale", "pansy", "phantom", "pointed",
    "poisonous", "pole", "precipice", "premium", "puffy", "rabbit", "reddened", "reef", "ribbon", "ribboned",
    "rolled", "rosy", "sandblasted", "scream", "sergeant", "sewing", "sharpness", "slash", "smelly", "smiley",
    "smithing", "snowman", "soap", "soft", "spool", "spring", "starred", "stiff", "strolling", "studded", "stuffed",
    "summer", "summoning", "sunflower", "sunrise", "swampy", "thieves", "tomato", "triangular", "triple",
    "tutorial", "ultra", "vanilla", "victoria", "vitamin", "warfare", "whoa", "wind", "woodcrafting", "woodsman",
    "fall", "life", "field", "hand", "speed", "spell", "solid", "recycled", "ripped", "rookie", "mountain",
    "ocean", "camping", "archer", "blazing", "cursed", "death", "flame", "ancient", "equip", "weighted", "wing",
    "maplestory", "platform", "mong", "fully", "door", "event", "monster", "magic", "dark", "golden", "steel",
    "work", "lucky", "mana", "three", "stone", "little", "luck", "lead", "over", "single", "pure", "fresh", "cheap",
    "broken", "space", "summon", "torn", "curse", "energy", "gift", "hero", "land", "right", "rock", "roll", "secret",
    "table", "water", "flower", "clock", "cross", "flying", "memory", "special", "power", "fire", "iron", "gold",
    "crimson", "luster",
    "zakum", "nemi",       # a boss the KB has no page of (only the Zakum Helmet), a game designer (not the Nemi Hat)
    # slang and people's names ("this game is hella fun" was Hella's Pendant, "my friend daniel" Daniel the Scholar;
    # "steely" stays: players call the knives so)
    "hella", "daniel", "esther", "zeta",
}
FIRST_JOBS = {"Warrior", "Magician", "Bowman", "Thief"}
_ROMAN_OR_NUMBER = re.compile(r"\s+(?:\d+|[IVX]+)$")


def _fold(s: str) -> str:
    """A normalized name without the apostrophes in its English words: "amazon's" = "amazons" (a Hebrew
    geresh, "ג'וניור", stays)."""
    return re.sub(r"(?<=[a-z0-9])'|'(?=[a-z])", "", s)


def _no_possessive(s: str) -> str:
    """ "amazon's judgement" -> "amazon judgement", "lupin's banana" -> "lupin banana"."""
    return re.sub(r"(?<=[a-z])'s(?= |$)", "", s)


# --- an English game name in Hebrew letters, as players transliterate it ("אילבי", "פאוור סטרייק", "צ'יף בנדיט")
# English sounds (longest spelling first) -> (consonant class, the Hebrew letters that write it); a class of "" is a
# letter Hebrew may leave out (h, w)
_EN_SOUNDS = [
    ("tch", "C", "(?:צ'|טש)"), ("sch", "S", "ש"), ("ch", "C", "(?:צ'|טש)"), ("sh", "S", "ש"), ("th", "T", "[תט]'?"),
    ("ph", "P", "פ"), ("ck", "K", "[קכ]"), ("kn", "N", "נ"), ("wr", "R", "ר"), ("gh", "", ""), ("qu", "K", "[קכ]ו?"),
    ("x", "KS", "[קכ]ס"), ("b", "B", "ב"), ("c", "K", "[קכ]"), ("d", "D", "ד"), ("f", "P", "פ"), ("g", "G", "ג"),
    ("j", "J", "(?:ג'|ז')"), ("k", "K", "[קכ]"), ("l", "L", "ל"), ("m", "M", "מ"), ("n", "N", "נ"), ("p", "P", "פ"),
    ("r", "R", "ר"), ("s", "S", "[סש]"), ("t", "T", "[טת]"), ("v", "B", "ב"), ("z", "Z", "ז"), ("h", "", "ה?"),
    ("w", "", "(?:וו|ו)?"),
]
_HE_SOUNDS = [("צ'", "C"), ("ג'", "J"), ("ז'", "J"), ("ת'", "T"), ("קס", "KS"), ("כס", "KS")] + \
    [(c, k) for k, cs in (("B", "ב"), ("G", "ג"), ("D", "ד"), ("Z", "ז"), ("K", "חכקך"), ("T", "טת"), ("L", "ל"),
                          ("M", "מם"), ("N", "נן"), ("S", "סש"), ("P", "פף"), ("C", "צץ"), ("R", "ר")) for c in cs]


def _en_sounds(word: str) -> list[tuple[str, str]]:
    """An English word as (consonant class, Hebrew pattern) parts; a vowel run is ("", its pattern). A vowel run must
    be written where Hebrew writes one: at the start (א), "o"/"u" inside (ו, or א for "lucky" = "לאקי"), "i"
    inside or at the end (י), a final "a" (ה/א); a lone "e" may go unwritten ("seven" = "סבן")."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(word)
    while i < n:
        run = re.match(r"[aeiouy]+", word[i:]) if not (word[i] == "y" and i == 0) else None
        if run:
            v = run.group(0)
            at_end = i + len(v) == n
            if set(v) & set("iy") or v == "ee":
                pat = "א?י{1,2}"                      # "ilbi" = "אילבי", "steely" = "סטילי"
            elif set(v) & set("ou"):
                pat = "(?:א?ו{1,2}|א)"                # "subi" = "סובי", "lucky" = "לאקי"
            elif at_end and v != "e":
                pat = "[הא]"                          # "mana" = "מאנה"
            else:
                pat = "[אה]?" if v == "a" else "[אי]?"     # "stab" = "סטאב", "seven" = "סבן"
            if i == 0:
                pat = f"א(?:{pat})?"                  # a word starts with a vowel letter: "אתנה", "אליקסיר"
            out.append(("", pat))
            i += len(v)
            continue
        if word[i] == "y":                     # "yeti": a consonant y
            out.append(("", "י"))
            i += 1
            continue
        for spelled, cls, pat in _EN_SOUNDS:
            if word.startswith(spelled, i):
                if spelled == "c" and word[i + 1:i + 2] in ("e", "i", "y"):
                    cls, pat = "S", "ס"
                if not (cls and out and out[-1][0] == cls):     # "ll", "ss": one letter
                    out.append((cls, pat))
                i += len(spelled)
                break
        else:
            return []                          # a digit or another letter: no transliteration
    return out


def _he_key(word: str) -> str:
    """A Hebrew word's consonants as _EN_SOUNDS classes ("פאוור" -> "PR"): the bucket _translits looks in."""
    w = word.translate(_FINALS)
    out, i = [], 0
    while i < len(w):
        for spelled, cls in _HE_SOUNDS:
            if w.startswith(spelled, i):
                if not out or out[-1] != cls:
                    out.append(cls)
                i += len(spelled)
                break
        else:
            i += 1                             # א ו י ה ע and anything else: no consonant
    return "".join(out)


class KnowledgeBase:
    def __init__(self, root: Path | None = None):
        self.root = root or kb_dir()
        self.entities: dict[str, dict] = {}
        idx = self.root / "index.json"
        # what was loaded, by content: the tables (tables.py) are never built into a folder that holds another KB
        self.index_hash = ""
        if idx.exists():
            raw = idx.read_bytes()
            self.index_hash = hashlib.sha1(raw).hexdigest()
            for e in json.loads(raw.decode("utf-8")):
                if isinstance(e.get("name"), str):
                    e["name"] = e["name"].strip()     # "Asking After Chun Ji " (the site's own spacing) missed exact lookups
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
        # "לאלינה" is the boat ride "To Ellinia" in aliases.json, and "how do I get to Ellinia" in a question: an
        # alias that is a glued Hebrew prefix and another entity's alias is that other entity, with the prefix
        for alias in [a for a in self.aliases if re.match(_PREFIX, a)]:
            for rest in {alias[1:], alias[2:] if re.match(f"{_PREFIX}$", alias[:2]) else ""}:
                if _heb_letters(rest) >= PREFIX_FROM and self.aliases.get(rest, self.aliases[alias]) != self.aliases[alias]:
                    del self.aliases[alias]
                    break
        self.aliases.update({_norm(a): k for a, k in ALIAS_SET.items() if k in self.entities})
        self._family_cats: dict[str, set[str]] = {}     # (filled by _short_forms)
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
        try:          # a page damaged on disk (an antivirus, a disk error) reads as what's left, never fails a question
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

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
            n = _name_norm(e["name"])
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

    @cached_property
    def _question_names(self) -> list[tuple[str, str, bool]]:
        """_names and, for a question, the shorter forms a player writes a name in (_short_forms), longest first;
        a name as the KB writes it before a short form of the same length."""
        return sorted(self._names + self._short_forms, key=lambda p: -len(p[0]))

    @cached_property
    def _first_key(self) -> dict[str, str]:
        """Normalized name -> the entity a name several entities share means (the first, as _names finds it)."""
        out: dict[str, str] = {}
        for key, e in self.entities.items():
            out.setdefault(_name_norm(e.get("name", "")), key)
        return out

    def _forms(self, name: str, cat: str) -> tuple[set[str], set[str]]:
        """(spellings, bases) of a KB name, normalized and without apostrophes (_fold).

        spellings: the same name typed loosely: "amazons judgement", "amazon judgement", "ticktock" (a hyphen
        left out). bases: the name without the part that marks one entity of a family, as players say it:
        "Arrow Bomb: Bow" -> "arrow bomb", "VIP Cab (Ellinia)" -> "vip cab", "The Pig Beach" -> "pig beach",
        "Ant Tunnel II" -> "ant tunnel", a class's "Hermit skills" -> "hermit" (_short_forms keeps a base only
        when it means one entity)."""
        def spell(raw: str) -> set[str]:
            n = _name_norm(raw)
            out = {_fold(n), _fold(_no_possessive(n))}
            if "-" in raw:
                out.add(_fold(_name_norm(raw.replace("-", ""))))
            return out

        raw_bases = {name}
        if cat == "class":
            raw_bases.add(re.sub(r"\s+skills$", "", name))
        for _ in range(3):              # "The Cave of Evil Eye I": the numeral, then "The"
            for b in list(raw_bases):
                for cut in (b.split(":")[0], b.split(" - ")[0], re.sub(r"\s*\(.*?\)", "", b),
                            _ROMAN_OR_NUMBER.sub("", b), re.sub(r"^The\s+", "", b)):
                    if cut.strip():
                        raw_bases.add(cut.strip())
        spellings = spell(name)
        bases = set().union(*(spell(b) for b in raw_bases if b != name)) - spellings if len(raw_bases) > 1 else set()
        return spellings - {_name_norm(name)}, bases

    @cached_property
    def _short_forms(self) -> list[tuple[str, str, bool]]:
        """(form, key, False): the short forms of _forms that mean one entity, and (form, "", False) for one that
        several entities share ("soul arrow": Soul Arrow: Bow or Soul Arrow: Crossbow), so it names nothing and
        no shorter name inside it counts either ("the cave of evil eye" is no Evil Eye).
        A form that is a KB name or alias itself is left to it; a base that starts a longer name of another entity
        is that family's ("return scroll": "Return Scroll - Nearest Town" or "Return Scroll to Henesys"), except a
        class's ("crusader" is the class, not the "Crusader T-Shirt"); a one-word base only a class's."""
        exact = {n for n, _, _ in self._names}
        no_the = lambda n: re.sub(r"^the ", "", n)  # noqa: E731
        named: dict[str, set[str]] = {}        # a KB name without its "the" -> the names
        for e in self.entities.values():
            named.setdefault(no_the(_fold(_name_norm(e.get("name", "")))), set()).add(_name_norm(e.get("name", "")))
        owners: dict[str, set[str]] = {}       # form -> the names it shortens
        kinds: dict[str, set[str]] = {}        # form -> "spelling" / "base" / "class"
        cats: dict[str, set[str]] = {}         # a name, folded -> the categories of the entities named so
        for key, e in self.entities.items():
            name, cat = e.get("name", ""), key.partition("/")[0]
            cats.setdefault(_fold(_name_norm(name)), set()).add(cat)
            if cat not in SHORT_FORM_CATEGORIES or key in self._common_npcs or not name:
                continue
            own = _name_norm(name)
            spellings, bases = self._forms(name, cat)
            for form, kind in [(f, "spelling") for f in spellings] + [(b, "class" if cat == "class" else "base")
                                                                     for b in bases]:
                if len(form) < 3 or form in exact or named.get(no_the(form), {own}) - {own}:
                    continue      # a name itself: "the land of wild boar" is Land of Wild Boar, not its II
                if kind == "base" and " " not in form:
                    continue      # one word: "The Judgement" is no "judgement", "Vaulter 2000" no "vaulter"
                owners.setdefault(form, set()).add(own)
                kinds.setdefault(form, set()).add(kind)
        starting: dict[str, set[str]] = {}     # the first words of a name -> the names that start so
        for n in cats:
            words = n.split()
            for i in range(1, len(words)):
                starting.setdefault(" ".join(words[:i]), set()).add(n)
        out = []
        self._family_cats = {}                 # a form that names nothing -> the categories of its family
        for form, names in owners.items():
            own = _fold(next(iter(names)))
            longer = starting.get(form, set()) - {own} if kinds[form] == {"base"} else set()
            if len(names) > 1 or longer:       # shared, or the start of another entity's name too
                out.append((form, "", False))
                self._family_cats[form] = set().union(*(cats.get(_fold(n), set()) for n in names | longer))
                continue
            out.append((form, self._first_key[next(iter(names))], False))
        # the jobs' Hebrew names (jobs.JOB_HE: "הרמיט", "צ'יף בנדיט"), but the 1st jobs' ("קשת" is any bow, "גנב" any
        # thief): the class pages are named "Hermit skills", and the alias list has none of them
        from .jobs import JOB_HE
        classes = {re.sub(r" skills$", "", _name_norm(e.get("name", ""))): k for k, e in self.entities.items()
                   if k.startswith("class/")}
        out += [(_norm(he), classes[_norm(job)], False) for job, he in JOB_HE.items()
                if job not in FIRST_JOBS and _norm(job) in classes and _norm(he) not in exact]
        return out

    @cached_property
    def _hebrew_words(self) -> set[str]:
        """Everyday Hebrew: the words of the app's own Hebrew texts (the UI, the translated guides and news, ~6,500
        words; they write game names in English). A transliteration is never one of them ("אורך" is "length", not
        Eurek; "שעות" "hours", not Shout; "לפניך" "in front of you", not Panic). Read once (~90 ms)."""
        from .i18n import STRINGS
        texts = [str(v.get("he", "")) for v in STRINGS.values() if isinstance(v, dict)]
        texts.append(PLAYER_HEBREW)

        def walk(x) -> None:
            if isinstance(x, str):
                texts.append(x)
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        for p in [*(ASSETS / "guides" / "he").glob("*.json"), ASSETS / "news" / "he.json"]:
            try:
                walk(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return {w.translate(_FINALS) for w in re.findall(r"[א-ת][א-ת'\"]*", fold_quotes("\n".join(texts)))}

    @cached_property
    def _prose_words(self) -> set[str]:
        """The words the KB's quest and guide pages write in lower case inside a sentence: everyday English, so no
        short name ("work" is no Work Gloves). Read once, the first time a question needs it (~60 ms)."""
        words: set[str] = set()
        for cat in ("quest", "guide"):
            for p in (self.root / "pages" / cat).glob("*.md"):
                try:
                    words.update(re.findall(r"(?<=[a-z,;] )([a-z]{4,})\b", p.read_text(encoding="utf-8")))
                except OSError:
                    pass
        return words

    def _common_word(self, word: str) -> bool:
        stem = word[:-1] if word.endswith("s") else word
        return bool({word, stem, word + "s"} & (COMMON_LEAD_WORDS | self._prose_words))

    @cached_property
    def _lead_words(self) -> dict[str, str]:
        """A name's first word alone -> its entity, when the word starts no other KB name: "ilbi" is Ilbi Throwing
        Stars, "steely" Steely Throwing Knives. When it starts several names that all start with one entity's whole
        name, that entity: "athena" (Athena Pierce, Athena Pierce's Letter), "kerning" (Kerning City and its shops).
        A word under SHORT_FROM letters, a word that is a name itself, or an everyday word (_common_word) never."""
        names: dict[str, str] = {}             # folded name without possessives -> key
        exact = {n for n, _, _ in self._names} | {f for f, _, _ in self._short_forms}
        for key, e in self.entities.items():
            if key.partition("/")[0] in SHORT_FORM_CATEGORIES and key not in self._common_npcs and e.get("name"):
                names.setdefault(_fold(_no_possessive(_name_norm(e["name"]))), key)
        families: dict[str, list[list[str]]] = {}
        for n in names:
            words = n.split()
            if len(words) > 1 and len(words[0]) >= SHORT_FROM and re.fullmatch("[a-z]+", words[0]):
                families.setdefault(words[0], []).append(words)
        out = {}
        for word, fam in families.items():
            if word in exact:
                continue
            common = []
            for col in zip(*fam):
                if len(set(col)) > 1:
                    break
                common.append(col[0])
            head = " ".join(common)
            if (len(fam) == 1 or head in names) and not self._common_word(word):
                out[word] = names[head] if len(fam) > 1 else names[" ".join(fam[0])]
        return out

    def _occurrence(self, hay: str, name: str, key: str, taken: list[tuple[int, int]]) -> tuple[int, int] | None:
        """The first place `name` stands in `hay` (" word word ... ") outside the spans already taken, as a
        (first word, last word + 1) range. Hebrew prefixes glue on to a long enough name ("לחילזון", "בהנסיס"),
        and a monster's name may be plural ("fire boars", "תמנונים"), an item's English name too ("red potions")."""
        heb = bool(HEBREW.search(name))
        # a monster's Hebrew plural turns its final letter plain: "גדם" -> "גדמים" (Stumps)
        stem = name[:-1] + name[-1].translate(_FINALS) if heb and key.startswith("monster/") \
            and name[-1] in "ךםןףץ" else ""
        if name not in hay and not (stem and stem in hay):
            return None          # cheap: most names aren't in the question at all
        pre = f"(?:{_PREFIX})?" if heb and _heb_letters(name) >= PREFIX_FROM else ""
        plural = ""
        if key.startswith("monster/"):
            plural = "(?:ימ|ות|ים)?" if heb else "(?:e?s)?"
        elif key.startswith("item/") and not heb and " " in name:
            plural = "(?:e?s)?"      # "red potions", "red snail shells" ("swords" is any sword, not the Sword)
        body = f"(?:{re.escape(name)}{plural}|{re.escape(stem)}(?:ים|ות))" if stem else f"{re.escape(name)}{plural}"
        for m in re.finditer(f"(?<= ){pre}{body}(?= )", hay):
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
        copies = [(f" {norm} ", False)]
        if not answer and _fold(norm) != norm:
            copies.append((f" {_fold(norm)} ", False))     # "amazons judgement", "lupins banana" (_short_forms)
        if HEBREW.search(norm) and not answer:
            loose = _heb_loose(norm)
            copies += [(f" {loose} ", True), (f" {_no_article(loose)} ", True)]
        out: list[tuple[str, int, int]] = []
        taken: list[tuple[int, int]] = []
        # a family's shared form ("the cave of evil eye", "snail hunting ground"): its words name no entity of
        # another category ("Evil Eye", "Snail"), and do the family's own kind still ("henesys hunting ground" is
        # in Henesys)
        family: list[tuple[int, int, set[str]]] = []
        names = self._names if answer else self._question_names
        blocks = {} if answer else self._family_cats
        for name, key, is_loose in names:
            if answer and name in FIRST_NAME_ALIASES:
                continue
            here = taken + [(a, b) for a, b, cats in family if key.partition("/")[0] not in cats]
            for hay, loose_copy in copies:
                if loose_copy != is_loose:
                    continue      # exact names in the text as typed; loose forms in the loose copies
                span = self._occurrence(hay, name, key, here)
                if span and not key and name in blocks:
                    family.append((*span, blocks[name]))
                    break
                if span and not HEBREW.search(name) and (answer or key in self._common_words) \
                        and not self._written(text, key, name, answer):
                    span = None       # a question's "max level" is no Max either; "where is Max" is
                if span and not answer and name.startswith("to ") and name[3:] in self._first_key \
                        and not self._written(text, key, name, answer):
                    span = None       # "how do i get to ellinia" is the town, not the boat map "To Ellinia"
                if span:
                    taken.append(span)
                    if key and key not in [k for k, _, _ in out]:
                        out.append((key, *span))
                    break
            if len(out) >= max_results:
                break
        taken += [(a, b) for a, b, _ in family]      # the words left over: no family's words
        if not answer and len(out) < max_results:
            out += self._lead_spans(f" {_fold(norm)} ", taken, [k for k, _, _ in out], max_results - len(out))
        if not answer and len(out) < max_results and HEBREW.search(norm):
            taken += [(s, e) for _, s, e in out]
            out += self._translit_spans(norm, taken, [k for k, _, _ in out], max_results - len(out))
        return out

    @cached_property
    def _translits(self) -> dict[str, list[tuple[int, list[str], list[re.Pattern], str]]]:
        """English names a Hebrew question writes in Hebrew letters ("אילבי", "לאקי סבן", "הרמיט"), for the names no
        alias has: by the first word's consonants (_he_key) -> [(words, their consonants, their patterns, key)].

        Only the names a player transliterates: a skill's or class's name and short form, an item's name of two words
        or more, and a name's first word alone (_lead_words: "סובי"). A one-word item ("לימון" is a lemon in Hebrew
        too) or a name two entities share never."""
        forms: dict[str, set[str]] = {}
        for key, e in self.entities.items():
            cat, name = key.partition("/")[0], _fold(_no_possessive(_name_norm(e.get("name", ""))))
            if cat in ("skill", "class") and (" " in name or len(name) >= 5) or cat == "item" and " " in name:
                forms.setdefault(name, set()).add(self._first_key.get(_name_norm(e["name"]), key))
        for form, key, _ in self._short_forms:
            if key and key.partition("/")[0] in ("skill", "class"):
                forms.setdefault(form, set()).add(key)
        for word, key in self._lead_words.items():
            forms.setdefault(word, set()).add(key)
        compiled: dict[str, tuple[str, re.Pattern] | None] = {}
        out: dict[str, list] = {}
        for form, keys in forms.items():
            if len({(self.get(k) or {}).get("name") for k in keys}) > 1:
                continue                       # two entities' name: no guess
            parts = []
            for w in form.split():
                if w not in compiled:
                    sounds = _en_sounds(w)
                    cls = re.sub(r"(.)\1+", r"\1", "".join(c for c, _ in sounds))
                    compiled[w] = (cls, re.compile("".join(p for _, p in sounds))) if sounds and cls else None
                parts.append(compiled[w])
            if parts and all(parts):
                out.setdefault(parts[0][0], []).append((len(parts), [c for c, _ in parts], [p for _, p in parts],
                                                       next(iter(keys))))
        for entries in out.values():
            entries.sort(key=lambda e: -e[0])  # the longest name first
        return out

    def _translit_spans(self, hay: str, taken: list[tuple[int, int]], found: list[str], room: int) -> list[tuple[str, int, int]]:
        """The Hebrew words left over that are an English name in Hebrew letters (_translits): "מה ההבדל בין אילבי
        לסובי". Every word of three letters or more and none an everyday Hebrew word (_hebrew_words: "את" is no
        "hat", "לפניך" no Panic); a glued prefix only before PREFIX_FROM letters ("לסובי"); one word alone of
        SHORT_FROM letters and two consonants at least ("מהר" is "fast", not Marr's Forest)."""
        words = [w.translate(_FINALS) for w in hay.split()]
        out: list[tuple[str, int, int]] = []
        free = lambda i: not any(a <= i < b for a, b in taken + [(s, e) for _, s, e in out])  # noqa: E731
        plain = lambda w: _heb_letters(w) >= 3 and w not in self._hebrew_words  # noqa: E731
        for i, word in enumerate(words):
            if len(out) >= room or not free(i) or not plain(word):
                continue
            starts = [word] + [word[n:] for n in (1, 2) if re.fullmatch(_PREFIX, word[:n])
                               and _heb_letters(word[n:]) >= PREFIX_FROM and plain(word[n:])]
            hits: dict[str, int] = {}          # key -> the words its name takes; the longest names only
            for first in starts:
                for n, keys, pats, key in self._translits.get(_he_key(first), []):
                    rest = words[i + 1:i + n]
                    if n == 1 and (_heb_letters(first) < SHORT_FROM or len(keys[0]) < 2) or hits and n < max(hits.values()):
                        continue
                    if len(rest) == n - 1 and all(free(i + 1 + j) and plain(w) for j, w in enumerate(rest)) \
                            and pats[0].fullmatch(first) \
                            and all(_he_key(w) == k and p.fullmatch(w) for w, k, p in zip(rest, keys[1:], pats[1:])):
                        hits[key] = n
            longest = [k for k, n in hits.items() if n == max(hits.values(), default=0)]
            if len(longest) == 1 and longest[0] not in found and longest[0] not in [k for k, _, _ in out]:
                out.append((longest[0], i, i + hits[longest[0]]))    # two names that fit: no guess
        return out

    def _lead_spans(self, hay: str, taken: list[tuple[int, int]], found: list[str], room: int) -> list[tuple[str, int, int]]:
        """The words left over that are a name's first word alone (_lead_words): "Ilbi vs Subi", "where is athena".
        A plural too: "ilbis". Not before a place's last word: "Florina Road" is no Florina Beach."""
        out = []
        words = hay.split()
        for i, word in enumerate(words):
            if len(out) >= room or any(a <= i < b for a, b in taken) or words[i + 1:i + 2] and words[i + 1] in self._place_words:
                continue
            key = self._lead_words.get(word) or (self._lead_words.get(word[:-1]) if word.endswith("s") else None)
            if key and key not in found and key not in [k for k, _, _ in out]:
                out.append((key, i, i + 1))
        return out

    @cached_property
    def _place_words(self) -> set[str]:
        """The last words of the KB's map names ("beach", "forest", "harbor") and of any place."""
        return {_name_norm(e["name"]).split()[-1] for e in self.entities.values()
                if e.get("category") == "map" and _name_norm(e.get("name", ""))} | {"island", "road", "street", "town", "city", "village", "map"}

    @cached_property
    def _common_npcs(self) -> set[str]:
        return {k for k, e in self.entities.items() if e.get("category") == "npc" and e.get("name") in COMMON_WORD_NPCS}

    @cached_property
    def _common_words(self) -> set[str]:
        """The NPCs named like an everyday word, and the items named for a whole kind of gear ("Sword", "Spear": a
        word of an equip type): a question names them only when written as a name (_written)."""
        kinds = {w.lower() for e in self.entities.values() if e.get("category") == "item"
                 and str(e.get("type") or "").startswith("Equip") for w in re.findall(r"[A-Za-z]+", str(e["type"]))}
        return self._common_npcs | {k for k, e in self.entities.items() if e.get("category") == "item"
                                    and str(e.get("name") or "").strip().lower() in kinds}

    def _written(self, text: str, key: str, name: str, answer: bool = True) -> bool:
        """An English entity name written as the game writes it (its own case). In an answer an NPC named like an
        everyday word never counts (the AI lists the ones it means)."""
        e = self.get(key) or {}
        if _norm(e.get("name", "")) != name or answer and key in self._common_npcs:
            return False          # an English alias, or an everyday word
        words = re.escape(fold_quotes(e["name"])).replace(r"\ ", r"\s+")
        plural = "(?:e?s)?" if key.startswith("monster/") else ""
        text = fold_quotes(text)
        found = list(re.finditer(rf"(?<![\w]){words}{plural}(?![\w])", text))
        if answer or key not in self._common_words:
            return bool(found)
        # a question: any sentence starts with a capital ("Max HP is important", "Rain or shine", "Exit the map"),
        # and "Max HP" / "Max level" is no NPC; "where is Max", "talk to Max" still are
        for m in found:
            before, after = text[:m.start()].rstrip(), text[m.end():].split(None, 1)
            if before and before[-1] not in ".!?:;\n\"(" and before.split()[-1].lower() not in ("the", "a", "an") \
                    and not (after and after[0].lower().strip(".,!?") in _STAT_WORDS):
                return True
        return False

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
            alias = m.group("name")
            # a short alias that is an everyday word or a first name: the player's own words stay as said
            # ("אלון חבר שלי משחק איתי" became "Oak חבר שלי")
            if alias in FIRST_NAME_ALIASES or _heb_letters(alias) <= 4 and not m.groupdict().get("pre") \
                    and alias.translate(_FINALS) in self._hebrew_words:
                return m.group(0)
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
                again: dict[str, int] = {}       # a name the list repeats: its next item of that name
                for n, line in enumerate(lines):
                    item = line.strip().lower()
                    k = self._drop_item(item, lines[n + 1].strip() if n + 1 < len(lines) else "", name,
                                        again.get(item, 0))
                    again[item] = again.get(item, 0) + 1
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

    def _drop_item(self, name: str, hint: str, monster: str, again: int = 0) -> str | None:
        """The item a drop line names. Some items share a name (the Lv 40 earring "Blue Moon" and the Lv 50 Thief
        top "Blue Moon"): the line under the name tells them apart ("Lv 50 · Thief" for equipment, the type
        word, "Potion" or "Monster Drop", otherwise), and when it can't (two "Dark Shadow" tops, Lv 40 · Thief),
        the item page whose own "Dropped By" list names this monster. Still a tie: the first one, as before, and
        for the name's next line in the same list (`again`) the next one: a list naming "Green Bennis Chainmail"
        twice drops the male and the female one."""
        keys = self._items_by_name.get(name)
        if not keys or len(keys) == 1:
            return keys[0] if keys and not again else None
        lv = re.match(r"Lv (\d+)\b", hint)
        if lv:
            fit = [k for k in keys if str(self.get(k).get("type") or "").startswith("Equip")
                   and str((self.get(k).get("props") or {}).get("Level Requirement", "")) == lv.group(1)]
        else:
            fit = [k for k in keys if hint and hint.lower() in str(self.get(k).get("type") or "").lower()]
        fit = fit or keys
        if len(fit) > 1:
            fit = [k for k in fit if self._dropped_by(k, monster)] or fit
        if again:
            rest = [k for k in keys if k != fit[0]]
            return rest[again - 1] if again - 1 < len(rest) else None
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

    def warm(self) -> None:
        """Build what the first question would (the mention matcher's names and Hebrew words, what is in the game):
        ~0.6 s that froze the chat on the first question after a start, since quick answers run on the GUI thread.
        Called on a background thread; a question racing it only builds a cache twice."""
        from . import availability, routes
        availability.of(self)
        self.find_mentions("איפה יש warm")          # (Hebrew too: its word lists are built on their first use)
        self.map_label("warm")
        self.droppers                               # noqa: B018 - the drop index and the route graph (KB-19)
        routes.of(self)

    def ensure_drop_table(self) -> None:
        """Make sure the flat tables beside index.json are current (tables.py): drops.tsv ("which monsters drop X"
        in one grep), names.tsv, rewards.tsv and the rest, rebuilt when the KB changed. The old name stays: every
        caller asks this before a question."""
        tables.ensure(self)

    ensure_tables = ensure_drop_table

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
        # one row for twins ("Jr. Boogie 1" and "2": the same stats and maps, both keys), none for a monster on no map
        # (a PQ copy of King Slime is no monster near the player; audit AI-26)
        merged: dict[tuple, list[dict]] = {}
        for r in rows:
            if r["maps"]:
                merged.setdefault((r["level"], r["hp"], r["exp"], r["mesos"], tuple(r["maps"])), []).append(r)
        for same in list(merged.values())[:40]:
            r = same[0]
            lines.append(f"{' / '.join(dict.fromkeys(x['name'] for x in same))} | {r['level']} | {r['hp']} | "
                         # map_label: not "Drake's Meal Table Dungeon"
                         f"{r['exp']} | {r['mesos']} | {', '.join(map(self.map_label, r['maps']))} | "
                         f"{', '.join(x['key'] for x in same)}")
        return "\n".join(lines) if len(lines) > 1 else ""
