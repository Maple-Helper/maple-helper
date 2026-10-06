"""Download the MapleStory Classic knowledge base from NiaMeowDB (meowdb.com).

Used with permission from the NiaMeowDB team. Polite by design: three workers,
each pausing 1 s between requests, the site's Retry-After honored, and resume
support so a re-run only fetches what is missing.

Output (under data/kb/):
    pages/<category>/<slug>.md   one markdown file per entity (front matter + text)
    index.json                   compact index: id, name, category, url, image, props
    skill_changes.json, pets.json, tiers.json   the list pages (tools/meowdb_sections.py)
    img/<category>/<slug>.png    entity images (monster sprites, item icons, ...)
    routes.json                  every map's portals and NPCs, and the taxi towns (maplehelper/routes.py)

Usage:
    python tools/scrape_meowdb.py            # full run (resumes)
    python tools/scrape_meowdb.py --limit 5  # quick test, 5 pages per category
    python tools/scrape_meowdb.py --refresh  # re-download everything
    python tools/scrape_meowdb.py --changed  # nightly: only pages changed on meowdb, plus new ones
    python tools/scrape_meowdb.py --community  # players' drop and mesos reports -> community.json (scrape_community.py)
    python tools/scrape_meowdb.py --routes   # only routes.json, the map connections (every run refreshes it too)
"""
from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://meowdb.com"
SITEMAP = f"{BASE}/msclassic/sitemap.xml"
USER_AGENT = "MapleHelper-KB/0.1 (MapleStory Classic companion app; used with NiaMeowDB permission)"
DELAY_SECONDS = 1.0
WORKERS = 3
# how a page is turned into text: bump it with every change to main_text/scrape_one's output, so the patch notes
# don't call every re-parsed page "updated" (the SITE_TOOLS change did: 186 monsters "updated", nothing new)
PARSER_VERSION = 2

# sitemap path prefix -> (category name, path depth that marks an entity page)
CATEGORIES = {
    "monsters": ("monster", 2),
    "item-db": ("item", 2),
    "maps": ("map", 2),
    "quest-tracker": ("quest", 2),
    "npcs": ("npc", 2),
    "skills": ("skill", 3),
    "classes": ("class", 2),
    "guides": ("guide", 2),
    "npc-shops": ("shop", 3),
    "crafting": ("crafting", 3),
    "formulas": ("formula", 2),
}
NUMERIC_IDS = {"monsters", "item-db", "maps", "quest-tracker", "npcs"}
LOCALES = {"es", "pt", "de", "fr", "ko", "ja", "zh-cn", "zh-tw", "th", "id", "vi", "tl", "pl", "ru", "it", "tr", "ms"}

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "data" / "kb"


