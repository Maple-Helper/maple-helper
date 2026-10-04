"""The portrait comes from the player's name tag found in the pixels, not from the AI's rough box alone."""
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
