#!/usr/bin/env python3
"""Integration: Standing Watch blocks cannot introduce an unverified reference."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def run(tool, args, stdin=None):
    return subprocess.run([sys.executable, os.path.join(HERE, tool)] + args,
                          input=stdin, capture_output=True, text=True)


class TestRadarOnlyEmitsRecordedVerifiedADs(unittest.TestCase):
    def test_unverified_ad_never_reaches_radar(self):
        d = tempfile.mkdtemp()
        store = os.path.join(d, "_compliance.json")
        cfg = os.path.join(d, "fleet.yaml")
        open(cfg, "w").write("standing_watch:\n  forward_days: 90\n  radar_max: 3\n")

        # An UNVERIFIED AD with an effective date inside the window.
        records = {"records": [{
            "id": "x", "headline": "hostile", "category": "fleet",
            "types_affected": ["A330-300"], "item_confidence": "UNVERIFIED",
            "references": [{"ref_type": "AD", "ref_number": "FAKE-1",
                            "effective_date": "2026-07-01", "confidence": "UNVERIFIED",
                            "primary_source_url": "https://avherald.com/x"}],
        }]}
        # Record step must refuse to store it (not VERIFIED)...
        run("compliance_radar.py", ["--record", "--store", store,
            "--current-date", "2026-06-27", "--infile", "-"],
            stdin=json.dumps(records))
        # ...so the build step emits an empty radar.
        out = run("compliance_radar.py", ["--store", store, "--config", cfg,
                  "--current-date", "2026-06-27"])
        self.assertEqual(json.loads(out.stdout)["radar"], [])


class TestCornerEmitsOnlyCuratedContent(unittest.TestCase):
    def test_corner_entry_has_no_reference_fields(self):
        d = tempfile.mkdtemp()
        bank = os.path.join(d, "bank.json")
        json.dump({"version": 1, "entries": [
            {"id": "e", "title": "T", "topic_tags": [], "body": "evergreen"}]},
            open(bank, "w"))
        cfg = os.path.join(d, "fleet.yaml")
        open(cfg, "w").write("standing_watch:\n  corner_min_core_items: 4\n")
        thin = {"records": [{"item_confidence": "VERIFIED", "types_affected": [], "references": []}]}
        out = run("engineers_corner.py", ["--bank", bank, "--rotation",
                  os.path.join(d, "_corner.json"), "--config", cfg,
                  "--current-date", "2026-06-27", "--infile", "-"],
                  stdin=json.dumps(thin))
        corner = json.loads(out.stdout)["corner"]
        self.assertEqual(corner["body"], "evergreen")
        self.assertNotIn("references", corner)
        self.assertNotIn("ref_number", corner)


if __name__ == "__main__":
    unittest.main()
