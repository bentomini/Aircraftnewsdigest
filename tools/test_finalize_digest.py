"""Tests for finalize_digest.py — deterministic post-render fixups for the digest.

Run: python tools/test_finalize_digest.py
"""
import contextlib
import io
import json
import os
import tempfile
import unittest

import finalize_digest as f

# A config path that does not exist forces _read_fleet_threshold to its default (5),
# so suppression tests are independent of the real config/fleet.yaml.
NO_CONFIG = "__no_such_config__.yaml"


class TestUnescape(unittest.TestCase):
    def test_ampersand(self):
        self.assertEqual(f.unescape_entities("Sources &amp; Confidence"),
                         "Sources & Confidence")

    def test_lt_gt(self):
        self.assertEqual(f.unescape_entities("a &lt;b&gt; c"), "a <b> c")

    def test_leaves_plain_text(self):
        self.assertEqual(f.unescape_entities("no entities here"), "no entities here")


class TestDedupeRefType(unittest.TestCase):
    def test_ad_ad_collapses(self):
        self.assertEqual(f.dedupe_ref_type("AD AD 2026-10-06"), "AD 2026-10-06")

    def test_single_ad_untouched(self):
        self.assertEqual(f.dedupe_ref_type("AD 2026-10-06"), "AD 2026-10-06")

    def test_sb_sb_collapses(self):
        self.assertEqual(f.dedupe_ref_type("SB SB 700-71-1234"), "SB 700-71-1234")

    def test_does_not_collapse_unrelated_words(self):
        # "and and" must not be touched; only known ref types
        self.assertEqual(f.dedupe_ref_type("and and then"), "and and then")

    def test_regulator_between_two_types_collapses(self):
        # G15: writer prepends ref_type 'AD' to ref_number 'FAA AD 2025-25-12'.
        self.assertEqual(
            f.dedupe_ref_type("AD FAA AD 2025-25-12"), "FAA AD 2025-25-12")

    def test_regulator_emergency_form_collapses(self):
        self.assertEqual(
            f.dedupe_ref_type("AD FAA emergency AD (MD-11 grounding, Nov 2025)"),
            "FAA emergency AD (MD-11 grounding, Nov 2025)")

    def test_bridge_does_not_cross_comma(self):
        # A later, distinct AD (after a comma) must NOT pull the leading type off.
        self.assertEqual(
            f.dedupe_ref_type("AD 2025-25-12, supersedes AD 2025-13-12"),
            "AD 2025-25-12, supersedes AD 2025-13-12")

    def test_ead_easa_ad_left_alone(self):
        # ref_type 'EAD' + ref_number 'EASA AD ...' is not a doubled type.
        self.assertEqual(
            f.dedupe_ref_type("EAD EASA AD 2026-0119-E"), "EAD EASA AD 2026-0119-E")

    def test_two_distinct_ads_in_headline_both_kept(self):
        # Regression: the bridge must NOT span digits/slash to a later, distinct AD.
        s = "FAA AD 2025-24-51 / EASA AD 2025-0268-E: ELAC software fix"
        self.assertEqual(f.dedupe_ref_type(s), s)

    def test_two_distinct_ads_in_prose_both_kept(self):
        s = ("EASA issued emergency AD 2025-0268-E and the FAA mirrored it "
             "with emergency AD 2025-24-51")
        self.assertEqual(f.dedupe_ref_type(s), s)


class TestStripOtherRefType(unittest.TestCase):
    def test_leading_other_stripped(self):
        line = "*Technical detail:* other NTSB docket DCA26FA194 — Preliminary Report"
        self.assertEqual(
            f.strip_other_ref_type(line),
            "*Technical detail:* NTSB docket DCA26FA194 — Preliminary Report")

    def test_other_as_second_reference_stripped(self):
        line = ("*Technical detail:* AD FAA AD 2025-1 [VERIFIED]; "
                "other NTSB docket DCA99 [UNVERIFIED]")
        self.assertEqual(
            f.strip_other_ref_type(line),
            "*Technical detail:* AD FAA AD 2025-1 [VERIFIED]; "
            "NTSB docket DCA99 [UNVERIFIED]")

    def test_prose_other_not_touched(self):
        line = "Trent 700 and other affected engines remain in service."
        self.assertEqual(f.strip_other_ref_type(line), line)

    def test_other_before_lowercase_not_touched(self):
        line = "*Technical detail:* other affected systems were reviewed."
        self.assertEqual(f.strip_other_ref_type(line), line)


