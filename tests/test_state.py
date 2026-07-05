"""Tests for lib/state.py: pre-change snapshots powering undo (PLAN 6.7)."""

import unittest

from jetson_postboot.lib.state import StateError, StateStore
from tests.support import make_work_dir


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.work = make_work_dir(self)
        self.path = self.work / "state" / "state.json"

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(StateStore(self.path).load(), {"modules": {}})

    def test_record_and_latest_roundtrip(self):
        store = StateStore(self.path)
        entry = store.record(
            "swap", {"swappiness": "60"}, backups=[self.work / "b" / "x"]
        )
        self.assertEqual(entry["values"], {"swappiness": "60"})
        latest = store.latest("swap")
        self.assertEqual(latest["values"], {"swappiness": "60"})
        self.assertEqual(len(latest["backups"]), 1)
        self.assertIn("timestamp", latest)

    def test_persists_across_instances(self):
        StateStore(self.path).record("swap", {"swappiness": "60"})
        fresh = StateStore(self.path)
        self.assertIsNotNone(fresh.latest("swap"))
        self.assertIsNone(fresh.latest("storage"))

    def test_multiple_records_keep_order(self):
        store = StateStore(self.path)
        store.record("swap", {"swappiness": "60"})
        store.record("swap", {"swappiness": "10"})
        self.assertEqual(store.latest("swap")["values"], {"swappiness": "10"})
        self.assertEqual(len(store.load()["modules"]["swap"]), 2)

    def test_corrupt_state_raises(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(StateError):
            StateStore(self.path).load()


if __name__ == "__main__":
    unittest.main()
