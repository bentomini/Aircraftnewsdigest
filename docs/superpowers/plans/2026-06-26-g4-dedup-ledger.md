# G4 — Cross-Run Dedup Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the weekly digest re-surfacing items it already reported, while still re-surfacing a reference when it materially changes (new revision/date), tagged as updated.

**Architecture:** A new deterministic stdlib tool `tools/dedup_ledger.py`, mirroring `tools/compute_window.py`. It has two modes. **Apply mode** runs after the audit gate (`05_final.json`) and before the Writer: it compares each record's identity (its references' `TYPE:NUMBER` + version, or an event slug for ref-less items) against a persistent ledger `runs/_seen.json`, suppresses records whose every component was already reported at the same version, annotates changed references as `updated`, and emits `06_deduped.json`. **Record mode** (`--record`) runs after the digest is written (next to the Step 6b window marker): it upserts every shown item's identity into the ledger with an atomic temp-file-and-rename write. The Writer renders an `[UPDATED since <prev>]` tag from the annotation, but the suppression itself is fully deterministic in the tool, so correctness never depends on the LLM.

**Decision (chosen by user, 2026-06-26):** *Suppress exact, re-surface on change.* A record is suppressed only if **every** identity component was already reported at the **same** version. A new revision/date re-surfaces the item, tagged updated. Identity version = `revision` if present, else `ref_date`. Ref-less event items are keyed on a slug of the headline + `event_date` (best-effort; ref-based dedup is the robust path).

**Tech Stack:** Python 3 stdlib only (`argparse`, `json`, `os`, `re`, `sys`, `tempfile`, `datetime`). `unittest` for tests. JSON Schema (draft 2020-12) for the record contract. Markdown agent/command docs.

> **STATUS: ✅ COMPLETE (executed inline 2026-06-26).** All 9 tasks implemented and verified.
> `tools/dedup_ledger.py` + `tools/test_dedup_ledger.py` (24 tests), schema fields, orchestrator
> Steps 4d/6c, writer `[UPDATED]` tag, roadmap/decisions/memory updated. Full suite green
> (24 dedup + 42 gate = 66). **Commit steps were skipped** — this workspace is not a git repo.
> **Deferred (non-blocking):** the writer `[UPDATED]`-tag live LLM smoke check (Task 8 Step 2) was
> not run; suppression is deterministic so correctness does not depend on it.

**Files:**
- Create: `tools/dedup_ledger.py`
- Create: `tools/test_dedup_ledger.py`
- Modify: `schema/record.schema.json` (add `dedup` to record; `dedup_status` + `previous_version` to reference)
- Modify: `.claude/commands/digest.md` (Step 4d apply, Step 6c record, Writer reads `06_deduped.json`)
- Modify: `.claude/agents/writer.md` (render the `[UPDATED since …]` tag)
- Modify: `roadmap.md` (check off G4 dedup half + decisions log)
- Ledger data file (runtime, not committed): `runs/_seen.json`

---

## Task 1: Identity + version helpers

**Files:**
- Create: `tools/dedup_ledger.py`
- Test: `tools/test_dedup_ledger.py`

- [ ] **Step 1: Write the failing test**

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/test_dedup_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dedup_ledger'` (run from `tools/`) or `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

Create `tools/dedup_ledger.py`:

```python
#!/usr/bin/env python3
"""dedup_ledger.py — cross-run dedup for the digest pipeline.

Suppresses items already reported in a previous run, UNLESS a reference changed
(new revision/date), in which case it re-surfaces tagged 'updated'. Identity of a
record = its references' TYPE:NUMBER + version (revision, else ref_date); a ref-less
event item is keyed on a slug of its headline + event_date.

Two modes (mirrors tools/compute_window.py):
  * apply  (default): read records, suppress/annotate against runs/_seen.json,
                      emit the survivors. Run AFTER the audit gate, BEFORE the writer.
  * --record         : upsert the SHOWN items into the ledger. Run AFTER the digest
                      is written (next to the Step 6b window marker).

See docs/superpowers/plans/2026-06-26-g4-dedup-ledger.md. Stdlib only.
"""
import argparse
import json
import os
import re
import sys
import tempfile

LEDGER_VERSION = 1


def normalize_number(s):
    """Upper-cased, trimmed, internal whitespace collapsed."""
    return re.sub(r"\s+", " ", (s or "").strip()).upper()


def ref_key(reference):
    """Stable cross-run identity for a reference: 'TYPE:NUMBER'."""
    rt = (reference.get("ref_type") or "other").strip().upper()
    return "%s:%s" % (rt, normalize_number(reference.get("ref_number")))


def ref_version(reference):
    """The version string: revision if present, else ref_date, else ''."""
    rev = (reference.get("revision") or "").strip()
    if rev:
        return rev
    return (reference.get("ref_date") or "").strip()


def slug(text):
    """Lower-case, non-alphanumerics to single hyphens; '' -> 'untitled'."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "untitled"


def record_components(record):
    """Identity components [(key, version), ...] for a record."""
    refs = record.get("references") or []
    if refs:
        return [(ref_key(r), ref_version(r)) for r in refs]
    key = "EVENT:%s" % slug(record.get("headline"))
    return [(key, (record.get("event_date") or "").strip())]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tools/test_dedup_ledger.py -v`
