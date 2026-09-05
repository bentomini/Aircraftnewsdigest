#!/usr/bin/env python3
"""Tests for tools/preflight.py — primary-source reachability probe."""
import json
import unittest

import preflight as pf


class TestEvaluate(unittest.TestCase):
    def test_200_direct_is_ok(self):
        self.assertTrue(pf.evaluate(200, "https://www.federalregister.gov/api/v1/documents/x.json"))

    def test_bot_wall_redirect_is_not_ok(self):
        # 200 after following a redirect that landed on the bot wall == blocked.
        self.assertFalse(pf.evaluate(200, "https://unblock.federalregister.gov/"))

    def test_non_200_is_not_ok(self):
        self.assertFalse(pf.evaluate(403, "https://www.govinfo.gov/x"))
        self.assertFalse(pf.evaluate(None, None))


class TestRun(unittest.TestCase):
    def _probe_all_ok(self, url, timeout=20):
        return 200, url, None

    def _probe_all_blocked(self, url, timeout=20):
        return 403, url, "HTTP Error 403"

    def test_all_ok(self):
        report = pf.run(pf.ENDPOINTS, self._probe_all_ok, "2026-07-27T00:00:00Z")
        self.assertTrue(report["all_ok"])
        self.assertTrue(report["any_ok"])
        self.assertEqual(len(report["endpoints"]), len(pf.ENDPOINTS))
        self.assertTrue(all(e["ok"] for e in report["endpoints"]))

    def test_all_blocked(self):
        report = pf.run(pf.ENDPOINTS, self._probe_all_blocked, "2026-07-27T00:00:00Z")
        self.assertFalse(report["all_ok"])
        self.assertFalse(report["any_ok"])

    def test_mixed_sets_any_but_not_all(self):
        calls = {"n": 0}

        def probe(url, timeout=20):
            calls["n"] += 1
            if calls["n"] == 1:
                return 200, url, None
            return 403, url, "HTTP Error 403"

        report = pf.run(pf.ENDPOINTS, probe, "2026-07-27T00:00:00Z")
        self.assertTrue(report["any_ok"])
        self.assertFalse(report["all_ok"])

    def test_report_is_json_serialisable(self):
        report = pf.run(pf.ENDPOINTS, self._probe_all_ok, "2026-07-27T00:00:00Z")
        json.dumps(report)  # must not raise


class TestEndpoints(unittest.TestCase):
    def test_endpoint_names_unique_and_urls_https(self):
        names = [n for n, _ in pf.ENDPOINTS]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(u.startswith("https://") for _, u in pf.ENDPOINTS))

    def test_covers_fr_api_govinfo_easa(self):
        names = {n for n, _ in pf.ENDPOINTS}
        self.assertLessEqual({"fr_api", "govinfo", "easa_ad"}, names)


if __name__ == "__main__":
    unittest.main()
