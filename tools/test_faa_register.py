#!/usr/bin/env python3
"""Tests for tools/faa_register.py — FAA enumeration via the Federal Register API."""
import unittest
from datetime import date

import faa_register as fr

SAMPLE = {
    "count": 1,
    "results": [{
        "document_number": "2026-15308",
        "publication_date": "2026-07-29",
        "type": "Rule",
        "title": "Airworthiness Directives; Airbus SAS Airplanes",
        "abstract": "The FAA is superseding AD 2025-16-12, which applied to Model A321-251N airplanes.",
    }],
}


class TestQueryUrl(unittest.TestCase):
    def test_url_carries_term_and_date_bounds(self):
        url = fr.fr_query_url("Airbus", date(2026, 8, 1), date(2026, 8, 7))
        self.assertIn("federalregister.gov/api/v1/documents.json", url)
        self.assertIn("conditions%5Bterm%5D=Airbus", url)
        self.assertIn("2026-08-01", url)
        self.assertIn("2026-08-07", url)

    def test_url_restricts_to_rule_and_prorule_types(self):
        url = fr.fr_query_url("Airbus", date(2026, 8, 1), date(2026, 8, 7))
        self.assertIn("conditions%5Btype%5D%5B%5D=RULE", url)
        self.assertIn("conditions%5Btype%5D%5B%5D=PRORULE", url)


class TestParse(unittest.TestCase):
    def test_extracts_document_number_as_ref(self):
        ads = fr.parse_fr_response(SAMPLE)
        self.assertEqual(ads[0]["ref_number"], "2026-15308")

    def test_source_url_is_the_fetchable_full_text_form(self):
        """The canonical /documents/ URL 302s to a bot wall; full_text/html works."""
        ads = fr.parse_fr_response(SAMPLE)
        self.assertEqual(
            ads[0]["source_url"],
            "https://www.federalregister.gov/documents/full_text/html/2026/07/29/2026-15308.html")

    def test_types_hint_read_from_abstract(self):
        ads = fr.parse_fr_response(SAMPLE)
        self.assertIn("A321", ads[0]["types_hint"])

    def test_empty_results_yield_empty_list(self):
        self.assertEqual(fr.parse_fr_response({"count": 0, "results": []}), [])


class TestManufacturerHint(unittest.TestCase):
    """Regression: faa_register never emitted manufacturer_hint, so
    coverage_ledger.classify() saw None -> unknown -> unconditionally
    unaccounted for every FAA AD whose abstract carried no model-code hint
    (7 of 15 unaccounted rows in the real 2026-08-16 run, all titled
    'Airworthiness Directives; Airbus Helicopters')."""

    def test_hint_derived_from_title_strips_boilerplate(self):
        ads = fr.parse_fr_response(SAMPLE)
        self.assertEqual(ads[0]["manufacturer_hint"], "Airbus SAS Airplanes")

    def test_hint_for_real_airbus_helicopters_title(self):
        payload = {"count": 1, "results": [{
            "document_number": "2026-16641",
            "publication_date": "2026-08-01",
            "type": "Rule",
            "title": "Airworthiness Directives; Airbus Helicopters",
            "abstract": "",
        }]}
        ads = fr.parse_fr_response(payload)
        self.assertEqual(ads[0]["manufacturer_hint"], "Airbus Helicopters")

    def test_every_entry_has_the_key_even_when_title_missing(self):
        payload = {"count": 1, "results": [{
            "document_number": "2026-00001", "publication_date": "2026-08-01",
        }]}
        ads = fr.parse_fr_response(payload)
        self.assertEqual(ads[0]["manufacturer_hint"], "")


class TestRefType(unittest.TestCase):
    """Regression: every FR document was hard-coded ref_type 'AD' downstream in
    regulator_sweep.py, including PRORULE (proposed rule) documents — which
    then mis-routed an NPRM away from Standing Watch -> On the Horizon."""

    def test_rule_type_maps_to_ad(self):
        ads = fr.parse_fr_response(SAMPLE)
        self.assertEqual(ads[0]["ref_type"], "AD")

    def test_prorule_type_maps_to_nprm(self):
        payload = {"count": 1, "results": [{
            "document_number": "2026-16157",
            "publication_date": "2026-07-30",
            "type": "Proposed Rule",
            "title": "Airworthiness Directives; Boeing Airplanes",
            "abstract": "This proposed AD applies to Model 787-9 airplanes.",
        }]}
        ads = fr.parse_fr_response(payload)
        self.assertEqual(ads[0]["ref_type"], "NPRM")

    def test_prorule_code_form_also_maps_to_nprm(self):
        self.assertEqual(fr._ref_type_from_doc_type("PRORULE"), "NPRM")

    def test_rule_code_form_also_maps_to_ad(self):
        self.assertEqual(fr._ref_type_from_doc_type("RULE"), "AD")

    def test_unknown_type_defaults_to_ad(self):
        self.assertEqual(fr._ref_type_from_doc_type("Notice"), "AD")

    def test_missing_type_defaults_to_ad(self):
        self.assertEqual(fr._ref_type_from_doc_type(None), "AD")


