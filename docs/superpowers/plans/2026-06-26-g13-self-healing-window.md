# G13 — Self-Healing Lookback Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the digest's lookback window self-healing — it grows to cover the gap since the last successful run of the same cadence (capped at a cold-start limit), so a first run and any skipped run never silently miss recent ADs.

**Architecture:** A new stdlib-only deterministic tool `tools/compute_window.py` computes `effective = min(cold_start_cap, max(nominal, gap))` from a per-cadence `runs/_state.json` marker, and (in `--record` mode) advances that marker after a successful run. The orchestrator (`.claude/commands/digest.md`) calls it in Step 1 to set the effective window used by every downstream stage, and in a new Step 6b to record the run. No LLM does the window math.

**Tech Stack:** Python 3 (stdlib only — `argparse`, `json`, `re`, `datetime`), `unittest`. Matches the existing `tools/validate_records.py` / `finalize_digest.py` conventions: regex YAML parsing, `sys.stdout/stderr.reconfigure("utf-8")`, tests run via `python tools/test_<name>.py` from the project root.

**Spec:** `docs/superpowers/specs/2026-06-26-g13-self-healing-window-design.md`

---

## Note on commits

This project is **not yet a git repository** (`git init` has never been run). Each task ends with a
**Checkpoint** step. If you run `git init` first, execute the shown `git` command; otherwise treat
the Checkpoint as a "stop, confirm tests pass, move on" marker and skip the git command. Do **not**
silently introduce git without asking the user.

## File Structure

- **Create** `tools/compute_window.py` — the whole feature. Pure rule function + state I/O + config
  parse + CLI (compute / record modes). One responsibility: decide and persist the lookback window.
- **Create** `tools/test_compute_window.py` — unit + CLI smoke tests for the above.
- **Modify** `config/fleet.yaml` — add `run.cold_start_lookback_days: 30`.
- **Modify** `.claude/commands/digest.md` — Step 1 computes the effective window; new Step 6b records the run.
- **Modify** `roadmap.md` — mark G13 resolved; add the decisions-log row.
- **Modify** `docs/superpowers/specs/2026-06-26-aviation-digest-spec.md` — flip G13 status to resolved in §11.

---

## Task 1: Pure window rule

**Files:**
- Create: `tools/compute_window.py`
- Test: `tools/test_compute_window.py`

- [ ] **Step 1: Write the failing tests**

Create `tools/test_compute_window.py`:

```python
"""Tests for compute_window.py — the self-healing lookback window.

Run: python tools/test_compute_window.py   (from project root)
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import compute_window as cw

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)


class TestRule(unittest.TestCase):
    def test_cold_start_returns_cap(self):
        self.assertEqual(cw.compute_effective_lookback(None, 7, 30), 30)

    def test_gap_below_nominal_returns_nominal(self):
        self.assertEqual(cw.compute_effective_lookback(2, 7, 30), 7)

    def test_gap_between_returns_gap(self):
        self.assertEqual(cw.compute_effective_lookback(14, 7, 30), 14)

    def test_gap_above_cap_returns_cap(self):
        self.assertEqual(cw.compute_effective_lookback(90, 7, 30), 30)

    def test_gap_equals_nominal_boundary(self):
        self.assertEqual(cw.compute_effective_lookback(7, 7, 30), 7)

    def test_gap_equals_cap_boundary(self):
        self.assertEqual(cw.compute_effective_lookback(30, 7, 30), 30)

    def test_negative_gap_returns_nominal(self):
        self.assertEqual(cw.compute_effective_lookback(-3, 7, 30), 7)

    def test_cap_below_nominal_never_undercovers(self):
        # misconfigured cap < nominal must not produce a window narrower than nominal
        self.assertEqual(cw.compute_effective_lookback(None, 7, 3), 7)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python tools/test_compute_window.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'compute_window'`.

- [ ] **Step 3: Create the module with the rule function**

Create `tools/compute_window.py`:

