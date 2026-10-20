"""The NPCs-on-this-map window (ui/npcoverlay.py): the list it builds from the player's map, the way it says to a
picked NPC and where that is measured, the guide it sets (LOCATION.guide) and lets go, and its life as a window —
shown from the setting, placed beside the minimap box, kept where the player leaves it, closed by its ✕."""
import os
import re
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSize, Qt

from maplehelper import routes
from maplehelper.i18n import I18n
from maplehelper.routes import MapInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

BIDI_MARKS = re.compile("[%s]" % "".join(map(chr, (0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)))))
MID, OTHER = "010020000", "010020001"        # a town with NPCs, and a map without any
MM = [2000, 4000, 1000, 2000]                # its minimap: width, height in map units, then the origin offset

# one map-unit step across/up-down, exact in binary so the thresholds are tested without float drift:
# 1/32 across is 125 units (past the 60 that says "left"/"right") and 125 up/down (past the 90 that says
# "up"/"down"); 1/64 is 31.25 and 62.5, both near enough to say nothing
FAR, NEAR = 1 / 32, 1 / 64


class _FakeGraph:
    """What npcoverlay reads of a routes.Graph: its maps' info and names, and the real _spot (it only needs
    .known, so a class attribute binds it to this graph). No map pictures: the guide panel is only words."""

    _spot = routes.Graph._spot
    picture_spot = routes.Graph.picture_spot

    def __init__(self):
        self.known = {
            MID: MapInfo(MID, "Fake Town", "", True, "", MM, [
                {"id": "1", "name": "Cara", "x": 200, "y": 0},        # (0.6, 0.5) on the picture
                {"id": "2", "name": "Al", "x": -800, "y": 2000},      # (0.1, 1.0)
                {"id": "1", "name": "Cara", "x": 1500, "y": 1000},    # the same NPC twice: one row, first place
                {"id": "3", "name": "bob", "x": 5000, "y": 0},        # off the picture: selectable, never guided
            ]),
            OTHER: MapInfo(OTHER, "Other Map", "", True, "", None, []),
        }

    def name(self, mid):
        m = self.known.get(mid)
        return m.name if m else mid

    def minimap(self, mid):
        return None


GRAPH = _FakeGraph()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def clean_location():
    from maplehelper.ui.location import LOCATION
    old = (LOCATION.here, LOCATION.state, LOCATION.follow, LOCATION.guide)
    LOCATION.here = LOCATION.follow = LOCATION.guide = None
    LOCATION.state = ""
    yield LOCATION
    LOCATION.here, LOCATION.state, LOCATION.follow, LOCATION.guide = old


@pytest.fixture
def overlay(qapp, isolated_store, kb, clean_location, monkeypatch):
    from maplehelper.ui import npcoverlay
    monkeypatch.setattr(npcoverlay.routes, "of", lambda kb: GRAPH)
    s = isolated_store.Settings()
    s["npc_overlay"] = True
    w = npcoverlay.NpcOverlay(kb, s, I18n("en"))
    yield w, s, clean_location
    clean_location.changed.disconnect(w.refresh)
    w.close()


def _here(mid=MID, spot=(0.5, 0.5)):
    from maplehelper.minimap import Here, View
    return Here(mid, spot, View(0, 0, 100, 100, (0, 0, 200, 200)))


def _row(w, name: str):
    return w._rows[next(i for i, n in enumerate(w._npcs) if n.name == name)]


# ------------------------------------------------------------ the list


def test_npcs_on_lists_each_npc_once_by_name():
    from maplehelper.ui.npcoverlay import npcs_on
    out = npcs_on(GRAPH, MID)
    assert [n.name for n in out] == ["Al", "bob", "Cara"]     # casefold: bob sits between Al and Cara
    al, bob, cara = out
    assert (al.key, al.spot) == ("npc/2", (0.1, 1.0))
    assert (cara.key, cara.spot) == ("npc/1", (0.6, 0.5))     # the first place it stands, not the second
    assert (bob.key, bob.spot) == ("npc/3", None)             # off the picture


def test_npcs_on_of_a_map_the_kb_doesnt_have():
    from maplehelper.ui.npcoverlay import npcs_on
    assert npcs_on(GRAPH, "999999999") == []