class TestTypeToken(unittest.TestCase):
    def test_engine_designation_does_not_produce_type_hint(self):
        """'Trent 700' must NOT match — it is an engine, not a Boeing 7x7 model."""
        payload = {"count": 1, "results": [{
            "document_number": "2026-00001",
            "publication_date": "2026-08-01",
            "title": "Airworthiness Directives; Rolls-Royce plc Engines",
            "abstract": "This AD applies to Trent 700 series engines.",
        }]}
        ads = fr.parse_fr_response(payload)
        self.assertEqual(ads[0]["types_hint"], [])

    def test_boeing_777_300er_yields_hint(self):
        payload = {"count": 1, "results": [{
            "document_number": "2026-00002",
            "publication_date": "2026-08-01",
            "title": "Airworthiness Directives; Boeing 777-300ER Airplanes",
            "abstract": "This AD applies to Model 777-300ER airplanes.",
        }]}
        ads = fr.parse_fr_response(payload)
        hint = ads[0]["types_hint"]
        self.assertTrue(any("777" in h for h in hint))

    def test_airbus_a321_251nx_yields_a321(self):
        payload = {"count": 1, "results": [{
            "document_number": "2026-00003",
            "publication_date": "2026-08-01",
            "title": "Airworthiness Directives; Airbus SAS Airplanes",
            "abstract": "This AD applies to Model A321-251NX airplanes.",
        }]}
        ads = fr.parse_fr_response(payload)
        self.assertIn("A321", ads[0]["types_hint"])


