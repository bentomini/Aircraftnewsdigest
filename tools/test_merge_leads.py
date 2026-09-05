#!/usr/bin/env python3
"""Tests for tools/merge_leads.py — union of swept and scanned leads."""
import json
import os
import unittest

import merge_leads as ml

SWEEP = {"coverage": [], "ads": [{
    "regulator": "EASA", "ref_type": "AD", "ref_number": "2026-0142",
    "subject": "Wings - Main Landing Gear Support Rib 5 - Inspections",
    "types_hint": ["A320", "A321"],
    "source_url": "https://ad.easa.europa.eu/ad/2026-0142",
}]}

SCANNER = {"records": [{
    "id": "scanner-lead", "headline": "Trade press item", "category": "fleet",
    "types_affected": ["A321neo"], "event_date": "2026-08-03",
    "item_confidence": "UNVERIFIED", "quotes": [],
    "references": [{"ref_type": "AD", "ref_number": "2026-0142",
                    "confidence": "UNVERIFIED", "primary_source_url": None}],
    "lead_sources": ["https://example.com/story"],
}]}


class TestSweepToLead(unittest.TestCase):
    def test_lead_is_unverified(self):
        lead = ml.sweep_to_lead(SWEEP["ads"][0])
        self.assertEqual(lead["item_confidence"], "UNVERIFIED")
        self.assertEqual(lead["references"][0]["confidence"], "UNVERIFIED")

    def test_sweep_url_goes_to_lead_sources_not_primary_source_url(self):
        """The sweep must never look like a confirmed primary fetch."""
        lead = ml.sweep_to_lead(SWEEP["ads"][0])
        self.assertIsNone(lead["references"][0]["primary_source_url"])
        self.assertIsNone(lead["references"][0]["fetched_text_snippet"])
        self.assertIn("https://ad.easa.europa.eu/ad/2026-0142", lead["lead_sources"])

    def test_lead_carries_no_quotes(self):
        self.assertEqual(ml.sweep_to_lead(SWEEP["ads"][0])["quotes"], [])


class TestMerge(unittest.TestCase):
    def test_shared_ad_yields_one_record(self):
        out = ml.merge(SWEEP, SCANNER)
        refs = [r["references"][0]["ref_number"] for r in out["records"]]
        self.assertEqual(refs.count("2026-0142"), 1)

    def test_scanner_only_lead_survives(self):
        scanner = {"records": [dict(SCANNER["records"][0], references=[
            {"ref_type": "AD", "ref_number": "9999-0001", "confidence": "UNVERIFIED",
             "primary_source_url": None}])]}
        out = ml.merge({"coverage": [], "ads": []}, scanner)
        self.assertEqual(len(out["records"]), 1)

    def test_sweep_only_ad_survives(self):
        out = ml.merge(SWEEP, {"records": []})
        self.assertEqual(len(out["records"]), 1)
        self.assertEqual(out["records"][0]["references"][0]["ref_number"], "2026-0142")

    def test_referenceless_scanner_lead_is_kept(self):
        """Incident leads have no reference and must not be dropped."""
        scanner = {"records": [{"id": "incident", "headline": "Diversion", "category": "fleet",
                                "references": [], "item_confidence": "UNVERIFIED",
                                "quotes": [], "lead_sources": ["https://x"]}]}
        out = ml.merge({"coverage": [], "ads": []}, scanner)
        self.assertEqual(len(out["records"]), 1)