# ------------------------------------------------------------ the way it says


@pytest.mark.parametrize("you,spot,want", [
    ((0.5, 0.5), (0.5 + FAR, 0.5), "npc_guide_right"),
    ((0.5, 0.5), (0.5 - FAR, 0.5), "npc_guide_left"),
    ((0.5, 0.5), (0.5, 0.5 - FAR), "npc_guide_up"),           # the smaller fraction is the higher on screen
    ((0.5, 0.5), (0.5, 0.5 + FAR), "npc_guide_down"),
    ((0.5, 0.5), (0.5 + FAR, 0.5 - FAR), "npc_guide_right_up"),
    ((0.5, 0.5), (0.5 + FAR, 0.5 + FAR), "npc_guide_right_down"),
    ((0.5, 0.5), (0.5 - FAR, 0.5 - FAR), "npc_guide_left_up"),
    ((0.5, 0.5), (0.5 - FAR, 0.5 + FAR), "npc_guide_left_down"),
    ((0.5, 0.5), (0.5 + NEAR, 0.5), "npc_guide_here"),        # 31.25 units aside: right by you
    ((0.5, 0.5), (0.5, 0.5 + NEAR), "npc_guide_here"),        # 62.5 units below: right by you
    ((0.5, 0.5), (0.5, 0.5), "npc_guide_here"),
    (None, (0.5, 0.5), "npc_guide_marked"),                   # no dot for the player (a shop folds the minimap)
    ((0.5, 0.5), None, "npc_guide_unplaced"),                 # no place for the NPC
])
def test_direction_where_the_npc_is_from_the_player(you, spot, want):
    from maplehelper.ui.npcoverlay import direction
    assert direction(GRAPH, MID, you, spot) == want


def test_direction_on_a_map_with_no_minimap_to_measure_on():
    from maplehelper.ui.npcoverlay import direction
    assert direction(GRAPH, OTHER, (0.5, 0.5), (0.9, 0.9)) == "npc_guide_marked"
    assert direction(GRAPH, "999999999", (0.5, 0.5), (0.9, 0.9)) == "npc_guide_marked"


def test_inside_a_shop_its_npcs_stand_on_the_room_picture(overlay, monkeypatch):
    """A shop has no minimap (the game folds it there, so the player has no dot) but its picture is the whole room,
    framed by the map's view rectangle (MapInfo.scene): its NPCs are placed on it and the guide marks them (the
    owner's, 2026-10-10: inside a shop it said "your spot isn't known yet" with no NPC on the picture)."""
    from maplehelper.ui import npcoverlay
    w, _, loc = overlay
    shop = "010020005"
    g = _FakeGraph()
    g.known[shop] = MapInfo(shop, "Fake Salon", "", True, "", None, [{"id": "414", "name": "Andre", "x": -72, "y": -12}],
                            scene=[800, 600, 400, 300])           # vr = (-400, -300, 400, 300)
    monkeypatch.setattr(npcoverlay.routes, "of", lambda kb: g)
    loc.set(_here(shop, None))
    andre = w._npcs[0]
    assert andre.spot == pytest.approx((328 / 800, 288 / 600))
    _row(w, "Andre").click()
    assert loc.guide == (shop, andre.spot, "npc/414")
    assert w._line.text() == "Andre is marked on the map"


# ------------------------------------------------------------ the window


def test_it_shows_only_while_the_setting_is_on(overlay):
    w, s, loc = overlay
    s["npc_overlay"] = False
    loc.set(_here())
    w.refresh()
    assert not w.isVisible()
    s["npc_overlay"] = True
    loc.set(_here(spot=(0.4, 0.4)))          # a later read: the window follows it out
    assert w.isVisible()


def test_it_lists_the_maps_npcs_and_asks_for_a_pick(overlay):
    w, _, loc = overlay
    loc.set(_here())
    assert [r.text() for r in w._rows] == ["Al", "bob", "Cara"]
    assert w._title.text() == "NPCs Here"
    assert w._map_name.text() == "Fake Town" and not w._map_name.isHidden()
    assert w._status.text() == "Click an NPC to be guided to it"


