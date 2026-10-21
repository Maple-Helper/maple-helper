"""A live look at the grind tracker full of data: the card over the game and the full tracker (this session, the
history and where it pays best), open on the screen from a made-up session in a throwaway store (the player's own
settings, logs and history are never read or written; only the players' prices are copied, read-only). A new pickup
lands every few seconds, so the live tracking and the numbers move as in a real session.

    python tools/tracker_demo.py [he|en]

Close the tracker window to end it. Reads nothing from the game and never touches it: the scanner's reads are off."""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["APPDATA"] = os.environ["LOCALAPPDATA"] = _tmp
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_real = Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Roaming" / "MapleHelper"
(Path(_tmp) / "MapleHelper").mkdir(exist_ok=True)
if (_real / "mesowatch.json").exists():         # the players' prices, read-only copy
    shutil.copy(_real / "mesowatch.json", Path(_tmp) / "MapleHelper" / "mesowatch.json")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

LANG = sys.argv[1] if len(sys.argv) > 1 else "he"
ETC = [("Horny Mushroom Cap", "item/366"), ("Zombie's Lost Tooth", "item/2635"), ("Leaf", "item/358"),
       ("Firewood", "item/362"), ("Mithril Ore", "item/415")]
HP, MP = [("Orange Potion", "item/271"), ("White Potion", "item/272")], [("Blue Potion", "item/273")]


