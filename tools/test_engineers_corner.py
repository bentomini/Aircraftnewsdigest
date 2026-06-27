#!/usr/bin/env python3
"""Tests for tools/engineers_corner.py — deterministic Engineer's Corner picker."""
import json
import os
import tempfile
import unittest

import engineers_corner as ec

BANK = [
    {"id": "trent", "title": "Trent", "topic_tags": ["Trent 700", "A330-300"], "body": "b1"},
    {"id": "generic", "title": "Generic", "topic_tags": [], "body": "b2"},
    {"id": "leap", "title": "Leap", "topic_tags": ["A321neo", "LEAP-1A"], "body": "b3"},
]


def rec(confidence, types):
    return {"item_confidence": confidence, "types_affected": types, "references": []}


class TestCoreCount(unittest.TestCase):
    def test_counts_only_verified_and_reported(self):
        records = [rec("VERIFIED", []), rec("REPORTED", []), rec("UNVERIFIED", [])]
        self.assertEqual(ec.count_core_items(records), 2)


class TestTrigger(unittest.TestCase):
    def test_suppressed_when_core_meets_threshold(self):
        records = [rec("VERIFIED", []) for _ in range(4)]
        chosen, idx = ec.select(BANK, records, {}, threshold=4)
        self.assertIsNone(chosen)
        self.assertIsNone(idx)

    def test_renders_when_core_below_threshold(self):
        records = [rec("VERIFIED", [])]
        chosen, idx = ec.select(BANK, records, {}, threshold=4)
        self.assertIsNotNone(chosen)


class TestGrounding(unittest.TestCase):
    def test_picks_entry_matching_a_verified_type(self):
        records = [rec("VERIFIED", ["A330-300"])]
        chosen, idx = ec.select(BANK, records, {}, threshold=4)
        self.assertEqual(chosen["id"], "trent")
        self.assertEqual(idx, 0)

    def test_grounding_ignores_unverified_types(self):
        records = [rec("UNVERIFIED", ["A330-300"])]   # not VERIFIED -> no grounding match
        chosen, idx = ec.select(BANK, records, {"last_index": 1}, threshold=4)
        self.assertEqual(chosen["id"], "leap")        # rotation: (1+1)%3 = 2

    def test_match_is_case_insensitive(self):
        records = [rec("VERIFIED", ["trent 700"])]
        chosen, _ = ec.select(BANK, records, {}, threshold=4)
        self.assertEqual(chosen["id"], "trent")


class TestRotationFallback(unittest.TestCase):
    def test_rotation_advances_from_last_index(self):
        records = [rec("VERIFIED", ["B777"])]          # no tag match
        chosen, idx = ec.select(BANK, records, {"last_index": 0}, threshold=4)
        self.assertEqual(idx, 1)
        self.assertEqual(chosen["id"], "generic")

    def test_rotation_wraps(self):
        records = [rec("VERIFIED", ["B777"])]
        chosen, idx = ec.select(BANK, records, {"last_index": 2}, threshold=4)
        self.assertEqual(idx, 0)

    def test_rotation_with_no_state_starts_at_zero(self):
        records = [rec("VERIFIED", ["B777"])]
        chosen, idx = ec.select(BANK, records, {}, threshold=4)
        self.assertEqual(idx, 0)


class TestLoadFailSafe(unittest.TestCase):
    def test_load_rotation_missing_returns_empty(self):
        self.assertEqual(ec.load_rotation("nope/nope.json"), {})

    def test_load_bank_roundtrip(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "bank.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": BANK}, f)
        self.assertEqual(ec.load_bank(path), BANK)


if __name__ == "__main__":
    unittest.main()
