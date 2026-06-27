#!/usr/bin/env python3
"""compute_window.py — self-healing lookback window for the digest pipeline.

Rule:  effective = min(cap, max(nominal, gap))
where cap = max(cold_start_cap, nominal)  — a cap configured below nominal is
raised to nominal so the window is never narrower than the cadence's floor.
gap = whole days since the last run of THIS cadence, or None on a cold start
(treated as 'long ago', clamped to cap).

See docs/superpowers/specs/2026-06-26-g13-self-healing-window-design.md
Stdlib only.
"""
import argparse
import json
import os
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
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, indent=2, ensure_ascii=False))
    return state


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

    try:
        with open(args.config, encoding="utf-8") as f:
            cold_start = parse_cold_start_from_yaml(f.read())
    except OSError as e:
        sys.stderr.write("[window] ERROR: cannot read config %s: %s\n" % (args.config, e))
        return 2

    state = load_state(args.state)
    gap = gap_days(last_run_date(state, args.cadence), args.current_date)
    effective = compute_effective_lookback(gap, args.nominal_lookback_days, cold_start)

    sys.stdout.write(str(effective))  # clean integer, no newline — orchestrator captures it
    sys.stderr.write("[window] %s\n" % explain(gap, effective, args.nominal_lookback_days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