class TestReorderSections(unittest.TestCase):
    SAMPLE = (
        "## Major Industry Events\n\n"
        "**Event X** — 2026-01-01.\n\n"
        "## Read-Across (Peer Types)\n\n"
        "**Peer Z** — 2026-02-02.\n\n"
        "## Directly Fleet-Relevant\n\n"
        "**Fleet Y** — A330-300, 2026-06-12.\n\n"
        "**Sources & Confidence:** 3 items — 3 VERIFIED.\n"
    )

    def test_fleet_section_first(self):
        out = f.reorder_sections(self.SAMPLE)
        self.assertLess(out.index("Directly Fleet-Relevant"), out.index("Read-Across"))
        self.assertLess(out.index("Read-Across"), out.index("Major Industry Events"))

    def test_sources_line_stays_last(self):
        out = f.reorder_sections(self.SAMPLE)
        self.assertGreater(out.index("Sources & Confidence"),
                           out.index("Major Industry Events"))

    def test_content_preserved(self):
        out = f.reorder_sections(self.SAMPLE)
        for needle in ("Event X", "Peer Z", "Fleet Y", "3 VERIFIED"):
            self.assertIn(needle, out)

    def test_already_ordered_is_stable(self):
        ordered = (
            "## Directly Fleet-Relevant\n\n**A** — x.\n\n"
            "## Major Industry Events\n\n**B** — y.\n\n"
            "**Sources & Confidence:** 2 items.\n"
        )
        out = f.reorder_sections(ordered)
        self.assertLess(out.index("**A**"), out.index("**B**"))
        self.assertIn("Sources & Confidence", out)


class TestStandingWatchOrder(unittest.TestCase):
    def test_standing_watch_after_core_before_sources(self):
        text = (
            "## Standing Watch\n\nradar stuff\n\n"
            "## Directly Fleet-Relevant\n\nan AD\n\n"
            "**Sources & Confidence:** 1 item.\n"
        )
        out = f.finalize(text)
        i_fleet = out.index("## Directly Fleet-Relevant")
        i_watch = out.index("## Standing Watch")
        i_sources = out.index("**Sources & Confidence")
        self.assertLess(i_fleet, i_watch)     # core section before Standing Watch
        self.assertLess(i_watch, i_sources)   # Standing Watch before the Sources footer

    def test_standing_watch_sorts_before_unknown_section(self):
        # Discriminates the SECTION_ORDER change: only an explicitly-ranked
        # Standing Watch sorts ahead of a genuinely-unknown H2 that precedes it.
        text = (
            "## Some Unknown Section\n\njunk\n\n"
            "## Standing Watch\n\nradar stuff\n\n"
            "**Sources & Confidence:** 1 item.\n"
        )
        out = f.finalize(text)
        self.assertLess(out.index("## Standing Watch"),
                        out.index("## Some Unknown Section"))


