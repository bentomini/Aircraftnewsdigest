# Standing Watch Content Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a walled-off `## Standing Watch` section (Compliance Radar, On the Horizon, Engineer's Corner) so thin weeks read as substantial (≥4 blocks) without diluting the verification trust model.

**Architecture:** Three blocks, three integration styles, each chosen to preserve "structure over instruction":
- **On the Horizon** (proposed FAA NPRMs / EASA PADs) rides the *existing* pipeline — add `NPRM`/`PAD` to the `ref_type` enum, add scanner queries, and have the writer route those records into the Horizon block. The deterministic gate (`validate_records.py`) never inspects `ref_type`, so proposed rules pass through verify → gate → audit → audit-gate → dedup exactly like ADs and can never reach the output unverified.
- **Compliance Radar** re-displays the *calendar effective dates* of already-VERIFIED ADs across runs. Needs a structured `effective_date` (new schema field, captured by the verifier) and a persistent store (`runs/_compliance.json`) written by a new deterministic tool `tools/compliance_radar.py`. The writer renders the selected JSON; it never fetches. (Flight-cycle compliance *times* are out of scope for v1 — they are not calendar dates and cannot go on a date radar deterministically.)
- **Engineer's Corner** is fully deterministic: author-curated evergreen entries in `config/engineers_corner.json`, selected by a tested tool `tools/engineers_corner.py` (grounded match → rotation fallback), and rendered only when core items < threshold. No LLM-generated facts.

**Tech Stack:** Python 3 stdlib only (matches `compute_window.py` / `dedup_ledger.py` / `finalize_digest.py`); `unittest`; Markdown agent prompts; JSON inter-stage artifacts; `config/fleet.yaml` for scalar params.

---

## File Structure

**New files:**
- `tools/compliance_radar.py` — select upcoming-effective-date AD entries from the store (build mode) + upsert this run's VERIFIED ADs (record mode). Mirrors `dedup_ledger.py` dual-mode shape.
- `tools/test_compliance_radar.py` — unit tests.
- `tools/engineers_corner.py` — pick a curated entry (grounded → rotation) when core items < threshold (build mode) + advance the rotation pointer (record mode).
- `tools/test_engineers_corner.py` — unit tests.
- `config/engineers_corner.json` — author-seeded evergreen entry bank (JSON, not YAML — stdlib-parseable).

**Modified files:**
- `schema/record.schema.json` — `NPRM`/`PAD` in `ref_type` enum; new `effective_date` field on a reference.
- `config/fleet.yaml` — `standing_watch:` scalar params.
- `.claude/agents/scanner.md` — NPRM/PAD discovery queries.
- `.claude/agents/verifier.md` — capture `effective_date`; use `NPRM`/`PAD` ref_type for proposed rules.
- `.claude/agents/writer.md` — render `## Standing Watch` + 3 sub-blocks; route NPRM/PAD into Horizon; read the two new artifacts.
- `tools/finalize_digest.py` + `tools/test_finalize_digest.py` — add `## Standing Watch` to enforced section order.
- `.claude/commands/digest.md` — wire build steps (4e/4f), writer inputs, and record steps (6d/6e).
- `CLAUDE.md` — amend the "no padding" rule; document the new section.
- `roadmap.md` — new stage entry + decisions-log rows.

**State files (created at runtime, gitignored like other `runs/_*.json`):**
- `runs/_compliance.json` — persistent AD effective-date store.
- `runs/_corner.json` — Engineer's Corner rotation pointer.
- `runs/<date>/07_radar.json`, `runs/<date>/08_corner.json` — per-run standing-watch artifacts.

---

## Task 1: Schema — add NPRM/PAD ref_type and effective_date

**Files:**
- Modify: `schema/record.schema.json` (the `reference` `$def`: `ref_type` enum ~line 104, properties block)

- [ ] **Step 1: Add NPRM and PAD to the ref_type enum**

In `schema/record.schema.json`, change the `ref_type` enum (in `$defs.reference.properties.ref_type`) from:

```json
          "enum": ["AD", "EAD", "SB", "SIL", "SL", "MSG-3", "other"]
```

to:

```json
          "enum": ["AD", "EAD", "NPRM", "PAD", "SB", "SIL", "SL", "MSG-3", "other"]
```

- [ ] **Step 2: Add the effective_date field to a reference**

In the same `reference` `properties` object, immediately after the `ref_date` property, add:

```json
        "effective_date": {
          "type": ["string", "null"],
          "description": "ISO date (YYYY-MM-DD) the AD/EAD becomes effective. Distinct from ref_date (publication). Captured by the verifier from the fetched text; powers the Compliance Radar."
        },
```

- [ ] **Step 3: Verify the schema is still valid JSON**

Run: `python -c "import json; json.load(open('schema/record.schema.json')); print('schema OK')"`
Expected: `schema OK`

- [ ] **Step 4: Commit**

```bash
git add schema/record.schema.json
git commit -m "feat(schema): add NPRM/PAD ref_type and reference effective_date"
```

---

## Task 2: fleet.yaml — standing_watch scalar params

**Files:**
- Modify: `config/fleet.yaml` (append a new top-level block before `verified_domains:`)

- [ ] **Step 1: Add the standing_watch block**

In `config/fleet.yaml`, after the `run:` block (ends at `carry_developing_events:` line) and before the `sources:` block, insert:

```yaml
# -----------------------------------------------------------------------------
# Standing Watch — the "filler / minimum-entry" content layer. Forward-looking
# primary-source intelligence + a walled-off educational block, rendered under
# their own ## Standing Watch heading so they never dilute the verified core.
# -----------------------------------------------------------------------------
standing_watch:
  forward_days: 90              # Compliance Radar: show ADs whose effective_date is within this many days ahead
  radar_max: 3                  # Compliance Radar: max entries shown
  horizon_max: 3               # On the Horizon: max proposed-rule (NPRM/PAD) items shown (writer-enforced)
  corner_min_core_items: 4     # Engineer's Corner renders only if fewer than this many substantive core items
```

- [ ] **Step 2: Verify YAML still parses**

Run: `python -c "import re; t=open('config/fleet.yaml').read(); assert 'standing_watch:' in t and 'forward_days: 90' in t; print('fleet.yaml OK')"`
Expected: `fleet.yaml OK`

- [ ] **Step 3: Commit**

```bash
git add config/fleet.yaml
git commit -m "feat(config): add standing_watch params (forward window, caps, corner threshold)"
```

---

## Task 3: compliance_radar.py — failing tests

**Files:**
- Create: `tools/test_compliance_radar.py`

- [ ] **Step 1: Write the failing tests**

