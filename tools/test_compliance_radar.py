#!/usr/bin/env python3
"""Tests for tools/compliance_radar.py — the Compliance Radar store + selector."""
import json
import os
import tempfile
import unittest

import compliance_radar as cr


def ad_record(ref_number, effective_date, confidence="VERIFIED", url=None, headline=None):
    return {
        "id": "rec-" + ref_number.lower().replace(" ", "-"),
        "headline": headline or ("FAA AD " + ref_number),
        "category": "fleet",
        "types_affected": ["A330-300"],
        "item_confidence": confidence,
        "references": [{
            "ref_type": "AD",
            "ref_number": ref_number,
            "effective_date": effective_date,
            "confidence": confidence,
            "primary_source_url": url or ("https://www.govinfo.gov/" + ref_number),
        }],
    }


class TestRecord(unittest.TestCase):
    def test_record_upserts_verified_ad_with_effective_date(self):
        store = {}
        cr.record_store(store, [ad_record("2026-10-06", "2026-09-01")], "2026-06-27")
        self.assertIn("AD:2026-10-06", store)
        self.assertEqual(store["AD:2026-10-06"]["effective_date"], "2026-09-01")

    def test_record_ignores_reference_without_effective_date(self):
        store = {}
        cr.record_store(store, [ad_record("2026-10-06", None)], "2026-06-27")
        self.assertEqual(store, {})

    def test_record_ignores_unverified_reference(self):
        store = {}
        rec = ad_record("2026-10-06", "2026-09-01", confidence="UNVERIFIED")
        cr.record_store(store, [rec], "2026-06-27")
        self.assertEqual(store, {})

    def test_record_upsert_overwrites_same_ad(self):
        store = {}
        cr.record_store(store, [ad_record("2026-10-06", "2026-09-01")], "2026-06-27")
        cr.record_store(store, [ad_record("2026-10-06", "2026-09-15")], "2026-07-04")
        self.assertEqual(store["AD:2026-10-06"]["effective_date"], "2026-09-15")

    def test_record_warns_on_unparseable_effective_date(self):
        store, warnings = {}, []
        cr.record_store(store, [ad_record("2026-10-06", "Q4 2026")], "2026-06-27",
                        warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("unparseable effective_date", warnings[0])
        # Still stored (never dropped) but it will never surface via select().
        self.assertIn("AD:2026-10-06", store)
        self.assertEqual(cr.select(store, "2026-06-27", 365, 5), [])

    def test_record_does_not_warn_on_good_date(self):
        warnings = []
        cr.record_store({}, [ad_record("2026-10-06", "2026-09-01")], "2026-06-27",
                        warn=warnings.append)
        self.assertEqual(warnings, [])


class TestSelect(unittest.TestCase):
    def setUp(self):
        self.store = {
            "AD:PAST": {"effective_date": "2026-01-01", "ref_type": "AD", "ref_number": "PAST",
                        "url": "https://www.govinfo.gov/past", "headline": "past"},
            "AD:SOON": {"effective_date": "2026-07-10", "ref_type": "AD", "ref_number": "SOON",
                        "url": "https://www.govinfo.gov/soon", "headline": "soon"},
            "AD:LATER": {"effective_date": "2026-08-20", "ref_type": "AD", "ref_number": "LATER",
                         "url": "https://www.govinfo.gov/later", "headline": "later"},
            "AD:FAR": {"effective_date": "2027-01-01", "ref_type": "AD", "ref_number": "FAR",
                       "url": "https://www.govinfo.gov/far", "headline": "far"},
        }

    def test_select_excludes_past_and_far(self):
        got = cr.select(self.store, "2026-06-27", forward_days=90, max_items=3)
        nums = [e["ref_number"] for e in got]
        self.assertEqual(nums, ["SOON", "LATER"])  # PAST excluded, FAR beyond 90d

    def test_select_sorts_ascending_by_effective_date(self):
        got = cr.select(self.store, "2026-06-27", forward_days=365, max_items=10)
        dates = [e["effective_date"] for e in got]
        self.assertEqual(dates, sorted(dates))

    def test_select_caps_at_max_items(self):
        got = cr.select(self.store, "2026-06-27", forward_days=365, max_items=1)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["ref_number"], "SOON")

    def test_select_includes_effective_today(self):
        got = cr.select(self.store, "2026-07-10", forward_days=90, max_items=3)
        self.assertIn("SOON", [e["ref_number"] for e in got])


class TestLoadSaveFailSafe(unittest.TestCase):
    def test_load_missing_file_returns_empty(self):
        self.assertEqual(cr.load_store("does/not/exist.json"), {})

    def test_load_malformed_returns_empty(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{ not json")
            path = f.name
        try:
            self.assertEqual(cr.load_store(path), {})
        finally:
            os.unlink(path)

    def test_save_then_load_roundtrip(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "_compliance.json")
        store = {"AD:X": {"effective_date": "2026-09-01", "ref_type": "AD",
                          "ref_number": "X", "url": "u", "headline": "h"}}
        cr.save_store(path, store, "2026-06-27")
        self.assertEqual(cr.load_store(path), store)


if __name__ == "__main__":
    unittest.main()