class TestReadAcrossSuppression(unittest.TestCase):
    @staticmethod
    def _digest(n_fleet):
        fleet = "".join(
            f"**Fleet item {i}** — A350, 2026-06-0{i}.\n*What happened:* x.\n\n"
            for i in range(1, n_fleet + 1)
        )
        return (
            "# Digest\n\n## Directly Fleet-Relevant\n\n" + fleet +
            "## Read-Across (Peer Types)\n\n"
            "**Peer item** — 787, 2026-06-09.\n*What happened:* peer.\n\n"
            "## Major Industry Events\n\n"
            "**Big event** — 2026-06-10.\n*What happened:* worldwide.\n\n"
            "**Sources & Confidence:** items.\n"
        )

    def test_counts_fleet_items(self):
        self.assertEqual(f._count_fleet_items(self._digest(5)), 5)
        self.assertEqual(f._count_fleet_items(self._digest(2)), 2)

    def test_suppressed_at_threshold(self):
        out = f.suppress_readacross(self._digest(5), NO_CONFIG)
        self.assertNotIn("## Read-Across", out)
        self.assertNotIn("Peer item", out)

    def test_kept_below_threshold(self):
        out = f.suppress_readacross(self._digest(4), NO_CONFIG)
        self.assertIn("## Read-Across", out)
        self.assertIn("Peer item", out)

    def test_major_events_survive_suppression(self):
        out = f.suppress_readacross(self._digest(6), NO_CONFIG)
        self.assertNotIn("Peer item", out)
        self.assertIn("## Major Industry Events", out)
        self.assertIn("Big event", out)

    def test_finalize_drops_no_orphan_header(self):
        # After suppression, reorder_sections must not leave an empty "## Read-Across" header.
        out = f.finalize(self._digest(5), config_path=NO_CONFIG)
        self.assertNotIn("## Read-Across", out)
        self.assertIn("## Directly Fleet-Relevant", out)
        self.assertIn("## Major Industry Events", out)

    def test_threshold_read_from_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("standing_watch:\n  read_across_min_fleet_items: 3\n")
            path = fh.name
        try:
            self.assertEqual(f._read_fleet_threshold(path), 3)
            out = f.suppress_readacross(self._digest(3), path)
            self.assertNotIn("Peer item", out)  # suppressed at 3 with this config
        finally:
            os.unlink(path)

    def test_missing_config_falls_back_to_default(self):
        self.assertEqual(f._read_fleet_threshold(NO_CONFIG), 5)


class TestFinalizeEndToEnd(unittest.TestCase):
    def test_all_fixups_together(self):
        raw = (
            "## Major Industry Events\n\n"
            "**Big event** — 2026-01-01.\n\n"
            "## Directly Fleet-Relevant\n\n"
            "*Technical detail:* AD AD 2026-10-06 — Rolls-Royce Ltd &amp; Co KG. "
            "[VERIFIED — primary source]\n\n"
            "**Sources &amp; Confidence:** 2 items — 1 VERIFIED, 0 REPORTED.\n"
        )
        out = f.finalize(raw)
        self.assertNotIn("&amp;", out)
        self.assertNotIn("AD AD", out)
        self.assertIn("Sources & Confidence", out)
        # Fleet section must precede Industry section after reordering.
        self.assertLess(out.index("Directly Fleet-Relevant"),
                        out.index("Major Industry Events"))


class TestStripRadarArtifacts(unittest.TestCase):
    def test_strips_comma_form_inside_headline_parens(self):
        text = "**FAA AD (FR doc 2026-13982, Compliance Radar — effective Aug 14): A330/A350 antenna corrosion**"
        out = f.strip_radar_artifacts(text)
        self.assertNotIn("Compliance Radar", out)
        self.assertIn("FR doc 2026-13982", out)

    def test_strips_full_paren_form(self):
        text = "**EASA AD 2026-0134 (Compliance Radar — effective 2026-08-14) ELAC**"
        out = f.strip_radar_artifacts(text)
        self.assertNotIn("Compliance Radar", out)

    def test_radar_section_heading_untouched(self):
        text = "### Compliance Radar\n- **AD 2026-12-11** becomes effective **2026-08-03**"
        self.assertEqual(f.strip_radar_artifacts(text), text)

    def test_unbounded_comma_form_leaves_headline_intact(self):
        # Regression for the 2026-07-27 review finding: a comma-form artifact with no
        # closing paren on the line must never eat the rest of the headline. Better to
        # leave the artifact standing than to destroy the reference/date/type it precedes.
        text = ("**EASA AD 2026-0134, Compliance Radar — effective 2026-08-14: ELAC B L104 "
                "replacement** — A321neo, 2026-07-08.")
        out = f.strip_radar_artifacts(text)
        self.assertIn("EASA AD 2026-0134", out)
        self.assertIn("effective 2026-08-14", out)
        self.assertIn("ELAC B L104 replacement", out)
        self.assertIn("A321neo, 2026-07-08.", out)