Expected: PASS (7 tests in TestIdentity).

- [ ] **Step 5: Commit**

```bash
git add tools/dedup_ledger.py tools/test_dedup_ledger.py
git commit -m "feat(dedup): identity + version helpers for cross-run dedup ledger"
```

---

## Task 2: Classify a component against the ledger

**Files:**
- Modify: `tools/dedup_ledger.py`
- Test: `tools/test_dedup_ledger.py`

- [ ] **Step 1: Write the failing test** (append to the test file)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/test_dedup_ledger.py TestClassify -v`
Expected: FAIL — `AttributeError: module 'dedup_ledger' has no attribute 'classify'`.

- [ ] **Step 3: Write minimal implementation** (append to `dedup_ledger.py`)

```python
def classify(key, version, ledger):
    """'new' | 'duplicate' | 'updated' for one identity component."""
    entry = ledger.get(key)
    if entry is None:
        return "new"
    if (entry.get("last_version") or "") == (version or ""):
        return "duplicate"
    return "updated"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tools/test_dedup_ledger.py TestClassify -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/dedup_ledger.py tools/test_dedup_ledger.py
git commit -m "feat(dedup): classify a reference against the ledger (new/duplicate/updated)"
```

---

## Task 3: Apply dedup over a record and a payload

**Files:**
- Modify: `tools/dedup_ledger.py`
- Test: `tools/test_dedup_ledger.py`

- [ ] **Step 1: Write the failing test** (append)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/test_dedup_ledger.py TestApply -v`
Expected: FAIL — `AttributeError: ... 'apply_dedup'`.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def apply_record(record, ledger):
    """Return (record_or_None, status). None => suppress. Does not mutate the input."""
    rec = dict(record)
    refs = [dict(r) for r in (rec.get("references") or [])]
    rec["references"] = refs

    statuses = []
    if refs:
        for r in refs:
            k, ver = ref_key(r), ref_version(r)
            st = classify(k, ver, ledger)
            statuses.append(st)
            r["dedup_status"] = st if st in ("new", "updated") else "unchanged"
            if st == "updated":
                r["previous_version"] = (ledger[k].get("last_version") or None)
    else:
        k, ver = record_components(rec)[0]
        statuses.append(classify(k, ver, ledger))

    if statuses and all(s == "duplicate" for s in statuses):
        return None, "suppressed"

    prev = [ledger[k]["last_reported"] for (k, _v) in record_components(rec)
            if ledger.get(k) and ledger[k].get("last_reported")]
    rec_status = "new" if any(s == "new" for s in statuses) else "updated"
    rec["dedup"] = {"status": rec_status,
                    "previously_reported": min(prev) if prev else None}
    return rec, rec_status


def apply_dedup(payload, ledger):
    """Suppress/annotate records against the ledger. Return (cleaned_payload, report)."""
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    kept, suppressed, counts = [], 0, {"new": 0, "updated": 0}
    for rec in records:
        out, status = apply_record(rec, ledger)
        if out is None:
            suppressed += 1
        else:
            kept.append(out)
            counts[status] = counts.get(status, 0) + 1
    report = {"kept": len(kept), "suppressed": suppressed,
              "new": counts["new"], "updated": counts["updated"]}
    return {"records": kept}, report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tools/test_dedup_ledger.py TestApply -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/dedup_ledger.py tools/test_dedup_ledger.py
