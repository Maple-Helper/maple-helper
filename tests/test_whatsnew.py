"""App release notes: every release ships its own, and players see only what's new to them."""
from maplehelper import __version__, whatsnew


def test_every_release_has_notes_in_both_languages():
    notes = {n["version"]: n for n in whatsnew.load()}
    assert __version__ in notes, f"add {__version__} to assets/notes/whatsnew.json before releasing"
    for n in notes.values():
        assert n.get("he") and n.get("en") and len(n["he"]) == len(n["en"]), n["version"]


def test_since_shows_only_unseen_versions_up_to_the_running_one(monkeypatch):
    notes = [{"version": v, "he": ["x"], "en": ["x"]} for v in ("0.5.0", "0.4.0", "0.3.0", "0.2.0")]
    monkeypatch.setattr(whatsnew, "load", lambda: notes)
    assert [n["version"] for n in whatsnew.since("0.2.0", "0.4.0")] == ["0.4.0", "0.3.0"]
    assert whatsnew.since("0.4.0", "0.4.0") == []


def test_versions_compare_as_numbers_not_text():
    assert whatsnew.version_tuple("0.10.0") > whatsnew.version_tuple("0.9.0")



def test_whats_new_window_lists_every_version_in_both_languages():
    import os
    import unicodedata
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel

    from maplehelper.ui.patchnotes import WhatsNewDialog
    app = QApplication.instance() or QApplication([])

    def bare(text):              # the rows carry direction marks around English names, and no-break spaces
        return "".join(ch for ch in text if unicodedata.category(ch) != "Cf").replace(" ", " ")
    notes = whatsnew.load()[:2]
    for lang in ("he", "en"):
        d = WhatsNewDialog(notes, lang, "")
        shown = bare(" ".join(lb.text() for lb in d.findChildren(QLabel)))
        assert all(n["version"] in shown for n in notes)
        assert all(bare(line) in shown for n in notes for line in n[lang]), lang
        assert d.layoutDirection() == (Qt.RightToLeft if lang == "he" else Qt.LeftToRight)
        d.close()
        d.deleteLater()
    app.processEvents()
