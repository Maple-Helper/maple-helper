"""Take cabs in the map window a chat card's ◎ opens (the owner, 2026-10-11): the same setting as the route page,
shown while a way from the player's map is, and the way found again when it flips."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def test_the_map_window_switch_is_the_route_pages_setting(isolated_store, kb, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from maplehelper.ui import mapview
    asked = []

    def fake_way(kb, key, here, taxi=True):
        asked.append(taxi)
        return None

    monkeypatch.setattr(mapview, "way_from_here", fake_way)
    s = isolated_store.Settings()
    assert s["route_taxi"] is True                          # cabs by default, as the route page always had
    dlg = mapview.MapLocationDialog(kb, "en", "", s)
    try:
        dlg.show_map("map/000000040")                       # no way from here: no switch to show
        assert dlg.taxi_row.isHidden()
        dlg.taxi.setChecked(False)                          # flipped: kept, and the way asked again on foot
        assert s["route_taxi"] is False and asked == [True, False]
    finally:
        dlg.close()