def fetch(url: str, binary: bool = False, retries: int = 3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 or e.code >= 500:
                time.sleep(retry_after(e) or 10 * (attempt + 1))
                continue
            raise
        except (http.client.HTTPException, OSError):
            # a dropped connection, a reset or a cut-off answer is a hiccup like a timeout: urllib wraps only the
            # request's errors in URLError, so one RemoteDisconnected used to abort the whole night
            time.sleep(5 * (attempt + 1))
    return None


def retry_after(e: urllib.error.HTTPError) -> int | None:
    """The wait the site asked for (seconds, at most 2 minutes); None when it didn't say."""
    try:
        return min(120, max(1, int((e.headers or {}).get("Retry-After") or "")))
    except (TypeError, ValueError):
        return None


LASTMOD: dict[str, str] = {}


def entity_urls() -> dict[str, list[str]]:
    xml = fetch(SITEMAP)
    if not xml:
        sys.exit("Could not download the sitemap.")
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>([^<]+)</loc>", block)
        mod = re.search(r"<lastmod>([^<]+)</lastmod>", block)
        if loc:
            LASTMOD[loc.group(1)] = mod.group(1) if mod else ""
    out: dict[str, list[str]] = {k: [] for k in CATEGORIES}
    for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
        path = loc.replace(f"{BASE}/msclassic/", "").strip("/")
        parts = path.split("/")
        if not parts or parts[0] in LOCALES:
            continue
        prefix = parts[0]
        if prefix in CATEGORIES and len(parts) == CATEGORIES[prefix][1]:
            if prefix in NUMERIC_IDS and not parts[-1].isdigit():
                continue  # listing pages such as item-db/all, item-db/equipment
            out[prefix].append(loc)
    return out


def json_ld(page: str) -> list[dict]:
    found = []
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            found.append(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return found


# rows of the site's own tools, not game data: the monster page's "Check your build against <monster>" panel
SITE_TOOLS = re.compile(r"Check your build against |Your damage on it How hard it hits you|Scroll Simulator for "
                        r"|Ad blocked\? |Buy us a coffee|Upvote what you've seen|Items players have personally seen drop")
# the site's own controls and placeholders, whole lines ("Next →" on 3,917 pages, "Log in to sell or buy" on 2,577)
SITE_LINES = {"Loading...", "Loading…", "Calculate", "Add to watchlist", "Next →", "🐾", "Log in to sell or buy",
              "Loading leaderboard...", "Loading drops", "Loading community builds...", "Loading community guides..."}
# ...and at the end of a value line: "Mesos per kill Loading...", "Base 1 Calculate", "no rolls yet Log in to submit"
SITE_TAILS = re.compile(r" (?:Loading\.\.\.|Loading…|Calculate|Log in to submit)$")
# the site-wide notice banner ("[ Notice ] Beginner's guide refreshed..."): one text change re-hashed 29 pages
NOTICE = re.compile(r'<section[^>]*aria-label="Notice".*?</section>', re.S)


def main_text(page: str, name: str) -> str:
    """Readable text of the entity's own content, without site navigation and footer."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>|<noscript.*?</noscript>", "", page, flags=re.S)
    body = NOTICE.sub("", body)
    body = re.sub(r"</(div|p|li|tr|h\d|section|table|ul|ol)>|<br\s*/?>", "\n", body)
    body = re.sub(r"</t[dh]>", " | ", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" ?\| ?(\n|$)", r"\1", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    # Skip the site navigation, then start at the breadcrumb ("... Database / <name>")
    # or the first heading with the name.
    nav = text.find("Explore the database")
    if nav > 0:
        text = text[nav:]
    start = -1
    m = re.search(r"/\s*" + re.escape(name) + r"\s*\n", text)
    if m:
        start = m.end()
    elif name in text:
        start = text.find(name)
    else:
        # a guide's name (its headline) is neither its breadcrumb nor its h1: start after the breadcrumb line
        # ("Home / MS Classic / Guides / Assassin 30-70"), or the whole site menu stays in the page
        m = re.search(r"^[ \t]*Home / [^\n]*\n", text, re.M)
        if m:
            start = m.end()
    if start > 0:
        text = text[start:]
    # Cut the site footer.
    for marker in ("Random Meow Dad Joke", "Spot a mistake, missing data", "A cozy fan database", "Buy me a coffee", "© 20", "Privacy\n"):
        i = text.find(marker)
        if i > 200:
            text = text[:i]
    lines = [SITE_TAILS.sub("", ln.strip()) for ln in text.split("\n")]
    lines = [ln for ln in lines if ln and ln not in SITE_LINES and not SITE_TOOLS.match(ln)]
    return "\n".join(lines).strip()


def slug_of(url: str) -> str:
    parts = url.replace(f"{BASE}/msclassic/", "").strip("/").split("/")
    return "__".join(parts[1:]) or parts[0]


def _name_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def image_candidates(entity: dict, category: str, slug: str, name: str) -> list[str]:
    """Where the site keeps a picture for this entity, most likely first."""
    first = image_url(entity, category, slug)
    out = [first] if first else []
    if category == "npc":
        bare = re.sub(r"\s*\(.*?\)", "", name)          # "Sam (Henesys Armor Seller)" → "Sam"
        out += [f"{BASE}/msclassic/npcs/{_name_slug(name)}.webp", f"{BASE}/msclassic/npcs/{_name_slug(bare)}.webp",
                f"{BASE}/msclassic/npcs/npc-{slug}.webp"]
    elif category == "map":
        out.append(f"{BASE}/msclassic/maps/minimaps/{slug}.png")
    return out


# a guide's picture is the site's 1200x630 social card: the app shows it as a 44 px list icon and, on hover, at
# 480 px (ui/guides.py COVER_W). Stored at that width the 32 cards take 2.4 MB, not 6.1.
GUIDE_IMG_W = 480


def save_image(data: bytes, path: Path, max_w: int | None = None) -> bool:
    """Store as PNG (the site serves some pictures as WebP), no wider than max_w. No Pillow is an error, not a bad
    picture: the nightly ran without it and threw every new picture away in silence ("pictures added: 0/11")."""
    import io
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        if max_w and img.width > max_w:
            img = img.resize((max_w, round(img.height * max_w / img.width)), Image.LANCZOS)
        img.save(path, "PNG")
        return True
    except Exception:
        return False


def image_url(entity: dict, category: str, slug: str) -> str | None:
    img = entity.get("image")
    if isinstance(img, dict):
        img = img.get("url")
    if isinstance(img, list):
        img = img[0] if img else None
    if img:
        return img if img.startswith("http") else BASE + img
    if category == "item" and slug.isdigit():
        return f"{BASE}/msclassic/api/assets/icons/{slug}"
    return None


def props_of(entity: dict) -> dict:
    props = {}
    for p in entity.get("additionalProperty", []) or []:
        if isinstance(p, dict) and "name" in p:
            props[p["name"]] = p.get("value")
    return props


def scrape_one(category: str, slug: str, url: str, refresh: bool, prev_hash: str | None = None) -> dict | None:
    page = fetch(url)
    time.sleep(DELAY_SECONDS)
    if not page:
        return None
    lds = json_ld(page)
    entity = next((d for d in lds if d.get("@type") not in ("BreadcrumbList", "WebSite", "Organization")), {})
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    title_name = title.group(1).split(" | ")[0].split(" - MapleStory Classic")[0].strip() if title else ""
    name = html.unescape(str(entity.get("name") or entity.get("headline") or title_name or slug)).strip()
    text = main_text(page, name)
    props = props_of(entity)
    img_file = None
    img_path = KB / "img" / category / f"{slug}.png"

    def get_picture():
        # not "url": that is the page's, written below (a loop over "url" put the picture's address in every page a
        # picture was fetched for, and back the next night: ~3,800 pages "updated" twice a week for nothing)
        for img_url in image_candidates(entity, category, slug, name):
            data = fetch(img_url, binary=True)
            time.sleep(DELAY_SECONDS / 2)
            if data and save_image(data, img_path, GUIDE_IMG_W if category == "guide" else None):
                break
    had_picture = img_path.exists()
    if not had_picture:
        get_picture()
    if img_path.exists():
        img_file = f"img/{category}/{slug}.png"
    front = {"name": name, "category": category, "url": url, "image": img_file, "props": props,
             "type": entity.get("category"), "source": "NiaMeowDB (meowdb.com)"}
    md = "---\n" + json.dumps(front, ensure_ascii=False, indent=1) + "\n---\n\n# " + name + "\n\n"
    if entity.get("description"):
        md += html.unescape(entity["description"]) + "\n\n"
    md += text + "\n"
    (KB / "pages" / category / f"{slug}.md").write_text(md, encoding="utf-8")
    digest = hashlib.sha1(md.encode("utf-8")).hexdigest()[:16]
    if had_picture and refresh and digest != prev_hash:
        get_picture()       # a refresh renews the picture of a page that changed, not all ~3,800 every Sunday
    return {"key": f"{category}/{slug}", "id": slug, "name": name, "category": category, "url": url,
            "image": img_file, "props": props, "type": entity.get("category"),
            "lastmod": LASTMOD.get(url, ""), "hash": digest, "parser": PARSER_VERSION}


def write_index(path: Path, entries: list[dict]) -> None:
    """index.json with one entity per line: the AI greps the KB folder, and on one 1.3 MB line Gemini's grep failed
    every time ("bufio.Scanner: token too long") while any other grep hit returned the whole index."""
    path.write_text("[\n" + ",\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n]\n", encoding="utf-8")


def scrape(limit: int | None, refresh: bool, changed_only: bool = False) -> None:
    urls = entity_urls()
    index_path = KB / "index.json"
    # the copy we have: what a change is measured against, also on a refresh (which counted all 4,161 pages
    # "changed" and published a new 21 MB KB every Sunday), and what a failed fetch keeps
    prev: dict[str, dict] = {}
    if index_path.exists():
        prev = {e["key"]: e for e in json.loads(index_path.read_text(encoding="utf-8"))}
    index: dict[str, dict] = {} if refresh else dict(prev)

    jobs = []
    for prefix, locs in urls.items():
        category = CATEGORIES[prefix][0]
        (KB / "pages" / category).mkdir(parents=True, exist_ok=True)
        (KB / "img" / category).mkdir(parents=True, exist_ok=True)
        for url in locs[:limit] if limit else locs:
            slug = slug_of(url)
            key = f"{category}/{slug}"
            have = (KB / "pages" / category / f"{slug}.md").exists() and key in index
            if have and not refresh:
                if not changed_only:
                    continue
                if index[key].get("lastmod", "") >= LASTMOD.get(url, ""):
                    continue       # unchanged since our copy
            jobs.append((category, slug, key, url))

    lock = threading.Lock()
    counter = [0]
    total = len(jobs)

    changes = [0]

    def work(job):
        category, slug, key, url = job
        entry = scrape_one(category, slug, url, refresh, prev.get(key, {}).get("hash"))
        with lock:
            counter[0] += 1
            if entry:
                if prev.get(key, {}).get("hash") != entry["hash"]:
                    changes[0] += 1
                index[key] = entry
                print(f"[{counter[0]}/{total}] {category}: {entry['name']}", flush=True)
            else:
                # still in the sitemap, so a site hiccup (5xx, 429, a timeout): keep our copy, or the patch notes
                # say "removed" tonight and "added" tomorrow
                if key in prev:
                    index[key] = prev[key]
                print(f"[{counter[0]}/{total}] skip (not fetched) {url}", flush=True)
            if counter[0] % 25 == 0:
                write_index(index_path, list(index.values()))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, jobs))

    write_index(index_path, list(index.values()))
    if refresh and not limit:
        changes[0] += drop_removed(prev, index)
    # the list pages (skill changes, pets, tier list): one at a time after the entity pages, counted as changes
    # so a night that only moves a pet's lifespan still publishes
    import meowdb_sections
    changes[0] += meowdb_sections.scrape(KB, fetch, DELAY_SECONDS)

    if not limit:
        # the news page too (one request: tools/scrape_news.py); a new item counts as a change, so it gets published.
        # Its pictures come once each (a new item's cover, a Nexon article's named pictures), kept on later nights
        import scrape_news
        changes[0] += scrape_news.update(KB, fetch, lambda url: fetch(url, binary=True))
    meta_path = KB / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta.update({"source": "NiaMeowDB (meowdb.com)", "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "count": len(index)})
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    changes[0] += scrape_routes()      # one file a night: the map connections the app routes with
    (KB / "last_run.json").write_text(json.dumps({"checked": len(jobs), "changed": changes[0]}), encoding="utf-8")
    print(f"Done. {len(index)} entities, {len(jobs)} checked, {changes[0]} changed.")


def drop_removed(prev: dict[str, dict], index: dict[str, dict]) -> int:
    """After a full refresh: delete the page and picture of every entity the sitemap no longer lists, so the AI's
    grep can't find what the site removed. Failed fetches were kept above, so only real removals are left."""
    gone = prev.keys() - index.keys()
    for key in gone:
        cat, _, slug = key.partition("/")
        for f in (KB / "pages" / cat / f"{slug}.md", KB / "img" / cat / f"{slug}.png"):
            f.unlink(missing_ok=True)
    if gone:
        print(f"removed from the site: {len(gone)}, e.g. {', '.join(sorted(gone)[:5])}")
    return len(gone)


def fill_images() -> None:
    """Fetch pictures for entities that have none (NPC portraits, map minimaps). Resumable."""
    index_path = KB / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    todo = [e for e in index if not e.get("image") and e["category"] in ("npc", "map")]
    lock = threading.Lock()
    done = [0]

    def work(e):
        cat, _, slug = e["key"].partition("/")
        (KB / "img" / cat).mkdir(parents=True, exist_ok=True)
        path = KB / "img" / cat / f"{slug}.png"
        for url in image_candidates({}, cat, slug, e["name"]):
            data = fetch(url, binary=True)
            time.sleep(DELAY_SECONDS / 2)
            if data and save_image(data, path):
                e["image"] = f"img/{cat}/{slug}.png"
                break
        with lock:
            done[0] += 1
            if done[0] % 50 == 0:
                print(f"[{done[0]}/{len(todo)}]", flush=True)
                write_index(index_path, index)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, todo))
    write_index(index_path, index)
    got = sum(1 for e in todo if e.get("image"))
    print(f"pictures added: {got}/{len(todo)}")


