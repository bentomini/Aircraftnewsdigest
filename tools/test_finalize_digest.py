"""Tests for finalize_digest.py — deterministic post-render fixups for the digest.

Run: python tools/test_finalize_digest.py
"""
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


if __name__ == "__main__":
    unittest.main()