git commit -m "feat(dedup): apply suppression + updated-tagging over records"
```

---

## Task 4: Ledger load, upsert, and atomic save

**Files:**
- Modify: `tools/dedup_ledger.py`
- Test: `tools/test_dedup_ledger.py`

- [ ] **Step 1: Write the failing test** (append)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/test_dedup_ledger.py TestLedgerIO -v`
Expected: FAIL — `AttributeError: ... 'load_ledger'`.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def load_ledger(path):
    """The 'seen' map from the ledger file, or {} if missing/malformed (fail-safe)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("seen"), dict):
        return data["seen"]
    return {}


def record_seen(seen, records, current_date):
    """Upsert every identity component of the SHOWN records into the seen map."""
    for rec in records:
        headline = rec.get("headline")
        for (key, version) in record_components(rec):
            entry = seen.get(key) or {"first_reported": current_date}
            entry.setdefault("first_reported", current_date)
            entry["last_reported"] = current_date
            entry["last_version"] = version
            entry["headline"] = headline
            seen[key] = entry
    return seen


def save_ledger(path, seen, current_date):
    """Atomically write the ledger (temp file + os.replace) so a crash can't corrupt it."""
    payload = {"version": LEDGER_VERSION, "updated": current_date, "seen": seen}
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tools/test_dedup_ledger.py TestLedgerIO -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/dedup_ledger.py tools/test_dedup_ledger.py
git commit -m "feat(dedup): ledger load + atomic upsert/save"
```

---

## Task 5: CLI (apply mode, --record mode, UTF-8 guard, exit codes)

**Files:**
- Modify: `tools/dedup_ledger.py`
- Test: `tools/test_dedup_ledger.py`

- [ ] **Step 1: Write the failing test** (append)

```python
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
            raw = open(out, "rb").read()
        raw.decode("utf-8")  # must not raise
        self.assertIn("—", raw.decode("utf-8"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/test_dedup_ledger.py TestCli -v`
Expected: FAIL — no `main`, CLI returns non-zero / empty stdout.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def main(argv=None):
    # Force UTF-8 so a redirected stdout/stderr on Windows (cp1252 default) never
    # corrupts non-ASCII output — same guard as validate_records.py/compute_window.py.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Cross-run dedup ledger for the digest pipeline.")
    ap.add_argument("--ledger", default="runs/_seen.json",
                    help="Persistent seen-items ledger.")
    ap.add_argument("--current-date", required=True, help="Runtime date, YYYY-MM-DD.")
    ap.add_argument("--infile", default="-", help="Records JSON path, or - for stdin.")
    ap.add_argument("--record", action="store_true",
                    help="Record mode: upsert the SHOWN items into the ledger. Run ONLY "
                         "after the digest has been written.")
    args = ap.parse_args(argv)

    raw = sys.stdin.read() if args.infile == "-" else open(args.infile, encoding="utf-8").read()
    payload = json.loads(raw)
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    seen = load_ledger(args.ledger)

    if args.record:
        record_seen(seen, records, args.current_date)
        save_ledger(args.ledger, seen, args.current_date)
        sys.stderr.write("[dedup] recorded %d shown item(s) into %s\n"
                         % (len(records), args.ledger))
        return 0

    cleaned, report = apply_dedup(payload, seen)
    sys.stdout.write(json.dumps(cleaned, indent=2, ensure_ascii=False))
    sys.stderr.write("\n[dedup] kept=%d suppressed=%d new=%d updated=%d\n"
                     % (report["kept"], report["suppressed"], report["new"], report["updated"]))
    return 1 if report["suppressed"] else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tools/test_dedup_ledger.py -v`
Expected: PASS — all tests across TestIdentity, TestClassify, TestApply, TestLedgerIO, TestCli.

- [ ] **Step 5: Commit**

```bash
git add tools/dedup_ledger.py tools/test_dedup_ledger.py
git commit -m "feat(dedup): CLI apply/--record modes with UTF-8 guard and exit codes"
```

---

## Task 6: Schema additions for the dedup annotation

**Files:**
- Modify: `schema/record.schema.json`
- Test: `tools/test_dedup_ledger.py`

- [ ] **Step 1: Write the failing test** (append)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/test_dedup_ledger.py TestSchema -v`
Expected: FAIL — `KeyError: 'dedup'`.

- [ ] **Step 3: Edit the schema**

In `schema/record.schema.json`, inside the top-level record `"properties"` (e.g. after the `verifier_notes` property), add:

```json
    "dedup": {
      "type": ["object", "null"],
      "description": "Added by dedup_ledger.py (apply mode). Present only on records that survived dedup. status=new (not previously reported) or updated (a reference changed revision/date since it was last reported).",
      "additionalProperties": false,
      "properties": {
        "status": { "type": "string", "enum": ["new", "updated"] },
        "previously_reported": { "type": ["string", "null"], "description": "ISO date this item was last reported, if known." }
      },
      "required": ["status"]
    }
```

In the `$defs.reference.properties` object (e.g. after `audit`), add:

```json
        "dedup_status": {
          "type": ["string", "null"],
          "enum": ["new", "updated", "unchanged", null],
          "description": "Added by dedup_ledger.py: new (first time reported), updated (revision/date changed since last reported), unchanged (re-shown because another reference on the same item changed)."
        },
        "previous_version": {
          "type": ["string", "null"],
          "description": "For dedup_status=updated: the version (revision or ref_date) last reported."
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tools/test_dedup_ledger.py TestSchema -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add schema/record.schema.json tools/test_dedup_ledger.py
git commit -m "feat(dedup): add dedup/dedup_status fields to the record schema"
```

---

## Task 7: Wire the orchestrator (apply before writer, record after digest)

**Files:**
- Modify: `.claude/commands/digest.md`

- [ ] **Step 1: Add Step 4d (apply) after Step 4c**

Insert a new section immediately after Step 4c (AUDIT GATE), before Step 5 (WRITE):

```markdown
## Step 4d — DEDUP (Bash — deterministic, never skip)
Suppress items already reported in a previous run, and tag any that changed since (new
revision/date). The Writer must only ever see this output:
```
python tools/dedup_ledger.py \
  --ledger runs/_seen.json \
  --current-date $CURRENT_DATE \
  --infile runs/$CURRENT_DATE/05_final.json \
  > runs/$CURRENT_DATE/06_deduped.json \
  2> runs/$CURRENT_DATE/06_dedup_report.txt
```
Read `runs/$CURRENT_DATE/06_dedup_report.txt` and surface its summary line
(`kept / suppressed / new / updated`) to the user. A non-zero exit just means something was
suppressed — expected and healthy. The ledger is **read-only here**; it is only written in Step 6c
after the digest succeeds. **`06_deduped.json` is the only thing the Writer may see.**
```

- [ ] **Step 2: Point the Writer at `06_deduped.json`**

In Step 5 (WRITE), change the input file the writer reads from `05_final.json` to
`06_deduped.json`. The relevant prompt line becomes:

```markdown
> Render the digest from `runs/{CURRENT_DATE}/06_deduped.json` per your instructions.
```

And update the final sentence of Step 4c from
`**`05_final.json` is the only thing the Writer may see.**` to note that `05_final.json` now feeds
the dedup step, and `06_deduped.json` is the writer's input.

- [ ] **Step 3: Add Step 6c (record) after Step 6b**

Insert after Step 6b (Record the run):

```markdown
## Step 6c — Record reported items (Bash — deterministic, never skip)
The digest now exists, so remember what it reported so the *next* run can suppress unchanged
repeats. Record only here, after a successful digest — never earlier, so a failed or empty run
does not poison the ledger:
```
python tools/dedup_ledger.py --record \
  --ledger runs/_seen.json \
  --current-date $CURRENT_DATE \
  --infile runs/$CURRENT_DATE/06_deduped.json
```
This upserts each shown item's identity (reference TYPE:NUMBER + version, or event slug) into
`runs/_seen.json` with an atomic write. It records only; it never touches the digest.
```

- [ ] **Step 4: Update the Guardrails list**

Add a bullet to the Guardrails section:

```markdown
- Never skip Step 4d (dedup) or Step 6c (the ledger update). Update the ledger (6c) only after the
  digest is written, mirroring the Step 6b run marker.
```

- [ ] **Step 5: Commit**

```bash
git add .claude/commands/digest.md
git commit -m "feat(dedup): wire dedup apply (4d) and ledger record (6c) into the orchestrator"
```

---

## Task 8: Writer renders the `[UPDATED since …]` tag

**Files:**
- Modify: `.claude/agents/writer.md`

- [ ] **Step 1: Add a rendering rule**

In `.claude/agents/writer.md`, in the section that describes per-item rendering (where confidence
tags are rendered inline), add the following rule verbatim:

```markdown
- **Dedup tag (render exactly, never invent):** If a record has `dedup.status == "updated"`, append
  ` [UPDATED since <previously_reported>]` to that item's headline, where `<previously_reported>` is
  the record's `dedup.previously_reported` value (omit the date if it is null: ` [UPDATED]`). If a
  specific reference carries `dedup_status == "updated"`, you may instead note ` [rev changed from
  <previous_version>]` after that reference. Do NOT add any dedup tag to records with
  `dedup.status == "new"` or with no `dedup` object. This is a presentation tag only — it never
  changes a reference, quote, date, or revision.
```

- [ ] **Step 2: Verify the writer dry-run still renders**

Run a dry-run with a hand-made `06_deduped.json` containing one `updated` record and confirm the
tag appears (manual check; the writer is an LLM stage, so this is a smoke check, not an assertion):

Run: dispatch the `writer` agent on a temp `06_deduped.json` (one record, `dedup.status="updated"`,
`previously_reported="2026-06-19"`) and confirm the headline shows `[UPDATED since 2026-06-19]`.
Expected: tag present. (Suppression correctness does not depend on this — it is enforced in Task 3.)

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/writer.md
git commit -m "feat(dedup): writer renders the [UPDATED since …] presentation tag"
```

---

## Task 9: Update roadmap, decisions log, and memory

**Files:**
- Modify: `roadmap.md`

- [ ] **Step 1: Close the G4 dedup open question**

In `roadmap.md`, under "Open questions", replace the **G4** bullet's "dedup half remains" text with a
done note pointing at `tools/dedup_ledger.py` + `runs/_seen.json`, and add a Stage 5 checklist line:

```markdown
- [x] G4 cross-run dedup SHIPPED (2026-06-26): `tools/dedup_ledger.py` (apply + `--record`),
      ledger `runs/_seen.json`, orchestrator Steps 4d/6c. Policy: suppress exact, re-surface on
      change (revision/date). NN tests pass. Built test-first per
      `docs/superpowers/plans/2026-06-26-g4-dedup-ledger.md`.
```

- [ ] **Step 2: Add a decisions-log row**

```markdown
| 2026-06-26 | G4 — cross-run dedup ledger | Suppress an item only if every reference was already reported at the same version; re-surface tagged `updated` on a new revision/date. Identity = reference `TYPE:NUMBER` + version (revision else ref_date); ref-less events keyed on a headline slug + event_date (best-effort). New deterministic tool `tools/dedup_ledger.py`; ledger `runs/_seen.json` written atomically only after a successful digest (Step 6c), mirroring the G13 run marker. |
```

- [ ] **Step 3: Run the full suite once more**

Run: `python tools/test_dedup_ledger.py -v && python tools/test_validate_records.py`
Expected: all green. Replace `NN` in Step 1 with the real dedup test count.

- [ ] **Step 4: Commit**

```bash
git add roadmap.md
git commit -m "docs(dedup): close G4 in roadmap + decisions log"
```

- [ ] **Step 5: Update project memory** (after merge)

Update `memory/digest-project-state.md`: G4 dedup is now SHIPPED; only the minor output-density
polish remains open. Note the new ledger file `runs/_seen.json` and the two new orchestrator steps.

---

## Self-Review

**Spec coverage:**
- Suppress already-reported items → Task 3 (`apply_dedup` suppress) + Task 5 (CLI, exit 1).
- Re-surface on change, tagged updated → Task 3 (`updated` status + `previous_version`) + Task 8 (writer tag).
- Persist across runs → Task 4 (`load_ledger`/`save_ledger`) + Task 7 (Steps 4d/6c).
- Record only after a successful digest → Task 7 Step 3 (Step 6c placement).
- Atomic write (addresses G13's noted non-atomic follow-up) → Task 4 (`save_ledger` temp + `os.replace`) + test.
- Schema stays valid (`additionalProperties: false`) → Task 6.
- UTF-8 on Windows (matches the G11 fix) → Task 5 (`reconfigure`) + test.

**Placeholder scan:** `NN` in Task 9 is intentionally filled from the real test count in Task 9 Step 3. No other placeholders.

**Type consistency:** `ref_key`, `ref_version`, `record_components`, `classify`, `apply_record`, `apply_dedup`, `load_ledger`, `record_seen`, `save_ledger`, `main` are named identically across all tasks and tests. Ledger entry shape `{first_reported, last_reported, last_version, headline}` is consistent between Task 4's `record_seen` and Task 2/3's reads.