class TestRib5Regression(unittest.TestCase):
    """THE regression this whole feature exists for.

    EASA AD 2026-0142 was published 2026-07-20, fell inside the 2026-07-31 run's
    26-day self-healed window, and was missed by two consecutive digests because
    no search engine surfaced it. It must now survive listing -> parse -> sweep ->
    merge with an empty scanner.
    """

    def test_rib5_ad_reaches_merged_leads_from_the_real_listing(self):
        import os
        import easa_biweekly as eb
        import regulator_sweep as rs

        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "biweekly_16_2026.txt")
        with open(fixture, encoding="utf-8") as fh:
            ads = eb.parse_biweekly(fh.read())

        coverage = {"regulator": "EASA", "from": "2026-07-20", "to": "2026-08-02",
                    "complete": True, "reason": None}
        faa_empty = ([], {"regulator": "FAA", "from": "2026-07-20", "to": "2026-08-02",
                          "complete": True, "reason": None})
        sweep = rs.build_sweep((ads, coverage), faa_empty)

        merged = ml.merge(sweep, {"records": []})
        numbers = [r["references"][0]["ref_number"] for r in merged["records"]
                   if r.get("references")]
        self.assertIn("2026-0142", numbers)

    def test_rib5_lead_reaches_the_verifier_as_unverified(self):
        """Recall must never become a second, unaudited path to VERIFIED."""
        import easa_biweekly as eb
        import regulator_sweep as rs

        ads = eb.parse_biweekly("AD 2026-0142 Wings Main Landing Gear Support Rib 5 A320 A321")
        coverage = {"regulator": "EASA", "from": "2026-07-20", "to": "2026-08-02",
                    "complete": True, "reason": None}
        sweep = rs.build_sweep((ads, coverage), ([], dict(coverage, regulator="FAA")))
        rec = ml.merge(sweep, {"records": []})["records"][0]

        self.assertEqual(rec["item_confidence"], "UNVERIFIED")
        self.assertEqual(rec["references"][0]["confidence"], "UNVERIFIED")
        self.assertIsNone(rec["references"][0]["primary_source_url"])
        self.assertEqual(rec["quotes"], [])


class TestCanonicalNumberKey(unittest.TestCase):
    """Fix round 1: the sweep and scanner format the same document differently.

    A real 2026-08-16 run showed 0 collapses out of 23 swept + 8 scanned leads,
    even though 2 of the 8 scanner leads were the same Federal Register
    documents the sweep already enumerated — just formatted as free text
    ("FR Doc. 2026-16157") with a different ref_type (NPRM vs AD).
    """

    def test_faa_ad_and_fr_doc_share_canonical_key(self):
        self.assertEqual(ml._key("AD", "2026-16157"), ml._key("NPRM", "FR Doc. 2026-16157"))

    def test_faa_ad_and_fr_doc_share_canonical_key_second_pair(self):
        self.assertEqual(ml._key("AD", "2026-16636"), ml._key("NPRM", "FR Doc. 2026-16636"))

    def test_docket_number_does_not_masquerade_as_document_number(self):
        """A docket clause must be stripped before extraction, not mistaken for the doc number."""
        docket_key = ml._key("NPRM", "Docket FAA-2026-7201; Project Identifier MCAI-2025-01290-T")
        swept_key = ml._key("AD", "2026-7201")
        self.assertNotEqual(docket_key, swept_key)


class TestFieldLevelMerge(unittest.TestCase):
    """Regression: the whole scanner record used to be dropped and replaced by
    an inferior sweep stub whenever its reference keys were a subset of the
    swept keys — discarding a researched record (long summary, multiple
    lead_sources, real category) in favour of just the FR title and a
    hard-coded category: "fleet". Only the reference (ref_number/ref_type)
    should be overwritten; the scanner's own record must survive."""

    SWEEP = {"coverage": [], "ads": [{
        "regulator": "FAA", "ref_type": "NPRM", "ref_number": "2026-16157",
        "subject": "Airworthiness Directives; The Boeing Company Airplanes",
        "types_hint": [], "source_url": "https://www.federalregister.gov/x",
        "issue_date": "2026-08-07",
    }]}
    SCANNER = {"records": [{
        "id": "b787-trent1000-fan-cowl-nprm", "headline": "787/Trent 1000 fan cowl NPRM",
        "category": "read_across", "types_affected": ["787"],
        "event_date": "2026-08-07", "item_confidence": "UNVERIFIED", "quotes": [],
        "summary": "A" * 457,
        "read_across": "Trent 1000 shares core architecture with Trent 7000/XWB.",
        "references": [{"ref_type": "NPRM", "ref_number": "FR Doc. 2026-16157",
                        "confidence": "UNVERIFIED", "primary_source_url": None}],
        "lead_sources": ["https://www.federalregister.gov/documents/2026/08/07/2026-16157/x",
                         "https://www.flightglobal.com/story", "https://aviationweek.com/story"],
    }]}

    def test_scanner_record_survives_not_replaced_by_stub(self):
        out = ml.merge(self.SWEEP, self.SCANNER)
        rec = next(r for r in out["records"] if r["id"] == "b787-trent1000-fan-cowl-nprm")
        self.assertEqual(rec["category"], "read_across")
        self.assertEqual(len(rec["summary"]), 457)
        self.assertEqual(len(rec["lead_sources"]), 3)
        self.assertIsNotNone(rec["read_across"])

    def test_reference_is_overwritten_with_sweep_canonical_values(self):
        out = ml.merge(self.SWEEP, self.SCANNER)
        rec = next(r for r in out["records"] if r["id"] == "b787-trent1000-fan-cowl-nprm")
        ref = rec["references"][0]
        self.assertEqual(ref["ref_number"], "2026-16157")
        self.assertEqual(ref["ref_type"], "NPRM")

    def test_reference_stays_unverified_no_leaked_primary_source(self):
        out = ml.merge(self.SWEEP, self.SCANNER)
        rec = next(r for r in out["records"] if r["id"] == "b787-trent1000-fan-cowl-nprm")
        ref = rec["references"][0]
        self.assertEqual(ref["confidence"], "UNVERIFIED")
        self.assertIsNone(ref["primary_source_url"])

    def test_sweep_ad_not_also_emitted_as_a_duplicate_stub(self):
        out = ml.merge(self.SWEEP, self.SCANNER)
        matches = [r for r in out["records"]
                   for ref in (r.get("references") or [])
                   if "2026-16157" in (ref.get("ref_number") or "")]
        self.assertEqual(len(matches), 1)

    def test_sweep_only_ad_still_enters_as_a_stub(self):
        """The guarantee that must survive this change."""
        out = ml.merge(self.SWEEP, {"records": []})
        self.assertEqual(len(out["records"]), 1)
        self.assertEqual(out["records"][0]["references"][0]["ref_number"], "2026-16157")


