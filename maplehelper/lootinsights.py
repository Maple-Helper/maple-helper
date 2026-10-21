"""What the loot history shows: the maps the player's logs ran on compared by what was really counted on each
(lootdb.spots), as a few plain conclusions: where the EXP is best, where the profit is, what the potions took, how much
of a map's profit is ETC still to be sold. Nothing here is guessed: every number is the player's own logs, and a map
with under MIN_SPOT_S on it is left out (a few minutes say little). No Qt; the page turns each Insight into text."""
from __future__ import annotations

from dataclasses import dataclass, field

MIN_SPOT_S = 10 * 60        # a map needs this long in the logs to be compared
POTIONS_HIGH = 0.3          # potions that cost this much of what a map made are worth a line
ETC_HIGH = 0.5              # ... and so is a map whose profit is mostly ETC
LEVEL_SPREAD = 5            # levels this far apart make the EXP comparison a rough one
CLEAR_LEAD = 0.05           # a map leads only by this much (5%) or more: closer is no conclusion


@dataclass
class Insight:
    key: str                                    # the i18n key ("loot_ins_best_exp")
    args: dict = field(default_factory=dict)    # its placeholders; maps as their ids under "map", "other", ...


def comparable(spots: list) -> list:
    return [sp for sp in spots if sp.seconds >= MIN_SPOT_S]


def _lead(a: float, b: float) -> int | None:
    """How much a beats b, in whole percent; None when b isn't a positive number to compare with."""
    return round((a - b) / b * 100) if b > 0 else None


def insights(spots: list, sessions: list | None = None) -> list[Insight]:
    """The conclusions, most useful first. spots: lootdb.Spot list; sessions: lootdb.Session list (for the best one)."""
    out: list[Insight] = []
    ok = comparable(spots)
    if not ok:
        return out
    exp_h = {sp.map: sp.per_hour(sp.exp) or 0 for sp in ok}
    net_h = {sp.map: sp.per_hour(sp.net) or 0 for sp in ok}
    if len(ok) == 1:
        out.append(Insight("loot_ins_single", {"map": ok[0].map, "m": MIN_SPOT_S // 60}))
    else:
        by_exp = sorted(ok, key=lambda sp: -exp_h[sp.map])
        by_net = sorted(ok, key=lambda sp: -net_h[sp.map])
        e1, e2 = by_exp[0], by_exp[1]
        n1, n2 = by_net[0], by_net[1]
        exp_pct = _lead(exp_h[e1.map], exp_h[e2.map])
        net_pct = _lead(net_h[n1.map], net_h[n2.map])
        exp_clear = exp_pct is not None and exp_pct >= CLEAR_LEAD * 100
        net_clear = net_pct is not None and net_pct >= CLEAR_LEAD * 100
        if e1.map == n1.map and exp_clear and net_clear:
            out.append(Insight("loot_ins_best_both", {"map": e1.map, "exp": exp_h[e1.map], "net": net_h[n1.map]}))
        else:
            if exp_clear:
                out.append(Insight("loot_ins_best_exp", {"map": e1.map, "exp": exp_h[e1.map], "pct": exp_pct,
                                                         "other": e2.map}))
            if net_clear:
                out.append(Insight("loot_ins_best_net", {"map": n1.map, "net": net_h[n1.map], "pct": net_pct,
                                                         "other": n2.map}))
            if exp_clear and net_clear and e1.map != n1.map:
                out.append(Insight("loot_ins_tradeoff", {"exp_map": e1.map, "net_map": n1.map}))
        levels = [lv for sp in ok if sp.levels for lv in sp.levels]
        if levels and max(levels) - min(levels) >= LEVEL_SPREAD:
            out.append(Insight("loot_ins_levels", {"lo": min(levels), "hi": max(levels)}))
    for sp in ok:
        made = sp.value
        if sp.potions_cost and sp.net < 0:
            out.append(Insight("loot_ins_loss", {"map": sp.map, "net": net_h[sp.map]}))
        elif made > 0 and sp.potions_cost / made >= POTIONS_HIGH:
            out.append(Insight("loot_ins_potions", {"map": sp.map, "pct": round(sp.potions_cost / made * 100)}))
        if sp.net > 0 and sp.etc_value / sp.net >= ETC_HIGH:
            out.append(Insight("loot_ins_etc", {"map": sp.map, "pct": min(100, round(sp.etc_value / sp.net * 100))}))
    if not any(sp.potions_seen for sp in ok):
        out.append(Insight("loot_ins_no_potions"))
    best = max((s for s in sessions or [] if s.seconds >= MIN_SPOT_S and s.maps),
               key=lambda s: s.per_hour(s.net) or 0, default=None)
    if best is not None and len(sessions or []) > 1:
        out.append(Insight("loot_ins_best_session", {"when": best.started, "map": next(iter(best.maps)),
                                                     "net": best.per_hour(best.net) or 0}))
    return out
