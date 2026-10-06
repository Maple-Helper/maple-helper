"""Render real Maple Helper windows to PNG for the website and README (step 1 of 2).

  The script points APPDATA at a fresh temp folder itself (never the player's real settings). For each language/mode:
  SHOT_LANG=he|en QT_SCALE_FACTOR=2.5 QT_QPA_PLATFORM=offscreen PYTHONPATH=.       python tools/site_shots.py <raw_dir> light|dark all item/298,item/379
  Step 2: python tools/site_shots_webp.py <raw_dir>  (crops, rounds the corners, writes the site repo's assets/shots)
"""
import os
import sys
import tempfile
import time

# always a fresh, empty APPDATA, set here and not left to the operator: the script writes the language, the theme
# and the active character's stats and crafts, and run from a normal shell it overwrote the owner's real ones
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="mh-site-shots-")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = sys.argv[1]
MODE = sys.argv[2] if len(sys.argv) > 2 else "light"
LANG = os.environ.get("SHOT_LANG", "he")
SUF = "" if LANG == "he" else "-" + LANG
os.makedirs(OUT, exist_ok=True)

app = QApplication([])
app.setStyle("Fusion")
from PySide6.QtGui import QFontDatabase  # noqa: E402
from maplehelper.ui import theme  # noqa: E402

for f in ("SegoeIcons.ttf", "segmdl2.ttf", "seguiemj.ttf", "seguisym.ttf", "segoeui.ttf", "msyh.ttc", "YuGothM.ttc", "msgothic.ttc"):
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + f)   # offscreen Qt sees no system fonts
fam = theme.load_fonts()
theme.FONT_FAMILY = fam
theme.set_mode(MODE)
app.setLayoutDirection(Qt.RightToLeft if LANG == "he" else Qt.LeftToRight)
css = theme.stylesheet(fam, 14)
app.setStyleSheet(css)

from maplehelper.store import Settings, Profiles  # noqa: E402

s = Settings()
s["language"] = LANG
s["appearance"] = MODE
p = Profiles()
if not p.active:
    p.add("Kiwi", "Thief", "Assassin", 34)
from maplehelper.kb import KnowledgeBase  # noqa: E402

kb = KnowledgeBase()
from maplehelper.brain import Brain  # noqa: E402
from maplehelper.ui.overlay import Overlay  # noqa: E402


def pump(sec=1.5):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def overlay(h=760):
    ov = Overlay(s, p, kb, Brain(kb))
    ov.setStyleSheet(css)
    ov.resize(480, h)
    ov.move(40, 40)
    ov.show()
    pump(0.6)
    return ov


def grab(w, name):
    pump(1.5)
    w.grab().save(os.path.join(OUT, name))
    print("saved", name, w.size().width(), w.size().height())


what = sys.argv[3] if len(sys.argv) > 3 else "all"

if what in ("all", "hp"):
    ov = overlay(500)
    ov.ask("כמה HP יש לבלו סנייל?" if LANG == "he" else "What is the HP of Blue Snail?")
    grab(ov, f"hp-{MODE}{SUF}.png")
    ov.close()

if what in ("all", "convo"):
    ov = overlay()
    ov.resize(480, 820)
    ov.ask("כמה HP יש לבלו סנייל?" if LANG == "he" else "What is the HP of Blue Snail?")
    pump(0.3)
    ov.ask("מה Mano מפיל?" if LANG == "he" else "What does Mano drop?")
    pump(0.6)
    grab(ov, f"convo-{MODE}{SUF}.png")
    ov.close()

if what in ("all", "mano"):
    ov = overlay()
    ov.ask("מה Mano מפיל?" if LANG == "he" else "What does Mano drop?")
    pump(0.5)
    ov.scroll.verticalScrollBar().setValue(0)
    grab(ov, f"mano-{MODE}{SUF}.png")
    ov.close()

if what in ("all", "wish"):
    from maplehelper.ui.wishlist import WishlistDialog
    keys = [k for k in sys.argv[4].split(",")] if len(sys.argv) > 4 else []
    d = WishlistDialog(keys, kb, LANG, css)
    d.show()
    grab(d, f"wishlist-{MODE}{SUF}.png")
    d.close()

if what in ("all", "guide"):
    from maplehelper.ui.guides import GuidesDialog
    d = GuidesDialog(kb, p.active, LANG, css, open_key="guide/warrior-class-guide")
    d.resize(500, 760)
    d.show()
    pump(0.6)
    d.open_guide("guide/warrior-class-guide")
    sb = d.browser.verticalScrollBar()
    anchor = os.environ.get("GUIDE_SCROLL")
    if anchor:
        sb.setValue(int(anchor))
    grab(d, f"guide-{MODE}{SUF}.png")
    d.close()

if what in ("all", "settings"):
    from maplehelper.ui.dialogs import SettingsDialog
    d = SettingsDialog(s, p, kb, lambda o=1.0: css)
    d.show()
    grab(d, f"settings-{MODE}{SUF}.png")
    d.close()

if what in ("all", "tools"):
    # the play tools: a Lv. 34 Assassin with stats from the Stat window, on the busiest pages
    c = p.active
    c.stats = {"acc": 78, "dmg_min": 160, "dmg_max": 340}
    c.crafts = {"woodcrafting": 3}
    p.save()
    from maplehelper.ui.tools import PAGES, ToolsDialog
    d = ToolsDialog(kb, p, s, LANG, css, {})
    d.resize(500, 760)
    d.show()
    for page in ("train", "calc", "crafting", "quests", "build", "farm", "town", "pets", "more"):
        d.show_page(PAGES.index(page))
        if page == "crafting":
            d.craft_pick.button(3).click()          # Woodcrafting: Vicious in Henesys Market
        if page == "quests":
            d.q_mode.group.buttons()[1].click()     # the ones not done yet: a full list (the level's tab can be empty)
            pump(1.0)
        grab(d, f"tools-{page}-{MODE}{SUF}.png")
    d.close()
