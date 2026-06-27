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


def _record(**kw):
    base = {
        "id": "r1", "headline": "GE90 disk AD", "category": "fleet",
        "types_affected": ["777-300ER"], "event_date": "2026-01-02",
        "item_confidence": "VERIFIED",
        "summary": "Disk replacement required.",
        "root_cause": "Iron inclusion in powder metal.",
        "oem_regulator_position": None,
        "references": [{
            "ref_type": "AD", "ref_number": "FAA AD 2025-25-07",
            "confidence": "VERIFIED", "effective_date": "2026-02-06",
            "primary_source_url": "https://www.govinfo.gov/x.htm",
        }],
        "quotes": [], "read_across": None,
    }
    base.update(kw)
    return base


class TestImageBlock(unittest.TestCase):
    def test_embed_renders_img_with_data_uri(self):
        d = {"embed": True, "data_uri": "data:image/jpeg;base64,QQ==",
             "alt_text": "lug", "caption": "A lug", "source_label": "NTSB"}
        cell, attr = rp.render_image(d)
        self.assertIn("data:image/jpeg;base64,QQ==", cell)
        self.assertEqual(attr, "")

    def test_link_only_renders_attribution_no_img(self):
        d = {"embed": False, "caption": "aircraft", "source_label": "Avherald",
             "link_url": "https://avherald.com/p"}
        cell, attr = rp.render_image(d)
        self.assertEqual(cell, "")
        self.assertIn("Avherald", attr)
        self.assertIn("https://avherald.com/p", attr)
        self.assertNotIn("data:image", attr)

    def test_none_directive_empty(self):
        self.assertEqual(rp.render_image(None), ("", ""))


class TestItem(unittest.TestCase):
    def test_item_has_headline_chip_source_link(self):
        html = rp.render_item(_record(), None)
        self.assertIn("GE90 disk AD", html)
        self.assertIn('class="tag v"', html)
        self.assertIn('href="https://www.govinfo.gov/x.htm"', html)
        self.assertIn("[VERIFIED — primary source]", html)

    def test_carryover_labelled(self):
        html = rp.render_item(_record(developing_carryover=True), None)
        self.assertIn("carried-over developing event", html)

    def test_embedded_image_item_uses_table(self):
        d = {"embed": True, "data_uri": "data:image/jpeg;base64,QQ==",
             "alt_text": "x", "caption": "c", "source_label": "NTSB"}
        html = rp.render_item(_record(), d)
        self.assertIn("<table", html)
        self.assertIn("data:image/jpeg;base64,QQ==", html)

    def test_dedup_updated_tag(self):
        rec = _record(dedup={"status": "updated", "previously_reported": "2026-06-01"})
        html = rp.render_item(rec, None)
        self.assertIn("[UPDATED since 2026-06-01]", html)


if __name__ == "__main__":
    unittest.main()