# ---------------------------------------------------------------- map connections (routes.json)
# The site's Pathfinder and World Map read one data file, /_data/maps.json: every map with its portals (the map each
# leads to, by id) and NPCs. The map pages say the same in prose ("Connected Maps ( 9 )" on one line, names only:
# two maps called "Mushroom Town" can't be told apart there, and nothing says which way a portal goes), so the route
# graph comes from that file. The Pathfinder's taxi towns are written in its own script, read from there.

ROUTES = "routes.json"
MAPS_DATA = f"{BASE}/_data/maps.json"
PATHFINDER = f"{BASE}/msclassic/pathfinder"
_MAP_ID = re.compile(r"^\d{9}$")
_ID_LIST = re.compile(r'\[(?:"\d{9}",)+"\d{9}"\]')


def routes_data(maps: list[dict], taxi: list[str]) -> dict:
    """What the app routes with, from the site's map data: ids, names, portals and NPCs with their minimap spots."""
    out, ids = [], {str(m.get("id") or "") for m in maps}
    for m in maps:
        mid = str(m.get("id") or "")
        if not _MAP_ID.match(mid):
            continue
        mm = None
        if m.get("hasMinimapImage") and m.get("minimapWidth") and m.get("minimapHeight"):
            mm = [m["minimapWidth"], m["minimapHeight"], m.get("miniMapCenterX") or 0, m.get("miniMapCenterY") or 0]
        portals = [{"to": p["toMapId"], "name": p.get("name") or "", "x": p.get("x") or 0, "y": p.get("y") or 0}
                   for p in m.get("portals") or [] if str(p.get("toMapId") or "") in ids and p["toMapId"] != mid]
        npcs = [{"id": str(n.get("id")), "name": n.get("name") or "", "x": n.get("x") or 0, "y": n.get("y") or 0}
                for n in m.get("npcs") or [] if n.get("id")]
        out.append({"id": mid, "name": m.get("name") or mid, "street": m.get("streetName") or "",
                    "region": m.get("region") or "", "town": bool(m.get("isTown")), "return": m.get("returnMap") or "",
                    "minimap": mm, "portals": portals, "npcs": npcs})
    out.sort(key=lambda m: m["id"])
    return {"source": "NiaMeowDB (meowdb.com) map data, as its Pathfinder reads it", "taxi": taxi, "maps": out}


