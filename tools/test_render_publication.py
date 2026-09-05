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

    def test_within_window_false_labels_carryover(self):
        html = rp.render_item(_record(within_window=False), None)
        self.assertIn("carried-over developing event", html)


class TestGrouping(unittest.TestCase):
    def test_all_nprm_record_is_horizon(self):
        rec = _record(references=[{"ref_type": "NPRM", "ref_number": "X",
                                   "confidence": "VERIFIED"}])
        self.assertTrue(rp.is_horizon(rec))

    def test_peer_type_nprm_is_not_horizon(self):
        """On the Horizon is fleet-scoped: a peer-type NPRM stays in Read-Across."""
        rec = _record(category="read_across",
                      references=[{"ref_type": "NPRM", "ref_number": "X",
                                   "confidence": "VERIFIED"}])
        self.assertFalse(rp.is_horizon(rec))

    def test_industry_nprm_is_not_horizon(self):
        rec = _record(category="industry",
                      references=[{"ref_type": "PAD", "ref_number": "X",
                                   "confidence": "VERIFIED"}])
        self.assertFalse(rp.is_horizon(rec))

    def test_mixed_refs_not_horizon(self):
        rec = _record(references=[{"ref_type": "NPRM", "ref_number": "X", "confidence": "VERIFIED"},
                                  {"ref_type": "AD", "ref_number": "Y", "confidence": "VERIFIED"}])
        self.assertFalse(rp.is_horizon(rec))

    def test_order_verified_before_reported_then_newest(self):
        a = _record(id="a", item_confidence="REPORTED", event_date="2026-06-20")
        b = _record(id="b", item_confidence="VERIFIED", event_date="2026-06-01")
        c = _record(id="c", item_confidence="VERIFIED", event_date="2026-06-10")
        ordered = [r["id"] for r in rp.order_records([a, b, c])]
        self.assertEqual(ordered, ["c", "b", "a"])

    def test_read_across_suppressed_when_fleet_threshold_met(self):
        by_cat = {"fleet": [1, 2, 3, 4, 5], "read_across": [9], "industry": []}
        rp.suppress_read_across(by_cat, 5)
        self.assertEqual(by_cat["read_across"], [])

    def test_read_across_kept_below_threshold(self):
        by_cat = {"fleet": [1, 2], "read_across": [9], "industry": []}
        rp.suppress_read_across(by_cat, 5)
        self.assertEqual(by_cat["read_across"], [9])


class TestConfigAndWatch(unittest.TestCase):
    YAML = """
operator:
  name: Cathay Pacific
fleet:
  - type: A330-300
  - type: 777-300ER
standing_watch:
  read_across_min_fleet_items: 5
"""

    def test_parse_operator_and_fleet(self):
        self.assertEqual(rp.parse_operator_name(self.YAML), "Cathay Pacific")
        self.assertEqual(rp.parse_fleet_types(self.YAML), ["A330-300", "777-300ER"])

    def test_parse_min_fleet_default(self):
        self.assertEqual(rp.parse_scalar(self.YAML, "read_across_min_fleet_items", 5), 5)
        self.assertEqual(rp.parse_scalar("", "read_across_min_fleet_items", 5), 5)

    def test_radar_block_renders_entries(self):
        radar = [{"ref_number": "FAA AD 1", "effective_date": "2026-07-30",
                  "headline": "Trent blade", "url": "https://govinfo.gov/y"}]
        html = rp.render_radar(radar)
        self.assertIn("FAA AD 1", html)
        self.assertIn("2026-07-30", html)
        self.assertIn("https://govinfo.gov/y", html)

    def test_radar_empty_is_blank(self):
        self.assertEqual(rp.render_radar([]), "")

    def test_sources_line_counts(self):
        recs = [_record(item_confidence="VERIFIED"),
                _record(item_confidence="REPORTED"),
                _record(item_confidence="VERIFIED")]
        line = rp.render_sources_line(recs)
        self.assertIn("3 items", line)
        self.assertIn("2 VERIFIED", line)
        self.assertIn("1 REPORTED", line)

    def test_corner_none_returns_empty(self):
        self.assertEqual(rp.render_corner(None), "")

    def test_corner_renders_title_and_body(self):
        html = rp.render_corner({"title": "Why FCRM matters", "body": "Some text."})
        self.assertIn("Why FCRM matters", html)
        self.assertIn("Some text.", html)


