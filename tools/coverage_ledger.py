#!/usr/bin/env python3
"""coverage_ledger.py — prove every swept AD was consciously handled.

Diffs the deterministic regulator sweep against what actually reached the
digest. An AD that matches the tracked fleet but appears nowhere lands in
`unaccounted`, which exits non-zero and is surfaced in the run report.

See docs/superpowers/specs/2026-08-08-regulator-sweep-design.md
Stdlib only.
"""
import argparse
import json
import re
import sys

_TYPE = re.compile(r"^\s+-\s+type:\s*(.+?)\s*$", re.MULTILINE)
_ALIASES = re.compile(r"^\s+aliases:\s*\[(.*?)\]\s*$", re.MULTILINE)
_DOCKET = re.compile(r"Docket\s+[A-Z]{2,4}-\d{4}-\d+", re.IGNORECASE)
_REF_NUM = re.compile(r"\d{4}-\d{4,5}")
# Reference-key prefixes dedup_ledger.py writes (see tools/dedup_ledger.py ref_key());
# EVENT: keys are headline slugs, not references, and must never be scanned for digits.
_REF_KEY_PREFIXES = {"AD", "EAD", "NPRM", "PAD", "SB", "SIL", "SL", "MSG-3", "OTHER"}


def tracked_tokens(config_text):
    """Lowercased tracked type names and aliases from fleet.yaml."""
    tokens = set()
    fleet_block = config_text.split("peers:")[0]
    for m in _TYPE.finditer(fleet_block):
        tokens.add(m.group(1).strip().strip('"\'').lower())
    for m in _ALIASES.finditer(fleet_block):
        for raw in m.group(1).split(","):
            tok = raw.strip().strip('"\'').lower()
            if tok:
                tokens.add(tok)
    return sorted(tokens)


def matches_fleet(types_hint, tokens):
    """True when any hint token overlaps a tracked type.

    An empty/unparseable hint returns True on purpose: a listing-parse failure
    must surface as `unaccounted`, never be silently excluded.
    """
    if not types_hint:
        return True
    for hint in types_hint:
        h = hint.strip().lower()
        for tok in tokens:
            if h and (h in tok or tok in h):
                return True
    return False


def classify(ad, reported_refs, seen_refs, tokens):
    ref = (ad.get("ref_number") or "").strip()
    if ref in reported_refs:
        return "reported"
    if ref in seen_refs:
        return "suppressed"
    if not matches_fleet(ad.get("types_hint"), tokens):
        return "excluded"
    return "unaccounted"


def reported_refs_from(records):
    refs = set()
    for rec in records.get("records") or []:
        for r in rec.get("references") or []:
            num = (r.get("ref_number") or "").strip()
            if num:
                refs.add(num)
                # FR docket strings embed the number, e.g. "FR Doc. 2026-15239 (Docket …)".
                # Strip the docket clause first so its own YYYY-NNNNN-shaped number is
                # never mistaken for the AD/FR document number (that would falsely mark
                # a genuinely-missed AD as reported).
                stripped = _DOCKET.sub("", num)
                for token in _REF_NUM.findall(stripped):
                    refs.add(token)
    return refs


def build_ledger(sweep, records, seen_refs, config_text):
    tokens = tracked_tokens(config_text)
    reported = reported_refs_from(records)
    out = {"reported": [], "suppressed": [], "excluded": [], "unaccounted": [],
           "coverage": sweep.get("coverage", [])}
    for ad in sweep.get("ads") or []:
        bucket = classify(ad, reported, seen_refs, tokens)
        entry = {"ref_number": ad.get("ref_number"), "regulator": ad.get("regulator"),
                 "subject": ad.get("subject"), "source_url": ad.get("source_url")}
        if bucket == "excluded":
            entry["reason"] = "types_hint %s matched no tracked type" % (ad.get("types_hint") or [])
        out[bucket].append(entry)
    return out


def load_seen_refs(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    refs = set()
    for key in (data.get("seen") or {}):
        # Ledger keys are 'TYPE:NUMBER' for references, 'EVENT:<slug>' for ref-less
        # items. Only extract from the value half of an actual reference key — an
        # EVENT slug can embed date-like digits that must never read as a ref number.
        prefix, sep, value = str(key).partition(":")
        if not sep or prefix.strip().upper() not in _REF_KEY_PREFIXES:
            continue
        for token in _REF_NUM.findall(value):
            refs.add(token)
    return refs


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Diff the regulator sweep against the digest.")
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--infile", required=True, help="06_deduped.json")
    ap.add_argument("--ledger", default="runs/_seen.json")
    ap.add_argument("--config", default="config/fleet.yaml")
    ap.add_argument("--outfile", required=True)
    args = ap.parse_args(argv)

    with open(args.sweep, encoding="utf-8") as fh:
        sweep = json.load(fh)
    with open(args.infile, encoding="utf-8") as fh:
        records = json.load(fh)
    with open(args.config, encoding="utf-8") as fh:
        config_text = fh.read()

    ledger = build_ledger(sweep, records, load_seen_refs(args.ledger), config_text)
    with open(args.outfile, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2, ensure_ascii=False)

    sys.stderr.write("[coverage] reported=%d suppressed=%d excluded=%d unaccounted=%d\n"
                     % (len(ledger["reported"]), len(ledger["suppressed"]),
                        len(ledger["excluded"]), len(ledger["unaccounted"])))
    for entry in ledger["unaccounted"]:
        sys.stderr.write("  !! UNACCOUNTED %s %s — %s (%s)\n"
                         % (entry["regulator"], entry["ref_number"],
                            entry["subject"], entry["source_url"]))
    return 1 if ledger["unaccounted"] else 0


if __name__ == "__main__":
    sys.exit(main())