Create `tools/test_compliance_radar.py`:

```python
#!/usr/bin/env python3
"""Tests for tools/compliance_radar.py — the Compliance Radar store + selector."""
import json
import os
import tempfile
import unittest

import compliance_radar as cr


def ad_record(ref_number, effective_date, confidence="VERIFIED", url=None, headline=None):
    return {
        "id": "rec-" + ref_number.lower().replace(" ", "-"),
        "headline": headline or ("FAA AD " + ref_number),
        "category": "fleet",
        "types_affected": ["A330-300"],
        "item_confidence": confidence,
        "references": [{
            "ref_type": "AD",
            "ref_number": ref_number,
            "effective_date": effective_date,
            "confidence": confidence,
            "primary_source_url": url or ("https://www.govinfo.gov/" + ref_number),
        }],
    }


class TestRecord(unittest.TestCase):
    def test_record_upserts_verified_ad_with_effective_date(self):
        store = {}
        cr.record_store(store, [ad_record("2026-10-06", "2026-09-01")], "2026-06-27")
        self.assertIn("AD:2026-10-06", store)
        self.assertEqual(store["AD:2026-10-06"]["effective_date"], "2026-09-01")

    def test_record_ignores_reference_without_effective_date(self):
        store = {}
        cr.record_store(store, [ad_record("2026-10-06", None)], "2026-06-27")
        self.assertEqual(store, {})

    def test_record_ignores_unverified_reference(self):
        store = {}
        rec = ad_record("2026-10-06", "2026-09-01", confidence="UNVERIFIED")
        cr.record_store(store, [rec], "2026-06-27")
        self.assertEqual(store, {})

    def test_record_upsert_overwrites_same_ad(self):
        store = {}
        cr.record_store(store, [ad_record("2026-10-06", "2026-09-01")], "2026-06-27")
        cr.record_store(store, [ad_record("2026-10-06", "2026-09-15")], "2026-07-04")
        self.assertEqual(store["AD:2026-10-06"]["effective_date"], "2026-09-15")


class TestSelect(unittest.TestCase):
    def setUp(self):
        self.store = {
            "AD:PAST": {"effective_date": "2026-01-01", "ref_type": "AD", "ref_number": "PAST",
                        "url": "https://www.govinfo.gov/past", "headline": "past"},
            "AD:SOON": {"effective_date": "2026-07-10", "ref_type": "AD", "ref_number": "SOON",
                        "url": "https://www.govinfo.gov/soon", "headline": "soon"},
            "AD:LATER": {"effective_date": "2026-08-20", "ref_type": "AD", "ref_number": "LATER",
                         "url": "https://www.govinfo.gov/later", "headline": "later"},
            "AD:FAR": {"effective_date": "2027-01-01", "ref_type": "AD", "ref_number": "FAR",
                       "url": "https://www.govinfo.gov/far", "headline": "far"},
        }

    def test_select_excludes_past_and_far(self):
        got = cr.select(self.store, "2026-06-27", forward_days=90, max_items=3)
        nums = [e["ref_number"] for e in got]
        self.assertEqual(nums, ["SOON", "LATER"])  # PAST excluded, FAR beyond 90d

    def test_select_sorts_ascending_by_effective_date(self):
        got = cr.select(self.store, "2026-06-27", forward_days=365, max_items=10)
        dates = [e["effective_date"] for e in got]
        self.assertEqual(dates, sorted(dates))

    def test_select_caps_at_max_items(self):
        got = cr.select(self.store, "2026-06-27", forward_days=365, max_items=1)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["ref_number"], "SOON")

    def test_select_includes_effective_today(self):
        got = cr.select(self.store, "2026-07-10", forward_days=90, max_items=3)
        self.assertIn("SOON", [e["ref_number"] for e in got])


class TestLoadSaveFailSafe(unittest.TestCase):
    def test_load_missing_file_returns_empty(self):
        self.assertEqual(cr.load_store("does/not/exist.json"), {})

    def test_load_malformed_returns_empty(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{ not json")
            path = f.name
        try:
            self.assertEqual(cr.load_store(path), {})
        finally:
            os.unlink(path)

    def test_save_then_load_roundtrip(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "_compliance.json")
        store = {"AD:X": {"effective_date": "2026-09-01", "ref_type": "AD",
                          "ref_number": "X", "url": "u", "headline": "h"}}
        cr.save_store(path, store, "2026-06-27")
        self.assertEqual(cr.load_store(path), store)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd tools && python -m unittest test_compliance_radar -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'compliance_radar'`

---

## Task 4: compliance_radar.py — implementation

**Files:**
- Create: `tools/compliance_radar.py`

- [ ] **Step 1: Write the implementation**

Create `tools/compliance_radar.py`:

