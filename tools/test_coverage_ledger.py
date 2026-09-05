#!/usr/bin/env python3
"""Tests for tools/coverage_ledger.py — proves every swept AD was handled."""
import json
import os
import tempfile
import unittest

import coverage_ledger as cl

YAML = """
fleet:
  - type: A330-300
    aliases: [A333, "330-300"]
  - type: A321neo
    aliases: [A21N, "A321-200N"]
  - type: 777-300ER
    aliases: [B77W]
"""

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REAL_FLEET_YAML = os.path.join(_REPO_ROOT, "config", "fleet.yaml")


class TestTrackedTokens(unittest.TestCase):
    def test_includes_types_and_aliases(self):
        tokens = cl.tracked_tokens(YAML)
        self.assertIn("a330-300", tokens)
        self.assertIn("a21n", tokens)
        self.assertIn("b77w", tokens)


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.tokens = cl.tracked_tokens(YAML)

    def test_reported_when_in_digest(self):
        ad = {"ref_number": "2026-0142", "types_hint": ["A321"]}
        self.assertEqual(cl.classify(ad, {"2026-0142"}, set(), self.tokens), "reported")

    def test_suppressed_when_in_prior_ledger(self):
        ad = {"ref_number": "2026-0142", "types_hint": ["A321"]}
        self.assertEqual(cl.classify(ad, set(), {"2026-0142"}, self.tokens), "suppressed")

    def test_excluded_when_no_tracked_type(self):
        ad = {"ref_number": "2026-9999", "types_hint": ["A220"]}
        self.assertEqual(cl.classify(ad, set(), set(), self.tokens), "excluded")

    def test_unaccounted_when_fleet_matching_but_absent(self):
        ad = {"ref_number": "2026-0142", "types_hint": ["A321"]}
        self.assertEqual(cl.classify(ad, set(), set(), self.tokens), "unaccounted")

    def test_empty_types_hint_fails_loud_not_silent(self):
        """A listing-parse failure must surface, never be quietly excluded."""
        ad = {"ref_number": "2026-0142", "types_hint": []}
        self.assertEqual(cl.classify(ad, set(), set(), self.tokens), "unaccounted")


class TestBuildLedger(unittest.TestCase):
    def test_unaccounted_collected_for_the_report(self):
        sweep = {"coverage": [], "ads": [
            {"ref_number": "2026-0142", "types_hint": ["A321"], "regulator": "EASA",
             "subject": "Rib 5", "source_url": "https://ad.easa.europa.eu/ad/2026-0142"}]}
        ledger = cl.build_ledger(sweep, records={"records": []}, seen_refs=set(), config_text=YAML)
        self.assertEqual(len(ledger["unaccounted"]), 1)
        self.assertEqual(ledger["unaccounted"][0]["ref_number"], "2026-0142")

    def test_counts_present_for_every_bucket(self):
        ledger = cl.build_ledger({"coverage": [], "ads": []}, {"records": []}, set(), YAML)
        for bucket in ("reported", "suppressed", "excluded", "unaccounted"):
            self.assertIn(bucket, ledger)


