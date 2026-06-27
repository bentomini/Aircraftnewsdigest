"""Tests for finalize_digest.py — deterministic post-render fixups for the digest.

Run: python tools/test_finalize_digest.py
"""
import unittest

import finalize_digest as f


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
