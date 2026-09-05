#!/usr/bin/env python3
"""Tests for tools/easa_biweekly.py — EASA biweekly AD listing enumeration."""
import os
import unittest
import zlib
from datetime import date

import easa_biweekly as eb

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "biweekly_16_2026.txt")


class TestPeriodMath(unittest.TestCase):
    def test_known_anchor_issue_06_2026(self):
        issue, year, start, end = eb.biweekly_period(date(2026, 3, 2))
        self.assertEqual((issue, year), (6, 2026))
        self.assertEqual(start, date(2026, 3, 2))
        self.assertEqual(end, date(2026, 3, 15))

    def test_known_issue_16_2026(self):
        issue, year, start, end = eb.biweekly_period(date(2026, 7, 20))
        self.assertEqual((issue, year), (16, 2026))
        self.assertEqual(start, date(2026, 7, 20))
        self.assertEqual(end, date(2026, 8, 2))

    def test_date_inside_period_maps_to_that_period(self):
        issue, year, start, end = eb.biweekly_period(date(2026, 7, 28))
        self.assertEqual((issue, year), (16, 2026))
        self.assertEqual(start, date(2026, 7, 20))

    def test_period_after_16_is_17(self):
        issue, year, start, end = eb.biweekly_period(date(2026, 8, 3))
        self.assertEqual((issue, year), (17, 2026))
        self.assertEqual(start, date(2026, 8, 3))
        self.assertEqual(end, date(2026, 8, 16))


class TestYearBoundary(unittest.TestCase):
    """Test year boundary transitions and multi-year fallback branches.

    These anchors were verified by live fetch of EASA biweekly PDFs on 2026-08-16.
    """
    def test_last_period_of_2025(self):
        issue, year, start, end = eb.biweekly_period(date(2025, 12, 15))
        self.assertEqual((issue, year), (26, 2025))
        self.assertEqual(start, date(2025, 12, 8))
        self.assertEqual(end, date(2025, 12, 21))

    def test_first_period_of_2026_from_december_date(self):
        """December 2025 date belonging to AD-year 2026 (exercises d.year + 1 branch)."""
        issue, year, start, end = eb.biweekly_period(date(2025, 12, 29))
        self.assertEqual((issue, year), (1, 2026))
        self.assertEqual(start, date(2025, 12, 22))
        self.assertEqual(end, date(2026, 1, 4))

    def test_mid_year_2025_anchor(self):
        issue, year, start, end = eb.biweekly_period(date(2025, 7, 21))
        self.assertEqual((issue, year), (16, 2025))
        self.assertEqual(start, date(2025, 7, 21))
        self.assertEqual(end, date(2025, 8, 3))

    def test_ad_year_boundary_contiguity(self):
        """Issue 26-2025 ends 2025-12-21; issue 01-2026 starts 2025-12-22 (contiguous)."""
        issue_26_2025, year_26, start_26, end_26 = eb.biweekly_period(date(2025, 12, 15))
        issue_01_2026, year_01, start_01, end_01 = eb.biweekly_period(date(2025, 12, 29))

        self.assertEqual((issue_26_2025, year_26), (26, 2025))
        self.assertEqual((issue_01_2026, year_01), (1, 2026))
        self.assertEqual(end_26, date(2025, 12, 21))
        self.assertEqual(start_01, date(2025, 12, 22))
        # Verify contiguity
        from datetime import timedelta
        self.assertEqual(start_01, end_26 + timedelta(days=1))


class TestUrl(unittest.TestCase):
    def test_url_matches_known_good_issue_16(self):
        url = eb.biweekly_url(16, 2026, date(2026, 7, 20), date(2026, 8, 2))
        self.assertEqual(
            url,
            "https://ad.easa.europa.eu/blob/easa_biweekly_2026-07-20_2026-08-02_16-2026.pdf/biweekly")

    def test_issue_number_is_zero_padded(self):
        url = eb.biweekly_url(6, 2026, date(2026, 3, 2), date(2026, 3, 15))
        self.assertIn("_06-2026.pdf", url)


