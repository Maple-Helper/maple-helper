"""The portrait comes from the player's name tag found in the pixels, not from the AI's rough box alone."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from maplehelper.portrait import find_name_tags, portrait_rect


def _scene() -> np.ndarray:
    """A grassy scene with a translucent name tag (the plate darkens what's behind it) and white letters."""
    w, h = 1200, 700
    x = np.linspace(0, 1, w)[None, :, None]
    y = np.linspace(0, 1, h)[:, None, None]
    img = (np.concatenate([150 + 60 * x + 0 * y, 200 + 40 * y + 0 * x, 100 + 30 * x * y], axis=2)).astype(np.uint8)
    img[600:700] = (120, 120, 120)                       # stone ground
    img[300:340, 690:740] = (200, 40, 40)                 # the character's cap...
    img[340:398, 690:740] = (60, 60, 160)                 # ...and body, standing on the tag
    plate = (slice(400, 426), slice(650, 770))
    img[plate] = (img[plate] * 0.42).astype(np.uint8)
    pil = Image.fromarray(img)
    ImageDraw.Draw(pil).text((658, 402), "KalimeroZz", fill=(255, 255, 255), font=ImageFont.load_default(size=20))
    return np.asarray(pil)


def test_finds_the_translucent_name_tag():
    tags = find_name_tags(_scene())
    assert len(tags) == 1
    x, y, w, h = tags[0]
    assert abs(x - 650) <= 3 and abs(y - 400) <= 2 and abs(w - 120) <= 6 and abs(h - 26) <= 2


def test_portrait_sits_on_the_tag_even_with_an_off_box():
    img = _scene()
    H, W = img.shape[:2]
    off_box = [600 / W, 250 / H, 60 / W, 90 / H]          # the AI pointed next to the character
    left, top, right, bottom = portrait_rect(img, off_box)
    assert left < 690 and right > 740 and top < 300 and 398 <= bottom <= 410
    assert portrait_rect(img, None) == (left, top, right, bottom)    # one tag in sight: no box needed


def test_no_tag_no_guess():
    img = np.full((500, 800, 3), 180, np.uint8)
    assert portrait_rect(img, [0.4, 0.4, 0.05, 0.1]) is None and portrait_rect(img, None) is None


def test_box_far_off_still_finds_the_only_tag():
    img = _scene()
    H, W = img.shape[:2]
    far = [100 / W, 100 / H, 50 / W, 80 / H]               # pointed at a sign across the screen
    left, top, right, bottom = portrait_rect(img, far)
    assert left < 690 and right > 740 and 398 <= bottom <= 410


def test_sprite_cut_out_of_its_background():
    """Background flooded from the edges up to the dark outline: the outlined sprite stays, the scenery goes."""
    from maplehelper.portrait import sprite_mask
    crop = np.zeros((100, 100, 3), np.uint8)
    crop[:] = (150, 210, 120)                            # grass behind
    crop[20:80, 30:70] = (20, 20, 20)                     # outline...
    crop[22:78, 32:68] = (200, 60, 60)                    # ...around the sprite
    m = sprite_mask(crop)
    assert m is not None and m[50, 50] and not m[5, 5] and m[20:80, 30:70].all() and m.sum() == 60 * 40


def test_tag_width_must_fit_the_name():
    from maplehelper.portrait import tag_fits_name
    assert tag_fits_name((0, 0, 116, 26), "KalimeroZz")         # measured live
    assert not tag_fits_name((0, 0, 74, 26), "KalimeroZz")      # a 6-letter name next to it
    assert not tag_fits_name((0, 0, 122, 42), "KalimeroZz")     # a label box of the Stat window


def test_no_tag_keeps_the_job_picture_even_for_a_new_character():
    """Live (2026-10-04): with no tag found, the AI's box alone cropped a lamp and an HP bar into a new character's
    portrait. No tag, no portrait, with or without one already."""
    import io

    from PIL import Image

    from maplehelper.ui.overlay import crop_portrait
    img = Image.new("RGB", (800, 450), (120, 180, 90))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    for have in (False, True):
        assert crop_portrait(buf.getvalue(), [0.5, 0.4, 0.04, 0.12], None, "KalimeroZz", have) is None


def test_a_bush_beside_the_sprite_is_not_part_of_it():
    """Live (2026-10-04): a dark green bush touching an archer's bow and legs came into the portrait."""
    from pathlib import Path

    from maplehelper.portrait import sprite_mask
    rgb = np.asarray(Image.open(Path(__file__).parent / "fixtures" / "portrait_bush.png").convert("RGB"))
    m = sprite_mask(rgb)
    assert m is not None
    assert not m[95:125, :30].any()                      # the bush at the crop's left edge
    assert m[20:60, 50:90].mean() > 0.9                  # the head
    assert m[125:140, 45:75].any()                       # the feet stay


def test_official_client_tag_with_soft_letters_and_an_overlapped_pair():
    # MapleStory Classic World scales its picture up, so the letters are light grey (at 225 the tag went unseen and
    # a "Hm" quick-slot box became the portrait); two overlapping tags make a short piece that must not count
    import numpy as np
    from PIL import Image

    from maplehelper import portrait
    a = np.asarray(Image.open(Path(__file__).parent / "fixtures" / "classic_world_tags.png").convert("RGB"))
    assert all(abs(t[3] - 29) <= 3 for t in portrait.find_name_tags(a))
    left, top, right, bottom = portrait.portrait_rect(a, None, "Kalimero")
    assert 470 <= (left + right) / 2 <= 610 and 590 <= bottom <= 620       # standing on the tag at (525, 608)


def _fixture(name):
    return np.asarray(Image.open(Path(__file__).parent / "fixtures" / name).convert("RGB"))


def _learned():
    return (np.asarray(Image.open(Path(__file__).parent / "fixtures" / "classic_letters_kalimero.png").convert("L"))
            > 127).astype(np.float32)


def test_the_learned_letters_find_the_tag_in_a_crowd():
    # "Oldcc Kalimero": two tags merged into one plate, which plate finding misses; the letters search finds it
    from maplehelper.portrait import find_learned
    hit = find_learned(_fixture("classic_crowd.png"), _learned())
    assert hit is not None and abs(hit[0][0] - 310) <= 4 and abs(hit[0][1] - 273) <= 3
    left, top, right, bottom = portrait_rect(_fixture("classic_crowd.png"), None, "Kalimero", _learned())
    assert abs((left + right) / 2 - 360) <= 6 and abs(bottom - 278) <= 4


def test_the_name_in_the_games_own_ui_is_no_tag():
    # the same letters in the status bar and over a shop window's portrait: no plate edge above them
    for name in ("classic_status_bar.png", "classic_shop_label.png"):
        assert portrait_rect(_fixture(name), None, "Kalimero", _learned()) is None, name


def test_a_tag_found_by_the_drawn_name_hands_its_letters_over_to_learn():
    found = {}
    assert portrait_rect(_fixture("classic_world_tags.png"), None, "Kalimero", None, found) is not None
    letters = found["letters"]
    assert letters.shape[0] >= 10 and letters.shape[1] > 3 * letters.shape[0]


def test_a_soft_outlined_sprite_against_bricks_comes_out_whole():
    # the scaled-up official client softens the outline: the flood took the hat and face along with the bricks
    from maplehelper.portrait import sprite_mask
    m = sprite_mask(_fixture("classic_sprite_bricks.png"))
    assert m is not None and 0.3 <= m.mean() <= 0.55
    rows = np.flatnonzero(m.any(axis=1))
    assert rows[0] <= 30 and rows[-1] >= 140                    # the hat's top down to the feet
    assert not m[:12].any()                                    # the bricks above the hat are gone


def _beam() -> np.ndarray:
    """A dark beam over a lighter wall, plate-wide for the name, with bright speckle where letters would start:
    the live scenery (bricks, a beam, a shop sign) that became a magician's portrait."""
    rng = np.random.default_rng(7)
    img = np.full((500, 800, 3), 180, np.uint8)
    img[300:330, 200:310] = (72, 72, 72)
    band = img[308:317, 200:310]
    band[rng.random(band.shape[:2]) < 0.10] = (205, 205, 205)
    return img


def test_a_scenery_edge_of_the_right_width_is_no_portrait():
    # live (2026-10-07): the tag search found nothing for "formatme", yet a beam of the right width near the AI's
    # box became the portrait. Without the name's letters, no rect, even with a plate-shaped edge by the box
    box = [240 / 800, 250 / 500, 40 / 800, 60 / 500]      # the AI pointed just above the beam
    assert portrait_rect(_beam(), box, "Formatme", None) is None


def test_another_players_tag_with_the_wrong_name_is_no_portrait():
    # another player's tag by the AI's box spells their name, not ours: its width fitting is not enough either
    box = [0.10, 0.10, 0.05, 0.06]                        # next to the other tag at (104, 102)
    assert portrait_rect(_fixture("classic_world_tags.png"), box, "Formatme", None) is None


def test_a_scenery_cutout_does_not_reach_the_feet():
    # the live portrait's cut-out is a band across the top (rows 0-53 of 128) with the bottom half transparent;
    # a real cut-out stands on the tag at the crop's bottom
    from maplehelper.portrait import mask_reaches_feet, sprite_mask
    scenery = np.asarray(Image.open(Path(__file__).parent / "fixtures" / "portrait_scenery_live.png"))
    assert not mask_reaches_feet(scenery[..., 3] > 127)
    crop = np.zeros((100, 100, 3), np.uint8)
    crop[:] = (150, 210, 120)
    crop[20:80, 30:70] = (20, 20, 20)
    crop[22:78, 32:68] = (200, 60, 60)
    assert mask_reaches_feet(sprite_mask(crop))
    for name in ("portrait_bush.png", "classic_sprite_bricks.png"):
        assert mask_reaches_feet(sprite_mask(_fixture(name))), name


def test_a_spritless_cutout_is_no_portrait():
    """A rect without a sprite in it (the nameless scene's grass, no outline to cut out) keeps the job picture
    instead of saving the scenery as the portrait."""
    import io

    from maplehelper.ui.overlay import crop_portrait
    img = _scene()
    H, W = img.shape[:2]
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG")
    assert crop_portrait(buf.getvalue(), [600 / W, 250 / H, 60 / W, 90 / H], None, "", False) is None