def main() -> None:
    app = QApplication([])
    from maplehelper.ui import theme
    fam = theme.load_fonts()
    theme.FONT_FAMILY = fam
    theme.set_mode("light")
    app.setLayoutDirection(Qt.RightToLeft if LANG == "he" else Qt.LeftToRight)
    app.setStyleSheet(theme.stylesheet(fam, 14))

    from maplehelper import lootdb, lootlog, mesowatch, plan, routes, store
    from maplehelper.hud import Hud
    from maplehelper.i18n import I18n
    from maplehelper.kb import KnowledgeBase
    from maplehelper.minimap import Here
    from maplehelper.ui.grindtracker import GrindTracker
    from maplehelper.ui.location import LOCATION
    from maplehelper.ui.lootoverlay import LootOverlay
    from maplehelper.ui.lootscan import LootScanner
    from maplehelper.ui.npcoverlay import apply_look

    mesowatch.Snapshot.stale = property(lambda self: False)      # the copied prices count, however old
    kb = KnowledgeBase(ROOT / "data" / "kb")
    p = store.Profiles()
    p.add("Kalimero", "Thief", "Assassin", 23)
    s = store.Settings()
    s["language"], s["appearance"], s["overlay_accent"] = LANG, "light", "app"
    s["tracker_guide_seen"] = True
    try:                                         # the windows over the game as the player set them (read-only)
        import json
        mine = json.loads((_real / "settings.json").read_text(encoding="utf-8"))
        for k in ("npc_overlay_opacity", "overlay_tint"):
            if k in mine and k in s.data:
                s[k] = mine[k]
    except (OSError, ValueError, AttributeError):
        pass
    apply_look(s)
    t = I18n(LANG)
    g = routes.of(kb)
    maps = [next(m for m in g.maps if g.name(m) == n) for n in
            ("Ant Tunnel I", "The Burnt Land I", "Ant Tunnel II", "Hunting Ground in the Deep Forest I")
            if any(g.name(m) == n for m in g.maps)]
    gear = [(e["name"], k) for k, e in kb.entities.items() if e.get("category") == "item"
            and str(e.get("type", "")).startswith("Equip") and (lootlog.sell_price(kb, k) or 0) > 150][:4]
    scan = LootScanner(kb, p, s)
    scan._timer.timeout.disconnect()            # no reads: nothing of the game is looked at
    cid = scan.cid()
    E = lootlog.Event
    from maplehelper import tables
    exp_of = {r["key"]: r["exp"] for r in tables.rows(kb, "monsters")}
    horny, zombie = exp_of["monster/19"], exp_of["monster/21"]
    rnd = random.Random(7)

    def past(start, secs, lvl, mid, kills, mesos):
        lid = lootdb.open_log(cid, "Kalimero", start, lvl)
        evs = [E(start + 5 * i, "exp", horny if i % 3 else zombie, map=mid) for i in range(kills)]
        evs.append(E(start + 60, "mesos", mesos, map=mid))
        for j, (name, key) in enumerate(ETC[:3 + rnd.randrange(3)]):
            evs.append(E(start + 90 + j, "item", 5 + rnd.randrange(40), name, key, map=mid))
        for j, (name, key) in enumerate(gear[:rnd.randrange(3)]):
            evs.append(E(start + 100 + j, "item", 1, name, key, map=mid))
        evs.append(E(start + 120, "potion", 10 + rnd.randrange(30), *HP[rnd.randrange(2)], map=mid))
        evs.append(E(start + 130, "potion", 5 + rnd.randrange(15), *MP[0], map=mid))
        lootdb.add_events(lid, evs, kb)
        lootdb.set_spots(lid, {mid: secs})
        lootdb.end_log(lid, start + secs)

    now = time.time()
    for i in range(8):                           # a week of sessions over the maps: the history and the compare
        past(now - (8 - i) * 86400, 1500 + rnd.randrange(3000), 20 + i // 3, maps[i % len(maps)],
             150 + rnd.randrange(350), 8000 + rnd.randrange(25000))

    # this session: 25 minutes in, on Ant Tunnel I
    scan.start()
    scan._log.start = now - 1500
    evs = []
    for i in range(150):
        evs.append(E(now - 1490 + 10 * i, "exp", horny if i % 4 else zombie))
        if i % 3 == 0:
            evs.append(E(now - 1488 + 10 * i, "mesos", 30 + i % 25))
    for j, (name, key) in enumerate(ETC):
        evs.append(E(now - 900 + 60 * j, "item", 2 + rnd.randrange(12), name, key))
    for j, (name, key) in enumerate(gear[:2]):
        evs.append(E(now - 500 + 60 * j, "item", 1, name, key))
    evs += [E(now - 200, "potion", 9, *HP[0]), E(now - 150, "potion", 3, *HP[1]), E(now - 100, "potion", 6, *MP[0])]
    scan._log.add(evs)
    scan._log.level = 23
    scan._log.on_map(maps[0], 1500)
    need = plan.exp_table(kb)[23]
    scan.hud = Hud(23, "Assassin", "Kalimero", int(need * 0.62), 62.0)
    scan.quick_state = "ok"
    scan.potion_counts = {"item/271": 14, "item/272": 120, "item/273": 80}
    LOCATION.set(Here(maps[0]))

    card = LootOverlay(scan, s, kb, t)
    d = GrindTracker(scan, kb, s, LANG, app.styleSheet())
    scan.changed.connect(card.refresh)
    scr = app.primaryScreen().availableGeometry()
    card.refresh()
    card.move(scr.right() - card.width() - 40, scr.top() + 60)
    card.show()
    tools_win = {}

    def tools(page: str):                        # the Play tools, as the app opens them from a link
        from maplehelper.ui.tools import ToolsDialog
        w = tools_win.get("w")
        if w is None or not w.isVisible():
            w = tools_win["w"] = ToolsDialog(kb, p, s, LANG, app.styleSheet(), {}, page)
            w.show()
        w.raise_()
        return w
    d.quest_requested.connect(lambda name: tools("quests").goto_quest(name))
    d.recipe_requested.connect(lambda prof, name: tools("crafting").goto_recipe(prof, name))
    d.resize(820, min(scr.height() - 120, 1000))
    d.move(scr.center().x() - 410, scr.top() + 60)
    d.show()
    d.raise_()

    def pickup() -> None:                        # a new pickup every few seconds: the live parts move
        k = rnd.random()
        at = time.time()
        if k < 0.45:
            ev = E(at, "exp", horny if rnd.random() < 0.75 else zombie)
        elif k < 0.65:
            ev = E(at, "mesos", 20 + rnd.randrange(40))
        elif k < 0.9:
            name, key = ETC[rnd.randrange(len(ETC))]
            ev = E(at, "item", 1 + rnd.randrange(3), name, key)
        else:
            name, key = HP[0] if rnd.random() < 0.6 else MP[0]
            ev = E(at, "potion", 1, name, key)
        scan._log.add([ev])
        scan.changed.emit()
    feed = QTimer(interval=3000, timeout=pickup)
    feed.start()
    d.finished.connect(app.quit)
    app.exec()
    scan.stop()


if __name__ == "__main__":
    main()
