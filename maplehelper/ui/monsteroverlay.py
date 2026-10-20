"""The "Monsters on this map" window over the game: the same see-through panel as the NPCs one (ui/npcoverlay.py),
listing what spawns on the player's map (the KB's spawns table, the most spawns first) with each monster's level,
HP, EXP and how many spawn. A list to read, not to pick from: no guide, and the NPCs window's ring on the game's
minimap is left alone."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from .. import bidi, combat, gamelookup
from .npcoverlay import GAP, PIC, NpcHere, NpcOverlay, look_palette, picture, qss


def mob_qss(settings) -> str:
    """A monster's row: a label in the look of the NPCs window's row buttons, in the palette the settings pick
    (the rest is that window's style sheet)."""
    c = look_palette(settings)
    return f"""
#Mob {{ background: {c['fill1']}; color: {c['text']}; border-radius: 8px; }}
#MobPic {{ background: transparent; }}
"""


class MonsterOverlay(NpcOverlay):
    """The window itself: NpcOverlay's panel, place, fold and life, with rows of monsters instead of NPCs. Its
    setting is monster_overlay, its place monster_overlay_geom."""

    SHOW_KEY, GEOM_KEY = "monster_overlay", "monster_overlay_geom"

    def _build(self) -> None:
        self._mobs: dict[str, tuple[int, int]] = {}      # monster key -> (level, how many spawn)
        self._pics: list[QLabel] = []                    # each row's picture
        super()._build()

    def _qss(self) -> str:
        """The NPCs window's style at the accent the ⚙ panel picked, with the monster rows' own look; the glass
        (its tint, its transparency) is NpcOverlay's, so this window follows the panel exactly as that one does."""
        return qss(self._settings) + mob_qss(self._settings)

    def _rebuild(self, mid: str | None) -> None:
        """The rows for this map: one per monster, the most spawns first."""
        self._mid = mid
        mobs = gamelookup.map_monsters(self._kb, mid) if mid else []
        self._npcs = [NpcHere(key, name, None) for key, name, _lv, _n in mobs]
        self._mobs = {key: (lv, n) for key, _name, lv, n in mobs}
        self._clear_list()
        self._pics = []
        for mob in self._npcs:
            self._add_item(self._mob_row(mob))
        self._retext()

    def _mob_row(self, mob: NpcHere) -> QFrame:
        """One monster's row: its picture at the reading start (the right in Hebrew), then its name and numbers."""
        box = QFrame(objectName="Mob")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(8, 6, 10, 6)
        lay.setSpacing(8)
        pic = QLabel(objectName="MobPic")
        pic.setFixedSize(PIC, PIC)
        pic.setAlignment(Qt.AlignCenter)
        pm = picture(self._kb, mob.key)
        if not pm.isNull():
            pic.setPixmap(pm)
        row = QLabel()
        row.setWordWrap(True)
        row.setTextFormat(Qt.PlainText)
        lay.addWidget(pic, 0, Qt.AlignVCenter)
        lay.addWidget(row, 1)
        self._rows.append(row)
        self._pics.append(pic)
        return box

    def _row_text(self, mob: NpcHere) -> str:
        t, rtl = self.t, self.t.rtl
        lv, count = self._mobs.get(mob.key, (0, 0))
        m = combat.monster(self._kb, mob.key)
        stats = (t("mob_stats", lv=m.level, hp=f"{m.hp:,}", exp=f"{m.exp:,}") if m is not None
                 else t("lv_short", n=lv))
        sub = " · ".join(x for x in (stats, t("mob_spawns", n=count) if count else "") if x)
        return f"{bidi.ltr_name(mob.name, rtl)}\n{bidi.plain(sub, rtl)}"

    def _retext(self) -> None:
        t = self.t
        self._retitle("monster_overlay_title")
        self._close.setAccessibleName(t("npc_overlay_close"))
        self._close.setToolTip(t("npc_overlay_close"))
        for row, mob in zip(self._rows, self._npcs):
            row.setText(self._row_text(mob))
        self._retext_status()

    def _retext_status(self) -> None:
        t = self.t
        key = ("npc_overlay_unknown" if self._mid is None else "monster_overlay_none" if not self._npcs else "")
        self._status.setText(bidi.plain(t(key), t.rtl) if key else "")
        self._status.setVisible(bool(key))
        self._fit()

    def _pick(self, npc: NpcHere) -> None:
        """Nothing to guide to: a monster's row is read, not picked."""

    def _clear_pick(self) -> None:
        """Never touches LOCATION.guide: that ring is the NPCs window's."""
        self._sel = None
        self._guide.hide()

    def _redraw_guide(self) -> None:
        self._guide.hide()

    def _default_spot(self, size: QSize) -> QPoint:
        """Beside where the NPCs window opens (right of the minimap box), so the two don't open on each other."""
        spot = super()._default_spot(size)
        return QPoint(spot.x() + size.width() + GAP, spot.y())