class TestComputeDegraded(unittest.TestCase):
    def _rec(self, ref_type="AD", confidence="UNVERIFIED"):
        return {"references": [{"ref_type": ref_type, "ref_number": "X", "confidence": confidence}]}

    def test_preflight_all_blocked_is_degraded(self):
        degraded, reason = f.compute_degraded({"any_ok": False}, [])
        self.assertTrue(degraded)
        self.assertIn("no primary-source endpoint reachable", reason)

    def test_regulatory_refs_zero_verified_is_degraded(self):
        degraded, reason = f.compute_degraded({"any_ok": True}, [self._rec(), self._rec("EAD")])
        self.assertTrue(degraded)
        self.assertIn("0 VERIFIED", reason)

    def test_verified_ref_present_not_degraded(self):
        recs = [self._rec(), self._rec(confidence="VERIFIED")]
        self.assertEqual(f.compute_degraded({"any_ok": True}, recs), (False, None))

    def test_quiet_week_no_regulatory_refs_not_degraded(self):
        recs = [{"references": []}, {"references": [{"ref_type": "other", "ref_number": "Y",
                                                    "confidence": "UNVERIFIED"}]}]
        self.assertEqual(f.compute_degraded({"any_ok": True}, recs), (False, None))

    def test_missing_preflight_falls_through_to_record_rule(self):
        self.assertEqual(f.compute_degraded(None, []), (False, None))


class TestDegradedBanner(unittest.TestCase):
    def test_banner_inserted_after_week_line(self):
        text = "# Digest — HK Fleet Watch\n**Week of 2026-08-02 | Fleet: A330-300**\n\n## Directly Fleet-Relevant\n"
        out = f.insert_degraded_banner(text, "0 VERIFIED")
        lines = out.split("\n")
        self.assertTrue(lines[1].startswith("**Week of"))
        self.assertEqual(lines[3], "**[DEGRADED — verification pipeline impaired: 0 VERIFIED]**")

    def test_banner_after_h1_when_no_week_line(self):
        out = f.insert_degraded_banner("# Digest\n\nBody\n", "reason")
        self.assertIn("# Digest\n\n**[DEGRADED — verification pipeline impaired: reason]**", out)


