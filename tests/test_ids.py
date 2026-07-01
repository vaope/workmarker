import unittest
from datetime import datetime, timezone

from workeventagent.ids import make_event_id, make_stable_id, make_unique_stable_id


class IdTest(unittest.TestCase):
    def test_stable_id_normalizes_titles(self):
        self.assertEqual(make_stable_id("KV Cache Few Shot"), "kv-cache-few-shot")
        self.assertEqual(make_stable_id("  Review   Blockers  "), "review-blockers")

    def test_stable_id_hashes_non_ascii_only_titles(self):
        blocker_id = make_stable_id("\u67e5\u770b\u5f53\u524d\u963b\u585e\u70b9")
        dataset_id = make_stable_id("\u6570\u636e\u96c6\u751f\u4ea7")

        self.assertRegex(blocker_id, r"^id-[0-9a-f]{8}$")
        self.assertRegex(dataset_id, r"^id-[0-9a-f]{8}$")
        self.assertNotEqual(blocker_id, dataset_id)
        self.assertEqual(blocker_id, make_stable_id("\u67e5\u770b\u5f53\u524d\u963b\u585e\u70b9"))

    def test_stable_id_keeps_ascii_slug_for_mixed_titles(self):
        self.assertEqual(make_stable_id("\u4f7f\u7528 KV cache \u4f18\u5316 few-shot"), "kv-cache-few-shot")
        self.assertEqual(make_stable_id("KV cache \u4f7f\u7528\u539f\u7406\u89e3\u8bfb"), "kv-cache")

    def test_stable_id_keeps_untitled_for_empty_titles(self):
        self.assertEqual(make_stable_id(""), "untitled")
        self.assertEqual(make_stable_id("   "), "untitled")

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