class TestSweepToLeadEventDate(unittest.TestCase):
    """Regression (Finding 6): swept EASA leads asserted within_window=True
    with event_date=None, so the deterministic window gate (validate_records
    ._in_window treats an undated item as always-in-window) was inert for
    every one of them. Drives the assertion from the real biweekly fixture."""

    def test_swept_easa_lead_carries_a_real_event_date(self):
        import easa_biweekly as eb
        import regulator_sweep as rs

        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "biweekly_16_2026.txt")
        with open(fixture, encoding="utf-8") as fh:
            ads = eb.parse_biweekly(fh.read())
        by_ref = {a["ref_number"]: a for a in ads}
        coverage = {"regulator": "EASA", "from": "2026-07-20", "to": "2026-08-02",
                    "complete": True, "reason": None}
        faa_empty = ([], dict(coverage, regulator="FAA"))
        sweep = rs.build_sweep(([by_ref["2026-0142"]], coverage), faa_empty)

        merged = ml.merge(sweep, {"records": []})
        rec = merged["records"][0]
        self.assertEqual(rec["event_date"], "2026-07-20")
        self.assertIsNone(rec["within_window"])


class TestMergeRealRun(unittest.TestCase):
    """Drive the collapse assertions from the actual production files, not hand-written strings."""

    RUN_DIR = os.path.join(os.path.dirname(__file__), "..", "runs", "2026-08-16")

    def _load(self, name):
        with open(os.path.join(self.RUN_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)

    def test_real_run_collapses_duplicates_to_29(self):
        out = ml.merge(self._load("00_sweep.json"), self._load("01_scanner.json"))
        self.assertEqual(len(out["records"]), 29)

    def test_16157_and_16636_each_appear_exactly_once(self):
        out = ml.merge(self._load("00_sweep.json"), self._load("01_scanner.json"))
        for num in ("2026-16157", "2026-16636"):
            matches = [ref.get("ref_number") for r in out["records"]
                       for ref in (r.get("references") or [])
                       if num in (ref.get("ref_number") or "")]
            self.assertEqual(len(matches), 1, "expected exactly one record referencing %s" % num)

    def test_sweep_ref_number_wins_over_scanner_free_text(self):
        """The surviving record must carry the sweep's clean ref_number, not the scanner's prose."""
        out = ml.merge(self._load("00_sweep.json"), self._load("01_scanner.json"))
        numbers = {ref.get("ref_number") for r in out["records"]
                   for ref in (r.get("references") or [])}
        self.assertIn("2026-16157", numbers)
        self.assertIn("2026-16636", numbers)

    def test_referenceless_lead_still_survives_real_merge(self):
        out = ml.merge(self._load("00_sweep.json"), self._load("01_scanner.json"))
        ids = [r.get("id") for r in out["records"]]
        self.assertIn("air-india-a320neo-hydraulic-altitude-drop", ids)


if __name__ == "__main__":
    unittest.main()