class TestHealthOut(unittest.TestCase):
    def test_main_writes_health_json(self):
        with tempfile.TemporaryDirectory() as td:
            digest = os.path.join(td, "d.md")
            preflight = os.path.join(td, "00_preflight.json")
            records = os.path.join(td, "06_deduped.json")
            health = os.path.join(td, "11_health.json")
            with open(digest, "w", encoding="utf-8") as f_handle:
                f_handle.write("# T\n**Week of 2026-08-02 | Fleet: X**\n\n## Directly Fleet-Relevant\n\n"
                        "**Item** — X, 2026-08-01.\n\n**Sources & Confidence:** 1 items — 0 VERIFIED, 0 REPORTED, 1 UNVERIFIED.\n")
            with open(preflight, "w", encoding="utf-8") as f_handle:
                json.dump({"any_ok": False}, f_handle)
            with open(records, "w", encoding="utf-8") as f_handle:
                json.dump({"records": []}, f_handle)
            rc = f.main(["--infile", digest, "--in-place", "--preflight", preflight,
                          "--records", records, "--health-out", health])
            self.assertEqual(rc, 0)
            with open(health, encoding="utf-8") as f_handle:
                data = json.load(f_handle)
            self.assertTrue(data["degraded"])
            with open(digest, encoding="utf-8") as f_handle:
                self.assertIn("[DEGRADED — verification pipeline impaired:", f_handle.read())

    def test_health_json_inputs_true_when_both_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            digest = os.path.join(td, "d.md")
            preflight = os.path.join(td, "00_preflight.json")
            records = os.path.join(td, "06_deduped.json")
            health = os.path.join(td, "11_health.json")
            with open(digest, "w", encoding="utf-8") as f_handle:
                f_handle.write("# T\n**Week of 2026-08-02 | Fleet: X**\n\n## Directly Fleet-Relevant\n\n"
                                "**Item** — X, 2026-08-01.\n")
            with open(preflight, "w", encoding="utf-8") as f_handle:
                json.dump({"any_ok": True}, f_handle)
            with open(records, "w", encoding="utf-8") as f_handle:
                json.dump({"records": []}, f_handle)
            rc = f.main(["--infile", digest, "--in-place", "--preflight", preflight,
                          "--records", records, "--health-out", health])
            self.assertEqual(rc, 0)
            with open(health, encoding="utf-8") as f_handle:
                data = json.load(f_handle)
            self.assertEqual(data["inputs"], {"preflight": True, "records": True})

    def test_unsupplied_paths_produce_no_warning_and_false_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            digest = os.path.join(td, "d.md")
            health = os.path.join(td, "11_health.json")
            with open(digest, "w", encoding="utf-8") as f_handle:
                f_handle.write("# T\n**Week of 2026-08-02 | Fleet: X**\n\n## Directly Fleet-Relevant\n\n"
                                "**Item** — X, 2026-08-01.\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                rc = f.main(["--infile", digest, "--in-place", "--health-out", health])
            self.assertEqual(rc, 0)
            self.assertNotIn("unreadable", stderr.getvalue())
            with open(health, encoding="utf-8") as f_handle:
                data = json.load(f_handle)
            self.assertEqual(data["inputs"], {"preflight": False, "records": False})

    def test_supplied_unreadable_preflight_warns_and_marks_input_false(self):
        with tempfile.TemporaryDirectory() as td:
            digest = os.path.join(td, "d.md")
            bad_preflight = os.path.join(td, "does_not_exist.json")
            health = os.path.join(td, "11_health.json")
            with open(digest, "w", encoding="utf-8") as f_handle:
                f_handle.write("# T\n**Week of 2026-08-02 | Fleet: X**\n\n## Directly Fleet-Relevant\n\n"
                                "**Item** — X, 2026-08-01.\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                rc = f.main(["--infile", digest, "--in-place",
                              "--preflight", bad_preflight, "--health-out", health])
            self.assertEqual(rc, 0)
            self.assertIn("preflight", stderr.getvalue())
            self.assertIn("unreadable", stderr.getvalue())
            with open(health, encoding="utf-8") as f_handle:
                data = json.load(f_handle)
            self.assertEqual(data["inputs"], {"preflight": False, "records": False})


