"""Tests for validate_records.py — the deterministic verification gate.

Run: python -m unittest tools.test_validate_records  (from project root)
  or: python tools/test_validate_records.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import validate_records as v


ALLOWLIST = ["faa.gov", "ad.easa.europa.eu", "gov.uk", "boeing.com"]


def ref(**kw):
    """Helper: a reference with sensible defaults, overridable per test."""
    base = {
        "ref_type": "AD",
        "ref_number": "2024-01-01",
        "revision": None,
        "ref_date": "2026-06-20",
        "effectivity": None,
        "primary_source_url": "https://drs.faa.gov/doc/2024-01-01",
        "primary_source_domain": None,
        "fetched_text_snippet": "This airworthiness directive requires inspection of...",
        "confidence": "VERIFIED",
    }
    base.update(kw)
    return base


def record(**kw):
    base = {
        "id": "item-1",
        "headline": "Test item",
        "category": "fleet",
        "types_affected": ["A330-300"],
        "event_date": "2026-06-20",
        "developing_carryover": False,
        "summary": "Something happened.",
        "references": [ref()],
        "quotes": [],
        "lead_sources": [],
        "item_confidence": "VERIFIED",
        "verifier_notes": "",
    }
    base.update(kw)
    return base


class TestDomainExtraction(unittest.TestCase):
    def test_strips_www_and_returns_host(self):
        self.assertEqual(v.extract_domain("https://www.boeing.com/commercial"), "boeing.com")

    def test_handles_subdomain(self):
        self.assertEqual(v.extract_domain("https://drs.faa.gov/doc/x"), "drs.faa.gov")

    def test_empty_url_returns_empty(self):
        self.assertEqual(v.extract_domain(None), "")
        self.assertEqual(v.extract_domain(""), "")


class TestAllowlist(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(v.domain_in_allowlist("faa.gov", ALLOWLIST))

    def test_subdomain_matches_parent(self):
        self.assertTrue(v.domain_in_allowlist("drs.faa.gov", ALLOWLIST))

    def test_trade_press_not_allowed(self):
        self.assertFalse(v.domain_in_allowlist("avherald.com", ALLOWLIST))

    def test_lookalike_suffix_not_allowed(self):
        # evil-faa.gov must NOT match faa.gov; notfaa.gov must not match
        self.assertFalse(v.domain_in_allowlist("evilfaa.gov", ALLOWLIST))
        self.assertFalse(v.domain_in_allowlist("faa.gov.evil.com", ALLOWLIST))


class TestReferenceGate(unittest.TestCase):
    def test_verified_with_full_provenance_stays_verified(self):
        r, viol = v.sanitize_reference(ref(), ALLOWLIST)
        self.assertEqual(r["confidence"], "VERIFIED")
        self.assertEqual(viol, [])

    def test_verified_without_primary_url_downgraded(self):
        r, viol = v.sanitize_reference(ref(primary_source_url=None), ALLOWLIST)
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertTrue(viol)

    def test_verified_with_non_allowlisted_url_downgraded(self):
        r, viol = v.sanitize_reference(
            ref(primary_source_url="https://avherald.com/x"), ALLOWLIST)
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertTrue(viol)

    def test_verified_without_fetched_text_downgraded(self):
        r, viol = v.sanitize_reference(ref(fetched_text_snippet=""), ALLOWLIST)
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertTrue(viol)

    def test_verified_with_trivial_fetched_text_downgraded(self):
        r, viol = v.sanitize_reference(ref(fetched_text_snippet="ok"), ALLOWLIST)
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertTrue(viol)

    def test_domain_is_populated_from_url(self):
        r, _ = v.sanitize_reference(ref(), ALLOWLIST)
        self.assertEqual(r["primary_source_domain"], "drs.faa.gov")

    def test_unverified_left_alone(self):
        r, viol = v.sanitize_reference(
            ref(confidence="UNVERIFIED", primary_source_url=None,
                fetched_text_snippet=None), ALLOWLIST)
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertEqual(viol, [])


class TestQuoteGate(unittest.TestCase):
    def good_quote(self, **kw):
        q = {
            "text": "not safety related and no replacement required after failure",
            "doc_title": "Service Letter 777-XX",
            "ref_number": "SL-777-XX",
            "revision_or_date": "2011",
            "url": "https://www.boeing.com/sl",
        }
        q.update(kw)
        return q

    def test_valid_quote_kept(self):
        keep, _ = v.sanitize_quote(self.good_quote(), ALLOWLIST, max_words=25)
        self.assertTrue(keep)

    def test_quote_over_word_limit_dropped(self):
        long_text = " ".join(["word"] * 30)
        keep, reason = v.sanitize_quote(self.good_quote(text=long_text), ALLOWLIST, max_words=25)
        self.assertFalse(keep)
        self.assertIn("word", reason.lower())

    def test_quote_off_allowlist_dropped(self):
        keep, _ = v.sanitize_quote(
            self.good_quote(url="https://avherald.com/x"), ALLOWLIST, max_words=25)
        self.assertFalse(keep)

    def test_quote_missing_url_dropped(self):
        keep, _ = v.sanitize_quote(self.good_quote(url=""), ALLOWLIST, max_words=25)
        self.assertFalse(keep)


class TestRollup(unittest.TestCase):
    def test_rollup_takes_highest(self):
        refs = [ref(confidence="UNVERIFIED"), ref(confidence="VERIFIED")]
        self.assertEqual(v.rollup_confidence(refs, has_event_reporting=False), "VERIFIED")

    def test_rollup_no_refs_with_event_is_reported(self):
        self.assertEqual(v.rollup_confidence([], has_event_reporting=True), "REPORTED")

    def test_rollup_no_refs_no_event_is_unverified(self):
        self.assertEqual(v.rollup_confidence([], has_event_reporting=False), "UNVERIFIED")


class TestWindow(unittest.TestCase):
    def test_in_window_kept(self):
        keep, _ = v.enforce_window(
            record(event_date="2026-06-22"), current_date="2026-06-25", lookback_days=7)
        self.assertTrue(keep)

    def test_outside_window_not_developing_dropped(self):
        keep, _ = v.enforce_window(
            record(event_date="2026-05-01", developing_carryover=False),
            current_date="2026-06-25", lookback_days=7)
        self.assertFalse(keep)

    def test_outside_window_developing_kept_and_flagged(self):
        rec = record(event_date="2026-05-01", developing_carryover=True)
        keep, _ = v.enforce_window(rec, current_date="2026-06-25", lookback_days=7)
        self.assertTrue(keep)
        self.assertFalse(rec["within_window"])


class TestEndToEnd(unittest.TestCase):
    def test_illegal_verified_cannot_pass(self):
        payload = {"records": [record(references=[
            ref(confidence="VERIFIED", primary_source_url="https://avherald.com/x")])]}
        cleaned, report = v.sanitize(
            payload, ALLOWLIST, max_words=25, current_date="2026-06-25", lookback_days=7)
        out_ref = cleaned["records"][0]["references"][0]
        self.assertEqual(out_ref["confidence"], "UNVERIFIED")
        self.assertEqual(cleaned["records"][0]["item_confidence"], "UNVERIFIED")
        self.assertTrue(report["violations"])

    def test_clean_payload_passes_unchanged_confidence(self):
        payload = {"records": [record()]}
        cleaned, report = v.sanitize(
            payload, ALLOWLIST, max_words=25, current_date="2026-06-25", lookback_days=7)
        self.assertEqual(cleaned["records"][0]["item_confidence"], "VERIFIED")
        self.assertEqual(report["violations"], [])

    def test_parse_allowlist_from_yaml(self):
        yaml_text = (
            "verified_domains:\n"
            "  # Regulators\n"
            "  - faa.gov\n"
            "  - ad.easa.europa.eu\n"
            "quotes:\n"
            "  max_words: 25\n"
        )
        got = v.parse_allowlist_from_yaml(yaml_text)
        self.assertEqual(got, ["faa.gov", "ad.easa.europa.eu"])


# ----------------------------------------------------------------------------
# Independent re-fetch audit gate. A reference may only KEEP its VERIFIED status
# if an independent auditor re-fetched the cited URL and confirmed the text.
# ----------------------------------------------------------------------------
GOOD_AUDIT = {
    "status": "confirmed",
    "checked_url": "https://ad.easa.europa.eu/ad/2026-0123",
    "excerpt": "This airworthiness directive requires repetitive inspection of the IP compressor.",
}


class TestAuditGate(unittest.TestCase):
    def test_verified_with_confirmed_audit_stays_verified(self):
        r, viol = v.enforce_audit_reference(ref(audit=dict(GOOD_AUDIT)))
        self.assertEqual(r["confidence"], "VERIFIED")
        self.assertEqual(viol, [])

    def test_verified_without_audit_field_downgraded(self):
        r, viol = v.enforce_audit_reference(ref())  # no audit key
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertTrue(viol)

    def test_verified_with_not_found_audit_downgraded(self):
        a = dict(GOOD_AUDIT, status="not_found")
        r, viol = v.enforce_audit_reference(ref(audit=a))
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertTrue(viol)

    def test_verified_with_fetch_failed_audit_downgraded(self):
        a = dict(GOOD_AUDIT, status="fetch_failed")
        r, viol = v.enforce_audit_reference(ref(audit=a))
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertTrue(viol)

    def test_verified_with_trivial_audit_excerpt_downgraded(self):
        a = dict(GOOD_AUDIT, excerpt="ok")
        r, viol = v.enforce_audit_reference(ref(audit=a))
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertTrue(viol)

    def test_unverified_reference_needs_no_audit(self):
        r, viol = v.enforce_audit_reference(
            ref(confidence="UNVERIFIED", primary_source_url=None, fetched_text_snippet=None))
        self.assertEqual(r["confidence"], "UNVERIFIED")
        self.assertEqual(viol, [])

    def test_quote_dropped_when_backing_reference_fails_audit(self):
        rec = record(
            references=[ref(primary_source_url="https://ad.easa.europa.eu/ad/2026-0123",
                            audit=dict(GOOD_AUDIT, status="not_found"))],
            quotes=[{"text": "requires repetitive inspection", "doc_title": "EASA AD",
                     "ref_number": "2026-0123", "revision_or_date": "2026-06-24",
                     "url": "https://ad.easa.europa.eu/ad/2026-0123"}])
        out, _ = v.enforce_audit_record(rec)
        self.assertEqual(out["references"][0]["confidence"], "UNVERIFIED")
        self.assertEqual(len(out["quotes"]), 0)

    def test_quote_kept_when_backing_reference_passes_audit(self):
        rec = record(
            references=[ref(primary_source_url="https://ad.easa.europa.eu/ad/2026-0123",
                            audit=dict(GOOD_AUDIT))],
            quotes=[{"text": "requires repetitive inspection", "doc_title": "EASA AD",
                     "ref_number": "2026-0123", "revision_or_date": "2026-06-24",
                     "url": "https://ad.easa.europa.eu/ad/2026-0123"}])
        out, _ = v.enforce_audit_record(rec)
        self.assertEqual(out["references"][0]["confidence"], "VERIFIED")
        self.assertEqual(len(out["quotes"]), 1)

    def test_item_confidence_recomputed_after_audit(self):
        payload = {"records": [record(references=[ref(audit=dict(GOOD_AUDIT, status="not_found"))])]}
        cleaned, report = v.enforce_audit(payload)
        self.assertEqual(cleaned["records"][0]["item_confidence"], "UNVERIFIED")
        self.assertTrue(report["violations"])

    def test_clean_audited_payload_unchanged(self):
        payload = {"records": [record(references=[ref(audit=dict(GOOD_AUDIT))])]}
        cleaned, report = v.enforce_audit(payload)
        self.assertEqual(cleaned["records"][0]["item_confidence"], "VERIFIED")
        self.assertEqual(report["violations"], [])


# ----------------------------------------------------------------------------
# Output encoding. The gate writes to stdout, which the orchestrator redirects
# to a file that a LATER stage (the audit gate) reads back as UTF-8. On Windows,
# redirected stdout defaults to cp1252, so a non-ASCII character (e.g. an
# em-dash) was written as a byte the next stage could not decode as UTF-8. The
# gate must therefore emit UTF-8 regardless of the ambient locale encoding.
# ----------------------------------------------------------------------------
class TestOutputEncoding(unittest.TestCase):
    def test_output_is_utf8_under_non_utf8_locale(self):
        script = os.path.abspath(v.__file__)
        config = os.path.join(os.path.dirname(script), "..", "config", "fleet.yaml")
        payload = {"records": [record(
            event_date=None,
            summary="High levels of corrosion fatigue — blade cracking risk.",
            references=[ref(confidence="UNVERIFIED", primary_source_url=None,
                            fetched_text_snippet=None)],
            item_confidence="UNVERIFIED",
        )]}
        with tempfile.TemporaryDirectory() as d:
            infile = os.path.join(d, "in.json")
            outfile = os.path.join(d, "out.json")
            with open(infile, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "cp1252"   # force a non-UTF-8 stdout
            env.pop("PYTHONUTF8", None)           # ensure UTF-8 mode is OFF
            with open(outfile, "wb") as out:
                subprocess.run(
                    [sys.executable, script, "--config", config,
                     "--current-date", "2026-06-26", "--lookback-days", "7",
                     "--infile", infile],
                    stdout=out, stderr=subprocess.DEVNULL, env=env, check=False)
            raw = open(outfile, "rb").read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            self.fail("gate output is not valid UTF-8 (regression): %s" % e)
        self.assertIn("—", text)


if __name__ == "__main__":
    unittest.main()