```python
#!/usr/bin/env python3
"""compute_window.py — self-healing lookback window for the digest pipeline.

Rule:  effective = min(cold_start_cap, max(nominal, gap))
where gap = whole days since the last run of THIS cadence, or None on a cold
start (treated as 'long ago', clamped to the cap).

See docs/superpowers/specs/2026-06-26-g13-self-healing-window-design.md
Stdlib only.
"""
import argparse
import json
import re
import sys
from datetime import date


def compute_effective_lookback(gap_or_none, nominal, cold_start_cap):
    """The deterministic window rule.

    gap_or_none: whole days since the last run of this cadence, or None on cold start.
    Returns a window never narrower than `nominal` and never wider than the cap.
    A cap accidentally configured below nominal is raised to nominal so we never
    under-cover.
    """
    cap = max(cold_start_cap, nominal)
    if gap_or_none is None:
        return cap
    return min(cap, max(nominal, gap_or_none))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python tools/test_compute_window.py`
Expected: PASS (8 tests in `TestRule`).

- [ ] **Step 5: Checkpoint**

```bash
git add tools/compute_window.py tools/test_compute_window.py
git commit -m "feat(window): add self-healing lookback rule function"
```

---

## Task 2: State I/O and config parsing

**Files:**
- Modify: `tools/compute_window.py`
- Test: `tools/test_compute_window.py`

- [ ] **Step 1: Write the failing tests**

Add these classes to `tools/test_compute_window.py` (above the `if __name__` block):

```python
class TestConfigParse(unittest.TestCase):
    def test_parses_cold_start(self):
        text = "run:\n  cadence: weekly\n  cold_start_lookback_days: 30\n"
        self.assertEqual(cw.parse_cold_start_from_yaml(text), 30)

    def test_default_when_absent(self):
        self.assertEqual(cw.parse_cold_start_from_yaml("run:\n  cadence: weekly\n"), 30)


class TestStateIO(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_missing_file_is_cold_start(self):
        self.assertEqual(cw.load_state(os.path.join(PROJECT_ROOT, "does", "not", "exist.json")), {})

    def test_malformed_json_is_cold_start(self):
        path = self._write("{ not valid json")
        try:
            self.assertEqual(cw.load_state(path), {})
        finally:
            os.remove(path)

    def test_last_run_date_per_cadence(self):
        state = {"weekly": {"last_run_date": "2026-06-19"},
                 "daily": {"last_run_date": "2026-06-25"}}
        self.assertEqual(cw.last_run_date(state, "weekly"), "2026-06-19")
        self.assertEqual(cw.last_run_date(state, "daily"), "2026-06-25")
        self.assertIsNone(cw.last_run_date(state, "monthly"))

    def test_gap_days(self):
        self.assertEqual(cw.gap_days("2026-06-19", "2026-06-26"), 7)
        self.assertIsNone(cw.gap_days(None, "2026-06-26"))
        self.assertIsNone(cw.gap_days("not-a-date", "2026-06-26"))

    def test_record_preserves_other_bucket(self):
        path = self._write(json.dumps({"daily": {"last_run_date": "2026-06-25"}}))
        try:
            cw.record_run(path, "weekly", "2026-06-26")
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(state["weekly"]["last_run_date"], "2026-06-26")
            self.assertEqual(state["daily"]["last_run_date"], "2026-06-25")
        finally:
            os.remove(path)

    def test_record_creates_file_when_absent(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        try:
            cw.record_run(path, "weekly", "2026-06-26")
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(state["weekly"]["last_run_date"], "2026-06-26")
        finally:
            if os.path.exists(path):
                os.remove(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python tools/test_compute_window.py`
Expected: FAIL — `AttributeError: module 'compute_window' has no attribute 'parse_cold_start_from_yaml'`.

- [ ] **Step 3: Add the state + config functions**

In `tools/compute_window.py`, add after `compute_effective_lookback`:

```python
def parse_cold_start_from_yaml(text, default=30):
    """Extract run.cold_start_lookback_days from fleet.yaml without a YAML lib."""
    m = re.search(r"^\s+cold_start_lookback_days:\s*(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else default


def load_state(path):
    """Parsed state dict, or {} if the file is missing or malformed (fail-safe: cold start)."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def last_run_date(state, cadence):
    """The last_run_date string for a cadence bucket, or None."""
    bucket = state.get(cadence)
    return bucket.get("last_run_date") if isinstance(bucket, dict) else None


def gap_days(last_run, current_date):
    """Whole days from last_run to current_date, or None if either is missing/unparseable."""
    if not last_run:
        return None
    try:
        return (date.fromisoformat(current_date) - date.fromisoformat(last_run)).days
    except (ValueError, TypeError):
        return None


def record_run(path, cadence, current_date):
    """Set state[cadence]['last_run_date'] = current_date, preserving other buckets."""
    state = load_state(path)
    bucket = state.get(cadence)
    if not isinstance(bucket, dict):
        bucket = {}
    bucket["last_run_date"] = current_date
    state[cadence] = bucket
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, indent=2, ensure_ascii=False))
    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python tools/test_compute_window.py`