def _fake_pdf(*lines):
    """A minimal PDF carrying `lines` inside one FlateDecode content stream."""
    body = " ".join("BT (%s) Tj ET" % ln for ln in lines).encode("latin-1")
    return b"%PDF-1.4\n1 0 obj\n<< /Length 1 >>\nstream\n" + zlib.compress(body) + b"\nendstream\nendobj\n"


class TestPdfText(unittest.TestCase):
    def test_extracts_text_from_flate_stream(self):
        raw = _fake_pdf("AD 2026-0142", "Main Landing Gear Support Rib 5")
        text = eb.extract_pdf_text(raw)
        self.assertIn("2026-0142", text)
        self.assertIn("Rib 5", text)

    def test_collapses_whitespace(self):
        raw = _fake_pdf("A    B")
        self.assertIn("A B", eb.extract_pdf_text(raw))

    def test_uncompressed_content_stream_is_used_as_is(self):
        """Regression: real EASA biweekly PDFs (verified 2026-08-16 live fetch)
        ship literal, uncompressed content streams (no /Filter). A stream that
        fails to zlib-inflate must be used as-is, not dropped."""
        raw = (b"%PDF-1.3\n1 0 obj\n<< /Length 40 >>\nstream\n"
               b"BT (2026-0144) Tj ET BT (Rudder Outer Skin) Tj ET\n"
               b"endstream\nendobj\n")
        text = eb.extract_pdf_text(raw)
        self.assertIn("2026-0144", text)
        self.assertIn("Rudder Outer Skin", text)

    def test_undecodable_bytes_yield_empty_string(self):
        self.assertEqual(eb.extract_pdf_text(b"not a pdf at all"), "")

    def test_flate_declared_but_corrupt_stream_is_skipped_not_treated_as_text(self):
        """Regression (fix round 1): a stream whose dict declares /FlateDecode
        (or any other filter) is binary-intended. If it fails to inflate it
        must be skipped, never appended as literal text — that path is only
        for streams whose dict declares no /Filter at all (e.g. a real EASA
        embedded JPEG declares /Filter /DCTDecode; treating its raw bytes as
        text pollutes the extracted output with garbage)."""
        raw = (b"%PDF-1.3\n1 0 obj\n<< /Filter /FlateDecode /Length 7 >>\nstream\n"
               b"NOTZLIB\nendstream\nendobj\n")
        self.assertEqual(eb.extract_pdf_text(raw), "")


class TestEscapeDecoding(unittest.TestCase):
    """Test single-pass escape decoding to prevent corruption from chained replacements."""

    def test_regression_backslash_n_sequence_not_corrupted(self):
        """Regression: \\next must extract with 'next' intact, not corrupted to ' ext'."""
        raw = _fake_pdf("\\\\next")
        text = eb.extract_pdf_text(raw)
        # Buggy code would produce ' ext' (space from escaped \n), losing "next"
        self.assertIn("next", text)
        self.assertNotIn(" ext", text)

    def test_escaped_parenthesis_left(self):
        """Escaped opening parenthesis \\( decodes to literal (."""
        raw = _fake_pdf("\\(value)")
        text = eb.extract_pdf_text(raw)
        self.assertIn("(value", text)

    def test_escaped_parenthesis_right(self):
        """Escaped closing parenthesis \\) decodes to literal )."""
        raw = _fake_pdf("value\\)")
        text = eb.extract_pdf_text(raw)
        self.assertIn("value)", text)

    def test_escaped_newline_becomes_space(self):
        """Escaped newline \\n decodes to a space."""
        raw = _fake_pdf("A\\nB")
        text = eb.extract_pdf_text(raw)
        self.assertIn("A B", text)

    def test_unrecognized_escape_yields_literal(self):
        """Unrecognized escape \\q yields literal q."""
        raw = _fake_pdf("\\qvalue")
        text = eb.extract_pdf_text(raw)
        self.assertIn("qvalue", text)


