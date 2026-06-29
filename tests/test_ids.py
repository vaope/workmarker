import unittest
from datetime import datetime, timezone

from workeventagent.ids import make_event_id, make_stable_id, make_unique_stable_id


class IdTest(unittest.TestCase):
    def test_stable_id_normalizes_titles(self):
        self.assertEqual(make_stable_id("KV Cache Few Shot"), "kv-cache-few-shot")
        self.assertEqual(make_stable_id("  Review   Blockers  "), "review-blockers")

    def test_unique_stable_id_adds_suffix_on_collision(self):
        existing = {"kv-cache-few-shot", "kv-cache-few-shot-2"}
        self.assertEqual(make_unique_stable_id("KV Cache Few Shot", existing), "kv-cache-few-shot-3")

    def test_event_id_uses_milliseconds_and_suffix(self):
        now = datetime(2026, 6, 29, 15, 30, 0, 123000, tzinfo=timezone.utc)
        first = make_event_id(now, "kv-cache-blockers", set())
        second = make_event_id(now, "kv-cache-blockers", {first})

        self.assertEqual(first, "20260629-153000123-kv-cache-blockers")
        self.assertEqual(second, "20260629-153000123-kv-cache-blockers-2")


if __name__ == "__main__":
    unittest.main()