def taxi_towns(map_ids: set[str]) -> list[str] | None:
    """The towns the Pathfinder's taxi option links, read from its page script; None when it can't be read."""
    page = fetch(PATHFINDER)
    time.sleep(DELAY_SECONDS)
    chunk = re.search(r'src="(/_next/static/chunks/app/msclassic/[^"]*pathfinder/page-[^"]+\.js)"', page or "")
    if not chunk:
        return None
    js = fetch(BASE + chunk.group(1))
    time.sleep(DELAY_SECONDS)
    lists = [json.loads(s) for s in _ID_LIST.findall(js or "")]
    lists = [ids for ids in lists if len(ids) >= 2 and set(ids) <= map_ids]
    return lists[0] if len(lists) == 1 else None


def write_routes(path: Path, data: dict) -> None:
    """One map per line, like index.json: the AI greps the KB folder."""
    head = json.dumps({k: v for k, v in data.items() if k != "maps"}, ensure_ascii=False)[:-1]
    path.write_text(head + ', "maps": [\n' + ",\n".join(json.dumps(m, ensure_ascii=False) for m in data["maps"])
                    + "\n]}\n", encoding="utf-8")


def scrape_routes() -> int:
    """Refresh routes.json; 1 when it changed. A failed download keeps the copy we have."""
    raw = fetch(MAPS_DATA)
    time.sleep(DELAY_SECONDS)
    try:
        maps = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        maps = None
    if not isinstance(maps, list) or not maps:
        print("routes: map data not available, keeping the previous routes.json")
        return 0
    path = KB / ROUTES
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    ids = {str(m.get("id")) for m in maps}
    taxi = taxi_towns(ids)
    if taxi is None:
        taxi = [t for t in old.get("taxi") or [] if t in ids]
        print("routes: taxi towns not read from the Pathfinder, keeping", taxi)
    data = routes_data(maps, taxi)
    if data == old:
        return 0
    write_routes(path, data)
    print(f"routes: {len(data['maps'])} maps, taxi {', '.join(taxi) or 'none'}")
    return 1


