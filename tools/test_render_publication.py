#!/usr/bin/env python3
"""Tests for tools/render_publication.py — the deterministic HTML renderer."""
import unittest

import render_publication as rp


class TestPrimitives(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(rp.html_escape("A & B <x>"), "A &amp; B &lt;x&gt;")
        self.assertEqual(rp.html_escape(None), "")

    def test_chip_verified(self):
        html = rp.chip("VERIFIED")
        self.assertIn("VERIFIED", html)
        self.assertIn('class="tag v"', html)

    def test_conf_inline_proposed_for_nprm(self):
        self.assertEqual(rp.conf_inline("VERIFIED", "NPRM"), "[PROPOSED — not yet final]")
        self.assertEqual(rp.conf_inline("VERIFIED", "AD"), "[VERIFIED — primary source]")

    def test_quote_renders_attribution_and_source(self):
        q = {"text": "loss of control", "doc_title": "AD; Airbus",
             "ref_number": "FAA AD 1", "revision_or_date": "2026-01-01",
             "url": "https://govinfo.gov/x"}
        html = rp.render_quote(q)
        self.assertIn("loss of control", html)
        self.assertIn("FAA AD 1", html)
        self.assertIn('href="https://govinfo.gov/x"', html)


if __name__ == "__main__":
    unittest.main()