class TestRealFleetYaml(unittest.TestCase):
    """Regression coverage using the ACTUAL config/fleet.yaml, not a toy fixture.

    EASA AD 2026-0142 (wing structural directive on the tracked A321neo fleet)
    was missed by two consecutive weekly digests because it carries only the
    generic family hint ["A318", "A319", "A320", "A321"], not "A321neo". This
    proves that hint, exactly as EASA emits it, is classified as fleet-matching
    against the real fleet config — the regression this feature exists to catch.
    """

    def setUp(self):
        with open(_REAL_FLEET_YAML, encoding="utf-8") as fh:
            self.config_text = fh.read()
        self.tokens = cl.tracked_tokens(self.config_text)

    def test_easa_generic_family_hint_matches_tracked_a321neo(self):
        # EASA yields bare family codes covering the whole A320 family, not the
        # NEO-specific suffix the fleet config uses.
        types_hint = ["A318", "A319", "A320", "A321"]
        self.assertTrue(cl.matches_fleet(types_hint, self.tokens))

    def test_ad_2026_0142_is_unaccounted_when_absent_from_digest_and_ledger(self):
        ad = {"ref_number": "2026-0142", "types_hint": ["A318", "A319", "A320", "A321"]}
        self.assertEqual(cl.classify(ad, set(), set(), self.tokens), "unaccounted")

    def test_faa_specific_variant_hint_matches_tracked_777_300er(self):
        # FAA yields full variants for Boeing types.
        self.assertTrue(cl.matches_fleet(["777-300ER"], self.tokens))

    def test_faa_family_code_hint_matches_tracked_a330_300(self):
        # FAA yields bare family codes for Airbus types too.
        self.assertTrue(cl.matches_fleet(["A330"], self.tokens))

    def test_hint_outside_fleet_does_not_match(self):
        # A318 alone (no A321) is not a tracked type or alias anywhere in the fleet.
        self.assertFalse(cl.matches_fleet(["A318"], self.tokens))


class TestReportedRefsFromDocketOverMatch(unittest.TestCase):
    """Fix round 1: a docket number embedded in a verbose reference string must
    never be mistaken for the AD/FR document number — that would falsely mark
    a genuinely-missed AD as 'reported'."""

    def test_docket_number_not_extracted_fr_document_number_still_is(self):
        records = {"records": [{"references": [
            {"ref_type": "AD",
             "ref_number": "FR Doc. 2026-15239 (Docket FAA-2025-2546); supersedes AD 2024-19-14"}]}]}
        refs = cl.reported_refs_from(records)
        self.assertIn("2026-15239", refs)
        self.assertNotIn("2025-2546", refs)

    def test_plain_ref_number_still_extracts(self):
        records = {"records": [{"references": [
            {"ref_type": "AD", "ref_number": "2026-0142"}]}]}
        refs = cl.reported_refs_from(records)
        self.assertIn("2026-0142", refs)

    def test_two_docket_clauses_yield_only_real_document_numbers(self):
        records = {"records": [{"references": [
            {"ref_type": "AD",
             "ref_number": "FR Doc. 2026-15239 (Docket FAA-2025-2546) and "
                            "FR Doc. 2026-15240 (Docket FAA-2025-2547)"}]}]}
        refs = cl.reported_refs_from(records)
        self.assertIn("2026-15239", refs)
        self.assertIn("2026-15240", refs)
        self.assertNotIn("2025-2546", refs)
        self.assertNotIn("2025-2547", refs)


class TestReportedRefsFromDocketNoPhrasing(unittest.TestCase):
    """Fix round 2: the Federal Register's own AD boilerplate writes 'Docket
    No. FAA-...' (with or without the period), not just 'Docket FAA-...'.
    This repo's own prior output (digests/2026-06-26-verifyproof.md) contains
    that exact phrasing, so the docket-strip must tolerate it too."""

    def _refs_for(self, ref_number):
        records = {"records": [{"references": [
            {"ref_type": "AD", "ref_number": ref_number}]}]}
        return cl.reported_refs_from(records)

    def test_docket_no_period_form(self):
        refs = self._refs_for("FR Doc. 2026-15239 (Docket FAA-2025-2546)")
        self.assertIn("2026-15239", refs)
        self.assertNotIn("2025-2546", refs)

    def test_docket_no_dot_form(self):
        refs = self._refs_for("FR Doc. 2026-15239 (Docket No. FAA-2026-3874)")
        self.assertIn("2026-15239", refs)
        self.assertNotIn("2026-3874", refs)

    def test_docket_no_without_period_form(self):
        refs = self._refs_for("FR Doc. 2026-15239 (Docket No FAA-2026-3874)")
        self.assertIn("2026-15239", refs)
        self.assertNotIn("2026-3874", refs)

    def test_two_docket_clauses_mixed_forms_both_dropped(self):
        refs = self._refs_for(
            "AD 2026-14-04; Docket No. FAA-2026-3480; Docket FAA-2026-3481")
        self.assertNotIn("2026-3480", refs)
        self.assertNotIn("2026-3481", refs)

    def test_plain_ref_number_still_extracts(self):
        refs = self._refs_for("2026-0142")
        self.assertIn("2026-0142", refs)


