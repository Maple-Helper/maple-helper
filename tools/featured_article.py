r"""Render a Nexon article for featured.py: tiles of the live page (en, or he from a translation file), its
animations' and links' places. Needs Playwright + Chrome (python -m pip install playwright).

    python tools/featured_article.py en [width]      -> out/en/tileNN.png + spots.json
    python tools/featured_article.py he [width]      (tools/featured/nexon-46506.he.json: English text -> Hebrew)
    python tools/featured_article.py en --dump       also writes en_text.json, the strings to translate
    ... --id 47298 --url https://www.nexon.com/maplestory/news/...   another article (default: 46506, Grand Launch)

tools/featured/nexon-<id>.he.json translates it; tools/featured/nexon-<id>.icons.json (optional) puts the KB's
pictures before the lines that name them: {"Copper Drake": ["monster/40"], "Focus": ["skill/bowman__focus"]}
(a line equal to the name, or starting with "<name>:" / "<name> and ").

Then the tiles go to assets/featured/<id>/<lang>/*.webp and the manifest (see featured.py)
the animations are
the article's GIFs as animated WebP at about the size they are shown.
"""
import base64
import json
import os
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
KB = Path(os.environ.get("MH_KB") or Path(__file__).resolve().parent.parent / "data" / "kb")    # the item pictures
lang = sys.argv[1] if len(sys.argv) > 1 else "en"
width = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 820
dump = "--dump" in sys.argv


