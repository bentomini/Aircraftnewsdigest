"""Tests for dedup_ledger.py — the cross-run dedup ledger.

Run: python tools/test_dedup_ledger.py   (from the tools/ dir or project root)
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import dedup_ledger as d


def ref(ref_type="AD", ref_number="2026-13-07", revision=None, ref_date="2026-06-19"):
    return {"ref_type": ref_type, "ref_number": ref_number,
            "revision": revision, "ref_date": ref_date, "confidence": "VERIFIED"}


def record(**kw):
    base = {"id": "item-1", "headline": "A330 fuselage skin cracking AD",
            "category": "fleet", "types_affected": ["A330-300"],
            "event_date": "2026-06-19", "summary": "An AD was issued.",
            "references": [ref()], "quotes": [], "item_confidence": "VERIFIED"}
    base.update(kw)
    return base


class TestIdentity(unittest.TestCase):
    def test_ref_key_normalises_type_and_number(self):
        self.assertEqual(d.ref_key(ref(ref_type="ad", ref_number=" 2026-13-07 ")),
                         "AD:2026-13-07")

    def test_ref_key_collapses_internal_whitespace(self):
        self.assertEqual(d.ref_key(ref(ref_number="2026-13-07   R1")), "AD:2026-13-07 R1")

    def test_ref_version_prefers_revision(self):
        self.assertEqual(d.ref_version(ref(revision="R2", ref_date="2026-06-19")), "R2")

    def test_ref_version_falls_back_to_ref_date(self):
        self.assertEqual(d.ref_version(ref(revision=None, ref_date="2026-06-19")), "2026-06-19")

    def test_ref_version_empty_when_neither(self):
        self.assertEqual(d.ref_version(ref(revision=None, ref_date=None)), "")

    def test_record_components_uses_references(self):
        comps = d.record_components(record())
        self.assertEqual(comps, [("AD:2026-13-07", "2026-06-19")])

    def test_record_components_event_slug_when_no_refs(self):
        comps = d.record_components(record(references=[], headline="UPS MD-11 hull loss"))
        self.assertEqual(comps, [("EVENT:ups-md-11-hull-loss", "2026-06-19")])


class TestClassify(unittest.TestCase):
    def test_unseen_key_is_new(self):
        self.assertEqual(d.classify("AD:2026-13-07", "2026-06-19", {}), "new")

    def test_same_version_is_duplicate(self):
        ledger = {"AD:2026-13-07": {"last_version": "2026-06-19", "last_reported": "2026-06-19"}}
        self.assertEqual(d.classify("AD:2026-13-07", "2026-06-19", ledger), "duplicate")

    def test_changed_version_is_updated(self):
        ledger = {"AD:2026-13-07": {"last_version": "2026-06-19", "last_reported": "2026-06-19"}}
        self.assertEqual(d.classify("AD:2026-13-07", "R1", ledger), "updated")

    def test_missing_versions_compare_equal(self):
        ledger = {"AD:X": {"last_version": "", "last_reported": "2026-06-19"}}
        self.assertEqual(d.classify("AD:X", "", ledger), "duplicate")


class TestApply(unittest.TestCase):
    def test_unseen_record_kept_and_marked_new(self):
        out, report = d.apply_dedup({"records": [record()]}, {})
        self.assertEqual(report["kept"], 1)
        self.assertEqual(report["suppressed"], 0)
        self.assertEqual(out["records"][0]["dedup"]["status"], "new")
        self.assertEqual(out["records"][0]["references"][0]["dedup_status"], "new")

    def test_unchanged_record_suppressed(self):
        ledger = {"AD:2026-13-07": {"last_version": "2026-06-19", "last_reported": "2026-06-19"}}
        out, report = d.apply_dedup({"records": [record()]}, ledger)
        self.assertEqual(report["suppressed"], 1)
        self.assertEqual(report["kept"], 0)
        self.assertEqual(out["records"], [])

    def test_revised_record_resurfaced_and_tagged_updated(self):
        ledger = {"AD:2026-13-07": {"last_version": "2026-06-19", "last_reported": "2026-06-19"}}
        rec = record(references=[ref(revision="R1", ref_date="2026-06-25")])
        out, report = d.apply_dedup({"records": [rec]}, ledger)
        self.assertEqual(report["kept"], 1)
        kept = out["records"][0]
        self.assertEqual(kept["dedup"]["status"], "updated")
        self.assertEqual(kept["dedup"]["previously_reported"], "2026-06-19")
        self.assertEqual(kept["references"][0]["dedup_status"], "updated")
        self.assertEqual(kept["references"][0]["previous_version"], "2026-06-19")

    def test_record_with_one_new_ref_is_kept(self):
        ledger = {"AD:2026-13-07": {"last_version": "2026-06-19", "last_reported": "2026-06-19"}}
        rec = record(references=[ref(), ref(ref_number="2026-14-01")])  # 2nd ref unseen
        out, report = d.apply_dedup({"records": [rec]}, ledger)
        self.assertEqual(report["kept"], 1)

    def test_does_not_mutate_input(self):
        rec = record()
        d.apply_dedup({"records": [rec]}, {})
        self.assertNotIn("dedup", rec)                       # caller's object untouched
        self.assertNotIn("dedup_status", rec["references"][0])


class TestLedgerIO(unittest.TestCase):
    def test_load_missing_file_is_empty(self):
        self.assertEqual(d.load_ledger("/no/such/file.json"), {})

    def test_load_malformed_file_is_empty(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{ not json")
            path = f.name
        try:
            self.assertEqual(d.load_ledger(path), {})
        finally:
            os.unlink(path)

    def test_record_then_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as dir_:
            path = os.path.join(dir_, "_seen.json")
            seen = d.record_seen({}, [record()], "2026-06-19")
            d.save_ledger(path, seen, "2026-06-19")
            back = d.load_ledger(path)
            self.assertIn("AD:2026-13-07", back)
            self.assertEqual(back["AD:2026-13-07"]["last_version"], "2026-06-19")
            self.assertEqual(back["AD:2026-13-07"]["first_reported"], "2026-06-19")

    def test_record_preserves_first_reported_on_update(self):
        seen = d.record_seen({}, [record()], "2026-06-19")
        rec2 = record(references=[ref(revision="R1", ref_date="2026-06-25")])
        seen = d.record_seen(seen, [rec2], "2026-06-26")
        entry = seen["AD:2026-13-07"]
        self.assertEqual(entry["first_reported"], "2026-06-19")  # unchanged
        self.assertEqual(entry["last_reported"], "2026-06-26")
        self.assertEqual(entry["last_version"], "R1")

    def test_save_is_atomic_no_tmp_left_behind(self):
        with tempfile.TemporaryDirectory() as dir_:
            path = os.path.join(dir_, "_seen.json")
            d.save_ledger(path, {"AD:X": {"last_version": "R0"}}, "2026-06-19")
            leftovers = [n for n in os.listdir(dir_) if n.endswith(".tmp")]
            self.assertEqual(leftovers, [])


def _run_cli(args, stdin_text=None):
    script = os.path.abspath(d.__file__)
    proc = subprocess.run([sys.executable, script] + args,
                          input=stdin_text, capture_output=True, text=True,
                          encoding="utf-8", check=False)
    return proc


class TestCli(unittest.TestCase):
    def test_apply_then_record_then_apply_suppresses_second_time(self):
        with tempfile.TemporaryDirectory() as dir_:
            ledger = os.path.join(dir_, "_seen.json")
            payload = json.dumps({"records": [record()]})

            # First apply: nothing seen yet -> kept, exit 0.
            p1 = _run_cli(["--ledger", ledger, "--current-date", "2026-06-19"], payload)
            self.assertEqual(p1.returncode, 0)
            self.assertEqual(len(json.loads(p1.stdout)["records"]), 1)

            # Record what was shown.
            infile = os.path.join(dir_, "06.json")
            with open(infile, "w", encoding="utf-8") as f:
                f.write(p1.stdout)
            p2 = _run_cli(["--ledger", ledger, "--current-date", "2026-06-19",
                           "--record", "--infile", infile])
            self.assertEqual(p2.returncode, 0)

            # Second apply, same item -> suppressed, exit 1.
            p3 = _run_cli(["--ledger", ledger, "--current-date", "2026-06-26"], payload)
            self.assertEqual(p3.returncode, 1)
            self.assertEqual(json.loads(p3.stdout)["records"], [])
            self.assertIn("suppressed=1", p3.stderr)

    def test_apply_emits_valid_utf8_under_cp1252_locale(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "cp1252"
        env.pop("PYTHONUTF8", None)
        script = os.path.abspath(d.__file__)
        rec = record(headline="A330 corrosion fatigue — blade cracking")
        with tempfile.TemporaryDirectory() as dir_:
            ledger = os.path.join(dir_, "_seen.json")
            out = os.path.join(dir_, "out.json")
            with open(out, "wb") as fo:
                subprocess.run([sys.executable, script, "--ledger", ledger,
                                "--current-date", "2026-06-26"],
                               input=json.dumps({"records": [rec]}).encode("utf-8"),
                               stdout=fo, stderr=subprocess.DEVNULL, env=env, check=False)
            with open(out, "rb") as fh:
                raw = fh.read()
        raw.decode("utf-8")  # must not raise
        self.assertIn("—", raw.decode("utf-8"))


class TestSchema(unittest.TestCase):
    def test_schema_declares_dedup_fields(self):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "..", "schema", "record.schema.json")
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        # record-level dedup object is allowed
        self.assertIn("dedup", schema["properties"])
        # reference-level annotation fields are allowed
        ref_props = schema["$defs"]["reference"]["properties"]
        self.assertIn("dedup_status", ref_props)
        self.assertIn("previous_version", ref_props)


if __name__ == "__main__":
    unittest.main()