```python
#!/usr/bin/env python3
"""compliance_radar.py — forward-looking AD effective-date radar for the digest.

Persists the calendar EFFECTIVE DATE of every VERIFIED AD/EAD the digest has
shown, then re-surfaces the ones whose effective date is coming up within a
forward window. This is the only "Standing Watch" block backed by a cross-run
store, because an AD reported weeks ago becomes interesting again as its
effective date approaches.

Two modes (mirrors tools/dedup_ledger.py):
  * build (default): read the store + current date, emit the upcoming entries
                     as { "radar": [...] } for the writer. Run BEFORE the writer.
  * --record       : upsert this run's VERIFIED AD/EAD references (those with a
                     non-null effective_date) into the store. Run AFTER the
                     digest is written, next to the dedup --record step.

Calendar effective dates only — flight-cycle compliance TIMES are not calendar
dates and are deliberately out of scope. Stdlib only.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta

STORE_VERSION = 1
RADAR_REF_TYPES = ("AD", "EAD")


def parse_scalar_from_yaml(text, key, default):
    """Read a top-level-ish integer scalar 'key: N' from fleet.yaml (no YAML lib)."""
    m = re.search(r"(?m)^\s*%s:\s*(\d+)" % re.escape(key), text)
    return int(m.group(1)) if m else default


def ref_id(reference):
    """Stable identity 'TYPE:NUMBER' (upper-cased), matching dedup_ledger's scheme."""
    rt = (reference.get("ref_type") or "other").strip().upper()
    num = re.sub(r"\s+", " ", (reference.get("ref_number") or "").strip()).upper()
    return "%s:%s" % (rt, num)


def record_store(store, records, current_date):
    """Upsert VERIFIED AD/EAD references that carry an effective_date. Mutates store."""
    for rec in records:
        for r in (rec.get("references") or []):
            if (r.get("ref_type") or "").upper() not in RADAR_REF_TYPES:
                continue
            if (r.get("confidence") or "").upper() != "VERIFIED":
                continue
            eff = (r.get("effective_date") or "").strip()
            if not eff:
                continue
            store[ref_id(r)] = {
                "effective_date": eff,
                "ref_type": (r.get("ref_type") or "").upper(),
                "ref_number": r.get("ref_number"),
                "url": r.get("primary_source_url"),
                "headline": rec.get("headline"),
                "recorded": current_date,
            }
    return store


def select(store, current_date, forward_days, max_items):
    """Entries whose effective_date is in [current_date, current_date+forward_days], soonest first."""
    today = date.fromisoformat(current_date)
    horizon = today + timedelta(days=forward_days)
    upcoming = []
    for entry in store.values():
        try:
            eff = date.fromisoformat(entry["effective_date"])
        except (ValueError, KeyError, TypeError):
            continue
        if today <= eff <= horizon:
            upcoming.append(entry)
    upcoming.sort(key=lambda e: e["effective_date"])
    return upcoming[:max_items]


def load_store(path):
    """The store map, or {} if missing/malformed (fail-safe)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("ads"), dict):
        return data["ads"]
    return {}


def save_store(path, store, current_date):
    """Atomically write the store (temp file + os.replace)."""
    payload = {"version": STORE_VERSION, "updated": current_date, "ads": store}
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


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Compliance Radar store + selector.")
    ap.add_argument("--store", default="runs/_compliance.json")
    ap.add_argument("--config", default="config/fleet.yaml")
    ap.add_argument("--current-date", required=True, help="Runtime date, YYYY-MM-DD.")
    ap.add_argument("--forward-days", type=int, default=None, help="Override standing_watch.forward_days.")
    ap.add_argument("--max-items", type=int, default=None, help="Override standing_watch.radar_max.")
    ap.add_argument("--infile", default="-", help="Records JSON path (record mode), or - for stdin.")
    ap.add_argument("--record", action="store_true",
                    help="Record mode: upsert this run's VERIFIED ADs into the store.")
    args = ap.parse_args(argv)

    cfg = ""
    try:
        cfg = open(args.config, encoding="utf-8").read()
    except OSError:
        pass
    forward_days = args.forward_days if args.forward_days is not None \
        else parse_scalar_from_yaml(cfg, "forward_days", 90)
    max_items = args.max_items if args.max_items is not None \
        else parse_scalar_from_yaml(cfg, "radar_max", 3)

    store = load_store(args.store)

    if args.record:
        raw = sys.stdin.read() if args.infile == "-" else open(args.infile, encoding="utf-8").read()
        payload = json.loads(raw)
        records = payload.get("records", []) if isinstance(payload, dict) else payload
        record_store(store, records, args.current_date)
        save_store(args.store, store, args.current_date)
        sys.stderr.write("[radar] store now holds %d AD(s)\n" % len(store))
        return 0

    radar = select(store, args.current_date, forward_days, max_items)
    sys.stdout.write(json.dumps({"radar": radar}, indent=2, ensure_ascii=False))
    sys.stderr.write("\n[radar] %d upcoming within %dd (showing <=%d)\n"
                     % (len(radar), forward_days, max_items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd tools && python -m unittest test_compliance_radar -v`
Expected: PASS (all tests)

- [ ] **Step 3: Commit**

```bash
git add tools/compliance_radar.py tools/test_compliance_radar.py
git commit -m "feat(radar): compliance_radar.py store + selector (effective-date radar)"
```

---

## Task 5: engineers_corner.json — seed the evergreen bank

**Files:**
- Create: `config/engineers_corner.json`

- [ ] **Step 1: Create the seeded bank**

Create `config/engineers_corner.json`. `topic_tags` are matched (case-insensitive) against the `types_affected` of VERIFIED items this run to ground the pick; entries with no match fall back to rotation. Bodies are author-written evergreen explainers (≤120 words), no live reference numbers.

```json
{
  "version": 1,
  "entries": [
    {
      "id": "trent-hpt-corrosion-fatigue",
      "title": "Why Trent HPT blades are a recurring corrosion-fatigue story",
      "topic_tags": ["Trent 700", "Trent XWB-84", "Trent XWB-97", "A330-300", "A350-900", "A350-1000"],
      "body": "High-pressure turbine blades live in the hottest, most oxidising part of the core. Protective coatings degrade with thermal cycling, and once the parent alloy is exposed, corrosion pits become crack initiation sites. Combine that with the cyclic loading of every flight and you get corrosion fatigue — the failure mode behind several Trent HPT directives. It is environment-dependent: blades on aircraft based in hot, humid, salt-laden climates age faster than a fleet-average assumption would predict, which is why service-life limits are increasingly tied to operating environment rather than cycles alone."
    },
    {
      "id": "why-ad-supersedes",
      "title": "What it means when an AD supersedes another",
      "topic_tags": [],
      "body": "A superseding AD does not just 'update' the old one — it withdraws and replaces it in full. The new directive restates the unsafe condition, then revises the compliance actions: often shorter thresholds, expanded effectivity, or a terminating modification that closes the repetitive inspection. Reading only the change summary is a trap; the legal requirement is the entire new AD. For planning, the key fields are the new effective date and whether prior compliance under the old AD counts toward the new one — sometimes it does, sometimes the clock restarts."
    },
    {
      "id": "leap-vs-gtf-architecture",
      "title": "LEAP-1A and PW1100G: two answers to the same efficiency question",
      "topic_tags": ["A321neo", "LEAP-1A", "PW1100G", "A320neo family"],
      "body": "Both engines chase propulsive efficiency through a higher bypass ratio, but by different routes. CFM's LEAP keeps a direct-drive fan and pushes the core — high pressure ratio, woven composite fan blades, ceramic-matrix-composite shroud segments in the turbine. Pratt's geared turbofan puts a reduction gearbox between fan and low-pressure spool, letting the fan turn slowly and the turbomachinery turn fast, each at its efficient speed. The trade-offs show up in the maintenance data: different hot-section behaviour, different in-service teething issues, and different powder-metal and bearing concerns over the type's life."
    },
    {
      "id": "ge90-115b-scale",
      "title": "The GE90-115B: living at the edge of fan-blade scale",
      "topic_tags": ["777-300ER", "GE90-115B"],
      "body": "The GE90-115B's fan is enormous, and its hollow titanium wide-chord blades were a structural milestone — large enough that fan-blade-off containment, bird-strike energy, and blade fatigue all sit at the leading edge of certification experience. That scale is why fan and containment-system service information on the type gets close attention: the loads and energies involved leave little margin, and inspection intervals reflect hard-won in-service knowledge rather than conservative guesses."
    }
  ]
}
```

