"""Pinned answers per character, and searching past chats."""
from maplehelper import pins


class FakeSettings(dict):
    def __getitem__(self, k):
        return self.get(k)


def test_pins_are_per_character_newest_first_without_duplicates():
    s = FakeSettings(pins={})
    pins.add(s, "kiwi", "q1", "a1", now=1)
    pins.add(s, "kiwi", "q2", "a2", now=2)
    pins.add(s, "kiwi", "q1 again", "a1", now=3)          # the same answer moves to the top
    assert [p["a"] for p in pins.items(s, "kiwi")] == ["a1", "a2"] and pins.items(s, "other") == []
    pins.remove(s, "kiwi", "a2")
    assert [p["a"] for p in pins.items(s, "kiwi")] == ["a1"]


def test_conversations_and_search():
    records = [{"role": "user", "text": "where is Mano?", "t": 1}, {"role": "assistant", "text": "Swamp.", "t": 2},
               {"role": "user", "text": "מה Mano מפיל?", "t": 3}, {"role": "assistant", "text": "Subi.", "t": 4},
               {"role": "user", "text": "unanswered", "t": 5}]
    pairs = pins.conversations(records)
    assert [p["q"] for p in pairs] == ["where is Mano?", "מה Mano מפיל?"]
    assert [p["a"] for p in pins.search(pairs, "mano")] == ["Subi.", "Swamp."]     # newest first
    assert pins.search(pairs, "mano swamp") == [pairs[0]]


def test_remove_with_no_character_writes_nothing():
    from maplehelper import pins
    settings = {"pins": {"a": [{"q": "q", "a": "x", "t": 1}]}}
    pins.remove(settings, None, "x")
    assert settings["pins"] == {"a": [{"q": "q", "a": "x", "t": 1}]}       # audit AI-25: no None key