class TestLoadSeenRefsEventKeyOverMatch(unittest.TestCase):
    """Fix round 1: runs/_seen.json mixes reference-shaped keys ('AD:2026-0142')
    with event-shaped keys ('EVENT:<headline-slug>'). Scanning the whole key
    string for digit-dash-digit runs can misread a slug's embedded digits as a
    suppressed AD reference — a false negative on a genuine miss."""

    def _load(self, seen):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "_seen.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "seen": seen}, fh)
            return cl.load_seen_refs(path)

    def test_event_slug_digits_not_treated_as_suppressed(self):
        seen = {"EVENT:wing-rib-directive-2026-0142-followup": {"last_version": ""}}
        refs = self._load(seen)
        self.assertNotIn("2026-0142", refs)

    def test_genuine_ad_key_is_extracted(self):
        seen = {"AD:2026-0142": {"last_version": ""}}
        refs = self._load(seen)
        self.assertIn("2026-0142", refs)

    def test_garbage_key_ignored_without_raising(self):
        seen = {"totally-unparseable-garbage": {"last_version": ""}}
        try:
            refs = self._load(seen)
        except Exception as exc:  # pragma: no cover - failure path
            self.fail("load_seen_refs raised on garbage key: %r" % exc)
        self.assertEqual(refs, set())


class TestManufacturerGate(unittest.TestCase):
    def _config(self):
        import os
        p = os.path.join(os.path.dirname(__file__), "..", "config", "fleet.yaml")
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def test_tracked_oems_from_real_config(self):
        oems = [o.upper() for o in cl.tracked_oems(self._config())]
        self.assertIn("AIRBUS", oems)
        self.assertIn("BOEING", oems)

    def test_empty_hint_with_tracked_oem_still_fails_loud(self):
        """The guarantee that must survive this change."""
        ad = {"ref_number": "2026-9001", "types_hint": [],
              "manufacturer_hint": "AIRBUS S.A.S."}
        self.assertEqual(cl.classify(ad, set(), set(), cl.tracked_tokens(self._config()),
                                     cl.tracked_oems(self._config())), "unaccounted")

    def test_empty_hint_with_untracked_oem_is_excluded(self):
        ad = {"ref_number": "2026-9002", "types_hint": [],
              "manufacturer_hint": "DIAMOND AIRCRAFT INDUSTRIES"}
        self.assertEqual(cl.classify(ad, set(), set(), cl.tracked_tokens(self._config()),
                                     cl.tracked_oems(self._config())), "excluded")

    def test_missing_manufacturer_hint_fails_loud(self):
        """No signal at all must remain loud — absence of evidence is not exclusion."""
        ad = {"ref_number": "2026-9003", "types_hint": [], "manufacturer_hint": ""}
        self.assertEqual(cl.classify(ad, set(), set(), cl.tracked_tokens(self._config()),
                                     cl.tracked_oems(self._config())), "unaccounted")

    def test_matching_type_hint_still_wins_regardless_of_manufacturer(self):
        ad = {"ref_number": "2026-0142", "types_hint": ["A321"], "manufacturer_hint": ""}
        self.assertEqual(cl.classify(ad, set(), set(), cl.tracked_tokens(self._config()),
                                     cl.tracked_oems(self._config())), "unaccounted")