Expected: PASS (all of `TestRule`, `TestConfigParse`, `TestStateIO`).

- [ ] **Step 5: Checkpoint**

```bash
git add tools/compute_window.py tools/test_compute_window.py
git commit -m "feat(window): add per-cadence state I/O and cold-start config parsing"
```

---

## Task 3: CLI (compute and record modes)

**Files:**
- Modify: `tools/compute_window.py`
- Test: `tools/test_compute_window.py`

- [ ] **Step 1: Write the failing tests**

Add this class to `tools/test_compute_window.py` (above the `if __name__` block):

```python
class TestCLI(unittest.TestCase):
    def _run(self, args):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "compute_window.py")] + args,
            capture_output=True, text=True, cwd=PROJECT_ROOT)

    def _tmp_state_path(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)  # start absent
        return path

    def test_compute_cold_start_prints_cap(self):
        path = self._tmp_state_path()
        try:
            r = self._run(["--config", "config/fleet.yaml", "--current-date", "2026-06-26",
                           "--cadence", "weekly", "--nominal-lookback-days", "7", "--state", path])
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), "30")        # clean integer on stdout
            self.assertIn("cold start", r.stderr)            # explanation on stderr
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_record_then_compute_steady_state(self):
        path = self._tmp_state_path()
        try:
            rec = self._run(["--record", "--state", path, "--current-date", "2026-06-26",
                             "--cadence", "weekly"])
            self.assertEqual(rec.returncode, 0)
            comp = self._run(["--config", "config/fleet.yaml", "--current-date", "2026-07-03",
                              "--cadence", "weekly", "--nominal-lookback-days", "7", "--state", path])
            self.assertEqual(comp.stdout.strip(), "7")       # 7-day gap -> nominal
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_record_then_compute_healed(self):
        path = self._tmp_state_path()
        try:
            self._run(["--record", "--state", path, "--current-date", "2026-06-12",
                       "--cadence", "weekly"])
            comp = self._run(["--config", "config/fleet.yaml", "--current-date", "2026-06-26",
                              "--cadence", "weekly", "--nominal-lookback-days", "7", "--state", path])
            self.assertEqual(comp.stdout.strip(), "14")      # 14-day gap -> healed to 14
            self.assertIn("healed", comp.stderr)
        finally:
            if os.path.exists(path):
                os.remove(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python tools/test_compute_window.py`
Expected: FAIL — the subprocess exits non-zero / prints an argparse error because `main` and the
`--record` / `--nominal-lookback-days` flags do not exist yet.

- [ ] **Step 3: Add the CLI**

In `tools/compute_window.py`, add after `record_run`:

```python
def explain(gap, effective, nominal):
    if gap is None:
        return "cold start -> %dd" % effective
    if effective > nominal:
        return "%dd gap -> healed to %dd" % (gap, effective)
    return "steady state -> %dd" % effective


def main(argv=None):
    # Force UTF-8 so a redirected stdout/stderr on Windows (cp1252 by default)
    # never corrupts non-ASCII output — same guard as validate_records.py.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Self-healing lookback window for the digest pipeline.")
    ap.add_argument("--config", default="config/fleet.yaml",
                    help="Path to fleet.yaml (for run.cold_start_lookback_days).")
    ap.add_argument("--current-date", required=True, help="Runtime date, YYYY-MM-DD.")
    ap.add_argument("--cadence", default="weekly",
                    help="weekly | daily — selects the per-cadence state bucket.")
    ap.add_argument("--nominal-lookback-days", type=int, default=7,
                    help="The cadence's nominal window; the effective window is never narrower.")
    ap.add_argument("--state", default="runs/_state.json",
                    help="Per-cadence last_run_date marker.")
    ap.add_argument("--record", action="store_true",
                    help="Record mode: set this cadence's last_run_date = current_date. "
                         "Run ONLY after a digest has been produced.")
    args = ap.parse_args(argv)

    if args.record:
        record_run(args.state, args.cadence, args.current_date)
        sys.stderr.write("[window] recorded %s last_run_date=%s\n"
                         % (args.cadence, args.current_date))
        return 0

    with open(args.config, encoding="utf-8") as f:
        cold_start = parse_cold_start_from_yaml(f.read())

    state = load_state(args.state)
    gap = gap_days(last_run_date(state, args.cadence), args.current_date)
    effective = compute_effective_lookback(gap, args.nominal_lookback_days, cold_start)

    sys.stdout.write(str(effective))  # clean integer, no newline — orchestrator captures it
    sys.stderr.write("[window] %s\n" % explain(gap, effective, args.nominal_lookback_days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python tools/test_compute_window.py`