def test_without_a_read_it_says_the_map_isnt_known(overlay):
    w, _, _ = overlay
    w.refresh()
    assert w.isVisible() and not w._rows
    assert w._status.text() == "Your map isn't known yet"


def test_a_map_with_no_npcs_says_so(overlay):
    w, _, loc = overlay
    loc.set(_here(OTHER))
    assert not w._rows and w._status.text() == "No NPCs on this map"


def test_a_map_with_no_npcs_folds_the_window_and_one_with_npcs_opens_it_again(overlay):
    """Nothing to list, nothing to scroll through: the window folds to its header and the line saying so (the
    owner's: "with maps with no npcs it should be collapsed"), and opens back to the height it had."""
    from maplehelper.ui import npcoverlay
    w, s, loc = overlay
    loc.set(_here())
    w.resize(w.width(), 500)
    loc.set(_here(OTHER))
    assert w._collapsed and not w._scroll.isVisible() and w._status.text() == "No NPCs on this map"
    assert w.height() < npcoverlay.MIN_H and w.minimumHeight() == w.maximumHeight() == w.height()
    w._remember()
    assert s["npc_overlay_geom"]["h"] == 500                  # the height it opens back to, not the fold's
    loc.set(_here())
    assert not w._collapsed and w._scroll.isVisible() and w.height() == 500
    assert w.minimumHeight() == npcoverlay.MIN_H


def test_an_npc_inside_a_shop_guides_to_its_door_then_to_itself_inside(overlay, monkeypatch):
    """An NPC in a building off the map is listed under its building and guided to the building's door; walking in,
    it is picked again there and guided to where it stands inside."""
    from maplehelper.routes import Leg
    from maplehelper.ui import npcoverlay
    w, _, loc = overlay
    shop, ally = "010020005", "010020006"
    g = _FakeGraph()
    g.known[shop] = MapInfo(shop, "Fake Shop", "", True, "", MM, [{"id": "7", "name": "Dee", "x": 0, "y": 0}])
    g.known[ally] = MapInfo(ally, "Fake Alley", "", False, "", MM, [{"id": "8", "name": "Eve", "x": 0, "y": 0}])
    g.edges = {MID: [Leg(MID, shop, "portal", "in01", spot=(0.25, 0.5)), Leg(MID, ally, "portal", "west00",
                                                                              spot=(0.0, 0.5))],
               shop: [Leg(shop, MID, "portal", "out00", spot=(0.5, 0.5))], ally: [Leg(ally, MID, "portal", "e")]}
    monkeypatch.setattr(npcoverlay.routes, "of", lambda kb: g)
    loc.set(_here())
    assert [n.name for n in w._npcs] == ["Al", "bob", "Cara", "Dee"]       # not the alley's Eve: not a door
    assert [h.text() for h, _ in w._heads] == ["Inside Fake Shop"]
    _row(w, "Dee").click()
    assert loc.guide == (MID, (0.25, 0.5), "npc/7")                          # her building's door here
    assert w._line.text() == "Dee is inside Fake Shop. The door to Fake Shop is to your left"
    loc.set(_here(shop, (0.9, 0.5)))                                           # walked in
    assert w._sel is not None and w._sel.name == "Dee" and not w._sel.inside
    assert loc.guide == (shop, (0.5, 0.5), "npc/7")                           # where she stands inside
    assert w._line.text() == "Dee is to your left"


def test_clicking_a_row_guides_and_clicking_it_again_lets_go(overlay):
    w, _, loc = overlay
    loc.set(_here())
    _row(w, "Cara").click()
    mid, spot, key = loc.guide
    assert (mid, key) == (MID, "npc/1") and spot == (0.6, 0.5)
    assert w._sel.name == "Cara" and w._guide.isVisible()
    assert w._line.text() == "Cara is to your right"   # 200 map units aside of the player at (0.5, 0.5)
    assert _row(w, "Cara").property("sel") is True
    _row(w, "Cara").click()                   # the same row again: the guide goes
    assert loc.guide is None and w._sel is None and not w._guide.isVisible()
    assert _row(w, "Cara").property("sel") is False


def test_an_npc_with_no_place_is_selectable_but_never_guides(overlay):
    w, _, loc = overlay
    loc.set(_here())
    _row(w, "bob").click()
    assert w._sel.name == "bob" and loc.guide is None and w._guide.isVisible()