- [ ] **Step 2: Verify it parses and entries are well-formed**

Run: `python -c "import json; d=json.load(open('config/engineers_corner.json')); assert all(set(e)>={'id','title','topic_tags','body'} for e in d['entries']); print('corner bank OK', len(d['entries']), 'entries')"`
Expected: `corner bank OK 4 entries`

- [ ] **Step 3: Commit**

```bash
git add config/engineers_corner.json
git commit -m "feat(corner): seed evergreen Engineer's Corner entry bank (JSON)"
```

---

## Task 6: engineers_corner.py — failing tests

**Files:**
- Create: `tools/test_engineers_corner.py`

- [ ] **Step 1: Write the failing tests**

Create `tools/test_engineers_corner.py`:

```python
#!/usr/bin/env python3
"""Tests for tools/engineers_corner.py — deterministic Engineer's Corner picker."""
import json
import os
import tempfile
import unittest

import engineers_corner as ec

BANK = [
    {"id": "trent", "title": "Trent", "topic_tags": ["Trent 700", "A330-300"], "body": "b1"},
    {"id": "generic", "title": "Generic", "topic_tags": [], "body": "b2"},
    {"id": "leap", "title": "Leap", "topic_tags": ["A321neo", "LEAP-1A"], "body": "b3"},
]


def rec(confidence, types):
    return {"item_confidence": confidence, "types_affected": types, "references": []}


class TestCoreCount(unittest.TestCase):
    def test_counts_only_verified_and_reported(self):
        records = [rec("VERIFIED", []), rec("REPORTED", []), rec("UNVERIFIED", [])]
        self.assertEqual(ec.count_core_items(records), 2)


class TestTrigger(unittest.TestCase):
    def test_suppressed_when_core_meets_threshold(self):
        records = [rec("VERIFIED", []) for _ in range(4)]
        chosen, idx = ec.select(BANK, records, {}, threshold=4)
        self.assertIsNone(chosen)
        self.assertIsNone(idx)

    def test_renders_when_core_below_threshold(self):
        records = [rec("VERIFIED", [])]
        chosen, idx = ec.select(BANK, records, {}, threshold=4)
        self.assertIsNotNone(chosen)


class TestGrounding(unittest.TestCase):
    def test_picks_entry_matching_a_verified_type(self):
        records = [rec("VERIFIED", ["A330-300"])]
        chosen, idx = ec.select(BANK, records, {}, threshold=4)
        self.assertEqual(chosen["id"], "trent")
        self.assertEqual(idx, 0)

    def test_grounding_ignores_unverified_types(self):
        records = [rec("UNVERIFIED", ["A330-300"])]   # not VERIFIED -> no grounding match
        chosen, idx = ec.select(BANK, records, {"last_index": 1}, threshold=4)
        self.assertEqual(chosen["id"], "leap")        # rotation: (1+1)%3 = 2

    def test_match_is_case_insensitive(self):
        records = [rec("VERIFIED", ["trent 700"])]
        chosen, _ = ec.select(BANK, records, {}, threshold=4)
        self.assertEqual(chosen["id"], "trent")


class TestRotationFallback(unittest.TestCase):
    def test_rotation_advances_from_last_index(self):
        records = [rec("VERIFIED", ["B777"])]          # no tag match
        chosen, idx = ec.select(BANK, records, {"last_index": 0}, threshold=4)
        self.assertEqual(idx, 1)
        self.assertEqual(chosen["id"], "generic")

    def test_rotation_wraps(self):
        records = [rec("VERIFIED", ["B777"])]
        chosen, idx = ec.select(BANK, records, {"last_index": 2}, threshold=4)
        self.assertEqual(idx, 0)

    def test_rotation_with_no_state_starts_at_zero(self):
        records = [rec("VERIFIED", ["B777"])]
        chosen, idx = ec.select(BANK, records, {}, threshold=4)
        self.assertEqual(idx, 0)


class TestLoadFailSafe(unittest.TestCase):
    def test_load_rotation_missing_returns_empty(self):
        self.assertEqual(ec.load_rotation("nope/nope.json"), {})

    def test_load_bank_roundtrip(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "bank.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": BANK}, f)
        self.assertEqual(ec.load_bank(path), BANK)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd tools && python -m unittest test_engineers_corner -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engineers_corner'`

---

## Task 7: engineers_corner.py — implementation

**Files:**
- Create: `tools/engineers_corner.py`

- [ ] **Step 1: Write the implementation**

Create `tools/engineers_corner.py`:

