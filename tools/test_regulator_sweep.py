#!/usr/bin/env python3
"""Tests for tools/regulator_sweep.py — deterministic regulator enumeration."""
import json
import os
import tempfile
import unittest

import regulator_sweep as rs

YAML = """
fleet:
  - type: A330-300
    oem: Airbus
  - type: 777-300ER
    oem: Boeing
engines:
  - name: Trent 700
    oem: Rolls-Royce
  - name: GE90-115B
    oem: GE Aerospace
"""


class TestOemTerms(unittest.TestCase):
    def test_terms_derived_from_config_not_hardcoded(self):
        terms = rs.oem_terms(YAML)
        self.assertIn("Airbus", terms)
        self.assertIn("Boeing", terms)
        self.assertIn("Rolls-Royce", terms)

    def test_terms_are_deduplicated(self):
        self.assertEqual(len(rs.oem_terms(YAML)), len(set(rs.oem_terms(YAML))))


class TestBuildSweep(unittest.TestCase):
    EASA = ([{"ref_number": "2026-0142", "subject": "Rib 5", "types_hint": ["A321"]}],
            {"regulator": "EASA", "from": "2026-08-01", "to": "2026-08-02",
             "complete": False, "reason": "biweekly 17-2026 not available"})
    FAA = ([{"ref_number": "2026-15308", "subject": "Airbus", "types_hint": ["A321"],
             "source_url": "https://x", "issue_date": "2026-07-29"}],
           {"regulator": "FAA", "from": "2026-08-01", "to": "2026-08-07",
            "complete": True, "reason": None})

    def test_ads_carry_their_regulator(self):
        out = rs.build_sweep(self.EASA, self.FAA)
        regs = {a["ref_number"]: a["regulator"] for a in out["ads"]}
        self.assertEqual(regs["2026-0142"], "EASA")
        self.assertEqual(regs["2026-15308"], "FAA")

    def test_easa_ads_get_a_portal_source_url(self):
        out = rs.build_sweep(self.EASA, self.FAA)
        ad = next(a for a in out["ads"] if a["ref_number"] == "2026-0142")
        self.assertEqual(ad["source_url"], "https://ad.easa.europa.eu/ad/2026-0142")

    def test_coverage_lists_both_regulators(self):
        out = rs.build_sweep(self.EASA, self.FAA)
        self.assertEqual({c["regulator"] for c in out["coverage"]}, {"EASA", "FAA"})

    def test_recall_partial_true_when_any_regulator_incomplete(self):
        self.assertTrue(rs.recall_partial(rs.build_sweep(self.EASA, self.FAA)))

    def test_recall_partial_false_when_all_complete(self):
        easa = (self.EASA[0], dict(self.EASA[1], complete=True, reason=None, to="2026-08-07"))
        self.assertFalse(rs.recall_partial(rs.build_sweep(easa, self.FAA)))

    def test_faa_ref_type_is_carried_through_not_hardcoded(self):
        """Regression: build_sweep hard-coded ref_type='AD' for every FAA
        document, including PRORULE (proposed rule) documents faa_register now
        correctly labels 'NPRM'."""
        faa_nprm = ([{"ref_number": "2026-16157", "subject": "787 fan cowl", "types_hint": [],
                      "source_url": "https://x", "issue_date": "2026-07-30", "ref_type": "NPRM"}],
                    {"regulator": "FAA", "from": "2026-08-01", "to": "2026-08-07",
                     "complete": True, "reason": None})
        out = rs.build_sweep(self.EASA, faa_nprm)
        ad = next(a for a in out["ads"] if a["ref_number"] == "2026-16157")
        self.assertEqual(ad["ref_type"], "NPRM")

    def test_faa_missing_ref_type_defaults_to_ad(self):
        """Backward compatibility: an ad dict without a ref_type key (e.g. from
        an older cached payload) must not raise, and should default to AD."""
        out = rs.build_sweep(self.EASA, self.FAA)
        ad = next(a for a in out["ads"] if a["ref_number"] == "2026-15308")
        self.assertEqual(ad["ref_type"], "AD")


if __name__ == "__main__":
    unittest.main()
