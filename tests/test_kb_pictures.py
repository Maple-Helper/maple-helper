"""Every entity a card can show has a picture, and drops are read with their item keys.

Runs against the downloaded knowledge base (data/kb); skipped where it isn't present (e.g. CI without data).
"""
import pytest

from maplehelper.kb import KnowledgeBase
from maplehelper.store import BUNDLED_KB

pytestmark = pytest.mark.skipif(not (BUNDLED_KB / "index.json").exists() or
                                len(KnowledgeBase(BUNDLED_KB).entities) < 1000,
                                reason="full knowledge base not downloaded")

CARD_CATEGORIES = ("monster", "item", "npc", "map", "quest", "skill", "class")


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase(BUNDLED_KB)


def test_every_card_has_a_picture(kb):
    missing = [k for k, e in kb.entities.items() if e["category"] in CARD_CATEGORIES and not kb.picture(k)]
    assert missing == []


def test_real_pictures_cover_almost_everything(kb):
    keys = [k for k, e in kb.entities.items() if e["category"] in CARD_CATEGORIES]
    real = sum(1 for k in keys if kb.image_path(k))
    # a night's new pages can wait a night for their pictures (the nightly's picture step only warns): most, not all.
    # A broken picture scrape loses far more than one in twenty
    assert real / len(keys) > 0.95


def test_quest_shows_its_npc(kb):
    # every quest whose giver has a picture shows it (Pio's Collecting Recycled Goods: Pio), whichever quests there are
    givers = {k: kb.npc_key(str((e.get("props") or {}).get("NPC") or "")) for k, e in kb.entities.items()
              if e["category"] == "quest" and not e.get("image")}
    shown = [k for k, npc in givers.items() if npc and kb.image_path(npc)]
    assert len(shown) > 50
    assert all(kb.image_path(k) == kb.image_path(givers[k]) and "npc" in str(kb.image_path(k)) for k in shown)


def test_blue_snail_drops_are_items_with_pictures(kb):
    # the drop lists change with players' reports: its own shell stays, and every drop is an item with a picture
    drops = kb.monster_drops(kb.monster_keys("Blue Snail")[0])
    names = {kb.get(k)["name"] for k in drops}
    assert "Blue Snail Shell" in names
    assert all(kb.get(k)["category"] == "item" and kb.image_path(k) for k in drops)