```python
#!/usr/bin/env python3
"""engineers_corner.py — deterministic picker for the Engineer's Corner block.

The Corner is the entertaining/educational block. To keep the project's
"never let an LLM invent facts" guarantee even here, the content is NOT
generated: it is author-curated evergreen entries in config/engineers_corner.json.
This tool only DECIDES which entry to show, and only when the week is thin.

Selection:
  * If substantive core items (VERIFIED or REPORTED) >= threshold -> show nothing.
  * Else GROUND the pick: choose the first entry whose topic_tags intersect the
    types_affected of a VERIFIED item this run (so the Corner relates to real news).
  * Else ROTATE: the next entry after the last shown (wrapping), so it varies.

Two modes (mirrors tools/dedup_ledger.py):
  * build (default): read deduped records + bank + rotation state, emit
                     { "corner": {entry} } or { "corner": null }. Run BEFORE the writer.
  * --record       : read the emitted 08_corner.json and persist its chosen_index
                     so the next run rotates onward. Run AFTER the digest is written.

Stdlib only.
"""
import argparse
import json
import os
import re
import sys
import tempfile

ROTATION_VERSION = 1


def parse_scalar_from_yaml(text, key, default):
    m = re.search(r"(?m)^\s*%s:\s*(\d+)" % re.escape(key), text)
    return int(m.group(1)) if m else default


def count_core_items(records):
    """Substantive core items = those with item_confidence VERIFIED or REPORTED."""
    return sum(1 for r in records
               if (r.get("item_confidence") or "").upper() in ("VERIFIED", "REPORTED"))


def grounded_types(records):
    """Lower-cased types_affected of VERIFIED records, for tag grounding."""
    out = set()
    for r in records:
        if (r.get("item_confidence") or "").upper() == "VERIFIED":
            for t in (r.get("types_affected") or []):
                out.add(t.strip().lower())
    return out


def select(bank, records, rotation, threshold):
    """Return (entry_or_None, chosen_index_or_None)."""
    if not bank:
        return None, None
    if count_core_items(records) >= threshold:
        return None, None

    types = grounded_types(records)
    for i, entry in enumerate(bank):
        tags = {t.strip().lower() for t in (entry.get("topic_tags") or [])}
        if tags & types:
            return entry, i

    last = rotation.get("last_index")
    nxt = 0 if not isinstance(last, int) else (last + 1) % len(bank)
    return bank[nxt], nxt


def load_bank(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return data["entries"]
    return []


def load_rotation(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_rotation(path, last_index, current_date):
    payload = {"version": ROTATION_VERSION, "updated": current_date, "last_index": last_index}
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


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Engineer's Corner deterministic picker.")
    ap.add_argument("--bank", default="config/engineers_corner.json")
    ap.add_argument("--rotation", default="runs/_corner.json")
    ap.add_argument("--config", default="config/fleet.yaml")
    ap.add_argument("--current-date", required=True, help="Runtime date, YYYY-MM-DD.")
    ap.add_argument("--threshold", type=int, default=None,
                    help="Override standing_watch.corner_min_core_items.")
    ap.add_argument("--infile", default="-",
                    help="Build: deduped records JSON / - for stdin. Record: the 08_corner.json.")
    ap.add_argument("--record", action="store_true",
                    help="Record mode: persist chosen_index from the emitted corner JSON.")
    args = ap.parse_args(argv)

    if args.record:
        raw = sys.stdin.read() if args.infile == "-" else open(args.infile, encoding="utf-8").read()
        emitted = json.loads(raw)
        idx = emitted.get("chosen_index")
        if isinstance(idx, int):
            save_rotation(args.rotation, idx, args.current_date)
            sys.stderr.write("[corner] rotation advanced to index %d\n" % idx)
        else:
            sys.stderr.write("[corner] nothing shown; rotation unchanged\n")
        return 0

    cfg = ""
    try:
        cfg = open(args.config, encoding="utf-8").read()
    except OSError:
        pass
    threshold = args.threshold if args.threshold is not None \
        else parse_scalar_from_yaml(cfg, "corner_min_core_items", 4)

    raw = sys.stdin.read() if args.infile == "-" else open(args.infile, encoding="utf-8").read()
    payload = json.loads(raw)
    records = payload.get("records", []) if isinstance(payload, dict) else payload

    bank = load_bank(args.bank)
    rotation = load_rotation(args.rotation)
    entry, idx = select(bank, records, rotation, threshold)

    out = {"corner": entry, "chosen_index": idx}
    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False))
    sys.stderr.write("\n[corner] %s\n" % ("suppressed (busy week)" if entry is None
                                          else "showing '%s'" % entry["id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd tools && python -m unittest test_engineers_corner -v`
Expected: PASS (all tests)

- [ ] **Step 3: Commit**

```bash
git add tools/engineers_corner.py tools/test_engineers_corner.py
git commit -m "feat(corner): engineers_corner.py deterministic picker (grounded -> rotation)"
```

---

## Task 8: finalize_digest.py — keep Standing Watch in section order

**Files:**
- Modify: `tools/finalize_digest.py:25-29` (the `SECTION_ORDER` list)
- Modify: `tools/test_finalize_digest.py` (add a test)

- [ ] **Step 1: Write the failing test**

