"""The region picker's logical → capture coordinate conversion (pure, no widget needed)."""
import pytest
from PySide6.QtCore import QRect

from maplehelper.ui.regionpick import from_capture, to_capture

PRIMARY = (QRect(0, 0, 2560, 1440), 1.5)
SECOND = (QRect(2560, 0, 1920, 1080), 1.0)
SECOND_SCALED = (QRect(2560, 0, 1920, 1080), 1.25)


def test_primary_screen_scales_from_its_own_origin():
    assert to_capture(QRect(100, 100, 200, 150), [PRIMARY]) == {"x": 150, "y": 150, "w": 300, "h": 225}


def test_second_monitor_at_native_origin_needs_no_shift():
    assert to_capture(QRect(2600, 100, 200, 150), [PRIMARY, SECOND]) == {
        "x": 2600, "y": 100, "w": 200, "h": 150}


def test_scaled_second_monitor_scales_from_its_origin():
    assert to_capture(QRect(2600, 100, 200, 150), [PRIMARY, SECOND_SCALED]) == {
        "x": 2610, "y": 125, "w": 250, "h": 188}


def test_straddling_box_follows_its_centre():
    # the box starts on the primary but its middle is past x=2560: the second screen's ratio wins
    assert to_capture(QRect(2500, 100, 200, 150), [PRIMARY, SECOND]) == {
        "x": 2500, "y": 100, "w": 200, "h": 150}


def test_no_scaling_passes_through():
    assert to_capture(QRect(2600, 100, 200, 150), [PRIMARY, SECOND_SCALED], scale=False) == {
        "x": 2600, "y": 100, "w": 200, "h": 150}


# Windows places each screen at its native origin: right of a 3840-px primary at 150% the next one starts at 3840
BESIDE = (QRect(3840, 0, 1920, 1080), 1.0)
BESIDE_SCALED = (QRect(3840, 0, 1536, 864), 1.25)


@pytest.mark.parametrize("rect,screens", [
    (QRect(100, 100, 200, 150), [PRIMARY]),
    (QRect(3900, 100, 200, 150), [PRIMARY, BESIDE]),
    (QRect(3900, 100, 200, 148), [PRIMARY, BESIDE_SCALED]),
    (QRect(100, 100, 200, 150), [PRIMARY, BESIDE_SCALED]),
])
def test_from_capture_finds_the_drawn_box_again(rect, screens):
    """The hidden-portal dots' window lies over the box the player drew: capture coordinates back to that same
    logical box, with that screen's ratio."""
    box, ratio = from_capture(to_capture(rect, screens), screens)
    assert box == rect
    assert ratio == next(r for g, r in screens if g.contains(rect.center()))


def test_nothing_cancels_the_picker():
    """No way to cancel (the owner's, 2026-10-08): Esc and a right-click leave it open; a drawn box closes it."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from maplehelper.ui import regionpick
    from maplehelper.ui.regionpick import RegionPicker
    QApplication.instance() or QApplication([])
    picker = RegionPicker(hint="x")
    assert not hasattr(picker, "cancelled")
    picked = []
    picker.picked.connect(picked.append)
    try:
        picker.show()
        QTest.keyClick(picker, Qt.Key_Escape)
        QTest.mouseClick(picker, Qt.RightButton, pos=QPoint(5, 5))
        assert picker.isVisible() and picked == []
        QTest.mousePress(picker, Qt.LeftButton, pos=QPoint(10, 10))
        QTest.mouseRelease(picker, Qt.LeftButton, pos=QPoint(10 + regionpick.MIN_SIDE + 40, 80))
        assert len(picked) == 1 and not picker.isVisible()
    finally:
        picker.close()
