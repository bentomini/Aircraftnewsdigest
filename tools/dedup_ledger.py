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


def classify(key, version, ledger):
    """'new' | 'duplicate' | 'updated' for one identity component."""
    entry = ledger.get(key)
    if entry is None:
        return "new"
    if (entry.get("last_version") or "") == (version or ""):
        return "duplicate"
    return "updated"


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