Add to `tools/test_finalize_digest.py` (inside the existing test class, or as a new method — match the file's existing style):

```python
def test_standing_watch_ordered_after_core_before_sources(self):
    import finalize_digest as fd
    text = (
        "**Sources & Confidence:** 1 item.\n"
        "## Standing Watch\n\nradar stuff\n\n"
        "## Directly Fleet-Relevant\n\nan AD\n"
    )
    out = fd.finalize(text)
    i_fleet = out.index("## Directly Fleet-Relevant")
    i_watch = out.index("## Standing Watch")
    i_sources = out.index("**Sources & Confidence")
    self.assertLess(i_fleet, i_watch)      # core section before Standing Watch
    self.assertLess(i_watch, i_sources)    # Standing Watch before the Sources footer
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd tools && python -m unittest test_finalize_digest -v`
Expected: FAIL — Standing Watch sorts to the end (unknown header rank) so `i_watch > i_sources`... actually the Sources line is split out as footer; the failure is `i_fleet < i_watch` may pass but the ordering of Standing Watch among unknowns is undefined. Confirm a concrete failure before implementing.

- [ ] **Step 3: Add Standing Watch to SECTION_ORDER**

In `tools/finalize_digest.py`, change:

```python
SECTION_ORDER = [
    "## Directly Fleet-Relevant",
    "## Read-Across (Peer Types)",
    "## Major Industry Events",
]
```

to:

```python
SECTION_ORDER = [
    "## Directly Fleet-Relevant",
    "## Read-Across (Peer Types)",
    "## Major Industry Events",
    "## Standing Watch",
]
```

- [ ] **Step 4: Run the full finalize test file to verify it passes**

Run: `cd tools && python -m unittest test_finalize_digest -v`
Expected: PASS (all tests, including the new one)

- [ ] **Step 5: Commit**

```bash
git add tools/finalize_digest.py tools/test_finalize_digest.py
git commit -m "feat(finalize): order ## Standing Watch after core sections, before Sources"
```

---

## Task 9: Scanner — add NPRM/PAD discovery queries

**Files:**
- Modify: `.claude/agents/scanner.md` (the "What to search" list, ~line 20-26)

- [ ] **Step 1: Add a proposed-rules bullet to "What to search"**

In `.claude/agents/scanner.md`, in the `## What to search` list, after the "Each regulator's recent ADs/EADs" bullet, add:

```markdown
- **Proposed rules in the pipeline** (for the "On the Horizon" block): FAA NPRMs
  (Notices of Proposed Rulemaking) and EASA PADs (Proposed ADs) affecting any fleet
  or peer type. Capture the claimed docket/PAD number in `ref_number` and set
  `ref_type` to `NPRM` (FAA) or `PAD` (EASA). These are leads like any other —
  emit UNVERIFIED, with the discovery URL in `lead_sources`.
```

- [ ] **Step 2: Verify the file still reads cleanly (manual)**

Run: `python -c "t=open('.claude/agents/scanner.md',encoding='utf-8').read(); assert 'NPRM' in t and 'PAD' in t; print('scanner OK')"`
Expected: `scanner OK`

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/scanner.md
git commit -m "feat(scanner): add NPRM/PAD proposed-rule discovery queries"
```

---

## Task 10: Verifier — capture effective_date; tag NPRM/PAD

**Files:**
- Modify: `.claude/agents/verifier.md` (Process step 3, ~line 25; add a proposed-rules note)

- [ ] **Step 1: Add effective_date capture to Process step 3**

In `.claude/agents/verifier.md`, change Process step 3 from:

```markdown
3. **Confirm against the fetched text:** `ref_number`, `revision`, `ref_date`, `effectivity`.
   Correct any value that the scanner got wrong. If you cannot confirm a field, set it null.
```

to:

```markdown
3. **Confirm against the fetched text:** `ref_number`, `revision`, `ref_date`, `effectivity`,
   and — for an AD/EAD — `effective_date` (the calendar date the directive becomes effective,
   distinct from the publication `ref_date`; e.g. "effective June 12, 2026" → `2026-06-12`).
   Correct any value that the scanner got wrong. If you cannot confirm a field, set it null.
```

- [ ] **Step 2: Add a proposed-rules note after the Process list**

In `.claude/agents/verifier.md`, immediately after Process step 5 (before `## Confidence rules`), add:

```markdown
### Proposed rules (NPRM / PAD)
A proposed rule (FAA NPRM, EASA PAD) is verified exactly like an AD — fetch the primary
document on an allowlisted domain, confirm the docket/PAD number against the text, keep
`ref_type` as `NPRM` or `PAD`. It is forward-looking, not yet in force, so it has no
`effective_date` (leave null) and the writer renders it as `[PROPOSED — not yet final]`.
```

- [ ] **Step 3: Verify the file still reads cleanly (manual)**

Run: `python -c "t=open('.claude/agents/verifier.md',encoding='utf-8').read(); assert 'effective_date' in t and 'PROPOSED' in t; print('verifier OK')"`
Expected: `verifier OK`

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/verifier.md
git commit -m "feat(verifier): capture AD effective_date; handle NPRM/PAD proposed rules"
```

---

## Task 11: Writer — render the Standing Watch section

**Files:**
- Modify: `.claude/agents/writer.md` (inputs paragraph ~line 10-12; section grouping ~line 32-38; add a Standing Watch spec block; Horizon routing)

- [ ] **Step 1: Tell the writer about the two new input files**

In `.claude/agents/writer.md`, change the opening inputs paragraph from:

```markdown
You receive a path to a **sanitised records JSON file** (already passed through the Verifier and
the deterministic gate `validate_records.py`). Read it. Read `config/fleet.yaml` only to get the
operator and fleet names for the read-across lines. **You have no web/fetch tools by design.**
```

to:

```markdown
You receive a path to a **sanitised records JSON file** (already passed through the Verifier and
the deterministic gate `validate_records.py`). Read it. Read `config/fleet.yaml` only to get the
operator and fleet names for the read-across lines. You also receive two optional Standing-Watch
inputs in the same run directory — `07_radar.json` (`{ "radar": [...] }`) and `08_corner.json`
(`{ "corner": {...} | null }`); read each if present. **You have no web/fetch tools by design.**
```

- [ ] **Step 2: Add Horizon routing to the section grouping rules**

In `.claude/agents/writer.md`, in the `## Output format (Markdown)` section, after the three `##` section bullets, add:

```markdown
**On the Horizon routing:** a record whose references are ALL of `ref_type` `NPRM` or `PAD`
is a proposed rule, not an active directive. Do NOT place it in the three sections above —
render it under `### On the Horizon` inside `## Standing Watch` (see below), and tag each such
reference `[PROPOSED — not yet final]` instead of a confidence tag colour. All other records
group by `category` as usual.
```

- [ ] **Step 3: Add the Standing Watch spec block**

In `.claude/agents/writer.md`, immediately before the `## Closing line (mandatory)` section, insert:

```markdown
## Standing Watch (render AFTER the three core sections, BEFORE the Sources line)
Add a single `## Standing Watch` section, introduced by one line:
`*Forward-looking and background items — not this week's verified incident intelligence.*`
Render these sub-blocks **in this order**, omitting any sub-block that has no content:

### Compliance Radar
From `07_radar.json` `radar[]` (already date-filtered and capped upstream — render all of them,
in the given order). One bullet per entry:
`- **{ref_type} {ref_number}** becomes effective **{effective_date}** — {headline}. ([source]({url}))`
If `radar` is empty or the file is absent, omit this sub-block.

### On the Horizon
The proposed-rule (NPRM/PAD) records routed here in Step 2. One item each, same per-item shape as
a core item (headline, what happened, technical detail with `[source]` link), but tag the
reference `[PROPOSED — not yet final]`. Show at most `standing_watch.horizon_max` (default 3);
if more exist, render the 3 nearest-dated and note "(+N more proposed rules this period)". Omit
the sub-block if there are no NPRM/PAD records.

### Engineer's Corner
From `08_corner.json`. If `corner` is non-null, render:
`**{corner.title}**` then a blank line then `{corner.body}` verbatim. This is curated evergreen
background — render it exactly as given; never add a reference, number, or date to it. If `corner`
is null or the file is absent, omit this sub-block.

Render the entire `## Standing Watch` section only if at least one sub-block has content.
```

- [ ] **Step 4: Verify the file still reads cleanly (manual)**

Run: `python -c "t=open('.claude/agents/writer.md',encoding='utf-8').read(); assert 'Standing Watch' in t and 'Compliance Radar' in t and 'On the Horizon' in t and 'Engineer' in t; print('writer OK')"`
Expected: `writer OK`

- [ ] **Step 5: Commit**

```bash
git add .claude/agents/writer.md
git commit -m "feat(writer): render ## Standing Watch (radar, horizon, corner) + NPRM/PAD routing"
```

---

## Task 12: Orchestrator — wire build and record steps

**Files:**
- Modify: `.claude/commands/digest.md` (add Steps 4e/4f after 4d; update Step 5 writer inputs; add Steps 6d/6e after 6c; update Guardrails)

- [ ] **Step 1: Add Step 4e (build radar) and 4f (build corner) after Step 4d**

In `.claude/commands/digest.md`, immediately after the Step 4d block (ends before `## Step 5 — WRITE`), insert:

```markdown
## Step 4e — BUILD COMPLIANCE RADAR (Bash — deterministic, never skip)
Select upcoming AD effective dates from the persistent store for the Standing Watch section:
```
python tools/compliance_radar.py \
  --store runs/_compliance.json \
  --config config/fleet.yaml \
  --current-date $CURRENT_DATE \
  > runs/$CURRENT_DATE/07_radar.json \
  2> runs/$CURRENT_DATE/07_radar_report.txt
```
This is read-only on the store (it is written in Step 6d, after the digest succeeds). The store is
empty on first run, so `07_radar.json` is `{ "radar": [] }` until ADs with effective dates have been
recorded — that is expected.

## Step 4f — BUILD ENGINEER'S CORNER (Bash — deterministic, never skip)
Decide whether to show a curated Corner entry and which one (only when the week is thin):
```
python tools/engineers_corner.py \
  --bank config/engineers_corner.json \
  --rotation runs/_corner.json \
  --config config/fleet.yaml \
  --current-date $CURRENT_DATE \
  --infile runs/$CURRENT_DATE/06_deduped.json \
  > runs/$CURRENT_DATE/08_corner.json \
  2> runs/$CURRENT_DATE/08_corner_report.txt
```
The rotation pointer is read-only here (advanced in Step 6e, after the digest succeeds). A busy week
(≥ `standing_watch.corner_min_core_items` substantive core items) yields `{ "corner": null }`.
```

- [ ] **Step 2: Update Step 5 (writer inputs)**

In `.claude/commands/digest.md`, change the Step 5 prompt block from:

```markdown
> Render the digest from `runs/{CURRENT_DATE}/06_deduped.json` per your instructions.
> cadence = {cadence}. Read that file (and config/fleet.yaml for operator/fleet names).
> Return ONLY the finished Markdown.
```

to:

```markdown
> Render the digest from `runs/{CURRENT_DATE}/06_deduped.json` per your instructions.
> Also read the Standing Watch inputs `runs/{CURRENT_DATE}/07_radar.json` and
> `runs/{CURRENT_DATE}/08_corner.json` and render the `## Standing Watch` section per your spec.
> cadence = {cadence}. Read those files (and config/fleet.yaml for operator/fleet names).
> Return ONLY the finished Markdown.
```

- [ ] **Step 3: Add Step 6d (record radar store) and 6e (record corner rotation) after Step 6c**

In `.claude/commands/digest.md`, immediately after the Step 6c block (before `## Guardrails`), insert:

```markdown
## Step 6d — RECORD COMPLIANCE STORE (Bash — deterministic, never skip)
The digest exists, so persist this run's VERIFIED ADs (those with an effective_date) so the radar
can re-surface them as their effective date approaches. Record only here, after success:
```
python tools/compliance_radar.py --record \
  --store runs/_compliance.json \
  --current-date $CURRENT_DATE \
  --infile runs/$CURRENT_DATE/06_deduped.json
```
Atomic write; it only upserts AD/EAD references already shown as VERIFIED. It never touches the digest.

## Step 6e — RECORD CORNER ROTATION (Bash — deterministic, never skip)
Persist which Corner entry was shown so the next thin week rotates onward. Record only here:
```
python tools/engineers_corner.py --record \
  --rotation runs/_corner.json \
  --current-date $CURRENT_DATE \
  --infile runs/$CURRENT_DATE/08_corner.json
```
If the Corner was suppressed this week (`corner: null`), this is a no-op and the rotation is unchanged.
```

- [ ] **Step 4: Update Guardrails to cover the new steps**

In `.claude/commands/digest.md`, in the `## Guardrails (do not violate)` list, add these bullets:

```markdown
- The Standing Watch section is supplementary: never let it carry a reference, quote, or date that
  did not come from `07_radar.json` (itself built only from previously-VERIFIED ADs), `08_corner.json`
  (curated evergreen text), or a gate-passed NPRM/PAD record in `06_deduped.json`. The writer still
  has no fetch tools, so it cannot add one.
- Never skip Steps 4e/4f (build the Standing Watch inputs) or 6d/6e (record the store and rotation).
  Record (6d/6e) only after the digest is written, mirroring Steps 6b/6c.
```

- [ ] **Step 5: Verify the orchestrator references are consistent (manual)**

Run: `python -c "t=open('.claude/commands/digest.md',encoding='utf-8').read(); assert '07_radar.json' in t and '08_corner.json' in t and 'Step 6d' in t and 'Step 6e' in t; print('orchestrator OK')"`
Expected: `orchestrator OK`

- [ ] **Step 6: Commit**

```bash
git add .claude/commands/digest.md
git commit -m "feat(orchestrator): wire Standing Watch build (4e/4f) + record (6d/6e) steps"
```

---

## Task 13: Integration test — Standing Watch cannot smuggle an unverified reference

**Files:**
- Create: `tools/test_standing_watch_integration.py`

This proves the trust invariant from the spec (§10): the radar only emits previously-VERIFIED ADs, and the corner only emits curated content — neither can introduce an unverified reference.

- [ ] **Step 1: Write the test**

Create `tools/test_standing_watch_integration.py`:

```python
#!/usr/bin/env python3
"""Integration: Standing Watch blocks cannot introduce an unverified reference."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def run(tool, args, stdin=None):
    return subprocess.run([sys.executable, os.path.join(HERE, tool)] + args,
                          input=stdin, capture_output=True, text=True)


class TestRadarOnlyEmitsRecordedVerifiedADs(unittest.TestCase):
    def test_unverified_ad_never_reaches_radar(self):
        d = tempfile.mkdtemp()
        store = os.path.join(d, "_compliance.json")
        cfg = os.path.join(d, "fleet.yaml")
        open(cfg, "w").write("standing_watch:\n  forward_days: 90\n  radar_max: 3\n")

        # An UNVERIFIED AD with an effective date inside the window.
        records = {"records": [{
            "id": "x", "headline": "hostile", "category": "fleet",
            "types_affected": ["A330-300"], "item_confidence": "UNVERIFIED",
            "references": [{"ref_type": "AD", "ref_number": "FAKE-1",
                            "effective_date": "2026-07-01", "confidence": "UNVERIFIED",
                            "primary_source_url": "https://avherald.com/x"}],
        }]}
        # Record step must refuse to store it (not VERIFIED)...
        run("compliance_radar.py", ["--record", "--store", store,
            "--current-date", "2026-06-27", "--infile", "-"],
            stdin=json.dumps(records))
        # ...so the build step emits an empty radar.
        out = run("compliance_radar.py", ["--store", store, "--config", cfg,
                  "--current-date", "2026-06-27"])
        self.assertEqual(json.loads(out.stdout)["radar"], [])


class TestCornerEmitsOnlyCuratedContent(unittest.TestCase):
    def test_corner_entry_has_no_reference_fields(self):
        d = tempfile.mkdtemp()
        bank = os.path.join(d, "bank.json")
        json.dump({"version": 1, "entries": [
            {"id": "e", "title": "T", "topic_tags": [], "body": "evergreen"}]},
            open(bank, "w"))
        cfg = os.path.join(d, "fleet.yaml")
        open(cfg, "w").write("standing_watch:\n  corner_min_core_items: 4\n")
        thin = {"records": [{"item_confidence": "VERIFIED", "types_affected": [], "references": []}]}
        out = run("engineers_corner.py", ["--bank", bank, "--rotation",
                  os.path.join(d, "_corner.json"), "--config", cfg,
                  "--current-date", "2026-06-27", "--infile", "-"],
                  stdin=json.dumps(thin))
        corner = json.loads(out.stdout)["corner"]
        self.assertEqual(corner["body"], "evergreen")
        self.assertNotIn("references", corner)
        self.assertNotIn("ref_number", corner)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the integration test**

Run: `cd tools && python -m unittest test_standing_watch_integration -v`
Expected: PASS (both tests)

- [ ] **Step 3: Run the FULL test suite to confirm nothing regressed**

Run: `cd tools && python -m unittest discover -p "test_*.py" -v`
Expected: PASS — all prior tests (validate_records, compute_window, dedup_ledger, finalize_digest) plus the new radar, corner, and integration tests.

- [ ] **Step 4: Commit**

```bash
git add tools/test_standing_watch_integration.py
git commit -m "test(standing-watch): radar/corner cannot smuggle an unverified reference"
```

---

## Task 14: Docs — CLAUDE.md and roadmap.md

**Files:**
- Modify: `CLAUDE.md` (the "Never do" / output-format areas)
- Modify: `roadmap.md` (new stage + decisions log)

- [ ] **Step 1: Amend the no-padding rule in CLAUDE.md**

In `CLAUDE.md`, under the `## Never do` list, change the bullet:

```markdown
- Pad the digest with commercial/route/financial news to hit a length.
```

to:

```markdown
- Pad the digest with commercial/route/financial news to hit a length. (The `## Standing Watch`
  section is NOT padding: it carries forward-looking *primary-source* intelligence — Compliance
  Radar of upcoming AD effective dates and On-the-Horizon proposed rules — plus a clearly walled-off
  curated Engineer's Corner. None of it asserts an unverified reference; all of it is structurally
  separated from the verified incident intelligence above it.)