class TestNonTrackedDivisionExclusion(unittest.TestCase):
    """Regression (fix round 3, found on real-data acceptance run): the tracked
    fleet is fixed-wing Airbus S.A.S. / Boeing only. `manufacturer_is_tracked`
    matched on the tracked OEM's first word, so 'AIRBUS HELICOPTERS' and
    'AIRBUS HELICOPTERS DEUTSCHLAND' wrongly matched plain 'airbus' and stayed
    loud forever, even though rotorcraft are permanently out of scope.

    Fix round 5 (found on the 2026-08-22 backfill acceptance run): the trailing
    \\b in the division exclusion regex never matched a REAL glued hint like
    "AIRBUS HELICOPTERSSA 330 / AS 332 / EC 225..." (no separator between
    "HELICOPTERS" and the next column), so the exclusion never fired and six
    Airbus Helicopters ADs stayed `unaccounted` forever. This is re-pointed at
    the actual glued text from the real biweekly fixture, not a sanitised
    hand-typed string, to prevent the regex from silently regressing again."""

    def _config(self):
        import os
        p = os.path.join(os.path.dirname(__file__), "..", "config", "fleet.yaml")
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def setUp(self):
        self.oems = cl.tracked_oems(self._config())

    def test_airbus_helicopters_deutschland_is_not_tracked(self):
        self.assertFalse(cl.manufacturer_is_tracked("AIRBUS HELICOPTERS DEUTSCHLAND", self.oems))

    def test_airbus_sas_is_still_tracked(self):
        """The guarantee that must survive this change: fixed-wing Airbus stays loud."""
        self.assertTrue(cl.manufacturer_is_tracked("AIRBUS S.A.S.", self.oems))

    def test_real_glued_airbus_helicopters_hint_is_not_tracked(self):
        """The exact live failure: no separator between 'HELICOPTERS' and the
        next column, taken verbatim from tools/fixtures/biweekly_16_2026.txt
        via the real parser (2026-0148, Electrical Power AD)."""
        import easa_biweekly as eb
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "biweekly_16_2026.txt")
        with open(fixture, encoding="utf-8") as fh:
            ads = eb.parse_biweekly(fh.read())
        hint = {a["ref_number"]: a for a in ads}["2026-0148"]["manufacturer_hint"]
        self.assertTrue(hint.upper().startswith("AIRBUS HELICOPTERSSA"), hint)
        self.assertFalse(cl.manufacturer_is_tracked(hint, self.oems))

    def test_classify_excludes_real_glued_airbus_helicopters_hint(self):
        import easa_biweekly as eb
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "biweekly_16_2026.txt")
        with open(fixture, encoding="utf-8") as fh:
            ads = eb.parse_biweekly(fh.read())
        hint = {a["ref_number"]: a for a in ads}["2026-0148"]["manufacturer_hint"]
        ad = {"ref_number": "2026-9004", "types_hint": [], "manufacturer_hint": hint}
        self.assertEqual(cl.classify(ad, set(), set(), cl.tracked_tokens(self._config()), self.oems),
                          "excluded")

    def test_classify_still_fails_loud_for_airbus_sas(self):
        ad = {"ref_number": "2026-9005", "types_hint": [], "manufacturer_hint": "AIRBUS S.A.S."}
        self.assertEqual(cl.classify(ad, set(), set(), cl.tracked_tokens(self._config()), self.oems),
                          "unaccounted")

    def test_backfill_six_helicopters_ads_now_excluded(self):
        """The six real Airbus Helicopters ADs from the 2026-08-22 backfill run
        that were wrongly `unaccounted` before this fix. Drives the assertion
        from the actual production sweep file, not hand-written strings."""
        import json
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sweep_path = os.path.join(repo_root, "runs", "_backfill_2026-08-22", "00_sweep.json")
        with open(sweep_path, encoding="utf-8") as fh:
            sweep = json.load(fh)
        ads_by_ref = {a["ref_number"]: a for a in sweep["ads"]}
        for ref in ("2026-0129", "2026-0148", "2026-0146", "2026-0132", "2026-0052", "2023-0148"):
            ad = ads_by_ref[ref]
            bucket = cl.classify(ad, set(), set(), cl.tracked_tokens(self._config()), self.oems)
            self.assertEqual(bucket, "excluded", "%s: expected excluded, got %s" % (ref, bucket))


