# G13 — Self-Healing Lookback Window — Design

**Status:** Approved (2026-06-26). Ready for implementation plan.
**Owner:** Joseph (solo founder)
**Resolves:** Roadmap open question **G13** (cold-start / window policy). Partially overlaps **G4**
(closes the *window-coverage* half; leaves the *dedup* half open).
**Source of truth for scope:** `config/fleet.yaml`. **Source of truth for contract:** `CLAUDE.md`.

---

## 1. Problem

The pipeline applies a strict fixed lookback window (`run.lookback_days`, 7 for weekly). On a
**first run** there is no prior week to have caught items published 8–14 days earlier, so genuinely
relevant, recent ADs are dropped. Observed live (spec §11, G13): three confirmable CX-fleet ADs
(Trent 700 AD 2026-10-06; A350 actuators AD 2026-12-05; A350 O₂-clamp AD 2026-09-14, published
Jun 12–18) fell just outside the Jun 19–26 window and were dropped.

The same gap reappears whenever a scheduled run is **skipped** (holiday, outage): the next run's
7-day window leaves a hole for the missed days.

The window mechanism itself needs no new plumbing — `lookback_days` already flows cleanly from
config → orchestrator → scanner/verifier prompts + the gate's `--lookback-days`. This is a
**policy** change, not a plumbing change.

## 2. Decision

Make the effective lookback **self-healing**: it grows to cover the gap since the last successful
run of the same cadence, bounded by a cold-start cap. Chosen over a do-nothing policy (A) and a
manual per-run override (B) because it matches the project's governing principle —
**structural over instructed**: the window heals itself rather than depending on the operator
passing the right number each time. (Roadmap decision recorded 2026-06-26.)

## 3. Core rule (deterministic)

```
gap        = (current_date − last_run_date).days        # for THIS cadence; None if no state
effective  = min(cold_start_lookback_days,
                 max(nominal_lookback_days, gap_or_cap))
```

where `gap_or_cap = gap` when state exists, else `cold_start_lookback_days` (cold start behaves as
"last run was long ago", clamped by the cap).

Behaviour:

| Situation | last_run | gap | effective (nominal 7, cap 30) |
|-----------|----------|-----|-------------------------------|
| Cold start | none | — | **30** |
| Steady state | 7 days ago | 7 | **7** (unchanged) |
| Same-day re-run | today | 0 | **7** (`max` floor) |
| Skipped a week | 14 days ago | 14 | **14** (self-heals) |
| Long dormancy | 90 days ago | 90 | **30** (cap guard) |
| Clock skew | future date | <0 | **7** (`max` floor) |

Two invariants:
- `max(nominal, …)` → the effective window is **never narrower than the cadence's nominal window**.
  We never under-cover.
- `min(cold_start_cap, …)` → a long dormancy can't trigger a runaway scan.

## 4. Components

### 4.1 New tool — `tools/compute_window.py`

Single-purpose, deterministic, unit-tested — the LLM does no window math (same pattern as
`validate_records.py` / `finalize_digest.py`).

**Compute mode (read-only, default):**
```
python tools/compute_window.py \
  --config config/fleet.yaml \
  --current-date YYYY-MM-DD \
  --cadence weekly \
  --nominal-lookback-days 7 \
  [--state runs/_state.json]
```
- Reads `run.cold_start_lookback_days` from config and `<cadence>.last_run_date` from the state
  file. Prints the **effective lookback integer** to stdout (nothing else on stdout).
- A human-readable explanation line ("cold start → 30d" / "14d gap → healed to 14d" /
  "steady state → 7d") goes to **stderr**, so stdout stays a clean integer the orchestrator can
  capture.

**Record mode (writes state):**
```
python tools/compute_window.py --record \
  --state runs/_state.json \
  --current-date YYYY-MM-DD \
  --cadence weekly
```
- Sets `<cadence>.last_run_date = current_date`, preserving the other cadence's bucket and any
  unrelated keys. Creates the file if absent. Called **only after a digest is successfully
  produced** (orchestrator Step 6b).