```

- [ ] **Step 2: Document the Standing Watch section in the Output format area of CLAUDE.md**

In `CLAUDE.md`, in the `## Output format` section, after the `End with a **Sources & Confidence** line …` paragraph, add:

```markdown
After the three core sections (and before the Sources line) a `## Standing Watch` section may
appear: **Compliance Radar** (upcoming AD effective dates, from `tools/compliance_radar.py`),
**On the Horizon** (NPRM/PAD proposed rules, gate-verified like any reference), and **Engineer's
Corner** (curated evergreen explainer from `config/engineers_corner.json`, shown only on thin
weeks). It guarantees a minimum of substantial reading without diluting the verified core.
```

- [ ] **Step 3: Add a roadmap stage and decisions-log rows**

In `roadmap.md`, after the `## Stage 5` block (before `## Decisions log`), add:

```markdown
## Stage 6 — Standing Watch content layer ✅ (filler / minimum-entry rule)

**Goal:** make thin weeks read as substantial (≥4 blocks) without diluting the verified core.
Spec: `docs/superpowers/specs/2026-06-26-standing-watch-content-layer-design.md`.
Plan: `docs/superpowers/plans/2026-06-27-standing-watch-content-layer.md`.

- [x] Schema: `NPRM`/`PAD` ref_type + reference `effective_date`
- [x] On the Horizon: scanner queries + verifier handling + writer routing (rides existing gates)
- [x] Compliance Radar: `tools/compliance_radar.py` + `runs/_compliance.json` store (test-first)
- [x] Engineer's Corner: `config/engineers_corner.json` bank + `tools/engineers_corner.py` picker (test-first)
- [x] Writer: `## Standing Watch` section (radar / horizon / corner)
- [x] `finalize_digest.py`: Standing Watch in enforced section order
- [x] Orchestrator: build Steps 4e/4f + record Steps 6d/6e
- [x] Integration test: Standing Watch cannot smuggle an unverified reference
```

And add to the `## Decisions log` table:

