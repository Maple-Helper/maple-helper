"""The nightly scrape must never publish a broken or emptied KB, and must not report changes that didn't happen
(launch audit, SCP-*). Everything here runs on simulated pages and answers: no network."""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import scrape_meowdb  # noqa: E402


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buf, "PNG")
    return buf.getvalue()


def test_a_missing_pillow_is_an_error_not_a_silently_lost_picture(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(ImportError):
        scrape_meowdb.save_image(b"x", tmp_path / "a.png")


def test_a_bad_picture_is_just_skipped(tmp_path):
    assert scrape_meowdb.save_image(b"not a picture", tmp_path / "a.png") is False
    assert scrape_meowdb.save_image(_png(), tmp_path / "b.png") is True