class TestDocument(unittest.TestCase):
    BUNDLE = {
        "records": [
            _record(id="f1", category="fleet", headline="GE90 AD"),
            _record(id="ra1", category="read_across", headline="Trent 1000 AD",
                    read_across="Trent family read-across."),
            _record(id="ind1", category="industry", headline="UPS MD-11 hearing"),
            _record(id="h1", category="fleet", headline="777 MLG NPRM",
                    references=[{"ref_type": "NPRM", "ref_number": "FAA-1",
                                 "confidence": "VERIFIED",
                                 "primary_source_url": "https://govinfo.gov/n"}]),
        ],
        "radar": [{"ref_number": "FAA AD 9", "effective_date": "2026-07-30",
                   "headline": "blade", "url": "https://govinfo.gov/y"}],
        "corner": None,
        "images": {"ind1": {"embed": False, "caption": "wreck",
                            "source_label": "Avherald", "link_url": "https://avherald.com/p"}},
        "operator": "Cathay Pacific",
        "fleet_types": ["A330-300", "777-300ER"],
        "min_fleet_items": 5,
        "date_label": "Week of 2026-06-27",
    }

    def test_document_has_sections_and_masthead(self):
        html = rp.render_document(self.BUNDLE)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Cathay Pacific", html)
        self.assertIn("Directly Fleet-Relevant", html)
        self.assertIn("Read-Across", html)
        self.assertIn("Major Industry Events", html)
        self.assertIn("Standing Watch", html)
        self.assertIn("On the Horizon", html)

    def test_horizon_record_not_in_core_sections(self):
        html = rp.render_document(self.BUNDLE)
        # The NPRM headline appears once, under On the Horizon, tagged PROPOSED.
        self.assertIn("777 MLG NPRM", html)
        self.assertIn("[PROPOSED — not yet final]", html)
        self.assertEqual(html.count("777 MLG NPRM"), 1)

    def test_sources_line_counts_standing_watch_items_too(self):
        """The tally must cover every rendered record, matching the Markdown digest."""
        html = rp.render_document(self.BUNDLE)
        self.assertIn("4 items", html)
        self.assertIn("4 VERIFIED", html)

    def test_illustrative_image_is_link_not_embedded(self):
        html = rp.render_document(self.BUNDLE)
        self.assertIn("avherald.com/p", html)
        self.assertNotIn("data:image", html)

    def test_no_unknown_reference_smuggled(self):
        # Verified-content guarantee: a ref number not in the bundle never appears.
        html = rp.render_document(self.BUNDLE)
        self.assertNotIn("FAA AD 2099", html)

    def test_read_across_suppressed_when_min_fleet_items_met(self):
        bundle = {
            "records": [
                _record(id="f1", category="fleet", headline="Fleet Item 1"),
                _record(id="f2", category="fleet", headline="Fleet Item 2"),
                _record(id="ra1", category="read_across", headline="Peer Type Event",
                        read_across="Peer type."),
            ],
            "radar": [],
            "corner": None,
            "images": {},
            "operator": "Cathay Pacific",
            "fleet_types": ["A330-300"],
            "min_fleet_items": 2,
            "date_label": "Week of 2026-06-27",
        }
        html = rp.render_document(bundle)
        self.assertNotIn("Read-Across (Peer Types)", html)
        self.assertNotIn("Peer Type Event", html)