class TestRecallPartial(unittest.TestCase):
    INCOMPLETE = {"coverage": [
        {"regulator": "EASA", "from": "2026-08-01", "to": "2026-08-02",
         "complete": False, "reason": "biweekly 17-2026 not available"},
        {"regulator": "FAA", "from": "2026-08-01", "to": "2026-08-07",
         "complete": True, "reason": None}]}
    COMPLETE = {"coverage": [
        {"regulator": "EASA", "from": "2026-08-01", "to": "2026-08-07",
         "complete": True, "reason": None}]}

    def test_partial_when_a_regulator_is_incomplete(self):
        partial, reason = f.compute_recall_partial(self.INCOMPLETE)
        self.assertTrue(partial)
        self.assertIn("EASA", reason)

    def test_not_partial_when_all_complete(self):
        partial, reason = f.compute_recall_partial(self.COMPLETE)
        self.assertFalse(partial)
        self.assertIsNone(reason)

    def test_missing_sweep_is_not_partial(self):
        self.assertEqual(f.compute_recall_partial(None), (False, None))

    def test_notice_renders_before_the_sources_line(self):
        md = "## Major Industry Events\n\nbody\n\n**Sources & Confidence:** 3 items\n"
        out = f.insert_recall_notice(md, "EASA enumerated only to 2026-08-02")
        self.assertIn("Recall", out)
        self.assertLess(out.index("Recall"), out.index("Sources & Confidence"))

    def test_notice_does_not_claim_degraded(self):
        out = f.insert_recall_notice("**Sources & Confidence:** 1 item\n", "EASA partial")
        self.assertNotIn("DEGRADED", out)

    def test_health_json_carries_recall_keys(self):
        with tempfile.TemporaryDirectory() as d:
            md = os.path.join(d, "digest.md")
            health = os.path.join(d, "health.json")
            with open(md, "w", encoding="utf-8") as fh:
                fh.write("**Sources & Confidence:** 1 item\n")
            f.main(["--infile", md, "--in-place", "--health-out", health])
            with open(health, encoding="utf-8") as fh:
                payload = json.load(fh)
        self.assertIn("recall_partial", payload)
        self.assertIn("recall_reason", payload)
        self.assertFalse(payload["recall_partial"])


class TestUnaccountedRecallGap(unittest.TestCase):
    """Finding 3 (highest value): nothing previously consumed 12_coverage.json
    downstream of Step 4g — the '!! UNACCOUNTED' alarm existed only in the
    Step 6 chat report, which nobody reads on an unattended weekly run. This
    wires it into 11_health.json, the Markdown notice, and (via the health
    payload) the email subject."""

    COVERAGE = {"reported": [], "suppressed": [], "excluded": [], "unaccounted": [
        {"ref_number": "2026-0142", "regulator": "EASA", "subject": "Rib 5",
         "source_url": "https://ad.easa.europa.eu/ad/2026-0142"},
        {"ref_number": "2026-0163", "regulator": "EASA", "subject": "GLU-2100",
         "source_url": "https://ad.easa.europa.eu/ad/2026-0163"},
    ]}

    def test_compute_unaccounted_counts_and_caps_refs(self):
        coverage = {"unaccounted": [{"ref_number": "R%d" % i} for i in range(15)]}
        count, refs = f.compute_unaccounted(coverage)
        self.assertEqual(count, 15)
        self.assertEqual(len(refs), 10)

    def test_compute_unaccounted_zero_when_empty(self):
        self.assertEqual(f.compute_unaccounted({"unaccounted": []}), (0, []))

    def test_compute_unaccounted_zero_when_none(self):
        self.assertEqual(f.compute_unaccounted(None), (0, []))

    def test_notice_names_count_and_refs(self):
        out = f.insert_recall_gap_notice("**Sources & Confidence:** 1 item\n",
                                          2, ["2026-0142", "2026-0163"])
        self.assertIn("2 fleet-matching", out)
        self.assertIn("2026-0142", out)
        self.assertIn("2026-0163", out)

    def test_notice_renders_before_the_sources_line(self):
        out = f.insert_recall_gap_notice(
            "## Major Industry Events\n\nbody\n\n**Sources & Confidence:** 3 items\n",
            1, ["2026-0142"])
        self.assertLess(out.index("Recall gap"), out.index("Sources & Confidence"))

    def test_health_json_carries_unaccounted_fields_and_markdown_gets_notice(self):
        with tempfile.TemporaryDirectory() as d:
            md = os.path.join(d, "digest.md")
            coverage = os.path.join(d, "12_coverage.json")
            health = os.path.join(d, "health.json")
            with open(md, "w", encoding="utf-8") as fh:
                fh.write("**Sources & Confidence:** 1 item\n")
            with open(coverage, "w", encoding="utf-8") as fh:
                json.dump(self.COVERAGE, fh)
            f.main(["--infile", md, "--in-place", "--coverage", coverage, "--health-out", health])
            with open(health, encoding="utf-8") as fh:
                payload = json.load(fh)
            with open(md, encoding="utf-8") as fh:
                out = fh.read()
        self.assertEqual(payload["unaccounted_count"], 2)
        self.assertEqual(payload["unaccounted_refs"], ["2026-0142", "2026-0163"])
        self.assertIn("Recall gap", out)

    def test_zero_unaccounted_no_notice_no_stderr_alarm(self):
        with tempfile.TemporaryDirectory() as d:
            md = os.path.join(d, "digest.md")
            coverage = os.path.join(d, "12_coverage.json")
            health = os.path.join(d, "health.json")
            with open(md, "w", encoding="utf-8") as fh:
                fh.write("**Sources & Confidence:** 1 item\n")
            with open(coverage, "w", encoding="utf-8") as fh:
                json.dump({"unaccounted": []}, fh)
            f.main(["--infile", md, "--in-place", "--coverage", coverage, "--health-out", health])
            with open(health, encoding="utf-8") as fh:
                payload = json.load(fh)
            with open(md, encoding="utf-8") as fh:
                out = fh.read()
        self.assertEqual(payload["unaccounted_count"], 0)
        self.assertNotIn("Recall gap", out)

    def test_missing_coverage_arg_defaults_to_zero(self):
        with tempfile.TemporaryDirectory() as d:
            md = os.path.join(d, "digest.md")
            health = os.path.join(d, "health.json")
            with open(md, "w", encoding="utf-8") as fh:
                fh.write("**Sources & Confidence:** 1 item\n")
            f.main(["--infile", md, "--in-place", "--health-out", health])
            with open(health, encoding="utf-8") as fh:
                payload = json.load(fh)
        self.assertEqual(payload["unaccounted_count"], 0)
        self.assertEqual(payload["unaccounted_refs"], [])


