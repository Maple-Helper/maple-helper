"""Flat tables of the knowledge base, one fact per line, for the AI to grep and for code to filter.

A list, filter or reverse question ("which quests give a cape", "who sells arrows", "best claw for a Lv. 30 Thief",
"what is in Ant Tunnel", "what uses Bronze Ore") made the AI open the pages one by one: 6-39 tool calls, up to
4 minutes, and it still missed some. Each table here answers one kind of question in one grep. They are written
next to index.json (the AI works in that folder), tab-separated with a header line, and every row carries the KB
keys of what it names, so the app can show its cards.

What is in the game: every table but names.tsv lists only what the KB confirms is in the game (availability.py),
as drops.tsv always has: a monster, map, NPC, quest or skill it doesn't list is not in the game, an item only when
a source of it is (a drop, a shop, a quest, a recipe or the Cash Shop).

TABLES is the one schema: the files' header lines, the typed rows() below and the AI's prompt (prompt_note) all
read it, so they can't drift apart.

Kept fresh: the mark file (drops.ingame) holds the tables' version and a fingerprint of what they were built from
(index.json, which holds every page's own content hash, community.json, routes.json and the pages' sizes). Any
change to those, or to TABLES, rebuilds them on the next question (ensure). Every file is written whole or not at
all (a temp file, then os.replace), the mark last; a KB folder swapped underneath a build is never written into.
A page that doesn't parse gives fewer rows, never a failed build.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from pathlib import Path

from . import availability, crafting, market, quests, sources

log = logging.getLogger("maplehelper")

TABLES_VERSION = 1          # bump when a builder changes what it writes (the schema itself is hashed in too)
MARK_FILE = "drops.ingame"  # the name the first table's mark had: an older app's mark reads as stale here
DROPS_MARK = ("drops.tsv lists only monsters the KB confirms are in the game (availability.py), with a source column\n"
              "and the players' votes on community drops\n"
              "rewards.tsv lists the item rewards of the quests the KB confirms are in the game\n")
COMMUNITY_FILE = "community.json"
ROUTES_FILE = "routes.json"
INPUTS = ("index.json", COMMUNITY_FILE, ROUTES_FILE)     # the files the tables are built from (with the pages)
REPLACE_TRIES = 10          # os.replace on Windows fails while a reader holds the old file: wait it out
MAPS_LISTED = 6             # a monster row's maps (the most spawns first); spawns.tsv has them all
ASK_WAIT = 5                # seconds a question waits for a build already running before it goes without (it
                            # answers without the tables anyway: 20 s felt stuck while a stale KB rebuilt them)
RETRY_AFTER = 600           # seconds before a failed build (a read-only folder) is tried again on the same files

# name -> (columns, what it answers). Only the columns' order and names are the file format.
TABLES: dict[str, tuple[tuple[str, ...], str]] = {
    "names": (("key", "category", "name", "type"),
              "every entity, in the game or not"),
    "drops": (("monster", "monster_level", "monster_key", "item", "item_type", "item_key", "source", "votes"),
              "source MSEA / community, votes '16 up 1 down'"),
    "rewards": (("quest", "quest_level", "quest_key", "area", "item", "count", "item_type", "item_key", "kind",
                 "for"),
                "kind sure / pick one (for = class) / random 16.7% / gender"),
    "equips": (("item", "key", "slot", "job", "req_lv", "req_str", "req_dex", "req_int", "req_luk", "watk", "matk",
                "wdef", "mdef", "acc", "avoid", "speed", "jump", "hp", "mp", "str", "dex", "int", "luk", "crit",
                "attack_speed", "slots", "sell", "buy", "seller"),
               "job Any = all classes; buy = cheapest NPC price"),
    "consumables": (("item", "key", "type", "hp", "mp", "effect", "req_lv", "sell", "buy", "seller"),
                    "potions, food, buffs, arrows, stars"),
    "scrolls": (("scroll", "key", "slot", "grade", "success", "stats", "sell", "buy", "seller"),
                "success %"),
    "monsters": (("monster", "key", "level", "hp", "mp", "exp", "hp_per_exp", "wdef", "mdef", "acc", "avoid",
                  "acc_needed", "element", "mesos", "mesos_kill", "boss", "respawn", "maps"),
                 "acc_needed = ACC to never miss at equal level; mesos = community range; respawn s"),
    "maps": (("map", "key", "street", "region", "town", "lv_min", "lv_max", "spawn_points", "exp_hr", "exp_rank",
              "monsters", "npcs", "connects"),
             "exp_hr = solo EXP/hour estimate, exp_rank 1 = best"),
    "spawns": (("monster", "monster_key", "level", "map", "map_key", "street", "count", "share", "mob_rate",
                "respawn"),
               "share %"),
    "npcs": (("npc", "key", "role", "map", "map_key", "street"), ""),
    "shops": (("npc", "npc_key", "item", "item_key", "item_type", "price", "place", "label", "rank"),
              "label = the price's build (COT2), rank = citizen grade needed"),
    "quests": (("quest", "key", "level", "area", "npc", "npc_key", "turn_in", "job", "exp", "mesos", "fame", "cycle",
                "after", "questline", "needs", "rewards"),
               "level = doable from; after = finish first; questline 1/3"),
    "quest_reqs": (("quest", "quest_key", "quest_level", "kind", "target", "target_key", "count"),
                   "kind defeat / collect / other"),
    "recipes": (("product", "product_key", "makes", "recipe", "discipline", "prof_lv", "craft_exp", "meso_cost",
                 "ingredient", "ingredient_key", "qty", "optional"),
                "a row per ingredient; recipe = which of a product's recipes"),
    "skills": (("skill", "key", "job", "rank", "max_lv", "kind", "mp", "damage", "targets", "cooldown", "element",
                "weapon", "prerequisite", "effect"),
               "values at max level, damage %"),
}
INT_COLUMNS = {"monster_level", "quest_level", "count", "req_lv", "req_str", "req_dex", "req_int", "req_luk", "watk",
               "matk", "wdef", "mdef", "acc", "avoid", "speed", "jump", "hp", "mp", "str", "dex", "int", "luk", "crit",
               "slots", "sell", "buy", "success", "level", "exp", "acc_needed", "lv_min", "lv_max", "spawn_points",
               "exp_hr", "exp_rank", "share", "price", "mesos", "fame", "makes", "recipe", "prof_lv", "craft_exp",
               "meso_cost", "qty", "max_lv", "damage", "targets"}
FLOAT_COLUMNS = {"hp_per_exp", "mesos_kill", "respawn", "mob_rate"}
TEXT_ONLY = {"monsters": {"mesos"}, "consumables": {"hp", "mp"}}    # "18-23", "40%": kept as written
GENERATED = tuple(f"{name}.tsv" for name in TABLES) + (MARK_FILE,)   # every file a build writes into the KB

_lock = threading.Lock()
_fresh: dict[str, tuple] = {}       # KB folder -> the inputs' file stats when its tables were last found current
_failed: dict[str, tuple[tuple, float]] = {}    # KB folder -> ((its file stats, KB loaded), when) of a failed build
_rows: dict[tuple[str, str], tuple[tuple, list[dict]]] = {}     # (folder, table) -> (file stat, typed rows)


# ---------------------------------------------------------------- freshness

def schema_hash() -> str:
    return hashlib.sha1(repr((TABLES_VERSION, TABLES)).encode("utf-8")).hexdigest()[:12]


def _sha(path: Path) -> str:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return "-"


def fingerprint(root: Path) -> str:
    """What the tables are built from, by content (a KB unpacked from a zip has new file times, the same tables):
    index.json (it holds each page's own content hash), community.json, routes.json, and every page's size, for a
    page changed without index.json (a test, a hand edit)."""
    h = hashlib.sha1()
    for name in INPUTS:
        h.update(f"{name} {_sha(root / name)}\n".encode())
    pages = root / "pages"
    try:
        for cat in sorted(os.scandir(pages), key=lambda d: d.name):
            if cat.is_dir():
                for f in sorted(os.scandir(cat.path), key=lambda d: d.name):
                    h.update(f"{cat.name}/{f.name} {f.stat().st_size}\n".encode())
    except OSError:
        pass
    return h.hexdigest()


def mark_text(root: Path) -> str:
    return DROPS_MARK + f"tables {TABLES_VERSION} schema {schema_hash()} inputs {fingerprint(root)}\n"


def _stats(root: Path) -> tuple:
    """The inputs', the mark's and the tables' file stats: unchanged since the last check, nothing to look at."""
    out = []
    for name in (*INPUTS, *GENERATED):
        try:
            st = (root / name).stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def current(root: Path) -> bool:
    """The tables in this folder are the ones its KB and this app's TABLES make."""
    if not all((root / name).exists() for name in GENERATED):
        return False
    try:
        return (root / MARK_FILE).read_text(encoding="utf-8") == mark_text(root)
    except OSError:
        return False


def building() -> bool:
    """A build is running (a KB swap waits for it: never a folder renamed with a file half written in it)."""
    return _lock.locked()


def ensure(kb, wait: float = ASK_WAIT) -> bool:
    """Make sure kb's folder has current tables: built when missing or stale. Called before every question (cheap
    when nothing changed: a few file stats). A build already running elsewhere is waited for up to `wait` seconds,
    then the question goes on with whatever exists. True when the tables are current."""
    root = Path(kb.root)
    if _fresh.get(str(root)) == _stats(root):
        return True
    if not _lock.acquire(timeout=wait):
        return False
    try:
        stats = _stats(root)
        if _fresh.get(str(root)) == stats:
            return True
        failed = _failed.get(str(root))
        tried = (stats, getattr(kb, "index_hash", ""))      # (a KB loaded since is tried at once)
        if failed and failed[0] == tried and time.monotonic() - failed[1] < RETRY_AFTER:
            return False            # a folder it can't write to: not a 2 s build before every question
        ok = current(root) or build(kb)
        if ok:
            _fresh[str(root)] = _stats(root)
            _failed.pop(str(root), None)
        else:
            _failed[str(root)] = ((_stats(root), getattr(kb, "index_hash", "")), time.monotonic())
        return ok
    finally:
        _lock.release()


def ensure_async(kb, then=None) -> threading.Thread:
    """ensure() on a background thread: at start-up and after a KB update, so the first question doesn't wait.
    then(): more warm-up on the same thread once the tables are done."""
    def work():
        try:
            ensure(kb, wait=600)
        except Exception:          # noqa: BLE001 - a background build never takes the app down
            log.warning("knowledge-base tables not built", exc_info=True)
        if then is not None:
            try:
                then()
            except Exception:      # noqa: BLE001 - only a warm-up: the question builds it if this didn't
                log.warning("knowledge-base warm-up failed", exc_info=True)
    th = threading.Thread(target=work, daemon=True, name="kb-tables")
    th.start()
    return th


def _write(path: Path, text: str) -> None:
    """Whole or not at all: a grep never reads half a table. A reader holding the old file (Windows) is waited out."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.2)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        v = f"{v:.2f}".rstrip("0").rstrip(".")
    return re.sub(r"[\t\r\n]+", " ", str(v)).strip()


def tsv(name: str, rows: list[dict]) -> str:
    cols = TABLES[name][0]
    return "\n".join(["\t".join(cols)] + ["\t".join(_cell(r.get(c)) for c in cols) for r in rows])


def build(kb, out: Path | None = None) -> bool:
    """Write every table into `out` (kb's own folder by default), the mark last. False, and nothing marked current,
    when the folder's KB isn't the one loaded (an update swapped it in meanwhile) or a file can't be written."""
    root = Path(kb.root)
    out = Path(out) if out else root
    loaded = getattr(kb, "index_hash", None)
    if loaded and loaded != _sha(root / "index.json"):
        log.info("knowledge-base tables not built: the folder holds another KB than the one loaded")
        return False
    t0 = time.perf_counter()
    for left in out.glob("*.tmp"):         # a build cut short (a crash, a power cut) left its temp files
        if left.name.split(".", 1)[0] + ".tsv" in GENERATED or left.name.startswith(MARK_FILE):
            try:
                left.unlink()
            except OSError:
                pass
    mark = mark_text(root)
    try:
        made = generate(kb)
    except Exception:          # noqa: BLE001 - a KB the shared lookups can't read: the question goes on without
        log.warning("knowledge-base tables not built", exc_info=True)
        return False
    try:
        for name, rows in made.items():
            _write(out / f"{name}.tsv", tsv(name, rows))
        if loaded and loaded != _sha(root / "index.json"):
            return False            # swapped while it was being built: no mark, the next question builds again
        _write(out / MARK_FILE, mark)
    except OSError:
        log.warning("knowledge-base tables not written", exc_info=True)
        return False
    log.info("knowledge-base tables built in %.1f s: %s", time.perf_counter() - t0,
             ", ".join(f"{n} {len(r)}" for n, r in made.items()))
    return True


# ---------------------------------------------------------------- reading them back (the query planner)

def _typed(name: str, col: str, v: str):
    if v == "" or col in TEXT_ONLY.get(name, ()):
        return v or None
    try:
        if col in INT_COLUMNS:
            return int(v)
        if col in FLOAT_COLUMNS:
            return float(v)
    except ValueError:
        return v
    return v


def ready(kb) -> bool:
    """kb's folder has current tables, without building them (the query planner reads only what is there: a test's
    build_prompt on data/kb must never write into it, and the app has ensured them before every question)."""
    root = Path(kb.root)
    if _fresh.get(str(root)) == _stats(root):
        return True
    if building() or not current(root):
        return False
    _fresh[str(root)] = _stats(root)
    return True


def rows(kb, name: str, build: bool = True) -> list[dict]:
    """A table's rows as dicts, numbers typed (int / float, None when empty), read from kb's folder and kept until
    the file changes. Builds the tables first when they are missing or stale (build=False: [] instead); [] when the
    file can't be read. KeyError for a table TABLES doesn't have."""
    if name not in TABLES:
        raise KeyError(name)
    root = Path(kb.root)
    if build:
        ensure(kb)
    elif not ready(kb):
        return []
    path = root / f"{name}.tsv"
    try:
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
        hit = _rows.get((str(root), name))
        if hit and hit[0] == sig:
            return hit[1]
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = parse(name, text)
    _rows[(str(root), name)] = (sig, out)
    return out


def parse(name: str, text: str) -> list[dict]:
    """A table file's text (tsv()) as rows() gives it: dicts, numbers typed."""
    lines = text.splitlines()
    cols = lines[0].split("\t") if lines else []
    return [{c: _typed(name, c, v) for c, v in zip(cols, ln.split("\t"))} for ln in lines[1:] if ln]


def prompt_note() -> str:
    """The AI's paragraph on the tables, from TABLES (names.tsv has its own words in the prompt)."""
    listed = "\n".join(f"- {name}.tsv: {', '.join(cols)}" + (f" ({desc})" if desc else "")
                       for name, (cols, desc) in TABLES.items() if name != "names")
    return ("Tables (tab-separated, header line first, one fact per line; key and *_key columns are entity keys). "
            "Except names.tsv they list only what is in the game: a monster, map, NPC, quest, skill or item a table "
            "lacks is not in the game.\n" + listed + "\n"
            "Any list, filter or reverse question (which quests give X, who sells X, what drops X or lives on map Y, "
            "best equip for a level and job, best EXP map, how to craft X, what uses X, a job's skills): grep the right "
            "table once, several tables in parallel in one turn when needed, and answer from its rows, never by opening "
            "pages one by one. Open a page only for a detail no table has.")


# ---------------------------------------------------------------- building

def _int(text) -> int | None:
    m = re.search(r"-?\d[\d,]*", str(text or ""))
    return int(m.group(0).replace(",", "")) if m else None


def _guard(rows: list, what: str, key: str, fn, *args) -> None:
    """One entity's rows, fn(*args): a page that doesn't parse is skipped (logged), the table goes on without it."""
    try:
        got = fn(*args)
    except Exception:          # noqa: BLE001 - any parse error on one page
        log.debug("table %s: %s skipped", what, key, exc_info=True)
        return
    if isinstance(got, dict):
        rows.append(got)
    elif got:
        rows.extend(got)


class _Ctx:
    """One build's shared lookups: page lines read once, the maps by their "name street" cell, the shops."""

    def __init__(self, kb):
        self.kb = kb
        self.open = availability.of(kb)
        self._lines: dict[str, list[str]] = {}
        self.map_by_cell: dict[str, str] = {}       # "Henesys Hunting Ground I Victoria Road" -> "map/010001010"
        self.map_by_name: dict[str, list[str]] = {}
        self.map_place: dict[str, tuple[str, str]] = {}      # map key -> (street, continent)
        for k, e in kb.entities.items():
            if e.get("category") != "map":
                continue
            m = re.search(r"^Location (.+?) / (.+?)\s*$", kb.page(k), re.M)
            street, cont = (m.group(1).strip(), m.group(2).strip()) if m else ("", "")
            self.map_place[k] = (street, cont)
            self.map_by_cell.setdefault(f"{e.get('name', '')} {street}".strip(), k)
            self.map_by_name.setdefault(e.get("name", ""), []).append(k)
        self._prices: dict[str, market.NpcPrices] = {}
        self._spawns: list[dict] | None = None

    def spawns(self) -> list[dict]:
        """spawns.tsv's rows, which maps.tsv groups per map too: read once."""
        if self._spawns is None:
            self._spawns = _spawns(self)
        return self._spawns

    def lines(self, key: str) -> list[str]:
        if key not in self._lines:
            self._lines[key] = [ln.strip() for ln in self.kb.page(key).split("\n---", 2)[-1].splitlines()]
        return self._lines[key]

    def map_key(self, cell: str) -> str:
        cell = cell.strip()
        if cell in self.map_by_cell:
            return self.map_by_cell[cell]
        named = self.map_by_name.get(cell) or []
        return named[0] if len(named) == 1 else ""

    def name(self, key: str) -> str:
        return (self.kb.get(key) or {}).get("name", "")

    def prices(self, key: str) -> market.NpcPrices:
        if key not in self._prices:
            self._prices[key] = market.npc_prices(self.kb, key)
        return self._prices[key]

    def place_open(self, where: str) -> bool:
        """A shop's "Victoria Road: Perion Department Store · Perion" is in a released place (availability's rule)."""
        m = availability._SHOP_PLACE.match(where)
        return bool(m) and self.open.place_open(m.group(1)) and self.open.place_open(m.group(3))

    def open_shops(self, key: str) -> list[tuple[str, str, int]]:
        return [s for s in self.prices(key).shops if self.place_open(s[1])]

    def buy(self, key: str) -> dict:
        """The cheapest shop in the game selling an item, and the NPC sell-back."""
        p = self.prices(key)
        shops = self.open_shops(key)
        out = {"sell": p.sell_back}
        if shops:
            npc, where, price = shops[0]
            town = where.rsplit(" · ", 1)[-1]
            out.update(buy=price, seller=f"{_npc_name(self.kb, npc)[0]} ({town})")
        return out

    def items(self, prefix: str = "", exclude: str | None = None):
        """Item keys of the game (a source of them is in it), by type prefix."""
        for k, e in self.kb.entities.items():
            t = str(e.get("type") or "")
            if e.get("category") == "item" and t.startswith(prefix) and t != exclude and self.open.item_open(k):
                yield k, e


def generate(kb) -> dict[str, list[dict]]:
    """Every table's rows, in TABLES order (nothing written). A builder that fails as a whole gives an empty table."""
    ctx = _Ctx(kb)
    out: dict[str, list[dict]] = {}
    for name in TABLES:
        try:
            out[name] = BUILDERS[name](ctx)
        except Exception:          # noqa: BLE001 - one broken table never stops the others
            log.warning("knowledge-base table %s not built", name, exc_info=True)
            out[name] = []
    return out


def _names(ctx) -> list[dict]:
    # index.json is one 1.3 MB line: Gemini's grep can't read a line that long ("bufio.Scanner: token too long",
    # answers took 30-140 s) and any other grep hit returns all of it. One entity per line instead.
    return [{"key": k, "category": e.get("category", ""), "name": e.get("name", ""), "type": e.get("type") or ""}
            for k, e in ctx.kb.entities.items()]


def _drops(ctx) -> list[dict]:
    """Every drop of the monsters in the game, with the list it is on and the players' votes on a community drop."""
    kb, rows = ctx.kb, []
    for ikey, monsters in kb.droppers.items():
        it = kb.get(ikey) or {}
        for m in monsters:
            me = kb.get(m) or {}
            v = kb.community_vote(m, ikey)
            rows.append({"monster": me.get("name"), "monster_level": (me.get("props") or {}).get("Level", ""),
                         "monster_key": m, "item": it.get("name"), "item_type": it.get("type") or "", "item_key": ikey,
                         "source": kb.drop_source(m, ikey) or sources.MSEA,
                         "votes": f"{v['up']} up {v['down']} down" if v else ""})
    return rows


def _rewards(ctx) -> list[dict]:
    """Every item a quest in the game gives. kind: "sure" (always given), "pick one" (the player chooses one of the
    class's), "random" (one of the set, with its odds), "gender"."""
    kb, rows = ctx.kb, []
    for k, e in kb.entities.items():
        if e.get("category") != "quest" or not ctx.open.quest_open(k):
            continue
        q = quests.quest(kb, k)
        if not q:
            continue
        given = [(r, "sure", "") for r in q.rewards]
        given += [(r, "pick one", c) for c, rs in q.class_rewards.items() for r in rs]
        given += [(r, "random", c) for c, rs in q.random_rewards.items() for r in rs]
        given += [(r, "gender", g) for g, rs in q.gender_rewards.items() for r in rs]
        for r, kind, who in given:
            m = re.fullmatch(r"(.+?) x ([\d,]+)(?: \(([\d.]+)%\))?", r)
            name, n, odds = (m.group(1), m.group(2), m.group(3)) if m else (r, "", None)
            ikey = kb._item_by_name.get(name.strip().lower(), "")
            it = (kb.get(ikey) if ikey else None) or {}
            rows.append({"quest": q.name, "quest_level": q.opens_at(), "quest_key": k, "area": q.area, "item": name,
                         "count": n.replace(",", ""), "item_type": it.get("type") or "", "item_key": ikey,
                         "kind": kind + (f" {odds}%" if odds else ""), "for": who})
    return rows


# ---- items

_ITEM_HEAD = re.compile(r"^(Equip|Use|Etc|Setup|Cash)(?: · .+?)? · No\. \d+$")
# an equip's stat lines: "W.ATK +30", "REQ LEV 10 REQ DEX 25 JOB Bowman", "Attack Speed Fast (4)"
_EQUIP_STAT = {"W.ATK": "watk", "M.ATK": "matk", "W.DEF": "wdef", "M.DEF": "mdef", "ACC": "acc", "AVOID": "avoid",
               "SPEED": "speed", "JUMP": "jump", "HP": "hp", "MP": "mp", "STR": "str", "DEX": "dex", "INT": "int",
               "LUK": "luk", "CRIT%": "crit"}
_REQ = re.compile(r"REQ (LEV|STR|DEX|INT|LUK|FAME) (\d+)")
_JOBS = {"Mage": "Magician"}        # the item pages' word for the class the rest of the KB calls Magician


def _stat_block(lines: list[str]) -> list[str]:
    """The lines under an item page's "Equip · Bow · No. 0663" header, up to the gender / trade line."""
    i = next((n for n, ln in enumerate(lines) if _ITEM_HEAD.match(ln)), None)
    if i is None:
        return []
    out = []
    for ln in lines[i + 1:i + 30]:
        if not ln or ln.startswith(("Male", "Female", "Tradeable", "Untradeable", "Weapon Details", "Worn on")):
            break
        out.append(ln)
    return out


def _equip(ctx, key: str, e: dict) -> dict:
    row = {"item": e["name"], "key": key, "slot": str(e.get("type") or "").split(" / ", 1)[-1], "job": "Any"}
    for ln in _stat_block(ctx.lines(key)):
        if ln.startswith("REQ "):
            for what, n in _REQ.findall(ln):
                row[{"LEV": "req_lv"}.get(what, f"req_{what.lower()}")] = int(n)
            job = re.search(r"JOB (\S+)", ln)
            if job:
                row["job"] = "/".join(_JOBS.get(j, j) for j in job.group(1).split("/"))
        elif ln.startswith("Upgrade Slots"):
            row["slots"] = _int(ln)
        elif ln.startswith("Attack Speed "):
            row["attack_speed"] = ln[len("Attack Speed "):]
        else:
            m = re.match(r"^(\S+) ([+-]\d[\d,]*)$", ln)
            if m and m.group(1) in _EQUIP_STAT:
                row[_EQUIP_STAT[m.group(1)]] = _int(m.group(2))
    # what the page's header lacked, from index.json's props
    p = e.get("props") or {}
    for prop, col in (("Level Requirement", "req_lv"), ("Weapon Attack", "watk"), ("Magic Attack", "matk"),
                      ("Weapon Defense", "wdef"), ("Magic Defense", "mdef"), ("Upgrade Slots", "slots")):
        if row.get(col) is None and isinstance(p.get(prop), (int, float)):
            row[col] = int(p[prop])
    row.update(ctx.buy(key))
    return row


def _equips(ctx) -> list[dict]:
    rows: list[dict] = []
    for k, e in ctx.items("Equip /"):
        _guard(rows, "equips", k, _equip, ctx, k, e)
    return rows


def _consumable(ctx, key: str, e: dict) -> dict:
    row = {"item": e["name"], "key": key, "type": str(e.get("type") or "").split(" / ", 1)[-1]}
    effects = []
    for ln in _stat_block(ctx.lines(key)):
        if ln.startswith(("NPC Sell-back", "Max per Stack", "Recharge Cost")):
            continue
        m = re.match(r"^(HP|MP) Recovery \+?([\d,]+%?)$", ln)
        if m:
            row[m.group(1).lower()] = m.group(2).replace(",", "")
        elif ln.startswith("Required Level"):
            row["req_lv"] = _int(ln)
        else:
            effects.append(ln)
    row["effect"] = "; ".join(effects)
    row.update(ctx.buy(key))
    return row


def _consumables(ctx) -> list[dict]:
    rows: list[dict] = []
    for k, e in ctx.items("Use", exclude="Use / Scroll"):
        _guard(rows, "consumables", k, _consumable, ctx, k, e)
    return rows


# the stat a scroll's name ends with, before "Scroll" ("Bottomwear HP Scroll: Lesser"), longest first
_SCROLL_STATS = sorted(("Attack", "Magic Attack", "Magic Def.", "Weapon Def.", "DEF", "HP", "MP", "Accuracy",
                        "Evasion", "STR", "DEX", "INT", "LUK", "Speed", "Jump", "Crit. Rate", "Crit. Damage"),
                       key=len, reverse=True)


def _scroll(ctx, key: str, e: dict) -> dict:
    name = e["name"]
    row = {"scroll": name, "key": key}
    m = re.match(r"^(.+?) Scroll(?:: (.+))?$", name)
    if m:
        head = m.group(1)
        for s in _SCROLL_STATS:
            if head.endswith(" " + s):
                head = head[:-len(s) - 1]
                break
        row.update(slot=head, grade=m.group(2) or "")
    for ln in ctx.lines(key):
        s = re.match(r"^Success rate: (\d+)%,?\s*(.*)$", ln)
        if s:
            row.update(success=int(s.group(1)), stats=s.group(2))
            break
    row.update(ctx.buy(key))
    return row


def _scrolls(ctx) -> list[dict]:
    rows: list[dict] = []
    for k, e in ctx.items("Use / Scroll"):
        _guard(rows, "scrolls", k, _scroll, ctx, k, e)
    return rows


# ---- monsters and maps

_ELEMENT = re.compile(r"^(?:(?:Holy|Fire|Ice|Lightning|Poison|Dark|Physical) (?:weak|strong|immune)\s*)+$")


def _monster(ctx, key: str, e: dict) -> dict:
    from . import combat
    kb, p = ctx.kb, e.get("props") or {}
    lines = ctx.lines(key)
    level, hp, exp = (p.get(f) for f in ("Level", "HP", "EXP"))
    avoid = p.get("Avoidability") or 0
    element = next((ln for ln in lines[:60] if _ELEMENT.match(ln) or ln == "No elemental affinity"), "")
    mesos = kb.community_mesos(key)
    m = combat.monster(kb, key)
    page = kb.page(key)
    maps = [ctx.name(ctx.map_key(c[0])) or c[0] for c in combat._map_rows(page) if ctx.open.map_open(c[0])]
    if len(maps) > MAPS_LISTED:          # most spawns first; spawns.tsv has every one
        maps = maps[:MAPS_LISTED] + [f"+{len(maps) - MAPS_LISTED} more in spawns.tsv"]
    return {"monster": e["name"], "key": key, "level": level, "hp": hp, "mp": p.get("MP"), "exp": exp,
            "hp_per_exp": round(hp / exp, 2) if isinstance(hp, (int, float)) and exp else None,
            "wdef": p.get("Physical Defense"), "mdef": p.get("Magic Defense"), "acc": p.get("Accuracy"),
            "avoid": p.get("Avoidability"),
            "acc_needed": combat.acc_needed(int(level), int(level), int(avoid)) if isinstance(level, (int, float))
            else None,
            "element": "" if element == "No elemental affinity" else element,
            "mesos": f"{mesos[0]}-{mesos[1]}" if mesos else "", "mesos_kill": kb.mesos_per_kill(key),
            "boss": "yes" if e.get("type") == "Boss Monster" or (m and m.boss) else "",
            "respawn": combat._respawn(page) or None,
            "maps": "; ".join(maps)}


def _monsters(ctx) -> list[dict]:
    rows: list[dict] = []
    for k, e in ctx.kb.entities.items():
        if e.get("category") == "monster" and ctx.open.monster_key_open(k):
            _guard(rows, "monsters", k, _monster, ctx, k, e)
    return sorted(rows, key=lambda r: (r["level"] if isinstance(r["level"], (int, float)) else 999, r["monster"]))


def _spawns(ctx: _Ctx) -> list[dict]:
    """A monster page's "Map Locations" table: "Map Region | Count | Share | Types | Mob Rate | Respawn"."""
    from . import combat
    rows: list[dict] = []

    def one(key: str, e: dict) -> list[dict]:
        out = []
        for c in combat._map_rows(ctx.kb.page(key)):
            if not ctx.open.map_open(c[0]):
                continue
            mk = ctx.map_key(c[0])
            share = re.search(r"(\d+)\s*%", c[2]) if len(c) > 2 else None
            rate = re.match(r"([\d.]+)\s*x", c[4]) if len(c) > 4 else None
            out.append({"monster": e["name"], "monster_key": key, "level": (e.get("props") or {}).get("Level"),
                        "map": ctx.name(mk) or c[0], "map_key": mk, "street": ctx.map_place.get(mk, ("",))[0],
                        "count": int(c[1]), "share": int(share.group(1)) if share else None,
                        "mob_rate": float(rate.group(1)) if rate else None,
                        "respawn": combat.respawn_seconds(c[5]) if len(c) > 5 else None})
        return out
    for k, e in ctx.kb.entities.items():
        if e.get("category") == "monster" and ctx.open.monster_key_open(k):
            _guard(rows, "spawns", k, one, k, e)
    return rows


def _routes(ctx) -> dict[str, dict]:
    import json
    try:
        data = json.loads((Path(ctx.kb.root) / ROUTES_FILE).read_text(encoding="utf-8"))
        return {f"map/{m['id']}": m for m in data.get("maps") or [] if isinstance(m, dict) and m.get("id")}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def _maps(ctx: _Ctx) -> list[dict]:
    routes = _routes(ctx)
    here: dict[str, list[dict]] = {}
    for s in ctx.spawns():
        here.setdefault(s["map_key"], []).append(s)
    rows: list[dict] = []

    def one(key: str, e: dict) -> dict:
        lines = ctx.lines(key)
        get = lambda prefix: next((ln[len(prefix):].strip() for ln in lines if ln.startswith(prefix)), "")  # noqa: E731
        lv = re.match(r"Lv (\d+)(?:-(\d+))?", get("Monster levels "))
        rank = re.match(r"#(\d+)", get("EXP rank "))
        exp_hr = next((_int(lines[i + 1]) for i, ln in enumerate(lines[:-1]) if ln == "EXP /hr"), None)
        street, cont = ctx.map_place.get(key, ("", ""))
        r = routes.get(key) or {}
        npcs = [n.get("name") or "" for n in r.get("npcs") or []
                if ctx.kb.get(f"npc/{n.get('id')}") and ctx.open.npc_open(f"npc/{n.get('id')}")]
        exits = []
        for p in r.get("portals") or []:
            to = f"map/{p.get('to')}"
            if ctx.kb.get(to) and ctx.open.entity_open(to) and ctx.name(to) not in exits and to != key:
                exits.append(ctx.name(to))
        mobs = sorted(here.get(key, []), key=lambda s: -s["count"])
        return {"map": e["name"], "key": key, "street": street, "region": cont, "town": "yes" if r.get("town") else "",
                "lv_min": int(lv.group(1)) if lv else None, "lv_max": int(lv.group(2) or lv.group(1)) if lv else None,
                "spawn_points": _int(get("Spawn points ")), "exp_hr": exp_hr,
                "exp_rank": int(rank.group(1)) if rank else None,
                "monsters": "; ".join(dict.fromkeys(s["monster"] for s in mobs)), "npcs": "; ".join(npcs),
                "connects": "; ".join(exits)}
    for k, e in ctx.kb.entities.items():
        if e.get("category") == "map" and ctx.open.entity_open(k):
            _guard(rows, "maps", k, one, k, e)
    return rows


# ---- NPCs and shops

def _npc_name(kb, text: str) -> tuple[str, str]:
    """(name, key) of the NPC a shop line names with its role glued on ("Arturo Grocer", "24 Hr Mobile Store
    Mobile Store"): the longest start of it that is an NPC's name."""
    words = text.split()
    for n in range(len(words), 0, -1):
        key = kb.npc_key(" ".join(words[:n]))
        if key:
            return (kb.get(key) or {}).get("name", " ".join(words[:n])), key
    return text, ""


def _npc(ctx, key: str, e: dict) -> dict:
    lines = ctx.lines(key)
    name = e["name"]
    role, place = [], ""
    i = next((n for n, ln in enumerate(lines) if ln in ("Location", "Locations")), None)
    if i is not None:
        # the first place it stands ("Locations" lists more, each line "<map> <street>")
        place = next((ln for ln in lines[i + 1:i + 4] if ln and ln != "Find path here"), "")
        # the role lines stand between the name (its second time, under the description) and "Location"
        first = max((n for n in range(i) if lines[n] == name), default=i)
        role = [ln for ln in lines[first + 1:i] if ln]
    mk = ctx.map_key(place) if place else ""
    return {"npc": name, "key": key, "role": " · ".join(role), "map": ctx.name(mk) or place, "map_key": mk,
            "street": ctx.map_place.get(mk, ("",))[0]}


def _npcs(ctx) -> list[dict]:
    rows: list[dict] = []
    for k, e in ctx.kb.entities.items():
        if e.get("category") == "npc" and ctx.open.npc_open(k):
            _guard(rows, "npcs", k, _npc, ctx, k, e)
    return rows


def _shop_item(kb, cell: str) -> tuple[str, str, str]:
    """(name, key, citizen grade) of an NPC shop table's item cell: the grade its price needs glued on ("Elixir
    Citizen of Honor +"), a gender mark the item's own name lacks ("Archer Pants (M)")."""
    items = kb._item_by_name
    words = cell.split()
    if cell.endswith(" +") and cell.lower() not in items:
        for n in range(len(words) - 2, 0, -1):
            name = " ".join(words[:n])
            if name.lower() in items:
                return name, items[name.lower()], " ".join(words[n:-1])
    plain = re.sub(r" \([MF]\)$", "", cell)
    return cell, items.get(cell.lower()) or items.get(plain.lower(), ""), ""


def _shops(ctx) -> list[dict]:
    """What NPCs sell: every item page's "Where to buy" (with the shop's town), then what an NPC page's "Shop
    inventory" adds. Only shops in places that are in the game."""
    kb = ctx.kb
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def from_item(key: str, e: dict) -> list[dict]:
        p, out = ctx.prices(key), []
        for npc, where, price in [*p.shops, *((n, w, None) for n, w in p.unpriced)]:
            if not ctx.place_open(where):
                continue
            name, nkey = _npc_name(kb, npc)
            if (nkey or name, key) in seen:
                continue
            seen.add((nkey or name, key))
            out.append({"npc": name, "npc_key": nkey, "item": e["name"], "item_key": key,
                        "item_type": e.get("type") or "", "price": price, "place": where,
                        "label": p.labels.get((npc, where), ""), "rank": p.ranks.get((npc, where), "")})
        return out

    def from_npc(key: str, e: dict) -> list[dict]:
        lines, out = ctx.lines(key), []
        if not any(ln.startswith("Shop inventory") for ln in lines):
            return out
        i = next(n for n, ln in enumerate(lines) if ln.startswith("Shop inventory"))
        label = sources.price_label(lines[i + 1]) if i + 1 < len(lines) else None
        place = next((ln[len("Sold at: "):] for ln in lines[i:i + 4] if ln.startswith("Sold at: ")), "")
        for ln in lines[i + 1:]:
            m = re.fullmatch(r"\| (.+?) \| ([\d,]+|-) mesos", ln)
            if not m:
                if ln.startswith("|") or ln.startswith(("COT", "Sold at", "Launch")) or not ln:
                    continue
                break
            name, ikey, rank = _shop_item(kb, m.group(1).strip())
            if (key, ikey or name) in seen:
                continue
            seen.add((key, ikey or name))
            it = kb.get(ikey) or {}
            out.append({"npc": e["name"], "npc_key": key, "item": it.get("name") or name, "item_key": ikey,
                        "item_type": it.get("type") or "", "price": _int(m.group(2)), "place": place,
                        "label": label or "", "rank": rank})
        return out

    for k, e in kb.entities.items():
        if e.get("category") == "item":
            _guard(rows, "shops", k, from_item, k, e)
    for k, e in kb.entities.items():
        if e.get("category") == "npc" and ctx.open.npc_open(k):
            _guard(rows, "shops", k, from_npc, k, e)
    return rows


# ---- quests

def _open_quests(ctx):
    for k, e in ctx.kb.entities.items():
        if e.get("category") == "quest" and ctx.open.quest_open(k):
            q = quests.quest(ctx.kb, k)
            if q:
                yield k, q


def _quest(ctx, key: str, q) -> dict:
    step = next((m for ln in ctx.lines(key) if (m := re.match(r"^Questline · Step (\d+) of (\d+)", ln))), None)
    given = [*q.rewards, *(r for rs in q.class_rewards.values() for r in rs),
             *(r for rs in q.random_rewards.values() for r in rs), *(r for rs in q.gender_rewards.values() for r in rs)]
    return {"quest": q.name, "key": key, "level": q.opens_at(), "area": q.area, "npc": q.npc,
            "npc_key": ctx.kb.npc_key(q.npc) or "", "turn_in": q.turn_in, "job": q.job, "exp": q.exp,
            "mesos": q.mesos, "fame": q.fame or None, "cycle": q.cycle, "after": "; ".join(q.afters),
            "questline": f"{step.group(1)}/{step.group(2)}" if step else "", "needs": "; ".join(q.needs),
            "rewards": "; ".join(dict.fromkeys(given))}


def _quests(ctx) -> list[dict]:
    rows: list[dict] = []
    for k, q in _open_quests(ctx):
        _guard(rows, "quests", k, _quest, ctx, k, q)
    return rows


def _quest_req(ctx, key: str, q) -> list[dict]:
    kb, out = ctx.kb, []
    for need in q.needs:
        m = re.fullmatch(r"(Defeat |Collect )?(.+?) x ([\d,]+)", need)
        if not m:
            out.append({"kind": "other", "target": need})
            continue
        name = m.group(2).strip()
        if m.group(1) == "Defeat ":
            keys = kb.monster_keys(name)
            target = next((k for k in keys if ctx.open.monster_key_open(k)), keys[0] if keys else "")
            out.append({"kind": "defeat", "target": name, "target_key": target, "count": _int(m.group(3))})
        else:
            out.append({"kind": "collect", "target": name, "target_key": kb._item_by_name.get(name.lower(), ""),
                        "count": _int(m.group(3))})
    for r in out:
        r.update(quest=q.name, quest_key=key, quest_level=q.opens_at())
    return out


def _quest_reqs(ctx) -> list[dict]:
    rows: list[dict] = []
    for k, q in _open_quests(ctx):
        _guard(rows, "quest_reqs", k, _quest_req, ctx, k, q)
    return rows


# ---- crafting

_PRODUCES = re.compile(r"^(\w+)(?: .+?)? Produces × ([\d,]+)$")     # "Woodcrafting Consumables Produces × 500"
_QTY = re.compile(r"^× ([\d,]+)$")


def _craftable(ctx, key: str, e: dict) -> list[dict]:
    """An item page's "Craftable" recipes: discipline, level, EXP, meso cost, then "<ingredient>" / "× <n>" pairs."""
    lines, out, n = ctx.lines(key), [], 0
    kb = ctx.kb
    for i, ln in enumerate(lines):
        m = _PRODUCES.match(ln)
        if not m:
            continue
        n += 1
        head = {"product": e["name"], "product_key": key, "makes": _int(m.group(2)), "recipe": n,
                "discipline": m.group(1)}
        j = i + 1
        while j < len(lines) and not _PRODUCES.match(lines[j]):
            s = lines[j]
            if s.startswith("Req. Level"):
                head["prof_lv"] = _int(s)
            elif s.startswith("Craft EXP"):
                head["craft_exp"] = _int(s)
            elif s.startswith("Meso Cost"):
                head["meso_cost"] = _int(s)
            elif s == "Ingredients":
                j += 1
                while j + 1 < len(lines) and _QTY.match(lines[j + 1]):
                    name = lines[j]
                    optional = name.endswith(" Optional")
                    name = name[:-len(" Optional")] if optional else name
                    out.append({**head, "ingredient": name, "ingredient_key": kb._item_by_name.get(name.lower(), ""),
                                "qty": _int(lines[j + 1]), "optional": "yes" if optional else ""})
                    j += 2
                break
            j += 1
    return out


def _recipes(ctx) -> list[dict]:
    """Every item page's own recipes, then the professions' recipe tables (pages/crafting) for a product with no
    item page recipe."""
    kb = ctx.kb
    rows: list[dict] = []
    for k, e in kb.entities.items():
        if e.get("category") == "item":
            _guard(rows, "recipes", k, _craftable, ctx, k, e)
    have = {r["product"].lower() for r in rows}
    extra: dict[str, int] = {}
    for prof in crafting.PROFESSIONS:
        try:
            levels = crafting.levels(kb, prof)
        except Exception:          # noqa: BLE001
            log.debug("table recipes: %s skipped", prof, exc_info=True)
            continue
        for lv in levels:
            for r in lv.recipes:
                m = re.fullmatch(r"(.+?) x ([\d,]+)", r.name)
                name, makes = (m.group(1), _int(m.group(2))) if m else (r.name, 1)
                if name.lower() in have:
                    continue
                extra[name] = extra.get(name, 0) + 1
                pkey = kb._item_by_name.get(name.lower(), "")
                for qty, ing in r.ingredients:
                    rows.append({"product": name, "product_key": pkey, "makes": makes, "recipe": extra[name],
                                 "discipline": crafting.NAMES[prof], "prof_lv": r.level, "craft_exp": r.exp,
                                 "meso_cost": r.catalyst, "ingredient": ing,
                                 "ingredient_key": kb._item_by_name.get(ing.lower(), ""), "qty": qty})
    return rows


# ---- skills

def _skill(ctx, key: str, e: dict) -> dict | None:
    lines, p = ctx.lines(key), e.get("props") or {}
    if "Not in initial launch" in lines:
        return None
    rank = str(p.get("Job Rank") or "")
    tier = _int(rank)
    if tier and not ctx.open.job_tier_open(tier):
        return None
    kind = next((m for ln in lines if (m := re.match(r"^(ACTIVE|PASSIVE)\b(.*?)(?:\s*Cast:.*)?$", ln))), None)
    i = next((n for n, ln in enumerate(lines) if re.fullmatch(r"Level \d+ \(MAX\)", ln)), None)
    effect = lines[i + 1] if i is not None and i + 1 < len(lines) else ""
    mp = re.search(r"\bMP -?(\d+)", effect)       # "MP -16; Damage 120%" (Invincible's page: "MP 36")
    dmg = re.search(r"[Dd]amage (\d+)%|(\d+)% damage", effect)
    element = next((m.group(1) for ln in lines if (m := re.match(r"^Element ((?:\w+)(?: and \w+)?) The skill", ln))),
                   "")
    weapon = next((m.group(1) for ln in lines if (m := re.match(r"^Weapon requirement (.+?) The skill", ln))), "")
    return {"skill": e["name"], "key": key, "job": p.get("Job", ""), "rank": rank, "max_lv": p.get("Max Level"),
            "kind": " ".join(kind.group(0).split(" Cast:")[0].lower().split()) if kind else "",
            "mp": int(mp.group(1)) if mp else None, "damage": int(dmg.group(1) or dmg.group(2)) if dmg else None,
            "targets": p.get("Target Cap"), "cooldown": p.get("Cooldown") or "",
            "element": "" if element == "None" else element, "weapon": weapon,
            "prerequisite": p.get("Prerequisite") or "", "effect": effect}


def _skills(ctx) -> list[dict]:
    rows: list[dict] = []
    for k, e in ctx.kb.entities.items():
        if e.get("category") == "skill":
            _guard(rows, "skills", k, _skill, ctx, k, e)
    return rows


BUILDERS = {"names": _names, "drops": _drops, "rewards": _rewards, "equips": _equips, "consumables": _consumables,
            "scrolls": _scrolls, "monsters": _monsters, "maps": _maps, "spawns": lambda ctx: ctx.spawns(),
            "npcs": _npcs, "shops": _shops, "quests": _quests, "quest_reqs": _quest_reqs, "recipes": _recipes,
            "skills": _skills}
