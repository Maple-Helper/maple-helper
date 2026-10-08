"""The bottom bar read on this computer (hud.py) and the grind read that uses it instead of the AI (overlay)."""
from pathlib import Path

import pytest
from PIL import Image

from maplehelper import hud

FIXTURES = Path(__file__).parent / "fixtures"
TABLE = {9: 1242, 10: 1716, 11: 2360, 30: 98000}


def _scene(with_inventory: bool = False) -> Image.Image:
    """A 2562x1440 play area: plain backdrop, the live bar at the bottom, and the inventory window if asked."""
    bar = Image.open(FIXTURES / "hud_bar_live.png").convert("RGB")
    img = Image.new("RGB", (2562, 1440), (110, 140, 190))
    img.paste(bar, (0, 1440 - bar.height))
    if with_inventory:
        img.paste(Image.open(FIXTURES / "inventory_etc_tab.png").convert("RGB"), (900, 300))
    return img


def test_level_comes_from_the_exp_not_the_stylized_digits():
    # 194 points at 11.31%: the level needs ~1715 EXP, the table's level 10 (the digits read "00")
    rows = [("Rogue", 700, 50), ("Kalimero", 700, 75), ("LV.", 560, 60), ("00", 640, 60),
            ("HP[444/444]", 1000, 50), ("MP[363/363]", 1250, 50), ("EXP. 194[11.31%]", 1500, 50)]
    got = hud.parse(rows, TABLE)
    assert (got.level, got.job, got.name, got.exp, got.exp_pct) == (10, "Rogue", "Kalimero", 194, 11.31)
    assert got.hp == (444, 444) and got.mp == (363, 363)
    assert got.profile_update() == {"name": "Kalimero", "level": 10, "job": "Rogue", "exp_percent": 11.31}


def test_a_pet_name_is_no_job_and_a_tiny_percentage_no_level():
    rows = [("Brown Kitty", 700, 20), ("Kalimero", 700, 75), ("EXP. 3[0.17%]", 1500, 50), ("HP[1/2]", 1000, 50)]
    got = hud.parse(rows, TABLE)
    assert got.job is None and got.name is None          # no job line: the name under it isn't sure either
    assert got.level is None and got.exp_pct == 0.17     # 0.17% is too coarse to tell the level


def test_not_the_bar_is_no_read():
    assert hud.parse([("CASH SHOP", 10, 10), ("hello", 20, 20)], TABLE) is None


def test_the_live_bar_reads():
    pytest.importorskip("rapidocr")
    got = hud.read(_scene(), TABLE)
    assert got is not None
    assert (got.level, got.job, got.name) == (10, "Rogue", "Kalimero")
    assert got.exp == 219 and got.exp_pct == 12.76 and got.hp == (431, 444)


def test_local_grind_read_skips_the_ai_unless_the_inventory_is_open():
    pytest.importorskip("rapidocr")
    from maplehelper.ui.overlay import local_grind_read
    update, grind = local_grind_read(_scene(), TABLE, "Kerning City Construction Site")
    assert update["level"] == 10 and update["exp_percent"] == 12.76
    assert grind == {"map": "Kerning City Construction Site"}
    assert local_grind_read(_scene(with_inventory=True), TABLE, None) is None      # mesos, potions, loot: the AI
    assert local_grind_read(None, TABLE, None) is None


def test_the_automatic_grind_read_asks_no_ai_when_the_bar_reads(isolated_store, kb, monkeypatch):
    pytest.importorskip("rapidocr")
    import time
    from unittest.mock import Mock

    from PySide6.QtWidgets import QApplication
    from maplehelper import capture, plan
    from maplehelper.ui import overlay

    qapp = QApplication.instance() or QApplication([])
    profiles = isolated_store.Profiles()
    profiles.add("Kalimero", "Thief", "Thief", 10)
    brain = Mock()
    win = overlay.Overlay(isolated_store.Settings(), profiles, kb, brain)
    win.add_system = Mock()
    monkeypatch.setattr(plan, "exp_table", lambda kb: TABLE)
    monkeypatch.setattr(overlay.osapi, "find_game_window", lambda: 123)
    monkeypatch.setattr(overlay.osapi, "window_rect", lambda hwnd: (5000, 5000, 800, 600))

    def grab(hwnd):
        capture.LAST_PROBLEM, capture.LAST_FULL, capture.LAST_CURSOR = None, _scene(), None
        return b"screenshot"
    monkeypatch.setattr(overlay.osapi, "capture_game", grab)
    got, finished = [], []
    win.grind_read.connect(got.append)
    win.sync_finished.connect(finished.append)
    win.auto_grind_read()
    deadline = time.monotonic() + 20
    while not finished and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert finished == [True] and not brain.ask.called
    assert got[0][1]["exp_percent"] == 12.76 and got[0][1]["level"] == 10
    assert profiles.active.exp_pct == 12.76