class TestEnumerate(unittest.TestCase):
    def test_dedups_across_terms(self):
        ads, cov = fr.enumerate_faa(["Airbus", "Boeing"], date(2026, 8, 1), date(2026, 8, 7),
                                    lambda url: SAMPLE)
        self.assertEqual(len(ads), 1)
        self.assertTrue(cov["complete"])

    def test_fetch_failure_marks_coverage_incomplete(self):
        def boom(url):
            raise OSError("connection reset")
        ads, cov = fr.enumerate_faa(["Airbus"], date(2026, 8, 1), date(2026, 8, 7), boom)
        self.assertEqual(ads, [])
        self.assertFalse(cov["complete"])
        self.assertIn("connection reset", cov["reason"])

    def test_soft_success_error_body_marks_coverage_incomplete(self):
        """HTTP 200 with an unexpected shape (e.g. rate-limit body) must not be
        read as zero results — that would falsely report complete=True."""
        ads, cov = fr.enumerate_faa(
            ["Airbus"], date(2026, 8, 1), date(2026, 8, 7),
            lambda url: {"error": "rate limited"})
        self.assertEqual(ads, [])
        self.assertFalse(cov["complete"])
        self.assertIn("Airbus", cov["reason"])

    def test_genuine_empty_result_set_marks_coverage_complete(self):
        """A well-formed response with zero results is a real quiet period, not
        a failure — analogous to the EASA quiet-period case."""
        ads, cov = fr.enumerate_faa(
            ["Airbus"], date(2026, 8, 1), date(2026, 8, 7),
            lambda url: {"count": 0, "results": []})
        self.assertEqual(ads, [])
        self.assertTrue(cov["complete"])

    def test_real_zero_hit_payload_with_no_results_key_marks_complete(self):
        """The live Federal Register API omits "results" entirely on a zero-hit
        query — observed verbatim. Must count as covered, not as a failure."""
        real_zero_hit = {
            "description": "Documents matching 'GE Aerospace', published from "
                            "07/31/2026 to 08/07/2026, and of type Rule or "
                            "Proposed Rule",
            "count": 0,
        }
        ads, cov = fr.enumerate_faa(
            ["GE Aerospace"], date(2026, 7, 31), date(2026, 8, 7),
            lambda url: real_zero_hit)
        self.assertEqual(ads, [])
        self.assertTrue(cov["complete"])

    def test_list_payload_marks_coverage_incomplete_without_raising(self):
        ads, cov = fr.enumerate_faa(
            ["Airbus"], date(2026, 8, 1), date(2026, 8, 7), lambda url: [])
        self.assertEqual(ads, [])
        self.assertFalse(cov["complete"])

    def test_none_payload_marks_coverage_incomplete_without_raising(self):
        ads, cov = fr.enumerate_faa(
            ["Airbus"], date(2026, 8, 1), date(2026, 8, 7), lambda url: None)
        self.assertEqual(ads, [])
        self.assertFalse(cov["complete"])

    def test_partial_term_failure_keeps_first_terms_ads(self):
        def fetch(url):
            if "Airbus" in url:
                return SAMPLE
            return {"error": "rate limited"}
        ads, cov = fr.enumerate_faa(
            ["Airbus", "Boeing"], date(2026, 8, 1), date(2026, 8, 7), fetch)
        self.assertEqual(len(ads), 1)
        self.assertEqual(ads[0]["ref_number"], "2026-15308")
        self.assertFalse(cov["complete"])

    def test_middle_term_failure_does_not_abandon_remaining_terms(self):
        """A failing term must not stop enumeration of the terms after it —
        FAA terms are independent manufacturer searches, unlike EASA's
        sequential biweekly periods."""
        first = {"count": 1, "results": [{
            "document_number": "2026-00010",
            "publication_date": "2026-08-01",
            "title": "Airworthiness Directives; Airbus SAS Airplanes",
            "abstract": "Model A321 airplanes.",
        }]}
        third = {"count": 1, "results": [{
            "document_number": "2026-00030",
            "publication_date": "2026-08-03",
            "title": "Airworthiness Directives; Boeing Airplanes",
            "abstract": "Model 777-300ER airplanes.",
        }]}

        def fetch(url):
            if "Airbus" in url:
                return first
            if "Rolls-Royce" in url:
                return {"error": "rate limited"}
            if "Boeing" in url:
                return third
            raise AssertionError("unexpected term in url: %s" % url)

        ads, cov = fr.enumerate_faa(
            ["Airbus", "Rolls-Royce", "Boeing"], date(2026, 8, 1), date(2026, 8, 7), fetch)
        refs = {ad["ref_number"] for ad in ads}
        self.assertEqual(refs, {"2026-00010", "2026-00030"})
        self.assertFalse(cov["complete"])
        self.assertIn("Rolls-Royce", cov["reason"])

    def test_string_results_value_marks_term_failed_without_raising(self):
        ads, cov = fr.enumerate_faa(
            ["Airbus"], date(2026, 8, 1), date(2026, 8, 7),
            lambda url: {"count": 1, "results": "x"})
        self.assertEqual(ads, [])
        self.assertFalse(cov["complete"])

    def test_dict_results_value_marks_term_failed_without_raising(self):
        ads, cov = fr.enumerate_faa(
            ["Airbus"], date(2026, 8, 1), date(2026, 8, 7),
            lambda url: {"count": 1, "results": {"a": 1}})
        self.assertEqual(ads, [])
        self.assertFalse(cov["complete"])

    def test_middle_term_crash_does_not_discard_earlier_or_later_ads(self):
        """The exact regression from fix round 2: a parse-time crash on one
        term must not propagate out of enumerate_faa and discard ADs already
        collected from a term that succeeded before it."""
        first = {"count": 1, "results": [{
            "document_number": "2026-00010",
            "publication_date": "2026-08-01",
            "title": "Airworthiness Directives; Airbus SAS Airplanes",
            "abstract": "Model A321 airplanes.",
        }]}
        third = {"count": 1, "results": [{
            "document_number": "2026-00030",
            "publication_date": "2026-08-03",
            "title": "Airworthiness Directives; Boeing Airplanes",
            "abstract": "Model 777-300ER airplanes.",
        }]}

        def fetch(url):
            if "Airbus" in url:
                return first
            if "Rolls-Royce" in url:
                return {"count": 1, "results": "x"}
            if "Boeing" in url:
                return third
            raise AssertionError("unexpected term in url: %s" % url)

        ads, cov = fr.enumerate_faa(
            ["Airbus", "Rolls-Royce", "Boeing"], date(2026, 8, 1), date(2026, 8, 7), fetch)
        refs = {ad["ref_number"] for ad in ads}
        self.assertEqual(refs, {"2026-00010", "2026-00030"})
        self.assertFalse(cov["complete"])
        self.assertIn("Rolls-Royce", cov["reason"])


if __name__ == "__main__":
    unittest.main()