# ----------------------------------------------------------------------------
# Writer-completeness gate. The deterministic HTML renderer builds from the same
# records the Markdown writer receives, so a record the writer silently omits
# makes the two artifacts disagree about what the digest contains. The writer is
# an LLM and cannot be trusted by instruction alone — check it mechanically.
# ----------------------------------------------------------------------------
def _rec(rid, refs=None, headline="Some headline"):
    return {"id": rid, "headline": headline,
            "references": [{"ref_number": n} for n in (refs or [])]}


class TestNoticesAreIdempotent(unittest.TestCase):
    """Re-running the finaliser on an already-finalised digest must REPLACE its
    notices, not stack a second copy. Regenerating a digest after a coverage
    fix is a normal operation and must not corrupt the file."""

    def test_recall_gap_notice_replaced_not_duplicated(self):
        base = "Body text.\n\n**Sources & Confidence:** 7 items\n"
        once = f.insert_recall_gap_notice(base, 5, ["2026-1", "2026-2"])
        twice = f.insert_recall_gap_notice(once, 1, ["2026-9"])
        self.assertEqual(twice.count("**Recall gap:**"), 1)
        self.assertIn("2026-9", twice)
        self.assertNotIn("2026-1", twice)

    def test_recall_partial_notice_replaced_not_duplicated(self):
        base = "Body text.\n\n**Sources & Confidence:** 7 items\n"
        once = f.insert_recall_notice(base, "first reason")
        twice = f.insert_recall_notice(once, "second reason")
        self.assertEqual(twice.count("**Recall note:**"), 1)
        self.assertIn("second reason", twice)
        self.assertNotIn("first reason", twice)

    def test_both_notices_coexist_and_each_stays_single(self):
        base = "Body text.\n\n**Sources & Confidence:** 7 items\n"
        out = f.insert_recall_notice(base, "why")
        out = f.insert_recall_gap_notice(out, 2, ["2026-3"])
        out = f.insert_recall_notice(out, "why again")
        out = f.insert_recall_gap_notice(out, 1, ["2026-4"])
        self.assertEqual(out.count("**Recall note:**"), 1)
        self.assertEqual(out.count("**Recall gap:**"), 1)
        self.assertEqual(out.count("**Sources & Confidence:**"), 1)