class TestParseBiweekly(unittest.TestCase):
    def _fixture(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            return fh.read()

    def test_finds_the_rib5_ad_that_was_missed(self):
        """The regression this whole feature exists for."""
        ads = eb.parse_biweekly(self._fixture())
        numbers = [a["ref_number"] for a in ads]
        self.assertIn("2026-0142", numbers)

    def test_finds_the_rudder_skin_ad(self):
        ads = eb.parse_biweekly(self._fixture())
        self.assertIn("2026-0144", [a["ref_number"] for a in ads])

    def test_captures_emergency_suffix_verbatim(self):
        ads = eb.parse_biweekly("AD 2026-0199-E Fire Protection Inspection A320")
        self.assertEqual(ads[0]["ref_number"], "2026-0199-E")

    def test_captures_revision_suffix_verbatim(self):
        ads = eb.parse_biweekly("AD 2025-0274R1 Fire Panel Inspection A321")
        self.assertEqual(ads[0]["ref_number"], "2025-0274R1")

    def test_types_hint_picks_up_airbus_models(self):
        ads = eb.parse_biweekly("AD 2026-0142 Wings Rib 5 Inspections A318, A319, A320 and A321 aeroplanes")
        self.assertIn("A321", ads[0]["types_hint"])

    def test_deduplicates_repeated_numbers(self):
        text = "AD 2026-0142 Wings A320 AD 2026-0142 Wings A320"
        self.assertEqual(len(eb.parse_biweekly(text)), 1)

    def test_types_hint_from_real_fixture_includes_a321_for_rib5_ad(self):
        """Regression (fix round 1, Critical): real fixture text glues the
        type list directly onto the next column ("...A320, A321Wings - Main
        Landing Gear..."), so a trailing \\b previously dropped A321 — the
        tracked fleet type on the AD this feature exists to catch."""
        ads = eb.parse_biweekly(self._fixture())
        by_ref = {a["ref_number"]: a for a in ads}
        self.assertIn("A321", by_ref["2026-0142"]["types_hint"])

    def test_real_fixture_never_fabricates_glued_revision_number(self):
        """Regression (fix round 1, Important): real fixture text glues a
        revision suffix directly onto the next column's date ("...Inspection
        2023-0148R12026-07-24AIRBUS..."). The parser must never fabricate
        "2023-0148R12" — it must fall back to the real base number instead."""
        numbers = [a["ref_number"] for a in eb.parse_biweekly(self._fixture())]
        self.assertNotIn("2023-0148R12", numbers)
        self.assertIn("2023-0148", numbers)

    def test_real_fixture_does_not_capture_g_prefixed_ad_as_bare_number(self):
        """Regression (fix round 1, Minor elevated; narrowed in fix round 2):
        the fixture contains an appliance AD "G-2026-0003". The lookbehind
        rejects the specific shape "<letter>-" immediately before the match
        (round 2: a wider "any alphanumeric" lookbehind wrongly dropped real
        ADs glued to preceding text, so it was narrowed to just this shape),
        so this doesn't get truncated into a fabricated standalone
        "2026-0003"."""
        numbers = [a["ref_number"] for a in eb.parse_biweekly(self._fixture())]
        self.assertNotIn("2026-0003", numbers)

    def test_real_fixture_keeps_ad_glued_to_preceding_letters_at_page_break(self):
        """Regression (fix round 2, Important): real fixture text glues a bare
        AD number onto arbitrary preceding text at a page break
        ("...TypeSubject2026-01452026-07-23DASSAULT..."). A lookbehind that
        excludes any preceding alphanumeric (not just the "<letter>-" shape of
        an alpha-prefixed identifier) wrongly drops this real AD."""
        numbers = [a["ref_number"] for a in eb.parse_biweekly(self._fixture())]
        self.assertIn("2026-0145", numbers)

    def test_real_fixture_keeps_ad_glued_to_preceding_digits(self):
        """Regression (fix round 2, Important): real fixture text glues a bare
        AD number onto the preceding column's year digits
        ("...Issued on 03/08/20262026-0153..."). A trailing digit before the
        match must not exclude it — only the "<letter>-" shape does."""
        numbers = [a["ref_number"] for a in eb.parse_biweekly(self._fixture())]
        self.assertIn("2026-0153", numbers)

    def test_real_fixture_yields_exact_expected_ref_number_set(self):
        """Set-equality regression (fix round 2): a membership spot-check
        cannot catch the parser silently dropping or inventing an entry
        elsewhere in the fixture. This asserts the full expected set, built
        from a manual census of every \\d{4}-\\d{4} candidate in the fixture
        (see task-3-report.md fix-round-2 completeness audit): 17 bare
        4-4-digit substrings total, of which 1 (the "G-2026-0003" appliance
        AD's numeric tail) is an artifact and must be excluded."""
        numbers = {a["ref_number"] for a in eb.parse_biweekly(self._fixture())}
        expected = {
            "2023-0148",
            "2026-0142", "2026-0143", "2026-0144", "2026-0145", "2026-0146",
            "2026-0147", "2026-0148", "2026-0149", "2026-0151", "2026-0152",
            "2026-0153", "2026-0154", "2026-0155", "2026-0156", "2026-0157",
        }
        self.assertEqual(numbers, expected)

    def test_all_real_fixture_ref_numbers_match_expected_shape(self):
        for a in eb.parse_biweekly(self._fixture()):
            self.assertRegex(a["ref_number"], r"^\d{4}-\d{4}(R\d+)?(-E)?$")

    def test_real_fixture_has_no_duplicate_ref_numbers(self):
        numbers = [a["ref_number"] for a in eb.parse_biweekly(self._fixture())]
        self.assertEqual(len(numbers), len(set(numbers)))


class TestManufacturerHint(unittest.TestCase):
    def _fixture(self):
        import os
        p = os.path.join(os.path.dirname(__file__), "fixtures", "biweekly_16_2026.txt")
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def test_airbus_ad_carries_airbus_manufacturer_hint(self):
        ads = {a["ref_number"]: a for a in eb.parse_biweekly(self._fixture())}
        self.assertIn("AIRBUS", ads["2026-0142"]["manufacturer_hint"].upper())

    def test_non_airbus_ad_carries_its_own_manufacturer(self):
        ads = {a["ref_number"]: a for a in eb.parse_biweekly(self._fixture())}
        hints = " ".join(a["manufacturer_hint"].upper() for a in ads.values())
        self.assertTrue(any(m in hints for m in ("DIAMOND", "DASSAULT", "SAAB", "PIPISTREL")))

    def test_every_entry_has_the_key_even_when_empty(self):
        for ad in eb.parse_biweekly("AD 2026-0142 something"):
            self.assertIn("manufacturer_hint", ad)


class TestManufacturerHintWindowOverrun(unittest.TestCase):
    """Regression (fix round 3, found on real-data acceptance run): a fixed
    60-char span can retain a leftover revision marker glued to the front of
    the window (see test_real_fixture_never_fabricates_glued_revision_number),
    and can bleed into the next row's manufacturer when the current row's
    name is short. Both must be handled so the hint names only the AD's own
    manufacturer."""

    def test_real_fixture_revision_marker_does_not_corrupt_manufacturer_hint(self):
        """Real fixture: '2023-0148' is glued to a revision marker the
        AD-number regex declines to attach, leaving
        'R12026-07-24AIRBUS HELICOPTERS...' as the raw window. The hint must
        start with the real manufacturer name, not leftover 'R1' + date
        digits."""
        with open(FIXTURE, encoding="utf-8") as fh:
            text = fh.read()
        by_ref = {a["ref_number"]: a for a in eb.parse_biweekly(text)}
        hint = by_ref["2023-0148"]["manufacturer_hint"]
        self.assertTrue(hint.upper().startswith("AIRBUS"), hint)

    def test_manufacturer_hint_truncates_at_next_row_reference_number(self):
        """A short manufacturer name lets the fixed span bleed into the next
        AD's manufacturer unless cut at the next row's reference number."""
        window = "2026-07-13SAAB AB 2026-0999AIRBUS S.A.S.A320"
        hint = eb._manufacturer_hint(window)
        self.assertIn("SAAB", hint.upper())
        self.assertNotIn("AIRBUS", hint.upper())

    def test_manufacturer_hint_strips_leading_revision_marker(self):
        """A revision marker (R1) glued to the front of the window,
        immediately before the date, must not become part of the hint."""
        window = "R12026-07-08AIRBUS HELICOPTERSAS 350 / EC 130"
        hint = eb._manufacturer_hint(window)
        self.assertFalse(hint.startswith("R1"), hint)
        self.assertTrue(hint.upper().startswith("AIRBUS"), hint)


class TestIssueDate(unittest.TestCase):
    """Regression (Finding 6): parse_biweekly discarded the issue date even
    though it sits immediately after the ref number (it is exactly what
    _manufacturer_hint strips past). sweep_to_lead then had no event_date to
    populate, so the deterministic window gate was inert for every swept EASA
    lead."""

    def _fixture(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            return fh.read()

    def test_plain_entry_carries_its_issue_date(self):
        ads = {a["ref_number"]: a for a in eb.parse_biweekly(self._fixture())}
        self.assertEqual(ads["2026-0142"]["issue_date"], "2026-07-20")

    def test_revision_prefixed_entry_carries_its_issue_date(self):
        """'2023-0148' is glued to a revision marker the ref-number regex
        declines to attach ('...2023-0148R12026-07-24AIRBUS...'); the date
        must still be recovered, not swallowed by the leftover R1."""
        ads = {a["ref_number"]: a for a in eb.parse_biweekly(self._fixture())}
        self.assertEqual(ads["2023-0148"]["issue_date"], "2026-07-24")

    def test_unparseable_leading_text_yields_none_not_a_fabricated_date(self):
        ads = eb.parse_biweekly("AD 2026-0142 no date follows this at all")
        self.assertIsNone(ads[0]["issue_date"])

    def test_every_entry_has_the_key(self):
        for ad in eb.parse_biweekly("AD 2026-0142 something"):
            self.assertIn("issue_date", ad)


class TestEnumerateEasa(unittest.TestCase):
    def test_complete_when_every_period_fetches(self):
        def ok(url):
            return _fake_pdf("AD 2026-0142 Wings Rib 5 A321")
        ads, cov = eb.enumerate_easa(date(2026, 7, 20), date(2026, 8, 2), ok)
        self.assertEqual([a["ref_number"] for a in ads], ["2026-0142"])
        self.assertTrue(cov["complete"])
        self.assertEqual(cov["regulator"], "EASA")
        self.assertIsNone(cov["reason"])

    def test_unpublished_current_period_marks_coverage_incomplete(self):
        def missing(url):
            raise OSError("HTTP Error 404: Not Found")
        ads, cov = eb.enumerate_easa(date(2026, 8, 3), date(2026, 8, 7), missing)
        self.assertEqual(ads, [])
        self.assertFalse(cov["complete"])
        self.assertIn("17-2026", cov["reason"])

    def test_partial_window_reports_the_covered_end_date(self):
        """Window spans two periods; only the earlier one is published."""
        def only_16(url):
            if "16-2026" in url:
                return _fake_pdf("AD 2026-0142 Wings Rib 5 A321")
            raise OSError("HTTP Error 404: Not Found")
        ads, cov = eb.enumerate_easa(date(2026, 8, 1), date(2026, 8, 7), only_16)
        self.assertEqual([a["ref_number"] for a in ads], ["2026-0142"])
        self.assertFalse(cov["complete"])
        self.assertEqual(cov["to"], "2026-08-02")

    def test_soft_404_html_response_marks_coverage_incomplete(self):
        """HTTP 200 with an HTML interstitial doesn't raise, but isn't a PDF."""
        def html(url):
            return b"<html>Service unavailable</html>"
        ads, cov = eb.enumerate_easa(date(2026, 7, 20), date(2026, 8, 2), html)
        self.assertEqual(ads, [])
        self.assertFalse(cov["complete"])
        self.assertIn("16-2026", cov["reason"])

    def test_empty_response_marks_coverage_incomplete(self):
        def empty(url):
            return b""
        ads, cov = eb.enumerate_easa(date(2026, 7, 20), date(2026, 8, 2), empty)
        self.assertEqual(ads, [])
        self.assertFalse(cov["complete"])
        self.assertIn("16-2026", cov["reason"])

    def test_quiet_period_valid_pdf_zero_ads_is_still_complete(self):
        """A real PDF with no AD numbers in it is a genuinely quiet fortnight,
        not a coverage failure — must not be flagged incomplete."""
        def quiet(url):
            return _fake_pdf("No advisory activity this period")
        ads, cov = eb.enumerate_easa(date(2026, 7, 20), date(2026, 8, 2), quiet)
        self.assertEqual(ads, [])
        self.assertTrue(cov["complete"])


if __name__ == "__main__":
    unittest.main()
