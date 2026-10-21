"""Pictures for the grind tracker's guide (ui/trackerguide.py): every part of the tracker drawn by the app itself,
offscreen, from a made-up session (the bar, the card over the game, the window's goal, alert, summary, monsters,
item tables, potions, live tracking, history and the maps compared), in Hebrew and English, at 2x for sharp text.

    python tools/tracker_guide_shots.py            -> assets/tracker_guide/<he|en>/<part>.png

Run again after changing how any of these parts look. Needs the real KB in data/kb (pictures, prices, maps)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_SCALE_FACTOR", "2")
_tmp = tempfile.mkdtemp()
os.environ["APPDATA"] = os.environ["LOCALAPPDATA"] = _tmp
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# the players' prices as MesoWatch had them last on this computer (the made-up session's loot is real items)
_mw = Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Roaming" / "MapleHelper" / "mesowatch.json"
if _mw.exists():
    (Path(_tmp) / "MapleHelper").mkdir(exist_ok=True)
    (Path(_tmp) / "MapleHelper" / "mesowatch.json").write_bytes(_mw.read_bytes())

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = ROOT / "assets" / "tracker_guide"


def _layout_rect(lay) -> QRect:
    r = QRect()
    for i in range(lay.count()):
        it = lay.itemAt(i)
        g = it.widget().geometry() if it.widget() is not None else it.geometry()
        r = r.united(g)
    return r


def main(lang: str) -> None:
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    from maplehelper.ui import theme
    for f in ("SegoeIcons.ttf", "segmdl2.ttf", "seguiemj.ttf", "seguisym.ttf", "segoeui.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + f)    # offscreen Qt sees no system fonts
    fam = theme.load_fonts()
    theme.FONT_FAMILY = fam
    theme.set_mode("light")
    app.setLayoutDirection(Qt.RightToLeft if lang == "he" else Qt.LeftToRight)
    app.setStyleSheet(theme.stylesheet(fam, 14))
    import time

    from maplehelper import grind, lootdb, lootlog, plan, routes, store
    store.Settings.path = Path(_tmp) / f"s_{lang}.json"
    store.Profiles.path = Path(_tmp) / f"p_{lang}.json"
    store.HISTORY_DIR = Path(_tmp) / "h"
    store.HISTORY_DIR.mkdir(exist_ok=True)
    lootlog.Store.path = Path(_tmp) / f"loot_{lang}.json"
    lootdb.PATH = Path(_tmp) / f"loot_{lang}.sqlite"
    grind.Store.path = Path(_tmp) / f"g_{lang}.json"
    from maplehelper.hud import Hud
    from maplehelper.i18n import I18n
    from maplehelper.kb import KnowledgeBase
    from maplehelper.minimap import Here
    from maplehelper.ui.gametoolbar import GameToolbar
    from maplehelper.ui.grindtracker import GrindTracker
    from maplehelper.ui.location import LOCATION
    from maplehelper.ui.lootoverlay import LootOverlay
    from maplehelper.ui.lootscan import LootScanner
    from maplehelper.ui.npcoverlay import apply_look

    kb = KnowledgeBase(ROOT / "data" / "kb")
    p = store.Profiles()
    p.add("Kalimero", "Thief", "Assassin", 23)
    s = store.Settings()
    s["language"], s["appearance"], s["overlay_accent"] = lang, "light", "app"
    apply_look(s)
    t = I18n(lang)
    g = routes.of(kb)
    ant = next(m for m in g.maps if g.name(m) == "Ant Tunnel I")
    other = next(m for m in g.maps if g.name(m) == "The Burnt Land I")
    scan = LootScanner(kb, p, s)
    cid = scan.cid()
    E = lootlog.Event
    exp_of = {r["key"]: r["exp"] for r in __import__("maplehelper.tables", fromlist=["rows"]).rows(kb, "monsters")}
    horny, zombie = exp_of["monster/19"], exp_of["monster/21"]

    def past(start, secs, lvl, mid, kills, mesos):
        lid = lootdb.open_log(cid, "Kalimero", start, lvl)
        lootdb.add_events(lid, [E(start + 5 * i, "exp", horny if i % 3 else zombie, map=mid) for i in range(kills)]
                          + [E(start + 60, "mesos", mesos, map=mid), E(start + 90, "item", 30, "Horny Mushroom Cap",
                                                                         "item/366", map=mid),
                             E(start + 120, "potion", 25, "Orange Potion", "item/271", map=mid)], kb)
        lootdb.set_spots(lid, {mid: secs})
        lootdb.end_log(lid, start + secs)
    now = time.time()
    past(now - 3 * 86400, 3600, 22, ant, 420, 21000)
    past(now - 2 * 86400, 2400, 22, other, 260, 26000)
    past(now - 86400, 1800, 23, ant, 230, 11000)
    scan.start()
    scan._log.start = now - 1500
    evs = []
    for i in range(150):
        evs.append(E(now - 1490 + 10 * i, "exp", horny if i % 4 else zombie))
        if i % 3 == 0:
            evs.append(E(now - 1488 + 10 * i, "mesos", 30 + i % 25))
    evs += [E(now - 600, "item", 6, "Horny Mushroom Cap", "item/366"), E(now - 400, "item", 2, "Leaf", "item/358"),
            E(now - 300, "item", 1, "Old Gladius", "item/542"), E(now - 200, "potion", 9, "Orange Potion", "item/271"),
            E(now - 60, "potion", 2, "Lemon", "item/240")]
    scan._log.add(evs)
    scan._log.level = 23
    scan._log.on_map(ant, 1500)
    need = plan.exp_table(kb)[23]
    scan.hud = Hud(23, "Assassin", "Kalimero", int(need * 0.62), 62.0)
    scan.potion_counts = {"item/271": 14, "item/240": 230}
    LOCATION.set(Here(ant))
    out = OUT / lang
    out.mkdir(parents=True, exist_ok=True)

    from maplehelper import mesowatch
    mesowatch.Snapshot.stale = property(lambda self: False)      # last night's copy is fine for a picture
    bar = GameToolbar(s, t)
    bar.set_monster_picture(kb.picture("monster/9"))           # the Orange Mushroom, as the app gives it
    s["loot_overlay"] = True
    bar.refresh()
    bar.show()
    _, natural = bar._width_limits()            # one row, as on a real screen (the offscreen one is narrow)
    bar.setMaximumWidth(natural)
    bar._fitting = True
    bar.resize(natural, bar.layout().totalHeightForWidth(natural))
    app.processEvents()
    bar.grab().save(str(out / "bar.png"))
    card = LootOverlay(scan, s, kb, t)
    card.refresh()
    card.show()
    app.processEvents()
    card.grab().save(str(out / "card.png"))

    d = GrindTracker(scan, kb, s, lang, app.styleSheet())
    d.resize(760, 2600)
    d.show()
    d.refresh()
    app.processEvents()
    body = d.stack.widget(0).widget()
    body.resize(740, body.sizeHint().height())
    app.processEvents()

    def part(name, *items, pad=6):
        r = QRect()
        for it in items:
            r = r.united(it.geometry() if hasattr(it, "geometry") and not hasattr(it, "count") else _layout_rect(it))
        body.grab(r.adjusted(-pad, -pad, pad, pad)).save(str(out / f"{name}.png"))
    part("top", d.alerts, d.goal)
    part("summary", d.summary)
    part("ways", d.ways_box)
    part("kills", d.compare_line, d.kills_box)
    part("items", d.etc_box, d.items_box)
    part("potions", d.potions_box)
    d.grab().save(str(out / "window.png"))
    for tab in ("history", "compare"):
        d.show_tab(tab)
        page = d.stack.currentWidget().widget()
        page.resize(740, page.sizeHint().height())
        app.processEvents()
        h = min(page.height(), 700)
        page.grab(QRect(0, 0, page.width(), h)).save(str(out / f"{tab}.png"))
    scan.stop()
    print(lang, "->", out)


if __name__ == "__main__":
    for lang in (sys.argv[1:] or ["he", "en"]):
        main(lang)