class TestWriterDroppedRecords(unittest.TestCase):
    def test_all_records_present_reports_nothing(self):
        md = ("**Alpha event on the wing spar happened today** — A350.\n"
              "**Bravo event on the oil pump happened today** — Trent.")
        count, ids = f.compute_dropped_records(
            md, [_rec("a", [], "Alpha event on the wing spar happened today"),
                 _rec("b", [], "Bravo event on the oil pump happened today")])
        self.assertEqual(count, 0)
        self.assertEqual(ids, [])

    def test_omitted_record_is_detected(self):
        md = "**Alpha event on the wing spar happened today** — A350."
        count, ids = f.compute_dropped_records(
            md, [_rec("a", [], "Alpha event on the wing spar happened today"),
                 _rec("b", [], "Bravo event on the oil pump happened today")])
        self.assertEqual(count, 1)
        self.assertEqual(ids, ["b"])

    def test_note_naming_the_reference_does_not_count_as_rendered(self):
        # Regression: the 2026-08-31 digest omitted two records but carried a note
        # explaining the omission that NAMED both reference numbers. Matching on
        # reference numbers scored them "present" — the exact false negative this
        # gate exists to prevent. Only the headline proves an item was rendered.
        md = ("**Alpha event on the wing spar happened today** — A350.\n\n"
              "*(Two peer-type proposed rules — FAA NPRM 2026-16956 and FAA NPRM "
              "2026-17056 — are excluded here: no placement exists for them.)*")
        count, ids = f.compute_dropped_records(
            md, [_rec("a", [], "Alpha event on the wing spar happened today"),
                 _rec("hpt", ["2026-16956"], "FAA proposes to supersede Trent 7000 HPT blade AD"),
                 _rec("lfcd", ["2026-17056"], "FAA proposes 787-8 forward cargo door inspection")])
        self.assertEqual(count, 2)
        self.assertEqual(ids, ["hpt", "lfcd"])

    def test_trailing_dedup_tag_after_headline_still_counts_as_present(self):
        md = "**Alpha event on the wing spar happened today [UPDATED since 2026-08-16]** — A350."
        count, ids = f.compute_dropped_records(
            md, [_rec("a", [], "Alpha event on the wing spar happened today")])
        self.assertEqual(count, 0)

    def test_whitespace_and_case_differences_tolerated(self):
        md = "**alpha  event on the   wing spar\nhappened today** — A350."
        count, ids = f.compute_dropped_records(
            md, [_rec("a", [], "Alpha event on the wing spar happened today")])
        self.assertEqual(count, 0)

    def test_no_records_supplied_is_not_a_failure(self):
        count, ids = f.compute_dropped_records("anything", [])
        self.assertEqual(count, 0)
        self.assertEqual(ids, [])

    def test_dropped_records_surface_in_health(self):
        with tempfile.TemporaryDirectory() as d:
            md = os.path.join(d, "digest.md")
            recs = os.path.join(d, "records.json")
            health = os.path.join(d, "health.json")
            with open(md, "w", encoding="utf-8") as fh:
                fh.write("**Kept event on the wing spar happened today** — A350.\n\n"
                         "**Sources & Confidence:** 1 item\n")
            with open(recs, "w", encoding="utf-8") as fh:
                json.dump({"records": [
                    _rec("kept", [], "Kept event on the wing spar happened today"),
                    _rec("lost", [], "Lost event on the oil pump happened today")]}, fh)
            f.main(["--infile", md, "--in-place", "--records", recs, "--health-out", health])
            with open(health, encoding="utf-8") as fh:
                payload = json.load(fh)
        self.assertEqual(payload["dropped_count"], 1)
        self.assertEqual(payload["dropped_ids"], ["lost"])


if __name__ == "__main__":
    unittest.main()