def stamp() -> None:
    """Mark the existing copy with sitemap lastmod + content hashes (baseline for --changed)."""
    entity_urls()   # fills LASTMOD from the sitemap
    index_path = KB / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for e in index:
        cat, _, slug = e["key"].partition("/")
        md = KB / "pages" / cat / f"{slug}.md"
        e["lastmod"] = LASTMOD.get(e["url"], "")
        if md.exists():
            e["hash"] = hashlib.sha1(md.read_text(encoding="utf-8").encode("utf-8")).hexdigest()[:16]
    write_index(index_path, index)
    print(f"stamped {len(index)} entities")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--refresh", action="store_true", help="re-download every page")
    ap.add_argument("--changed", action="store_true", help="only pages whose sitemap lastmod is newer, plus new pages")
    ap.add_argument("--stamp", action="store_true", help="record sitemap lastmod and content hash for the current copy")
    ap.add_argument("--images", action="store_true", help="fetch missing NPC portraits and map minimaps")
    ap.add_argument("--community", action="store_true", help="players' drop and mesos reports (community.json)")
    ap.add_argument("--routes", action="store_true", help="only refresh routes.json (map connections)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.community:
        import scrape_community    # (its own module: the monster pages load these lists in the browser)
        sys.exit(scrape_community.main(["--limit", str(args.limit)] if args.limit else []))
    if args.routes:
        scrape_routes()
    elif args.stamp:
        stamp()
    elif args.images:
        fill_images()
    else:
        scrape(args.limit, args.refresh, args.changed)
