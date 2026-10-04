"""Site screenshots, step 2: raw PNGs from tools/site_shots.py -> <site repo>/assets/shots/[en/]<name>-<mode>.webp.

Run: python tools/site_shots_webp.py <raw_dir> <maple-helper-site checkout>/assets/shots     (needs Pillow)
The website lives in its own repository: https://github.com/Maple-Helper/maple-helper-site
"""
import os
import sys

from PIL import Image, ImageChops, ImageDraw

RAW = sys.argv[1]
OUT = sys.argv[2]
M, R = 30, 55
for base in ['mano', 'hp', 'wishlist', 'settings', 'guide', 'tools-train', 'tools-calc', 'tools-crafting', 'tools-quests', 'tools-build', 'tools-farm', 'tools-town', 'tools-pets', 'tools-more']:
    for mode in ['light', 'dark']:
        for lang in ['he', 'en']:
            src = f'{RAW}/{base}-{mode}{"" if lang == "he" else "-en"}.png'
            dst = f'{OUT}/{"" if lang == "he" else "en/"}{base}-{mode}.webp'
            im = Image.open(src).convert('RGBA')
            w, h = im.size
            im = im.crop((M, M, w - M, h - M))
            mask = Image.new('L', im.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.size[0] - 1, im.size[1] - 1), radius=R, fill=255)
            im.putalpha(ImageChops.multiply(im.split()[3], mask))
            im.save(dst, 'WEBP', quality=84, method=6)
            print(dst.split('shots/')[1], im.size, os.path.getsize(dst) // 1024, 'KB')
