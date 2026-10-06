"""Dates as the UI shows them: "6.10" in Hebrew, "Oct 6" in English (never day.month in English, where a US
reader takes 3.10 for March 10), with the year only when it isn't this year (HEB-16)."""
from __future__ import annotations

from datetime import date

# not strftime's %b: that follows the system locale
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def day(d: date, rtl: bool, year: bool | None = None) -> str:
    """6.10 / Oct 6; with the year (6.10.2025 / Oct 6, 2025) when year=True, or by default when it isn't this year."""
    if year is None:
        year = d.year != date.today().year
    if rtl:
        return f"{d.day}.{d.month}" + (f".{d.year}" if year else "")
    return f"{MONTHS[d.month - 1]} {d.day}" + (f", {d.year}" if year else "")


def iso(s: str, rtl: bool, year: bool | None = None) -> str:
    """The same for a "2026-10-06" string; anything else comes back as it is."""
    try:
        return day(date.fromisoformat(str(s)[:10]), rtl, year)
    except ValueError:
        return str(s)