class TestManufacturerHeadWordBoundary(unittest.TestCase):
    """Regression (fix round 4): the tracked OEM head "ge" (GE Aerospace) was
    matched as a bare substring, so it matched inside "Landing Gear" — which
    appears in most airframe AD subjects — and kept every one of them
    permanently loud regardless of actual manufacturer. Matching must be
    anchored on word boundaries so "ge" only matches the standalone token."""

    def _config(self):
        import os
        p = os.path.join(os.path.dirname(__file__), "..", "config", "fleet.yaml")
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def setUp(self):
        self.oems = cl.tracked_oems(self._config())

    def test_landing_gear_in_dassault_subject_does_not_false_match_ge(self):
        hint = "DASSAULT AVIATIONFalcon 6XLanding Gear - Main Landing Gear Axle Beam"
        self.assertFalse(cl.manufacturer_is_tracked(hint, self.oems))

    def test_landing_gear_in_pilatus_subject_does_not_false_match_ge(self):
        hint = "PILATUS AIRCRAFT LTDPC-24Landing Gear"
        self.assertFalse(cl.manufacturer_is_tracked(hint, self.oems))

    def test_real_ge_aerospace_still_matches(self):
        self.assertTrue(cl.manufacturer_is_tracked("GE AEROSPACE", self.oems))

    def test_real_ge_aerospace_matches_within_longer_text(self):
        self.assertTrue(cl.manufacturer_is_tracked("GE Aerospace GEnx engines", self.oems))

    def test_airbus_sas_still_tracked(self):
        self.assertTrue(cl.manufacturer_is_tracked("AIRBUS S.A.S.", self.oems))

    def test_airbus_helicopters_deutschland_still_excluded(self):
        self.assertFalse(cl.manufacturer_is_tracked("AIRBUS HELICOPTERS DEUTSCHLAND", self.oems))

    def test_boeing_still_tracked(self):
        self.assertTrue(cl.manufacturer_is_tracked("BOEING", self.oems))

    def test_rolls_royce_still_tracked(self):
        self.assertTrue(cl.manufacturer_is_tracked("ROLLS-ROYCE DEUTSCHLAND Ltd & Co KG", self.oems))

    def test_diamond_still_untracked(self):
        self.assertFalse(cl.manufacturer_is_tracked("DIAMOND AIRCRAFT INDUSTRIES", self.oems))

    def test_empty_hint_still_unknown(self):
        self.assertIsNone(cl.manufacturer_is_tracked("", self.oems))


class TestNonTrackedDivisionWordBoundary(unittest.TestCase):
    """Hardening (Task 9, carried over from the previous task's review): the
    exclusion check (_NON_TRACKED_DIVISIONS) used a plain substring test while the
    tracked-OEM check immediately below it was hardened to word-boundary matching
    in fix round 4, after a real collision ('ge' inside 'Landing Gear'). Harden the
    exclusion branch the same way, for defense-in-depth on the branch that decides
    loud-vs-silent."""

    def _config(self):
        p = os.path.join(os.path.dirname(__file__), "..", "config", "fleet.yaml")
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def setUp(self):
        self.oems = cl.tracked_oems(self._config())

    def test_airbus_helicopters_deutschland_still_excluded(self):
        self.assertFalse(cl.manufacturer_is_tracked("AIRBUS HELICOPTERS DEUTSCHLAND", self.oems))

    def test_airbus_sas_still_tracked(self):
        self.assertTrue(cl.manufacturer_is_tracked("AIRBUS S.A.S.", self.oems))