Expected: PASS (all classes, including `TestCLI`).

- [ ] **Step 5: Checkpoint**

```bash
git add tools/compute_window.py tools/test_compute_window.py
git commit -m "feat(window): add compute/record CLI to compute_window"
```

---

## Task 4: Config — add `cold_start_lookback_days`

**Files:**
- Modify: `config/fleet.yaml` (the `run:` block, around lines 98-102)
- Test: `tools/test_compute_window.py`

- [ ] **Step 1: Write the failing test**

Add this class to `tools/test_compute_window.py` (above the `if __name__` block):

```python
class TestRealConfig(unittest.TestCase):
    def test_real_config_declares_cold_start(self):
        with open(os.path.join(PROJECT_ROOT, "config", "fleet.yaml"), encoding="utf-8") as f:
            text = f.read()
        # parse must find a real value, not fall back to the default-by-absence path
        self.assertRegex(text, r"^\s+cold_start_lookback_days:\s*\d+", )
        self.assertEqual(cw.parse_cold_start_from_yaml(text), 30)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/test_compute_window.py`
Expected: FAIL — `AssertionError: Regex didn't match` (config has no such key yet).

- [ ] **Step 3: Add the config key**

In `config/fleet.yaml`, change the `run:` block from:

```yaml
run:
  cadence: weekly                  # weekly | daily
  lookback_days: 7                 # 7 for weekly, 1 for daily
  target_words: 1000               # weekly target; brevity over completeness
  carry_developing_events: true    # older still-developing events allowed if flagged, never as "new"
```

to:

```yaml
run:
  cadence: weekly                  # weekly | daily
  lookback_days: 7                 # 7 for weekly, 1 for daily (the nominal/floor window)
  cold_start_lookback_days: 30     # first-run + skipped-run cap (days); self-healing window upper bound
  target_words: 1000               # weekly target; brevity over completeness
  carry_developing_events: true    # older still-developing events allowed if flagged, never as "new"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tools/test_compute_window.py`
Expected: PASS (including `TestRealConfig`).

- [ ] **Step 5: Checkpoint**

```bash
git add config/fleet.yaml tools/test_compute_window.py
git commit -m "feat(window): declare cold_start_lookback_days in fleet.yaml"
```

---

## Task 5: Wire the orchestrator (`digest.md`)

**Files:**
- Modify: `.claude/commands/digest.md` (Step 1, ~lines 30-34; new Step 6b after Step 6)

This is a prompt/instruction file — no unit test. Verify by reading the result and running the
manual smoke commands in Step 4 below.

- [ ] **Step 1: Replace Step 1 with the effective-window version**

In `.claude/commands/digest.md`, replace the existing `## Step 1 — Runtime parameters (Bash)` block:

```markdown
## Step 1 — Runtime parameters (Bash)
- Compute today's date: `date +%F` → call this `CURRENT_DATE`. **Always compute at runtime; never hardcode.**
- Set `LOOKBACK_DAYS`: from `config/fleet.yaml` `run.lookback_days` (7 for weekly, 1 for daily) — override if the cadence arg differs.
- Create the run directory: `mkdir -p runs/$CURRENT_DATE digests`.
- Tell the user the run parameters before proceeding.
```

with:

```markdown
## Step 1 — Runtime parameters (Bash)
- Compute today's date: `date +%F` → call this `CURRENT_DATE`. **Always compute at runtime; never hardcode.**
- Set `NOMINAL_LOOKBACK`: from `config/fleet.yaml` `run.lookback_days` (7 for weekly, 1 for daily) — override if the cadence arg differs.
- Create the run directory: `mkdir -p runs/$CURRENT_DATE digests`.
- **Compute the self-healing window (deterministic, never skip).** The lookback grows to cover the
  gap since the last successful run of this cadence (capped by `run.cold_start_lookback_days`), so a
  first run or a skipped run never silently misses recent items:
  ```
  LOOKBACK_DAYS=$(python tools/compute_window.py \
    --config config/fleet.yaml \
    --current-date $CURRENT_DATE \
    --cadence {cadence} \
    --nominal-lookback-days $NOMINAL_LOOKBACK \
    --state runs/_state.json)
  ```
  The tool prints the effective integer to stdout and a one-line reason to stderr
  (`cold start -> 30d` / `14d gap -> healed to 14d` / `steady state -> 7d`).
- Use `LOOKBACK_DAYS` (the **effective** window) for every downstream step — the scanner prompt, the
  verifier prompt, and both gates' `--lookback-days`.
- Tell the user the run parameters before proceeding: `CURRENT_DATE`, cadence, `NOMINAL_LOOKBACK` vs
  the effective `LOOKBACK_DAYS`, and the reason line from the tool's stderr.
```

- [ ] **Step 2: Add Step 6b (record the run) after Step 6**

In `.claude/commands/digest.md`, immediately after the `## Step 6 — Report to the user` block,
insert:

```markdown
## Step 6b — Record the run (Bash — deterministic, never skip)
The digest now exists, so advance the per-cadence run marker. This makes the *next* run's window
self-heal (it measures the gap since this run). Record only here, after a successful digest — never
earlier, so a failed or empty run does not move the marker and cause the next run to under-cover:
```
python tools/compute_window.py --record \
  --state runs/_state.json \
  --current-date $CURRENT_DATE \
  --cadence {cadence}
```
This writes `runs/_state.json` (`{cadence}.last_run_date = $CURRENT_DATE`), preserving the other
cadence's marker. It records the run only; it never touches the digest.
```

- [ ] **Step 3: Add Step 6b to the guardrails note**

In `.claude/commands/digest.md`, under `## Guardrails (do not violate)`, change the line:

```markdown
- Never skip Step 4 or Step 4c. The two deterministic gates are the point.
```

to:

```markdown
- Never skip Step 4 or Step 4c (the two deterministic gates) or Step 6b (the run marker that makes
  the next window self-heal). Record the marker (6b) only after the digest is written.
```

- [ ] **Step 4: Manually verify the wiring end-to-end (Bash)**

Run these from the project root to confirm the commands the orchestrator now issues behave:

```bash
# cold start (use a throwaway state path so runs/_state.json is untouched)
python tools/compute_window.py --config config/fleet.yaml --current-date 2026-06-26 \
  --cadence weekly --nominal-lookback-days 7 --state /tmp/_wtest.json   # stdout: 30

# record, then a 7-day-later run -> steady state
python tools/compute_window.py --record --state /tmp/_wtest.json --current-date 2026-06-26 --cadence weekly
python tools/compute_window.py --config config/fleet.yaml --current-date 2026-07-03 \
  --cadence weekly --nominal-lookback-days 7 --state /tmp/_wtest.json    # stdout: 7

# a 14-day-later run -> healed to 14
python tools/compute_window.py --config config/fleet.yaml --current-date 2026-07-10 \
  --cadence weekly --nominal-lookback-days 7 --state /tmp/_wtest.json    # stdout: 14
rm -f /tmp/_wtest.json
```

Expected stdout values: `30`, then `7`, then `14`. (On Windows Git Bash `/tmp` resolves fine; or use
a path under the scratchpad dir.)

- [ ] **Step 5: Checkpoint**

```bash
git add .claude/commands/digest.md
git commit -m "feat(window): orchestrator computes self-healing window and records the run (Step 6b)"
```

---

## Task 6: Update roadmap and spec status