def test_the_way_follows_the_player_on_the_same_map(overlay):
    w, _, loc = overlay
    loc.set(_here(spot=(0.1, 0.5)))
    _row(w, "Cara").click()                   # Cara at 0.6: far to the right of the player at 0.1
    assert w._line.text() == "Cara is to your right"
    loc.set(_here(spot=(0.6, 0.5)))           # the player walks to her: the list stays, the sentence moves
    assert [r.text() for r in w._rows] == ["Al", "bob", "Cara"]
    assert loc.guide == (MID, (0.6, 0.5), "npc/1")
    assert w._line.text() == "Cara is right by you"


def test_a_new_map_rebuilds_the_list_and_lets_the_guide_go(overlay):
    w, _, loc = overlay
    loc.set(_here())
    _row(w, "Cara").click()
    loc.set(_here(OTHER))
    assert loc.guide is None and w._sel is None and not w._rows
    loc.set(_here())                          # and back: the earlier pick is not remembered
    _row(w, "Al").click()
    assert loc.guide == (MID, (0.1, 1.0), "npc/2")


def test_the_stop_guiding_button_clears_the_pick(overlay):
    w, _, loc = overlay
    loc.set(_here())
    _row(w, "Cara").click()
    w._clear.click()
    assert loc.guide is None and w._sel is None and not w._guide.isVisible()
    assert w._status.text() == "Click an NPC to be guided to it"


def test_the_x_turns_the_setting_off_and_hides(overlay):
    w, s, loc = overlay
    loc.set(_here())
    _row(w, "Cara").click()
    w._close.click()
    assert s["npc_overlay"] is False and not w.isVisible() and loc.guide is None


def test_the_x_also_says_it_closed(overlay):
    """The toolbar's NPCs-here button unchecks when the window's own ✕ closes it (app.py wires closed → its
    refresh)."""
    w, _, _ = overlay
    seen = []
    w.closed.connect(lambda: seen.append(True))
    w._close.click()
    assert seen == [True]