```markdown
| 2026-06-27 | Standing Watch content layer | Filler for thin weeks via forward-looking primary-source intel (Compliance Radar, On-the-Horizon NPRM/PAD) + a walled-off curated Engineer's Corner. On-the-Horizon rides the existing gates (NPRM/PAD added to ref_type enum; gate ignores ref_type). Corner is deterministic + author-curated (JSON bank) — no LLM-generated facts, preserving the verify-everything ethos even in the entertaining block |
```

- [ ] **Step 4: Verify docs are consistent (manual)**

Run: `python -c "assert 'Standing Watch' in open('CLAUDE.md',encoding='utf-8').read(); assert 'Stage 6' in open('roadmap.md',encoding='utf-8').read(); print('docs OK')"`
Expected: `docs OK`

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md roadmap.md
git commit -m "docs: document Standing Watch layer (CLAUDE.md + roadmap Stage 6)"
```

---

## Post-implementation: manual smoke (not a code task)

The writer and the agent-prompt changes (Tasks 9–11) are LLM-driven and cannot be unit-tested. After the code lands, run one live `/digest weekly` and confirm by eye:
- A thin week renders `## Standing Watch` with Compliance Radar (if the store has upcoming ADs), On the Horizon (if any NPRM/PAD verified), and Engineer's Corner.
- Section order survives `finalize_digest.py`: core sections → Standing Watch → Sources line.
- Every Compliance Radar / On-the-Horizon entry has a working `[source]` link.
- A busy week (≥4 core items) omits Engineer's Corner.

Record findings in `roadmap.md` Stage 6 and `SESSION_LOG.md`.

---

## Self-Review notes (author)

- **Spec coverage:** Radar (§4) → Tasks 1,3,4,12; On the Horizon (§5) → Tasks 1,9,10,11; Engineer's Corner (§6) → Tasks 5,6,7,11; trigger logic (§7) → Tasks 7 (corner threshold), 11 (always-on radar/horizon); placement/wall (§3) → Tasks 8,11; "no change to gates" (§8) → confirmed (gate ignores `ref_type`); touch points (§9) → all covered; success criteria (§10) → Task 13 + manual smoke.
- **Deviations from spec, by design:** corner bank is **JSON not YAML** (stdlib-only parse reliability); compliance store is a **separate `runs/_compliance.json`** rather than extending `_seen.json` (single responsibility; date-window selection vs identity dedup) — both were flagged as plan-time decisions in the spec.
- **Type consistency:** `record_store`/`select`/`load_store`/`save_store` (radar) and `count_core_items`/`grounded_types`/`select`/`load_bank`/`load_rotation`/`save_rotation` (corner) names match between tests and implementations; artifact names `07_radar.json`/`08_corner.json` consistent across writer, orchestrator, and tests; `{ "radar": [...] }` and `{ "corner": ..., "chosen_index": ... }` shapes consistent across tool output, writer spec, and record mode.