class TestReportingLinks(unittest.TestCase):
    def _rec(self, refs, lead=None):
        return {"id": "x", "headline": "H", "category": "fleet", "types_affected": ["A330-300"],
                "summary": "S.", "references": refs, "item_confidence": "REPORTED",
                "lead_sources": [lead] if lead else []}

    def test_unverified_ref_without_url_gets_reporting_link(self):
        rec = self._rec([{"ref_type": "AD", "ref_number": "X-1", "confidence": "UNVERIFIED",
                          "primary_source_url": None}], lead="https://avherald.com/x")
        html = rp.render_item(rec, None)
        self.assertIn('class="rep"', html)
        self.assertIn("[reporting]", html)
        self.assertIn("https://avherald.com/x", html)

    def test_ref_with_primary_url_prefers_source_link(self):
        rec = self._rec([{"ref_type": "AD", "ref_number": "X-1", "confidence": "VERIFIED",
                          "primary_source_url": "https://www.govinfo.gov/x"}],
                        lead="https://avherald.com/x")
        html = rp.render_item(rec, None)
        self.assertIn("[source]", html)
        self.assertNotIn("[reporting]", html)

    def test_referenceless_reported_item_gets_reporting_after_summary(self):
        rec = self._rec([], lead="https://avherald.com/y")
        html = rp.render_item(rec, None)
        self.assertIn("[reporting]", html)

    def test_no_lead_no_reporting_link(self):
        rec = self._rec([], lead=None)
        self.assertNotIn("[reporting]", rp.render_item(rec, None))


class TestDegradedBanner(unittest.TestCase):
    def test_banner_rendered_when_degraded(self):
        html = rp.render_document({"records": [], "health": {"degraded": True, "reason": "0 VERIFIED"}})
        self.assertIn('class="degraded"', html)
        self.assertIn("0 VERIFIED", html)

    def test_no_banner_when_healthy(self):
        html = rp.render_document({"records": [], "health": {"degraded": False, "reason": None}})
        self.assertNotIn('class="degraded"', html)

    def test_no_banner_when_health_absent(self):
        self.assertNotIn('class="degraded"', rp.render_document({"records": []}))


class TestRecallNotice(unittest.TestCase):
    def test_html_shows_recall_notice_when_partial(self):
        bundle = dict(TestDocument.BUNDLE,
                      health={"degraded": False, "recall_partial": True,
                              "recall_reason": "EASA enumerated only to 2026-08-02"})
        html = rp.render_document(bundle)
        self.assertIn("Recall note", html)
        self.assertIn("2026-08-02", html)

    def test_no_recall_notice_when_complete(self):
        bundle = dict(TestDocument.BUNDLE, health={"degraded": False, "recall_partial": False})
        self.assertNotIn("Recall note", rp.render_document(bundle))


class TestRecallGapNotice(unittest.TestCase):
    """Finding 3: the coverage ledger's unaccounted alarm never reached the
    HTML publication — this renders it beside the existing recall notice."""

    def test_html_shows_recall_gap_notice_when_unaccounted(self):
        bundle = dict(TestDocument.BUNDLE,
                      health={"degraded": False, "unaccounted_count": 2,
                              "unaccounted_refs": ["2026-0142", "2026-0163"]})
        html = rp.render_document(bundle)
        self.assertIn("Recall gap", html)
        self.assertIn("2026-0142", html)
        self.assertIn("2026-0163", html)

    def test_no_recall_gap_notice_when_zero(self):
        bundle = dict(TestDocument.BUNDLE, health={"degraded": False, "unaccounted_count": 0})
        self.assertNotIn("Recall gap", rp.render_document(bundle))

    def test_no_recall_gap_notice_when_health_absent(self):
        self.assertNotIn("Recall gap", rp.render_document({"records": []}))

    def test_recall_gap_and_recall_partial_can_coexist(self):
        bundle = dict(TestDocument.BUNDLE,
                      health={"degraded": False, "recall_partial": True,
                              "recall_reason": "EASA enumerated only to 2026-08-02",
                              "unaccounted_count": 1, "unaccounted_refs": ["2026-0163"]})
        html = rp.render_document(bundle)
        self.assertIn("Recall note", html)
        self.assertIn("Recall gap", html)


if __name__ == "__main__":
    unittest.main()
