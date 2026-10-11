"""The hidden-portal dots over the game's minimap (ui/portaldots.py): placed from each minimap read's view over the
drawn box, hidden when there is nothing to place; always on (no Settings switch), and kept out of screen captures
while every other window over the game shows in them."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

BOX = {"x": 100, "y": 100, "w": 200, "h": 200}


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def clean_location():
    from maplehelper.ui.location import LOCATION
    old = LOCATION.here, LOCATION.state, LOCATION.follow, LOCATION.guide
    LOCATION.here, LOCATION.state, LOCATION.follow, LOCATION.guide = None, "", None, None
    yield LOCATION
    LOCATION.here, LOCATION.state, LOCATION.follow, LOCATION.guide = old


@pytest.fixture
def dots(qapp, isolated_store, kb, clean_location, monkeypatch):
    from maplehelper.ui import portaldots
    spots = {"010003000": [(0.5, 0.5), (0.9, 0.9), (0.0, 0.0)]}
    monkeypatch.setattr(portaldots.routes, "of", lambda kb: SimpleNamespace(hidden_spots=lambda m: spots.get(m, [])))
    s = isolated_store.Settings()
    s["minimap_region"] = dict(BOX)
    w = portaldots.PortalDots(kb, s)
    yield w, s, clean_location
    clean_location.changed.disconnect(w.refresh)
    clean_location.followed.disconnect(w.refresh)
    clean_location.guided.disconnect(w.refresh)
    w.close()


def _here(mid="010003000", panel=(0, 0, 200, 200)):
    from maplehelper.minimap import Here, View
    return Here(mid, (0.3, 0.3), View(10, 20, 100, 50, panel))


def _place(loc, here):
    """A confirmed read that placed its map, as the scanner hands it over: the map, and the follow started there."""
    loc.set(here)
    loc.set_follow((here.map, here.view))


def test_dots_sit_on_the_box_where_the_map_was_placed(dots, qapp):
    """Each hidden portal in view is a blue dot at its place in the drawn box; one outside the window shown is
    left out. The window covers the box exactly and never takes a click."""
    from PySide6.QtCore import QPointF, QRect, Qt
    w, _, loc = dots
    _place(loc, _here(panel=(15, 5, 200, 200)))
    assert w.isVisible() and w.geometry() == QRect(100, 100, 200, 200)
    assert w._dots == [QPointF(60, 45), QPointF(100, 65)]          # (0, 0) lands at x=10, left of the panel
    assert w.testAttribute(Qt.WA_TransparentForMouseEvents)
    img = w.grab().toImage()
    c = img.pixelColor(60, 45)
    assert c.blue() > 200 and c.red() < 80                          # a blue dot, drawn
    assert img.pixelColor(150, 150).alpha() == 0                   # and nothing else


@pytest.mark.parametrize("change", ["no_view", "no_read", "no_box", "none_in_view", "other_map"])
def test_nothing_to_place_hides_the_dots(dots, change):
    from maplehelper.minimap import Here
    w, s, loc = dots
    _place(loc, _here())
    assert w.isVisible()
    if change == "no_view":
        _place(loc, Here("010003000", (0.3, 0.3)))
    elif change == "no_read":
        loc.set(None)
    elif change == "no_box":
        s["minimap_region"] = None
        w.refresh()
    elif change == "none_in_view":
        _place(loc, _here(panel=(150, 150, 200, 200)))
    else:
        _place(loc, _here("100000000"))                 # a map with no hidden portals
    assert not w.isVisible() and w._dots == []


def test_the_guided_npc_rings_where_the_map_was_placed(dots):
    """A guide rings its NPC green on the game's minimap (wider than the blue dots) from the follow alone: on a map
    without hidden portals the ring shows by itself, and beside the dots on one with them. Another map's guide, or
    none, leaves the window with nothing to draw."""
    from PySide6.QtCore import QPointF

    w, _, loc = dots
    _place(loc, _here("100000000"))                      # a map with no hidden portals: nothing to draw
    assert not w.isVisible()
    loc.set_guide(("100000000", (0.5, 0.5), "npc/303"))
    assert w.isVisible() and w._dots == [] and w._ring == QPointF(60, 45)
    img = w.grab().toImage()
    c = img.pixelColor(60, 38)                            # on the ring, 7 px above its centre
    assert c.green() > 90 and c.red() < 90                # the NPC's green, drawn
    loc.set_guide(("010003000", (0.5, 0.5), "npc/303"))   # a guide for another map: nothing to ring here
    assert not w.isVisible() and w._ring is None
    loc.set_guide(("100000000", (0.5, 0.5), "npc/303"))
    loc.set_guide(None)                                   # guiding stopped: the ring goes with it
    assert not w.isVisible() and w._ring is None
    _place(loc, _here())                                  # a map with hidden portals, guided there
    loc.set_guide(("010003000", (0.5, 0.5), "npc/303"))
    assert w.isVisible() and w._dots and w._ring == QPointF(60, 45)    # beside the dots


def test_the_follow_moves_the_dots_between_reads_and_a_lost_one_hides_them(dots):
    """The fast follow is newer than the read: the dots move with it (the minimap scrolled 5 px left); a follow
    that lost the picture (the loading screen) or sees another map (a teleport not yet confirmed by the reads) hides
    them at once, while the read still names the old map."""
    from PySide6.QtCore import QPointF

    from maplehelper.minimap import View
    w, _, loc = dots
    _place(loc, _here())
    assert w._dots[0] == QPointF(60, 45)
    loc.set_follow(("010003000", View(5, 20, 100, 50, (0, 0, 200, 200))))
    assert w.isVisible() and w._dots[0] == QPointF(55, 45)
    loc.set(_here())                                    # an older read landing after it: the follow still counts
    assert w._dots[0] == QPointF(55, 45)
    loc.set_follow(("010003000", None))
    assert not w.isVisible()
    loc.set_follow(("010003000", View(5, 20, 100, 50, (0, 0, 200, 200))))
    assert w.isVisible()
    loc.set_follow(("100000000", View(5, 20, 100, 50, (0, 0, 200, 200))))
    assert not w.isVisible()


def test_entering_a_shop_never_flashes_the_streets_dots_back(dots):
    """Into a shop: the follow loses the street's picture, then a read names the shop (no minimap picture) and the
    follow is cleared, while the known map is still the street for a read until the shop is confirmed. The street's
    last read view must not bring its dots back meanwhile (they flashed for a second, live)."""
    from maplehelper.minimap import Here
    w, _, loc = dots
    _place(loc, _here())
    assert w.isVisible()
    loc.set_follow(("010003000", None))                 # the loading screen
    assert not w.isVisible()
    loc.set_follow(None)                                # the shop read cleared the follow; the street is still known
    assert loc.here.view is not None and not w.isVisible()
    loc.set(Here("010003001", None))                    # the shop confirmed
    assert not w.isVisible()


def test_the_dots_ignore_a_stale_setting(dots):
    """The dots have no setting any more (the owner, 2026-10-11): an old settings.json's False hides nothing."""
    w, s, loc = dots
    s["minimap_hidden_portals"] = False
    _place(loc, _here())
    assert w.isVisible() and len(w._dots) == 3


