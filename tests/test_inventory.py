"""The inventory is read from the pixels: slots found on a grid, icons matched to the database's pictures."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from maplehelper import inventory

FIXTURE = Path(__file__).parent / "fixtures" / "inventory_equip_tab.png"   # a real game frame: Equip tab, 3 items
COLS, ROWS, CELL = [20, 116, 212, 308], [19, 109, 200, 291, 381, 472], 85   # its slots, measured on the lines
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="real knowledge base not present")


class _KB:
    """Item pictures on disk, like the knowledge base's."""
    def __init__(self, tmp_path, twins: bool = False):
        self.root = tmp_path
        self.entities, self._paths = {}, {}
        for i, color in enumerate([(200, 40, 40), (40, 160, 60)] + ([(200, 40, 40)] if twins else [])):
            im = Image.new("RGBA", (30, 29), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.ellipse((4, 3, 26, 25), fill=color + (255,), outline=(0, 0, 0, 255))
            j = 0 if i == 2 else i              # the twin: item 0's very picture under another name
            d.rectangle((12, 10, 18 + 4 * j, 14 + 6 * j), fill=(250, 250, 250, 255))
            p = tmp_path / f"{i}.png"
            im.save(p)
            k = f"item/{i}"
            self.entities[k] = {"category": "item", "name": f"Thing {i}"}
            self._paths[k] = p

    def image_path(self, k):
        return self._paths.get(k)

    def get(self, k):
        return self.entities.get(k)


def _grid(cols=4, rows=3, col_pitch=96, row_pitch=96, gap=(40, 90, 160)) -> Image.Image:
    img = Image.new("RGB", (600, 400), gap)
    for r in range(rows):
        for c in range(cols):
            x, y = 50 + c * col_pitch, 40 + r * row_pitch
            img.paste((224, 222, 212), (x, y, x + 83, y + 83))
    return img


def test_slots_and_the_icon_are_found(tmp_path):
    kb = _KB(tmp_path)
    icon = Image.open(kb.image_path("item/1")).convert("RGBA")
    img = _grid()
    big = icon.resize((60, 58), Image.NEAREST)
    img.paste(big, (50 + 96 + 11, 40 + 12), big)
    assert len(inventory.find_slots(np.asarray(img))) == 12
    slots = inventory.read(img, kb)
    assert [s.index for s in slots] == [2] and slots[0].matches[0][0] == "item/1"
    assert slots[0].status == "certain"
    assert "Thing 1" in inventory.describe(slots, kb)


def test_the_slot_under_the_mouse_is_the_one_the_tooltip_names(tmp_path):
    kb = _KB(tmp_path)
    icon = Image.open(kb.image_path("item/0")).convert("RGBA")
    img = _grid()
    big = icon.resize((60, 58), Image.NEAREST)
    img.paste(big, (50 + 11, 40 + 12), big)
    assert [s.index for s in inventory.read(img, kb)] == [1]
    hovered = inventory.read(img, kb, cursor=(80, 70))      # the hand cursor is no item...
    assert [(s.index, s.status) for s in hovered] == [(1, "hovered")]
    assert "tooltip" in inventory.describe(hovered, kb)    # ...but the game's tooltip names it


def test_rows_are_measured_on_their_own_pitch(tmp_path):
    """The game spaces rows closer than columns (lines of 5 px, not 11): rows 2-6 were cut at the column pitch."""
    kb = _KB(tmp_path)
    icon = Image.open(kb.image_path("item/1")).convert("RGBA").resize((60, 58), Image.NEAREST)
    img = Image.new("RGB", (600, 700), (250, 250, 250))
    for r in range(6):
        for c in range(4):
            x, y = 50 + c * 96, 40 + r * 90
            img.paste((224, 222, 212), (x, y, x + 85, y + 85))
    for r in range(6):
        img.paste(icon, (50 + 96 * 2 + 12, 40 + r * 90 + 14), icon)
    slots = inventory.find_slots(np.asarray(img))
    assert sorted({y for _, y, _ in slots}) == [40 + r * 90 for r in range(6)]
    read = inventory.read(img, kb)
    assert [s.index for s in read] == [3, 7, 11, 15, 19, 23]
    assert all(s.status == "certain" and s.matches[0][0] == "item/1" for s in read)


def test_a_wide_icon_is_read_and_a_cut_row_is_not(tmp_path):
    """An icon as wide as its slot (Elixir, Return Scroll) is an item; a row the screen's edge cuts is no slot."""
    kb = _KB(tmp_path)
    wide = Image.open(kb.image_path("item/0")).convert("RGBA").resize((85, 60), Image.NEAREST)
    img = Image.new("RGB", (600, 400), (250, 250, 250))
    for r in range(4):
        for c in range(4):
            x, y = 50 + c * 96, 40 + r * 90
            img.paste((224, 222, 212), (x, y, x + 85, y + 85))
    img.paste(wide, (50 + 96, 40 + 20), wide)
    cut = img.crop((0, 0, 600, 40 + 3 * 90 + 50))           # row 4 is half off the screen
    assert len(inventory.find_slots(np.asarray(cut))) == 12
    read = inventory.read(cut, kb)
    assert [(s.index, s.matches[0][0]) for s in read] == [(2, "item/0")]


def test_items_sharing_one_picture_are_ambiguous_not_a_guess(tmp_path):
    kb = _KB(tmp_path, twins=True)                          # Thing 0 and Thing 2: one picture
    icon = Image.open(kb.image_path("item/0")).convert("RGBA").resize((60, 58), Image.NEAREST)
    img = _grid()
    img.paste(icon, (50 + 11, 40 + 12), icon)
    (slot,) = inventory.read(img, kb)
    assert slot.status == "ambiguous"
    assert sorted(k for k, _ in slot.matches) == ["item/0", "item/2"]
    text = inventory.for_ai([slot], kb)
    assert "AMBIGUOUS" in text and "Thing 0" in text and "Thing 2" in text
    assert "almost always right" not in text and "tooltip" in text and "F5" in text


def test_a_stack_count_does_not_hide_the_item(tmp_path):
    """The game prints the count (light digits outlined in black) over the icon's bottom left."""
    kb = _KB(tmp_path)
    icon = Image.open(kb.image_path("item/1")).convert("RGBA").resize((76, 74), Image.NEAREST)
    img = _grid()
    img.paste(icon, (50 + 4, 40 + 6), icon)
    font = ImageFont.truetype("arialbd.ttf", 20) if Path("C:/Windows/Fonts/arialbd.ttf").exists() else None
    if font is None:
        pytest.skip("no bold font to draw the count with")
    ImageDraw.Draw(img).text((50 + 3, 40 + 83 - 22), "92", font=font, fill=(235, 235, 240),
                             stroke_width=2, stroke_fill=(0, 0, 0))
    (slot,) = inventory.read(img, kb)
    assert slot.status == "certain" and slot.matches[0][0] == "item/1"


def test_the_real_frame_grid_is_cut_on_its_lines():
    rgb = np.asarray(Image.open(FIXTURE).convert("RGB"))
    slots = inventory.find_slots(rgb)
    assert sorted({x for x, _, _ in slots}) == COLS and sorted({y for _, y, _ in slots}) == ROWS
    assert {n for _, _, n in slots} == {CELL}
    assert len(inventory.find_slots(rgb[:520])) == 20          # the 6th row cut by the edge: not a slot


@needs_kb
def test_real_items_are_named_in_every_row():
    """Real game pixels: the three real items (Green Skullcap, Wooden Club, Razor) copied into rows 2-6 too."""
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    img = Image.open(FIXTURE).convert("RGB")
    cells = [img.crop((x, ROWS[0], x + CELL, ROWS[0] + CELL)) for x in COLS[:3]]
    for y in ROWS[1:]:
        for x, cell in zip(COLS, cells):
            img.paste(cell, (x, y))
    read = inventory.read(img, kb)
    assert [s.index for s in read] == [r * 4 + c + 1 for r in range(6) for c in range(3)]
    assert {s.status for s in read} == {"certain"}
    assert [kb.get(s.matches[0][0])["name"] for s in read] == ["Green Skullcap", "Wooden Club", "Razor"] * 6


def _on_real_slot(kb, key: str, count: str | None = None) -> Image.Image:
    """A KB icon drawn into the real frame's empty slot 4, at the game's scale (the slot is 32 icon pixels)."""
    img = Image.open(FIXTURE).convert("RGB")
    icon = Image.open(kb.image_path(key)).convert("RGBA")
    s = CELL / 32
    icon = icon.resize((round(icon.width * s), round(icon.height * s)), Image.NEAREST)
    x, y = COLS[3], ROWS[0]
    img.paste(icon, (x + (CELL - icon.width) // 2, y + CELL - icon.height - 2), icon)
    if count:
        font = ImageFont.truetype("arialbd.ttf", 20)
        ImageDraw.Draw(img).text((x + 3, y + CELL - 22), count, font=font, fill=(235, 235, 240), stroke_width=2,
                                 stroke_fill=(0, 0, 0))
    return img


@needs_kb
def test_a_scroll_is_one_of_its_look_alikes_on_the_real_kb():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    slot = inventory.read(_on_real_slot(kb, "item/118"), kb)[3]        # Gloves Attack Scroll: Lesser
    assert slot.index == 4 and slot.status == "ambiguous"
    names = {kb.get(k)["name"] for k, _ in slot.matches}
    assert "Gloves Attack Scroll: Lesser" in names and len(names) >= 20
    assert all(n.endswith(": Lesser") for n in names)


@needs_kb
@pytest.mark.skipif(not Path("C:/Windows/Fonts/arialbd.ttf").exists(), reason="no bold font for the count")
def test_small_items_with_a_count_are_named_on_the_real_kb():
    """Ores and shells were read as ingots and hats: the slot's speckle stretched the in-game crop."""
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    for key in ("item/348", "item/413", "item/270"):                    # Snail Shell, Bronze Ore, Red Potion
        slot = inventory.read(_on_real_slot(kb, key, "37"), kb)[3]
        assert (slot.status, slot.matches[0][0]) == ("certain", key)


@needs_kb
def test_items_the_kb_has_no_source_for_are_candidates_too():
    """Half the KB's items have no source in the game by its pages (Roger's Apple, the tutorial's): left out, they
    were named after a look-alike for certain. One drawn with another's very picture is one of the two."""
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    keys = set(inventory._index(kb).keys)
    assert {"item/270", "item/348", "item/118", "item/2564", "item/2634"} <= keys


ETC = Path(__file__).parent / "fixtures" / "inventory_etc_tab.png"   # a real game frame: Etc tab at 85 px slots


@needs_kb
def test_real_etc_items_with_counts_are_named():
    """Real game pixels: two-digit counts ("16", "10") stand apart at this scale; only their first digit was
    hidden and the slot went unnamed. Tree Branch and Rotten Root Fragment are one shape in two colours."""
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    read = inventory.read(Image.open(ETC), kb)
    assert {s.status for s in read} == {"certain"}
    assert [(s.index, kb.get(s.matches[0][0])["name"]) for s in read] == [
        (1, "Jr. Sentinel Shellpiece"), (2, "Snail Shell"), (3, "Tree Branch"), (4, "Blue Snail Shell"),
        (5, "Monster Card"), (6, "Mushroom Spore"), (7, "Old Wooden Board"), (8, "Omok Table"),
        (9, "Red Snail Shell"), (10, "Silver Ore"), (11, "Squishy Liquid"), (13, "Orange Mushroom Cap")]


@needs_kb
@pytest.mark.parametrize("scale,resample", [(0.5, Image.BILINEAR), (0.5, Image.LANCZOS), (0.6, Image.BILINEAR),
                                            (0.6, Image.LANCZOS)])
def test_a_small_window_still_names_items_with_counts(scale, resample):
    """~42-50 px slots: the counted items went "unknown" (audit SCR-15). Never a wrong name either way."""
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    full = {s.index: s.matches[0][0] for s in inventory.read(Image.open(ETC), kb)}
    im = Image.open(ETC).convert("RGB")
    small = inventory.read(im.resize((round(im.width * scale), round(im.height * scale)), resample), kb)
    assert {s.index: s.matches[0][0] for s in small if s.status == "certain"} == full


def test_a_count_is_hidden_whole():
    """The count's digits stand a few pixels apart at a big scale: the box reaches the last one."""
    c = np.asarray(Image.open(ETC).convert("RGB"))
    x, y, n = inventory.find_slots(c)[0]                     # "16" over a Jr. Sentinel Shellpiece
    top, right = inventory._count_box(c[y:y + n, x:x + n])
    assert n * 0.6 <= top < n * 0.7 and right >= n * 0.4