**Files:**
- Modify: `roadmap.md` (Stage 5 G13 line ~127; Open questions ~152-154; Decisions log ~149)
- Modify: `docs/superpowers/specs/2026-06-26-aviation-digest-spec.md` (§11 G13 row ~199)

- [ ] **Step 1: Mark G13 done in the Stage 5 checklist**

In `roadmap.md`, change:

```markdown
- [ ] Window/cold-start decision (G13) — one-time wider first-run lookback vs. accept steady-state cadence
```

to:

```markdown
- [x] Window/cold-start decision (G13) RESOLVED: self-healing window via `tools/compute_window.py`
      (`effective = min(cold_start_cap, max(nominal, gap))`, per-cadence `runs/_state.json`, cap 30d).
      Orchestrator Step 1 computes it; Step 6b records the run on success. Spec + plan in
      `docs/superpowers/{specs,plans}/2026-06-26-g13-self-healing-window*.md`. G4 dedup still open.
```

- [ ] **Step 2: Remove G13 from Open questions**

In `roadmap.md`, delete the `**G13 — cold-start / window policy:**` bullet (the 3 lines under
`## Open questions`). Leave `G4` and `Output density` in place.

- [ ] **Step 3: Add the decisions-log row**

In `roadmap.md`, append to the Decisions log table (after the last row):

```markdown
| 2026-06-26 | G13 — self-healing lookback window | Effective window = `min(cold_start_cap, max(nominal, gap_since_last_run))`; per-cadence state in `runs/_state.json`; cold-start/cap default 30d. New deterministic tool `tools/compute_window.py`; orchestrator records the run marker only on success (Step 6b). Fixes cold-start + skipped-run coverage; structural over manual override. G4 dedup stays open. |
```

- [ ] **Step 4: Flip the G13 row in the spec**

In `docs/superpowers/specs/2026-06-26-aviation-digest-spec.md` §11, change the **G13** row's status
cell from:

```markdown
| **G13** | **Cold-start window.** ... | **OPEN** (documented, not changed). Options: a one-time wider lookback for the first run, or accept that steady-state weekly cadence self-covers. Ties to **G4** (no cross-run memory). |
```

so the final cell reads:

```markdown
... | **RESOLVED** 2026-06-26: self-healing window — `tools/compute_window.py` sets `effective = min(cold_start_cap, max(nominal, gap_since_last_run))` (per-cadence `runs/_state.json`, cap 30d); orchestrator Step 1 computes it, Step 6b records the run. G4 dedup half still open. |
```

- [ ] **Step 5: Verify the full test suite still passes**

Run: `python tools/test_compute_window.py` and `python tools/test_validate_records.py`
Expected: PASS for both (the window change touches no `validate_records` behaviour).

- [ ] **Step 6: Checkpoint**

```bash
git add roadmap.md docs/superpowers/specs/2026-06-26-aviation-digest-spec.md
git commit -m "docs(window): mark G13 resolved in roadmap and spec"
```

---

## Self-Review

**Spec coverage:**
- §3 core rule → Task 1 (`compute_effective_lookback`, all boundary tests).
- §4.1 tool, compute + record modes → Tasks 2-3.
- §4.2 state file, per-cadence buckets, fail-safe wide → Task 2 (`load_state`, `last_run_date`, `record_run`, malformed→{}).
- §4.3 config `cold_start_lookback_days` → Task 4.
- §4.4 orchestrator Step 1 + Step 6b → Task 5.
- §6 testing (pure rule, state I/O, CLI smoke) → Tasks 1-3.
- §7 decisions-log + status → Task 6.
- Out-of-scope (G4 dedup) → not implemented, called out in Task 6 notes. ✅ no gaps.

**Placeholder scan:** none — every code/test step shows complete content; the only "TODO"-style
items are the intentional Checkpoint git-or-skip note (project isn't a git repo).

**Type/name consistency:** `compute_effective_lookback(gap_or_none, nominal, cold_start_cap)`,
`parse_cold_start_from_yaml`, `load_state`, `last_run_date`, `gap_days`, `record_run`, `explain`,
`main` — names match across the module, the tests, and the orchestrator CLI flags
(`--nominal-lookback-days`, `--cadence`, `--state`, `--record`). State shape
`{cadence: {last_run_date: ...}}` is consistent in Task 2 tests, `record_run`, and the orchestrator.
```
