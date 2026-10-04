"""Download the MapleStory Classic knowledge base from NiaMeowDB (meowdb.com).

Used with permission from the NiaMeowDB team. Polite by design: a single
worker, a fixed delay between requests, and resume support so a re-run only
fetches what is missing.

Output (under data/kb/):
    pages/<category>/<slug>.md   one markdown file per entity (front matter + text)
    index.json                   compact index: id, name, category, url, image, props
    img/<category>/<slug>.png    entity images (monster sprites, item icons, ...)

Usage:
    python tools/scrape_meowdb.py            # full run (resumes)
    python tools/scrape_meowdb.py --limit 5  # quick test, 5 pages per category
    python tools/scrape_meowdb.py --refresh  # re-download everything
    python tools/scrape_meowdb.py --changed  # nightly: only pages changed on meowdb, plus new ones
    python tools/scrape_meowdb.py --community  # players' drop and mesos reports -> community.json (scrape_community.py)
"""
from __future__ import annotations

import argparse
import hashlib
import html
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
                time.sleep(10 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(5 * (attempt + 1))
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


def main_text(page: str, name: str) -> str:
    """Readable text of the entity's own content, without site navigation and footer."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>|<noscript.*?</noscript>", "", page, flags=re.S)
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
    if start > 0:
        text = text[start:]
    # Cut the site footer.
    for marker in ("Random Meow Dad Joke", "Spot a mistake, missing data", "A cozy fan database", "Buy me a coffee", "© 20", "Privacy\n"):
        i = text.find(marker)
        if i > 200:
            text = text[:i]
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln and ln not in {"Loading...", "Calculate", "Add to watchlist"}]
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


def save_image(data: bytes, path: Path) -> bool:
    """Store as PNG (the site serves some pictures as WebP)."""
    try:
        import io
        from PIL import Image
        Image.open(io.BytesIO(data)).save(path, "PNG")
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


def scrape_one(category: str, slug: str, url: str, refresh: bool) -> dict | None:
    page = fetch(url)
    time.sleep(DELAY_SECONDS)
    if not page:
        return None
    lds = json_ld(page)
    entity = next((d for d in lds if d.get("@type") not in ("BreadcrumbList", "WebSite", "Organization")), {})
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    title_name = title.group(1).split(" | ")[0].split(" - MapleStory Classic")[0].strip() if title else ""
    name = html.unescape(str(entity.get("name") or entity.get("headline") or title_name or slug))
    text = main_text(page, name)
    props = props_of(entity)
    img_file = None
    img_path = KB / "img" / category / f"{slug}.png"
    if not img_path.exists() or refresh:
        for url in image_candidates(entity, category, slug, name):
            data = fetch(url, binary=True)
            time.sleep(DELAY_SECONDS / 2)
            if data and save_image(data, img_path):
                break
    if img_path.exists():
        img_file = f"img/{category}/{slug}.png"
    front = {"name": name, "category": category, "url": url, "image": img_file, "props": props,
             "type": entity.get("category"), "source": "NiaMeowDB (meowdb.com)"}
    md = "---\n" + json.dumps(front, ensure_ascii=False, indent=1) + "\n---\n\n# " + name + "\n\n"
    if entity.get("description"):
        md += html.unescape(entity["description"]) + "\n\n"
    md += text + "\n"
    (KB / "pages" / category / f"{slug}.md").write_text(md, encoding="utf-8")
    return {"key": f"{category}/{slug}", "id": slug, "name": name, "category": category, "url": url,
            "image": img_file, "props": props, "type": entity.get("category"),
            "lastmod": LASTMOD.get(url, ""), "hash": hashlib.sha1(md.encode("utf-8")).hexdigest()[:16]}


def write_index(path: Path, entries: list[dict]) -> None:
    """index.json with one entity per line: the AI greps the KB folder, and on one 1.3 MB line Gemini's grep failed
    every time ("bufio.Scanner: token too long") while any other grep hit returned the whole index."""
    path.write_text("[\n" + ",\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n]\n", encoding="utf-8")


def scrape(limit: int | None, refresh: bool, changed_only: bool = False) -> None:
    urls = entity_urls()
    index_path = KB / "index.json"
    index: dict[str, dict] = {}
    if index_path.exists() and not refresh:
        index = {e["key"]: e for e in json.loads(index_path.read_text(encoding="utf-8"))}

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
        entry = scrape_one(category, slug, url, refresh)
        with lock:
            counter[0] += 1
            if entry:
                if index.get(key, {}).get("hash") != entry["hash"]:
                    changes[0] += 1
                index[key] = entry
                print(f"[{counter[0]}/{total}] {category}: {entry['name']}", flush=True)
            else:
                print(f"[{counter[0]}/{total}] skip (not found) {url}", flush=True)
            if counter[0] % 25 == 0:
                write_index(index_path, list(index.values()))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, jobs))

    write_index(index_path, list(index.values()))
    meta_path = KB / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta.update({"source": "NiaMeowDB (meowdb.com)", "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "count": len(index)})
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (KB / "last_run.json").write_text(json.dumps({"checked": len(jobs), "changed": changes[0]}), encoding="utf-8")
    print(f"Done. {len(index)} entities, {len(jobs)} checked, {changes[0]} changed.")



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
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.community:
        import scrape_community    # (its own module: the monster pages load these lists in the browser)
        sys.exit(scrape_community.main(["--limit", str(args.limit)] if args.limit else []))
    elif args.stamp:
        stamp()
    elif args.images:
        fill_images()
    else:
        scrape(args.limit, args.refresh, args.changed)
