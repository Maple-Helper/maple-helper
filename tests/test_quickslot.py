"""The game's quick slots read on this computer (quickslot.py): the panel found at any size, each slot's potion named by
its picture, its count read digit by digit, and potions used worked out from the counts going down."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from maplehelper import quickslot

FIX = Path(__file__).resolve().parent / "fixtures" / "quickslot"
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
LABELS = json.loads((FIX / "labels.json").read_text(encoding="utf-8"))


def _crop(name: str) -> np.ndarray:
    """A live capture of the panel (cut 2 px around it), padded so the search has room around it."""
    a = np.asarray(Image.open(FIX / name).convert("RGB"))
    return np.pad(a, ((40, 40), (40, 40), (0, 0)), constant_values=128)


@pytest.fixture(scope="module")
def kb():
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@pytest.mark.parametrize("row", LABELS, ids=lambda r: f"{r['file']}-{r['slot']}-{r['count']}")
def test_every_count_reads_as_the_game_shows_it(row):
    rgb = _crop(row["file"])
    grid = quickslot.locate(rgb, (50, 80))
    assert grid is not None
    assert quickslot.read_count(quickslot._count_area(rgb, grid, row["slot"])) == row["count"]


def test_an_empty_slot_and_a_skill_have_no_count():
    rgb = _crop("q0001.png")
    grid = quickslot.locate(rgb, (50, 80))
    for slot in (0, 4):         # a throwing star skill, the attack key
        assert quickslot.read_count(quickslot._count_area(rgb, grid, slot)) is None


def _frame(panel: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, tuple]:
    """A game picture of `size` with the panel where the game draws it (x 0.688, y 0.82 of the picture), the panel
    scaled with the picture as the game's interface is."""
    w, h = size
    k = h / 1440
    img = Image.new("RGB", size, (150, 110, 70))            # the brick wall behind it
    p = Image.fromarray(panel)
    if k != 1:
        p = p.resize((round(p.width * k), round(p.height * k)), Image.BILINEAR)
    img.paste(p, (round(w * 0.688), round(h * 0.82)))
    return np.asarray(img), (0, 0, w, h)


@pytest.mark.parametrize("size", [(2562, 1440), (1920, 1080)])
def test_the_panel_is_found_in_the_game_picture_at_its_size(size, kb):
    panel = np.asarray(Image.open(FIX / "q0001.png").convert("RGB"))
    rgb, play = _frame(panel, size)
    rd = quickslot.Reader(kb).read(rgb, play)
    assert rd is not None
    got = {kb.get(key)["name"]: n for key, n in rd.potions.values()}
    if size == (2562, 1440):
        assert got == {"Lemon": 501, "Orange": 13, "Blue Potion": 0, "Meat": 431, "Red Potion": 0}
    else:   # smaller: every potion still named, the counts that read at all read right
        assert {"Lemon", "Orange", "Meat"} <= set(got)
        assert all(got[n] == v for n, v in {"Lemon": 501, "Orange": 13, "Meat": 431}.items() if n in got)


def test_no_panel_no_read(kb):
    rgb = np.full((1440, 2562, 3), (150, 110, 70), np.uint8)
    assert quickslot.Reader(kb).read(rgb, (0, 0, 2562, 1440)) is None


def test_a_slot_shows_the_potion_in_it_never_a_skill(kb):
    rgb = _crop("q0001.png")
    grid = quickslot.locate(rgb, (50, 80))
    names = []
    for x0, y0, x1, y1 in grid.slots():
        key = quickslot.identify(rgb[y0:y1, x0:x1], kb, grid.pitch)
        names.append(kb.get(key)["name"] if key else None)
    # the pink milk box is no potion the KB has: not named
    assert names == [None, "Lemon", "Orange", "Blue Potion", None, "Meat", None, "Red Potion"]


def _read(**counts) -> quickslot.Read:
    return quickslot.Read({i: (k, n) for i, (k, n) in enumerate(counts.items())})


def test_potions_used_are_the_counts_going_down():
    w = quickslot.Watcher()
    assert w.read(_read(lemon=501), 0) == {}
    assert w.read(_read(lemon=501), 2) == {}            # the baseline: read twice
    assert w.read(_read(lemon=500), 4) == {}            # a new count waits for a second read
    assert w.read(_read(lemon=500), 6) == {"lemon": 1}
    assert w.read(_read(lemon=520), 8) == {}
    assert w.read(_read(lemon=520), 10) == {}           # bought more: nothing used, counted from 520 now
    assert w.read(_read(lemon=518), 12) == {}
    assert w.read(_read(lemon=518), 14) == {"lemon": 2}


def test_a_one_read_slip_counts_nothing():
    w = quickslot.Watcher()
    for t in (0, 2):
        w.read(_read(lemon=487), t)
    assert w.read(_read(lemon=48), 4) == {}             # a digit missed once
    assert w.read(_read(lemon=487), 6) == {}
    assert w.read(_read(lemon=486), 8) == {}
    assert w.read(_read(lemon=486), 10) == {"lemon": 1}


def test_a_drop_too_big_to_be_used_is_no_use():
    w = quickslot.Watcher()
    for t in (0, 2):
        w.read(_read(lemon=487), t)
    used = {}
    for t in range(4, 30, 2):                           # 48 read again and again: the potions were put away
        used |= w.read(_read(lemon=48), t)
    assert used == {}
    assert w.read(_read(lemon=47), 32) == {}
    assert w.read(_read(lemon=47), 34) == {"lemon": 1}  # counted from 48 since


def test_a_potion_seen_in_one_dimmed_frame_never_counts():
    w = quickslot.Watcher()
    w.read(quickslot.Read({0: ("milk", 13)}), 0)        # a misread picture, once
    for t in (2, 4, 6):
        assert w.read(quickslot.Read({0: ("lemon", 501)}), t) == {}
    assert w.read(quickslot.Read({0: ("milk", 0)}), 8) == {}
    assert w.read(quickslot.Read({0: ("milk", 0)}), 10) == {}


def test_two_slots_of_one_potion_that_disagree_are_skipped():
    w = quickslot.Watcher()
    for t in (0, 2):
        w.read(quickslot.Read({0: ("lemon", 100), 1: ("lemon", 100)}), t)
    assert w.read(quickslot.Read({0: ("lemon", 10), 1: ("lemon", 99)}), 4) == {}
    assert w.read(quickslot.Read({0: ("lemon", 99), 1: ("lemon", 99)}), 6) == {}
    assert w.read(quickslot.Read({0: ("lemon", 99), 1: ("lemon", 99)}), 8) == {"lemon": 1}