class TestEndToEndScenarios(unittest.TestCase):
    """The controller's six end-to-end scenarios, re-checked after the fix-round
    edits to reported_refs_from and load_seen_refs."""

    def setUp(self):
        with open(_REAL_FLEET_YAML, encoding="utf-8") as fh:
            self.config_text = fh.read()
        self.sweep_2026_0142 = {"coverage": [], "ads": [
            {"ref_number": "2026-0142", "types_hint": ["A318", "A319", "A320", "A321"],
             "regulator": "EASA", "subject": "Rib 5",
             "source_url": "https://ad.easa.europa.eu/ad/2026-0142"}]}

    def test_unaccounted_with_empty_digest(self):
        ledger = cl.build_ledger(self.sweep_2026_0142, {"records": []}, set(), self.config_text)
        self.assertEqual([e["ref_number"] for e in ledger["unaccounted"]], ["2026-0142"])
        self.assertEqual(ledger["reported"], [])
        self.assertEqual(ledger["suppressed"], [])

    def test_reported_when_digest_carries_it(self):
        records = {"records": [{"references": [
            {"ref_type": "AD",
             "ref_number": "FR Doc. 2026-0142 (Docket EASA-2025-9999)"}]}]}
        ledger = cl.build_ledger(self.sweep_2026_0142, records, set(), self.config_text)
        self.assertEqual([e["ref_number"] for e in ledger["reported"]], ["2026-0142"])
        self.assertEqual(ledger["unaccounted"], [])

    def test_suppressed_when_in_prior_dedup_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "_seen.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "seen": {
                    "AD:2026-0142": {"last_version": ""},
                    "EVENT:unrelated-2026-0142-slug": {"last_version": ""}}}, fh)
            seen_refs = cl.load_seen_refs(path)
        ledger = cl.build_ledger(self.sweep_2026_0142, {"records": []}, seen_refs, self.config_text)
        self.assertEqual([e["ref_number"] for e in ledger["suppressed"]], ["2026-0142"])
        self.assertEqual(ledger["unaccounted"], [])

    def test_empty_types_hint_still_fails_loud(self):
        sweep = {"coverage": [], "ads": [
            {"ref_number": "2026-0142", "types_hint": [], "regulator": "EASA",
             "subject": "Rib 5", "source_url": "https://ad.easa.europa.eu/ad/2026-0142"}]}
        ledger = cl.build_ledger(sweep, {"records": []}, set(), self.config_text)
        self.assertEqual([e["ref_number"] for e in ledger["unaccounted"]], ["2026-0142"])

    def test_off_fleet_a220_still_excluded(self):
        sweep = {"coverage": [], "ads": [
            {"ref_number": "2026-9999", "types_hint": ["A220"], "regulator": "EASA",
             "subject": "Unrelated", "source_url": "https://ad.easa.europa.eu/ad/2026-9999"}]}
        ledger = cl.build_ledger(sweep, {"records": []}, set(), self.config_text)
        self.assertEqual([e["ref_number"] for e in ledger["excluded"]], ["2026-9999"])

    def test_main_exits_1_on_non_empty_unaccounted(self):
        with tempfile.TemporaryDirectory() as d:
            sweep_path = os.path.join(d, "sweep.json")
            infile_path = os.path.join(d, "deduped.json")
            ledger_path = os.path.join(d, "seen.json")
            out_path = os.path.join(d, "out.json")
            with open(sweep_path, "w", encoding="utf-8") as fh:
                json.dump(self.sweep_2026_0142, fh)
            with open(infile_path, "w", encoding="utf-8") as fh:
                json.dump({"records": []}, fh)
            with open(ledger_path, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "seen": {}}, fh)
            rc = cl.main(["--sweep", sweep_path, "--infile", infile_path,
                          "--ledger", ledger_path, "--config", _REAL_FLEET_YAML,
                          "--outfile", out_path])
            self.assertEqual(rc, 1)
            with open(out_path, encoding="utf-8") as fh:
                out = json.load(fh)
            self.assertEqual(len(out["unaccounted"]), 1)


if __name__ == "__main__":
    unittest.main()
