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


def record_store(store, records, current_date, warn=None):
    """Upsert VERIFIED AD/EAD references that carry an effective_date. Mutates store.

    ``warn`` is an optional callable taking one message string. An effective_date
    that does not parse as ISO ``YYYY-MM-DD`` is still stored (we never drop the
    AD), but a warning is emitted so the operator notices: ``select()`` silently
    skips unparseable dates, so without this the AD would just never surface on
    the radar with no trace. Defaults to writing to stderr.
    """
    if warn is None:
        def warn(msg):
            sys.stderr.write(msg + "\n")
    for rec in records:
        for r in (rec.get("references") or []):
            if (r.get("ref_type") or "").upper() not in RADAR_REF_TYPES:
                continue
            if (r.get("confidence") or "").upper() != "VERIFIED":
                continue
            eff = (r.get("effective_date") or "").strip()
            if not eff:
                continue
            try:
                date.fromisoformat(eff)
            except (ValueError, TypeError):
                warn("[radar] WARNING: %s has unparseable effective_date %r — "
                     "stored but it will never surface on the radar until corrected"
                     % (ref_id(r), eff))
            store[ref_id(r)] = {
                "effective_date": eff,
                "ref_type": (r.get("ref_type") or "").upper(),
                "ref_number": r.get("ref_number"),
                "url": r.get("primary_source_url"),
                "headline": rec.get("headline"),
                "recorded": current_date,
            }
    return store


def select(store, current_date, forward_days, max_items, exclude=None):
    """Entries whose effective_date is in [current_date, current_date+forward_days],
    soonest first. Entries whose ref_id is in ``exclude`` are skipped — used to
    keep the radar from repeating a reference already in this week's digest body."""
    today = date.fromisoformat(current_date)
    horizon = today + timedelta(days=forward_days)
    exclude = exclude or set()
    upcoming = []
    for key, entry in store.items():
        if key in exclude:
            continue
        try:
            eff = date.fromisoformat(entry["effective_date"])
        except (ValueError, KeyError, TypeError):
            continue
        if today <= eff <= horizon:
            upcoming.append(entry)
    upcoming.sort(key=lambda e: e["effective_date"])
    return upcoming[:max_items]


def collect_ref_ids(records):
    """All ref_ids present in a records list, any confidence (same-week exclusion)."""
    return {ref_id(r) for rec in records for r in (rec.get("references") or [])}


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
    ap.add_argument("--exclude-infile", default=None,
                    help="Records JSON whose references are excluded from the radar "
                         "(build mode; pass this week's 06_deduped.json).")
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

    exclude = set()
    if args.exclude_infile:
        try:
            with open(args.exclude_infile, encoding="utf-8") as f:
                p = json.load(f)
            exclude = collect_ref_ids(p.get("records", []) if isinstance(p, dict) else p)
        except (OSError, ValueError):
            pass  # fail-safe: unreadable exclude file never blocks the radar

    radar = select(store, args.current_date, forward_days, max_items, exclude=exclude)
    sys.stdout.write(json.dumps({"radar": radar}, indent=2, ensure_ascii=False))
    sys.stderr.write("\n[radar] %d upcoming within %dd (showing <=%d, %d excluded as in-body)\n"
                     % (len(radar), forward_days, max_items, len(exclude)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