def test_the_guide_is_its_own_window_docked_left_of_the_list(overlay):
    """The way to the picked NPC opens as a small window of its own beside the list (the owner's: "a little window
    to the left, not part of the same NPCs here window"), and goes with it: moved with the list, hidden with it."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QGuiApplication
    from maplehelper.ui import npcoverlay
    w, _, loc = overlay
    room = QGuiApplication.primaryScreen().availableGeometry()
    loc.set(_here())                          # shown at its own place first, then moved
    w.move(room.left() + 400, room.top() + 50)
    _row(w, "Cara").click()
    g = w._guide
    assert g.isVisible() and g.isWindow() and g is not w and not w.isAncestorOf(g)
    assert g.title.text() == "Cara"
    gap = (w.x() + npcoverlay.SHADOW) - (g.x() + g.width() - npcoverlay.SHADOW)    # panel edge to panel edge
    assert gap == npcoverlay.GUIDE_GAP and g.y() == w.y()
    w.move(w.pos() + QPoint(30, 20))
    assert g.x() + g.width() - npcoverlay.SHADOW == w.x() + npcoverlay.SHADOW - npcoverlay.GUIDE_GAP
    assert g.y() == w.y()
    g.close_btn.click()                       # its ✕ stops guiding; the list stays
    assert not g.isVisible() and loc.guide is None and w.isVisible()
    _row(w, "Cara").click()
    w.hide()
    assert not g.isVisible()


def test_the_guide_opens_right_of_the_list_with_no_room_left(overlay):
    from PySide6.QtGui import QGuiApplication
    from maplehelper.ui import npcoverlay
    w, _, loc = overlay
    room = QGuiApplication.primaryScreen().availableGeometry()
    loc.set(_here())
    w.move(room.left() - npcoverlay.SHADOW, room.top() + 50)    # the list's panel against the screen's left edge
    _row(w, "Cara").click()
    g = w._guide
    assert g.x() + npcoverlay.SHADOW == w.x() + w.width() - npcoverlay.SHADOW + npcoverlay.GUIDE_GAP


def test_set_kb_builds_the_list_again(overlay, monkeypatch):
    w, _, loc = overlay
    loc.set(_here())                       # the player's map first: set_kb rebuilds the list of that map
    from maplehelper.ui import npcoverlay
    other = _FakeGraph()
    other.known[MID] = MapInfo(MID, "Fake Town", "", True, "", MM, [{"id": "9", "name": "New Face", "x": 0, "y": 0}])
    monkeypatch.setattr(npcoverlay.routes, "of", lambda kb: other)   # a KB update: the same map, other NPCs
    w.set_kb(w._kb)
    assert [r.text() for r in w._rows] == ["New Face"]


def test_apply_language_retexts_and_mirrors(overlay):
    w, _, loc = overlay
    loc.set(_here())
    w.apply_language(I18n("he"))
    assert w.layoutDirection() == Qt.RightToLeft
    plain = BIDI_MARKS.sub("", w._title.text())
    assert plain == "NPC במפה"
    from maplehelper import bidi
    assert w._map_name.text() == bidi.ltr_name("Fake Town", True)     # one left-to-right block, under the title
    from maplehelper import bidi
    assert w._status.text() == bidi.plain("לחצו על NPC כדי להגיע אליו", True)


# ------------------------------------------------------------ its place on the screen


def test_it_opens_just_right_of_the_drawn_minimap_box(overlay):
    from maplehelper.ui import npcoverlay
    w, s, loc = overlay
    s["minimap_region"] = {"x": 100, "y": 100, "w": 200, "h": 200}    # 1:1 on the offscreen screen
    loc.set(_here())
    assert w.pos() == QPoint(100 + 200 + npcoverlay.GAP, 100)        # its top lined up with the box's


def test_the_place_is_kept_when_moved_and_reopened_there(overlay):
    w, s, loc = overlay
    loc.set(_here())
    w.move(90, 70)
    w.resize(280, 330)
    w._remember()                            # the debounced save, as the 400 ms timer would fire it
    assert s["npc_overlay_geom"] == {"x": 90, "y": 70, "w": 280, "h": 330}
    w.hide()
    w.refresh()
    assert w.pos() == QPoint(90, 70) and w.size() == QSize(280, 330)


# ------------------------------------------------------------ the real knowledge base

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "routes.json").exists(), reason="no routes.json in the real knowledge base")


@pytest.fixture(scope="module")
def real():
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@needs_kb
def test_ellinias_npcs_are_one_row_each_where_the_kb_places_them(real):
    from maplehelper.ui.npcoverlay import npcs_on
    out = npcs_on(routes.of(real), "010002000")
    own = [n.name for n in out if not n.inside]
    names = [n.name for n in out]
    assert own == sorted(own, key=str.casefold)
    if "Regular Cab" not in names:           # the KB's Ellinia moved since this test knew it
        pytest.skip("Ellinia's NPCs aren't where this test knows them")
    assert len(names) == len(set(names))
    fr = next((n for n in out if n.name == "Francois"), None)
    assert fr is not None and fr.key == "npc/303"
    assert fr.spot == pytest.approx((0.540, 0.480), abs=0.01)


@needs_kb
def test_kerning_citys_buildings_list_their_npcs_at_their_doors(real):
    """The NPCs inside the shops off a town are listed too (the owner's, 2026-10-10: "I'm in Kerning City but it's
    not listing the NPCs inside shops"), each guided to its building's door: the doors the game names in00.., into
    a map that leads back. Not the subway or the construction site, which are maps of their own."""
    from maplehelper.ui.npcoverlay import buildings_off, npcs_on
    g = routes.of(real)
    doors = {leg.to: leg.spot for leg in buildings_off(g, "010003000")}
    if "010003002" not in doors:              # the KB's Kerning City moved since this test knew it
        pytest.skip("Kerning City's buildings aren't where this test knows them")
    assert {"010003001", "010003002", "010003004", "010003007"} <= set(doors)     # shops, hospital, civic center
    assert not {"010003060", "010003010"} & set(doors)                             # subway, construction site
    out = npcs_on(g, "010003000")
    inside = [n.inside for n in out]
    assert inside[:inside.index("010003002")] == [""] * len([n for n in out if not n.inside]) + [
        i for i in inside[:inside.index("010003002")] if i]                         # the town's own NPCs first
    faymus = next(n for n in out if n.name == "Dr. Faymus")
    assert faymus.inside == "010003002" and faymus.spot == doors["010003002"]


@needs_kb
def test_a_real_click_guides_with_the_map_picture(real, qapp, isolated_store, clean_location):
    from maplehelper.ui import npcoverlay
    s = isolated_store.Settings()
    s["npc_overlay"] = True
    w = npcoverlay.NpcOverlay(real, s, I18n("en"))
    try:
        clean_location.set(_here("010002000"))
        fr = next((n for n in w._npcs if n.name == "Francois"), None)
        if fr is None:
            pytest.skip("Ellinia's NPCs aren't where this test knows them")
        w._rows[w._npcs.index(fr)].click()
        assert clean_location.guide == ("010002000", fr.spot, "npc/303")
        assert not w._pic.pixmap().isNull()          # the map's own picture, with her dot and the player's
    finally:
        clean_location.changed.disconnect(w.refresh)
        w.close()


def test_turning_it_off_from_the_bar_or_settings_lets_the_guide_go(overlay):
    """Only the ✕ cleared the pick: switched off elsewhere, the ring stayed on the game's minimap."""
    w, s, loc = overlay
    loc.set(_here())
    _row(w, "Cara").click()
    assert loc.guide is not None
    s["npc_overlay"] = False
    w.refresh()
    assert not w.isVisible() and loc.guide is None and w._sel is None


def test_the_default_look_follows_the_apps_appearance_live(overlay, monkeypatch):
    """The default look ("app") is the app's own: light glass and its dark text in the light appearance, dark
    glass and light text in the dark one. A change of appearance restyles the open window and its guide on the
    app's restyle (apply_look, then refresh), not on the next start."""
    from maplehelper.ui import npcoverlay, theme
    monkeypatch.setattr(theme, "HIGH_CONTRAST", False)
    w, s, loc = overlay
    loc.set(_here())
    try:
        for mode in ("light", "dark"):
            s["appearance"] = mode
            npcoverlay.apply_look(s)
            w.refresh()
            c = theme.PALETTES[mode]
            assert npcoverlay.current_tint() == c["glass"]
            for sheet in (w.styleSheet(), w._guide.styleSheet()):
                assert f"#Title {{ color: {c['text']};" in sheet and f"#Status {{ color: {c['muted']};" in sheet
                assert f"QPushButton {{ background: {c['fill2']}; color: {c['text']};" in sheet
            assert theme.ORANGE_DEEP in w.styleSheet()               # the app's orange lights the picked row
    finally:
        npcoverlay.apply_look({})


# ------------------------------------------------------------ pictures, the way there, the title


def _png(path, w=60, h=90):
    from PySide6.QtGui import QColor, QPixmap
    pm = QPixmap(w, h)
    pm.fill(QColor("#3a7"))
    assert pm.save(str(path))
    return path


def test_each_npc_row_has_its_picture(overlay, tmp_path, monkeypatch):
    """Every row starts with the NPC's KB picture, scaled into a 32 px box with its shape kept."""
    from maplehelper.ui import npcoverlay
    w, _, loc = overlay
    pics = {"npc/1": _png(tmp_path / "cara.png")}
    monkeypatch.setattr(w._kb, "picture", lambda key: pics.get(key))
    loc.set(_here())
    cara, al = _row(w, "Cara"), _row(w, "Al")
    assert cara.pic.size() == QSize(npcoverlay.PIC, npcoverlay.PIC)
    pm = cara.pic.pixmap()
    assert pm.height() == npcoverlay.PIC and pm.width() < npcoverlay.PIC      # 60x90 kept its shape
    assert al.pic.pixmap().isNull()              # no picture to read: an empty box, the names still lined up
    assert cara.text() == "Cara" and cara.name.wordWrap()                     # a long name wraps, never cut


def test_a_long_npc_name_wraps_inside_the_list_and_keeps_its_way_button(overlay, qapp, monkeypatch):
    """A button's own text neither wraps nor elides: a long name pushed the row past the list's width, its start
    and the way button cut off. Now the name wraps and the row stays inside the list."""
    from maplehelper.ui import npcoverlay
    w, _, loc = overlay
    g = _FakeGraph()
    long = "Community Board of the Very Long Kerning City Construction Site"
    g.known[MID] = MapInfo(MID, "Fake Town", "", True, "", MM, [{"id": "9", "name": long, "x": 0, "y": 0}])
    monkeypatch.setattr(npcoverlay.routes, "of", lambda kb: g)
    w.apply_language(I18n("he"))
    loc.set(_here())
    w.resize(npcoverlay.MIN_W, 400)
    w.show()
    qapp.processEvents()
    row, way, view = w._rows[0], w._ways[0], w._scroll.viewport()
    assert BIDI_MARKS.sub("", row.text()) == long
    for part in (row, way):
        left = part.mapTo(view, part.rect().topLeft()).x()
        assert left >= 0 and left + part.width() <= view.width()
    assert row.name.height() >= row.name.heightForWidth(row.name.width())


def test_picture_reads_the_kbs_own_or_its_category_icon(kb):
    """kb.picture: an NPC the KB has no picture of still gets its category's icon, never nothing."""
    from maplehelper.ui import npcoverlay
    pm = npcoverlay.picture(kb, "npc/999999")
    assert not pm.isNull() and max(pm.width(), pm.height()) == npcoverlay.PIC
    assert npcoverlay.picture(object(), "npc/1").isNull()     # no KB to ask


def test_how_to_get_there_beside_each_npc_opens_the_way_from_here(overlay):
    """Beside each row, "How to get there": the map window on the way to that NPC (MAP_REQUESTS, as an NPC card's
    button) — and the row itself still guides on the minimap, without asking for the window."""
    from maplehelper.ui.widgets import MAP_REQUESTS
    w, _, loc = overlay
    loc.set(_here())
    asked = []
    MAP_REQUESTS.requested.connect(asked.append)
    try:
        assert len(w._ways) == len(w._rows) == 3
        way = w._ways[[n.name for n in w._npcs].index("Cara")]
        assert way.toolTip() == "How to get there" and way.accessibleName() == "How to get there"
        way.click()
        assert asked == ["npc/1"] and loc.guide is None          # the way asked for, nothing picked
        _row(w, "Cara").click()
        assert asked == ["npc/1"] and loc.guide == (MID, (0.6, 0.5), "npc/1")
        w.apply_language(I18n("he"))
        assert way.toolTip() == "איך מגיעים"
    finally:
        MAP_REQUESTS.requested.disconnect(asked.append)


def test_the_way_button_sits_at_the_rows_end_and_the_picture_at_its_start_in_hebrew(overlay, qapp):
    """Hebrew reads from the right: the row (its picture first) on the right, the way button on its left."""
    w, _, loc = overlay
    w.apply_language(I18n("he"))
    loc.set(_here())
    qapp.processEvents()
    row, way = w._rows[0], w._ways[0]
    assert row.isRightToLeft()
    assert way.mapTo(w, way.rect().topLeft()).x() < row.mapTo(w, row.rect().topLeft()).x()


def test_a_long_map_name_shows_in_full_under_the_title(overlay, monkeypatch, qapp):
    """The window's name on its own line and the map's under it, wrapped — never one line with its start cut off
    ("rest North of Ellinia • NPC במפה", the owner's)."""
    from maplehelper.ui import npcoverlay
    w, _, loc = overlay
    long = "The Forest North of Ellinia, Deep Inside the Very Long Mushroom Valley"
    g = _FakeGraph()
    g.known[MID] = MapInfo(MID, long, "", True, "", MM, g.known[MID].npcs)
    monkeypatch.setattr(npcoverlay.routes, "of", lambda kb: g)
    w.apply_language(I18n("he"))
    loc.set(_here())
    w.resize(npcoverlay.MIN_W, npcoverlay.MIN_H)
    qapp.processEvents()
    lb = w._map_name
    assert BIDI_MARKS.sub("", lb.text()) == long and lb.wordWrap()
    assert BIDI_MARKS.sub("", w._title.text()) == "NPC במפה"
    assert lb.height() >= lb.heightForWidth(lb.width())             # every wrapped line fits
    assert lb.mapTo(w, lb.rect().topRight()).x() < w.width()         # inside the window
