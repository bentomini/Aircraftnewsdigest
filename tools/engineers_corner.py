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


def is_proposed_rule(record):
    """True if the record's references are ALL NPRM/PAD (routed to On the Horizon, not core).

    Such records are rendered in the Standing Watch 'On the Horizon' block by the writer,
    so they are NOT core-section intelligence and must not count toward the corner trigger
    or ground the corner pick (spec section 7). A record with no references is not a
    proposed rule (it is an event-level core item)."""
    refs = record.get("references") or []
    return bool(refs) and all((r.get("ref_type") or "").upper() in ("NPRM", "PAD")
                              for r in refs)


def count_core_items(records):
    """Substantive core items = VERIFIED/REPORTED records that land in the core sections.

    Excludes proposed-rule (NPRM/PAD-only) records, which the writer routes to On the Horizon."""
    return sum(1 for r in records
               if (r.get("item_confidence") or "").upper() in ("VERIFIED", "REPORTED")
               and not is_proposed_rule(r))


def grounded_types(records):
    """Lower-cased types_affected of core VERIFIED records, for tag grounding.

    Skips proposed-rule records so the corner grounds on actual core news, not horizon items."""
    out = set()
    for r in records:
        if (r.get("item_confidence") or "").upper() == "VERIFIED" and not is_proposed_rule(r):
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