Internals factored as a pure function for testing:
`compute_effective_lookback(gap_or_none, nominal, cold_start_cap) -> int`.

### 4.2 State file — `runs/_state.json`

Per-cadence buckets so weekly heals against the last *weekly* run and daily against the last
*daily* run (avoids a "daily" digest unexpectedly spanning a week because the previous run was a
weekly one):

```json
{
  "weekly": { "last_run_date": "2026-06-26" },
  "daily":  { "last_run_date": "2026-06-25" }
}
```

Only `last_run_date` is consumed. The `_` prefix mirrors the `runs/_seen.json` name reserved for
G4's dedup ledger; both sit alongside the dated run directories, not inside one.

**Fail-safe direction:** a missing or malformed state file → treated as **cold start (wide)**.
Over-covering can at worst re-surface an item (bounded; that is G4's dedup concern, out of scope
here); under-covering would silently miss a real AD. We bias to never-miss.

### 4.3 Config addition — `config/fleet.yaml` `run:`

```yaml
cold_start_lookback_days: 30   # first-run + skipped-run cap (days); tunable
```
`lookback_days: 7` remains the weekly nominal/floor; daily nominal stays 1 (mapped by the
orchestrator, as today). The new tool reads only `cold_start_lookback_days` from config; the
nominal is passed in via `--nominal-lookback-days` so the cadence→nominal mapping stays in one
place (the orchestrator).

### 4.4 Orchestrator wiring — `.claude/commands/digest.md`

- **Step 1** — after computing `CURRENT_DATE` and the nominal `LOOKBACK_DAYS`, call
  `compute_window.py` (compute mode) → `EFFECTIVE_LOOKBACK`. Use `EFFECTIVE_LOOKBACK` (not the
  nominal) everywhere downstream: scanner prompt, verifier prompt, both gates' `--lookback-days`.
  Report to the user: nominal vs effective and the reason line from stderr.
- **New Step 6b — record run (Bash, deterministic, never skip)** — after the digest file exists,
  call `compute_window.py --record` to advance the marker for this cadence. Placed after the digest
  is written so a failed or empty run never advances the marker.

## 5. Out of scope

- **G4 dedup half** — remembering which ref-numbers/event-ids were already reported, to suppress
  re-surfacing across runs. This design fixes window **coverage**, not re-surfacing. `runs/_state.json`
  leaves room to add a `seen` ledger later under the same file or `runs/_seen.json`.
- **Daily target word count (G9)** and other unrelated gaps.

## 6. Testing (TDD)

**Pure `compute_effective_lookback(gap_or_none, nominal, cap)`:**
- cold start (`gap_or_none = None`) → `cap`
- `gap < nominal` → `nominal`
- `nominal < gap < cap` → `gap`
- `gap > cap` → `cap`
- boundaries: `gap == nominal` → `nominal`; `gap == cap` → `cap`
- negative gap (clock skew) → `nominal`

**State I/O:**
- missing file → cold start (effective = cap)
- malformed JSON → cold start (fail-safe wide), no crash
- per-cadence isolation: reading `weekly` ignores `daily`'s date
- `--record` writes the correct date and **preserves the other cadence bucket** and unrelated keys
- `--record` creates the file when absent

**CLI smoke:** compute mode prints a single integer to stdout; the explanation goes to stderr.

## 7. Decisions log entry (to add to roadmap.md)

| Date | Decision | Notes |
|------|----------|-------|
| 2026-06-26 | G13 — self-healing lookback window | Effective window = `min(cold_start_cap, max(nominal, gap_since_last_run))`, per-cadence state in `runs/_state.json`, cold-start/cap default 30d. New deterministic tool `tools/compute_window.py`; orchestrator records the run marker only on success. Fixes cold-start + skipped-run coverage; structural over manual override. Dedup (G4) stays open. |
