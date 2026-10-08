"""The game's bottom bar, read on this computer: level, job, name, HP, MP and EXP, with no AI call.

The grind tracker sent a screenshot to the player's AI every minute only to read these numbers (seconds per read,
from the player's usage quota, and a misread name now and then). RapidOCR reads the bar's strip in ~0.5 s. The
level's digits are the game's stylized orange ones, which it misreads ("10" as "00"), so the level is worked out
from the EXP instead: the points and the percentage give the EXP the level needs, and the KB's EXP table has one
level that needs that much. The read digits only count where they agree with it. No Qt: runs on a worker thread.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image

log = logging.getLogger("maplehelper")

STRIP = 0.06          # the bar is the picture's bottom 4-5% (1440 px: ~65 rows); a little more for other sizes
_TABLE_FIT = 0.02     # the EXP a level needs, worked out from points / percent, must be this close to the table's
_MIN_PCT = 1.0        # under 1% the percentage's two decimals are too coarse to work the level out
_HP = re.compile(r"HP\W*(\d+)\s*/\s*(\d+)", re.I)
_MP = re.compile(r"MP\W*(\d+)\s*/\s*(\d+)", re.I)
_EXP = re.compile(r"EXP\W*(\d+)\s*\[?\s*(\d+(?:[.,]\d+)?)\s*%", re.I)
_CHROME = ("cash shop", "menu", "short-cut", "shortcut", "lv")

_engine_lock = threading.Lock()
_engine: object | None = None
_engine_failed = False


@dataclass(frozen=True)
class Hud:
    level: int | None
    job: str | None
    name: str | None
    exp: int | None
    exp_pct: float | None
    hp: tuple[int, int] | None = None
    mp: tuple[int, int] | None = None

    def profile_update(self) -> dict:
        """The read as the AI's META profile_update says it: only what was read."""
        out: dict = {}
        if self.name:
            out["name"] = self.name
        if self.level:
            out["level"] = self.level
        if self.job:
            out["job"] = self.job
        if self.exp_pct is not None:
            out["exp_percent"] = self.exp_pct
        return out


def _ocr():
    global _engine, _engine_failed
    if _engine is None and not _engine_failed:
        with _engine_lock:
            if _engine is None and not _engine_failed:
                try:
                    from rapidocr import RapidOCR

                    from .minimap import _OCR_PARAMS
                    _engine = RapidOCR(params=_OCR_PARAMS)
                except Exception as e:  # noqa: BLE001 - no reader: the caller falls back to the AI
                    _engine_failed = True
                    log.warning("HUD OCR unavailable: %s", e)
    return _engine


def _rows(strip: np.ndarray) -> list[tuple[str, float, float]]:
    """(text, x centre, y centre) of every line read in the strip."""
    eng = _ocr()
    if eng is None:
        return []
    with _engine_lock:
        found = eng(strip)
    if found is None or not found.txts:
        return []
    out = []
    for text, box in zip(found.txts, np.asarray(found.boxes).reshape(len(found.txts), -1, 2)):
        out.append((str(text).strip(), float(box[:, 0].mean()), float(box[:, 1].mean())))
    return out


def level_from_exp(table: dict[int, int], exp: int, pct: float) -> int | None:
    """The one level whose EXP to the next matches points / percent (None: too little to tell, or no single fit)."""
    if pct < _MIN_PCT or exp <= 0:
        return None
    need = exp * 100 / pct
    fits = [lv for lv, n in table.items() if n and abs(n - need) <= _TABLE_FIT * n]
    return fits[0] if len(fits) == 1 else None


def parse(rows: list[tuple[str, float, float]], table: dict[int, int]) -> Hud | None:
    """The bar's values from the read lines; None when it isn't the bar (no EXP and no HP line)."""
    text = " ".join(t for t, _, _ in rows)
    hp, mp, ex = _HP.search(text), _MP.search(text), _EXP.search(text)
    if not ex and not hp:
        return None
    exp = int(ex.group(1)) if ex else None
    pct = float(ex.group(2).replace(",", ".")) if ex else None
    if pct is not None and not 0 <= pct <= 100:
        exp, pct = None, None
    # the job over the name, left of the HP gauge: the two word lines there (not LV., the digits, the buttons)
    hp_x = min((x for t, x, _ in rows if t.upper().startswith(("HP", "MP", "EXP"))), default=None)
    words = sorted((y, x, t) for t, x, y in rows
                   if (hp_x is None or x < hp_x) and re.fullmatch(r"[A-Za-z][A-Za-z0-9 ]{1,15}", t)
                   and t.lower().rstrip(".") not in _CHROME)
    # only a job the game has ("Rogue" is Thief's HUD name): a pet's name over the bar ("Brown Kitty") or OCR noise
    # ("oiag") took the job's place in 2 of 65 live frames. The name is the line right under it, or none.
    from .jobs import canonical_job
    at = next((i for i, (_, _, t) in enumerate(words) if canonical_job(t)), None)
    job = words[at][2] if at is not None else None
    below = [w for w in words[at + 1:] if abs(w[1] - words[at][1]) < 120] if at is not None else []
    name = below[0][2] if below else None
    derived = level_from_exp(table, exp, pct) if exp is not None and pct is not None else None
    digits = next((int(t) for t, x, _ in rows if t.isdigit() and (hp_x is None or x < hp_x) and 0 < int(t) <= 300),
                  None)
    level = derived if derived else (digits if digits and exp is not None and pct is not None
                                     and exp <= table.get(digits, 0) else None)
    return Hud(level, job, name, exp, pct,
               (int(hp.group(1)), int(hp.group(2))) if hp else None,
               (int(mp.group(1)), int(mp.group(2))) if mp else None)


def read(img: Image.Image, table: dict[int, int]) -> Hud | None:
    """The bar in a game screenshot (the play area, as capture.LAST_FULL holds it); None when it can't be read."""
    if img is None or img.height < 100:
        return None
    w, h = img.size
    strip = np.asarray(img.convert("RGB").crop((0, int(h * (1 - STRIP)), w, h)))
    try:
        return parse(_rows(strip), table)
    except Exception:  # noqa: BLE001 - a bad read is no read; the caller falls back to the AI
        log.debug("HUD read failed", exc_info=True)
        return None