def test_settings_has_no_hidden_portal_switch(qapp, isolated_store, kb, monkeypatch):
    from maplehelper import providers, store
    from maplehelper.i18n import STRINGS
    from maplehelper.ui.dialogs import SettingsDialog
    for name in ("claude", "codex"):
        monkeypatch.setattr(type(providers.get(name)), "account",
                            lambda self: {"status": "logged_out", "email": None, "method": None})
        monkeypatch.setattr(type(providers.get(name)), "status", lambda self: "logged_out")
    assert "minimap_hidden_portals" not in store.DEFAULT_SETTINGS and "minimap_hidden_portals" not in STRINGS
    assert "overlays_in_screenshots" not in store.DEFAULT_SETTINGS and "overlays_in_screenshots" not in STRINGS
    s = isolated_store.Settings()
    s["language"] = "en"
    s["provider"] = "claude"
    dlg = SettingsDialog(s, isolated_store.Profiles(), kb, lambda *_: "")
    try:
        assert not hasattr(dlg, "hidden_portals") and not hasattr(dlg, "overlay_shots") and not dlg.unsaved()
        dlg._save()
        assert "minimap_hidden_portals" not in s.data and "overlays_in_screenshots" not in s.data
    finally:
        dlg.close()


# --- screen captures: the dots stay out, every other window over the game shows ---------------------------------


def test_the_dots_show_kept_out_of_captures(dots, monkeypatch):
    """The dots sit on the very box the minimap reads capture: they ask to stay out (keep_out=True) on show."""
    from maplehelper.ui import portaldots
    calls = []
    monkeypatch.setattr(portaldots, "set_capturable", lambda w, settings, keep_out=False: calls.append((w, keep_out)))
    w, _, loc = dots
    _place(loc, _here())
    assert w.isVisible() and calls == [(w, True)]


class _User32:
    def __init__(self):
        self.calls = []

    def SetWindowDisplayAffinity(self, hwnd, mode):     # noqa: N802 - the Win32 name
        self.calls.append(mode)
        return 1


@pytest.mark.skipif(os.sys.platform != "win32", reason="display affinity is Windows-only")
def test_display_affinity_keeps_only_the_dots_out(qapp, isolated_store, monkeypatch):
    """keep_out: WDA_EXCLUDEFROMCAPTURE; every other window: WDA_NONE, whatever an old settings.json says."""
    import ctypes

    from PySide6.QtWidgets import QWidget

    from maplehelper.ui import npcoverlay
    user32 = _User32()
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    s = isolated_store.Settings()
    s["overlays_in_screenshots"] = False                # a stale key: it no longer hides anything
    w = QWidget()
    try:
        npcoverlay.set_capturable(w, s)
        npcoverlay.set_capturable(w, s, keep_out=True)
    finally:
        w.close()
    assert user32.calls == [npcoverlay.WDA_NONE, npcoverlay.WDA_EXCLUDEFROMCAPTURE]
    assert (npcoverlay.WDA_NONE, npcoverlay.WDA_EXCLUDEFROMCAPTURE) == (0x0, 0x11)


def test_the_app_never_reapplies_capture_without_keeping_the_dots_out(monkeypatch):
    """Each window sets its capture mode on show. An app-wide re-apply (once run after every Settings save) must
    keep the dots out too, or the minimap reads would see them until their next show."""
    from maplehelper import app
    from maplehelper.ui import npcoverlay
    reapply = getattr(app.MapleHelperApp, "_apply_overlay_capture", None)
    if reapply is None:
        return                  # none any more (the setting it served is gone): only the windows' own shows set it
    calls = {}
    monkeypatch.setattr(npcoverlay, "set_capturable",
                        lambda w, settings, keep_out=False: calls.__setitem__(w, keep_out))
    dots, npc, toolbar = object(), SimpleNamespace(_guide=None), object()
    reapply(SimpleNamespace(portal_dots=dots, npc_overlay=npc, monster_overlay=None, game_toolbar=toolbar,
                            loot_overlay=None, settings={}))
    assert calls[dots] is True and calls[npc] is False and calls[toolbar] is False
