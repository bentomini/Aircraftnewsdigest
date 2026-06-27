#!/usr/bin/env python3
"""Tests for tools/fetch_images.py — the deterministic image gate."""
import unittest

import fetch_images as fi


class TestConfigParser(unittest.TestCase):
    YAML = """
quotes:
  max_words: 25

imagery:
  enabled: true
  embed_allowlist:
    - ntsb.gov
    - faa.gov
    - govinfo.gov
    - easa.europa.eu
  max_width_px: 480
  max_bytes: 5000000
  download_timeout_s: 15

verified_domains:
  - ntsb.gov
"""

    def test_parses_all_fields(self):
        cfg = fi.parse_imagery_config(self.YAML)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["embed_allowlist"],
                         ["ntsb.gov", "faa.gov", "govinfo.gov", "easa.europa.eu"])
        self.assertEqual(cfg["max_width_px"], 480)
        self.assertEqual(cfg["max_bytes"], 5000000)
        self.assertEqual(cfg["download_timeout_s"], 15)

    def test_missing_block_uses_defaults(self):
        cfg = fi.parse_imagery_config("verified_domains:\n  - ntsb.gov\n")
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["embed_allowlist"], [])

    def test_enabled_false_parsed(self):
        cfg = fi.parse_imagery_config("imagery:\n  enabled: false\n")
        self.assertFalse(cfg["enabled"])


class TestDomain(unittest.TestCase):
    def test_subdomain_matches(self):
        self.assertTrue(fi.domain_in_allowlist("ad.easa.europa.eu", ["easa.europa.eu"]))

    def test_lookalike_rejected(self):
        self.assertFalse(fi.domain_in_allowlist("ntsb.gov.evil.com", ["ntsb.gov"]))


if __name__ == "__main__":
    unittest.main()