def _arg(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


ARTICLE = _arg("--id", "46506")
URL = _arg("--url", "https://www.nexon.com/maplestory/news/general/46506/grand-launch-release-notes")
out = Path(f"out/{lang}")
out.mkdir(parents=True, exist_ok=True)
idx = {e["name"].lower(): e for e in json.load(open(KB / "index.json", encoding="utf-8")) if e["category"] == "item"}
TIER = {"greater": "item/50", "intermediate": "item/57"}      # every scroll of a tier has the one icon


def icon(name):
    base = re.sub(r"\s*\(x\d+\)$", "", name).strip().lower()
    e = idx.get(base)
    key = e["key"] if e and e.get("image") else None
    if not key and "scroll:" in base:
        key = TIER.get(base.rsplit(":", 1)[1].strip())
    if not key:
        return None
    p = KB / "img" / "item" / (key.split("/")[1] + ".png")
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
# body.html: the CMS article's body (g.nexonstatic.com/maplestory/cms/v1/news/<id>, its "body")
BODY = open("body.html", encoding="utf-8").read() if Path("body.html").exists() else ""
box = re.search(r"<details.*?</details>", BODY, re.S)
names = re.findall(r'<div style="flex: 1; min-width: 0;">([^<]+)</div>', box.group(0)) if box else []
icons = {n: icon(n) for n in names}
print("icons missing:", [n for n, v in icons.items() if not v])
tr = json.load(open(Path(__file__).parent / "featured" / f"nexon-{ARTICLE}.he.json", encoding="utf-8")) if lang == "he" else {}
_pics = Path(__file__).parent / "featured" / f"nexon-{ARTICLE}.icons.json"


def picture(key):
    p = KB / "img" / f"{key}.png"
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else None


line_pics = {n: [u for u in map(picture, keys) if u] for n, keys in
             (json.load(open(_pics, encoding="utf-8")).items() if _pics.exists() else ())}
print("pictures missing:", [n for n, v in line_pics.items() if not v])
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": width, "height": 1000}, device_scale_factor=2)
    pg.goto(URL, wait_until="networkidle", timeout=120000)
    pg.wait_for_selector(".cms-ms")
    # every lazy picture loaded: scroll through, then wait
    h = pg.evaluate("document.body.scrollHeight")
    for y in range(0, h + 1000, 700):
        pg.evaluate(f"window.scrollTo(0,{y})")
        pg.wait_for_timeout(120)
    pg.evaluate("document.querySelectorAll('.cms-ms img').forEach(i=>{i.loading='eager'})")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(1500)
    # the Magic Box list: open, every item with its picture
    pg.evaluate(r"""(icons) => {
      document.querySelectorAll('.cms-ms details').forEach(d => d.open = true);
      document.querySelectorAll('.cms-ms details div[style*="flex: 1"]').forEach(div => {
        const src = icons[div.textContent.trim()]
        if (!src) return;
        const row = div.parentElement
        const bullet = row.querySelector('span')
        if (bullet) bullet.remove();
        row.style.alignItems = 'center';
        const im = document.createElement('img')
        im.src = src;
        im.style.cssText = 'width:32px;height:32px;object-fit:contain;image-rendering:pixelated;flex-shrink:0';
        row.insertBefore(im, div);
      });
    }""", icons)
    # the KB's pictures before the lines that name them (before translating: the names are English)
    pg.evaluate(r"""(pics) => {
      document.querySelectorAll('.cms-ms li, .cms-ms p').forEach(el => {
        const own = el.cloneNode(true)    // the line's own text, not its sub-list's
        own.querySelectorAll('ul, ol').forEach(l => l.remove())
        const t = own.textContent.replace(/\s+/g, ' ').trim()
        const srcs = Object.keys(pics).filter(n => t === n || t.startsWith(n + ':') || t.startsWith(n + ' and ')
          || t.includes(' and ' + n + ':')).flatMap(n => pics[n])
        if (!srcs.length) return;
        const span = document.createElement('span')
        span.style.cssText = 'display:inline-flex;gap:4px;vertical-align:middle;margin-inline-end:6px'
        srcs.forEach(src => { const im = document.createElement('img'); im.src = src
          im.style.cssText = 'height:32px;width:auto;max-width:64px;object-fit:contain;image-rendering:pixelated;display:inline-block;margin:0'
          span.appendChild(im) })
        el.insertBefore(span, el.firstChild)
      });
    }""", line_pics)
    if dump:
        texts = pg.evaluate(r"""() => { const w = document.createTreeWalker(document.querySelector('.cms-ms'), NodeFilter.SHOW_TEXT);
          const out = []; let n; while (n = w.nextNode()) { const t = n.nodeValue.replace(/\s+/g,' ').trim(); if (t && /[A-Za-z]/.test(t)) out.push(t); } return [...new Set(out)]; }""")
        json.dump(texts, open("en_text.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print("texts", len(texts))
    if tr:
        missing = pg.evaluate(r"""(tr) => { const root = document.querySelector('.cms-ms');
          const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
          const miss = []
          let n
          const nodes=[];
          while (n = w.nextNode()) nodes.push(n);
          for (const n of nodes) { const t = n.nodeValue.replace(/\s+/g,' ').trim()
          if (!t || !/[A-Za-z]/.test(t)) continue;
            if (tr[t]) { const lead = /^\s/.test(n.nodeValue) ? ' ' : '', tail = /\s$/.test(n.nodeValue) ? ' ' : ''
            n.nodeValue = lead + tr[t] + tail
            } else miss.push(t)
            }
          root.setAttribute('dir', 'rtl');
          root.querySelectorAll('[style*="text-align: left"]').forEach(e => e.style.textAlign = 'right');
          root.querySelectorAll('[style*="padding-left"]').forEach(e => { const v = e.style.paddingLeft
          e.style.paddingLeft = e.style.paddingRight || '0'
          e.style.paddingRight = v
          });
          return miss; }""", tr)
        print("untranslated:", len(missing), missing[:10])
        pg.wait_for_timeout(800)
    # nothing of the site's own over the article (its header bar, cookie banner...)
    pg.evaluate(r"""() => { const root = document.querySelector('.cms-ms');
      document.querySelectorAll('body *').forEach(e => { if (root.contains(e) || e.contains(root)) return;
        const cs = getComputedStyle(e); if (cs.position === 'fixed' || cs.position === 'sticky') e.style.display = 'none'; }); }""")
    pg.evaluate("window.scrollTo(0,0)")
    pg.wait_for_timeout(300)
    top = pg.evaluate("document.querySelector('.cms-ms').getBoundingClientRect().top + scrollY")
    left, wid, hei = pg.evaluate("(() => { const b = document.querySelector('.cms-ms').getBoundingClientRect(); return [b.left, b.width, b.height]; })()")
    spots = pg.evaluate(r"""(o) => { const root = document.querySelector('.cms-ms'); const rb = root.getBoundingClientRect();
      const r = e => { const b = e.getBoundingClientRect()
      return [b.x - rb.x, b.y - rb.y, b.width, b.height]
      };
      return { gifs: [...root.querySelectorAll('img')].filter(i => /\.gif/i.test(i.src)).map(i => ({src: i.src, rect: r(i)})),
               links: [...root.querySelectorAll('a[href]')].map(a => ({href: a.href, rect: r(a)})).filter(l => l.rect[2] > 0) }; }""", {})
    TILE = 1000
    tiles = []
    y = 0
    k = 0
    for f in out.glob("tile*.png"):
        f.unlink()
    while y < hei:
        hh = min(TILE, hei - y)
        pg.evaluate(f"window.scrollTo(0,{top + y})")
        pg.wait_for_timeout(250)
        sy = pg.evaluate("scrollY")
        clip = {"x": left, "y": top + y - sy, "width": wid, "height": hh}
        name = f"tile{k:02d}.png"
        pg.screenshot(path=str(out / name), clip=clip)
        tiles.append({"file": name, "y": y, "h": hh})
        y += hh
        k += 1
    json.dump({"width": wid, "height": hei, "scale": 2, "tiles": tiles, **spots}, open(out / "spots.json", "w"), indent=1)
    print(wid, hei, len(tiles), "tiles", len(spots["gifs"]), "gifs", len(spots["links"]), "links")
    b.close()
