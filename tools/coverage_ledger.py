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
_OEM = re.compile(r"^\s+oem:\s*(.+?)\s*$", re.MULTILINE)
_DOCKET = re.compile(r"Docket\s+(?:No\.?\s*)?[A-Z]{2,4}-\d{4}-\d+", re.IGNORECASE)
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


def tracked_oems(config_text):
    """Manufacturer names from fleet.yaml, lowercased. Airframe and engine OEMs both."""
    seen = []
    for m in _OEM.finditer(config_text):
        name = m.group(1).strip().strip('"\'').lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def matches_fleet(types_hint, tokens):
    """True when any hint token overlaps a tracked type.

    An empty/unparseable hint returns True on purpose: a listing-parse failure
    must surface as `unaccounted`, never be silently excluded.
    """
    if not types_hint:
        return True
    return matches_fleet_strict(types_hint, tokens)


def matches_fleet_strict(types_hint, tokens):
    """True when any hint token overlaps a tracked type. Empty hint returns
    False plainly — unlike `matches_fleet`, the empty case is not special-cased
    here; callers that need fail-loud-on-empty handle it explicitly."""
    if not types_hint:
        return False
    for hint in types_hint:
        h = hint.strip().lower()
        for tok in tokens:
            if h and (h in tok or tok in h):
                return True
    return False


# The tracked fleet is fixed-wing only (Airbus S.A.S. + Boeing commercial
# aircraft). These OEM-branded divisions build different, permanently
# out-of-scope products (rotorcraft, defence/space) even though their
# manufacturer text shares the tracked OEM's first word — e.g. "AIRBUS
# HELICOPTERS" would otherwise match plain "airbus" and stay loud forever.
# Checked BEFORE the tracked-OEM substring test. Extend as similar
# false-positive divisions turn up.
_NON_TRACKED_DIVISIONS = (
    "airbus helicopters",
    "airbus defence",
    "boeing helicopters",
    # Airbus Canada builds the A220 (ex-Bombardier C Series) — a different,
    # permanently out-of-scope airframe that shares the tracked OEM's first
    # word. NOT a general "regional jet" rule: only this named division.
    "airbus canada",
)


def manufacturer_is_tracked(hint, oems):
    """True when the hint names a tracked OEM. Empty hint returns None (unknown).

    Word-boundary matching, not substring: the OEM head "ge" (GE Aerospace) would
    otherwise match inside "Landing Gear", which appears in most airframe AD
    subjects and would keep every one of them permanently loud.
    """
    h = (hint or "").strip().lower()
    if not h:
        return None
    for division in _NON_TRACKED_DIVISIONS:
        # LEADING boundary only, no trailing \b: real biweekly/FR hints glue the
        # division name directly onto the next column with no separator (e.g.
        # "AIRBUS HELICOPTERSSA 330 / AS 332 / EC 225..."), so a trailing \b
        # never matches (the transition into "SA..." isn't a word boundary,
        # since both sides are word characters). A leading boundary is enough
        # to avoid a false mid-word match, and \s* between the division's own
        # words tolerates it being glued together the same way.
        pattern = r"\b" + r"\s*".join(re.escape(w) for w in division.split())
        if re.search(pattern, h):
            return False
    for oem in oems:
        head = (oem.split() or [oem])[0]
        if head and re.search(r"\b%s\b" % re.escape(head), h):
            return True
    return False


def classify(ad, reported_refs, seen_refs, tokens, oems=()):
    ref = (ad.get("ref_number") or "").strip()
    if ref in reported_refs:
        return "reported"
    if ref in seen_refs:
        return "suppressed"
    hint = ad.get("types_hint")
    if matches_fleet_strict(hint, tokens):        # non-empty AND matching
        return "unaccounted"
    if hint:                                       # non-empty but no tracked type
        return "excluded"
    tracked = manufacturer_is_tracked(ad.get("manufacturer_hint"), oems)
    if tracked is False:                           # known, and not ours
        return "excluded"
    return "unaccounted"                           # tracked OEM, or no signal at all


def refs_from_source_url(url):
    """Document numbers embedded in a primary_source_url.

    The sweep identifies an FAA AD by its Federal Register DOCUMENT number
    ("2026-18055"); the verifier may record it by its FAA AD number
    ("2026-17-09") or its docket ("FAA-2026-8795"). Those never compare equal,
    so an AD plainly present in the digest was reported as unaccounted. The FR
    number is in the record all along — in the URL — so harvest it there and
    stop depending on which identifier the verifier happened to pick.

    Only the final path segment is scanned, so the date directories in
    ".../2026/09/03/2026-18055.html" and the "FR-2026-07-28" package name in
    govinfo URLs cannot contribute a spurious document number. Marking a
    genuinely missed AD as reported is the dangerous direction.
    """
    if not url:
        return set()
    tail = str(url).rstrip("/").rsplit("/", 1)[-1]
    tail = re.sub(r"\.(?:html?|pdf)$", "", tail, flags=re.IGNORECASE)
    return set(_REF_NUM.findall(tail))


def reported_refs_from(records):
    refs = set()
    for rec in records.get("records") or []:
        for r in rec.get("references") or []:
            refs |= refs_from_source_url(r.get("primary_source_url"))
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
    oems = tracked_oems(config_text)
    reported = reported_refs_from(records)
    out = {"reported": [], "suppressed": [], "excluded": [], "unaccounted": [],
           "coverage": sweep.get("coverage", [])}
    for ad in sweep.get("ads") or []:
        bucket = classify(ad, reported, seen_refs, tokens, oems)
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
