#!/usr/bin/env python3
"""merge_leads.py — union swept regulator ADs with scanner leads.

Both streams are UNVERIFIED discovery output. Where they name the same AD the
sweep's REFERENCE wins (ref_number/ref_type), because it came from the
agency's own listing rather than a search result — but the scanner's record
(summary, category, lead_sources, read_across) is kept, not replaced.

See docs/superpowers/specs/2026-08-08-regulator-sweep-design.md
Stdlib only.
"""
import argparse
import json
import re
import sys

# Same approach as coverage_ledger.py's _DOCKET: strip docket clauses before
# looking for a document number, so a docket number (FAA-2026-7201) is never
# mistaken for the document it accompanies.
_DOCKET = re.compile(r"Docket\s+(?:No\.?\s*)?[A-Z]{2,4}-\d{4}-\d+", re.IGNORECASE)
_FAA_DOC = re.compile(r"\d{4}-\d{4,5}")
_EASA_DOC = re.compile(r"\d{4}-\d{4}(?:R\d+)?(?:-E)?")


def canonical_number(ref_number):
    """The regulator document number inside free text, or None if none is present.

    The sweep and the scanner format the same document differently (sweep:
    "2026-16157"; scanner: "FR Doc. 2026-16157", ref_type NPRM vs AD) — match
    on this canonical number, not the raw string or ref_type.
    """
    if not ref_number:
        return None
    text = _DOCKET.sub(" ", ref_number)
    m = _FAA_DOC.search(text) or _EASA_DOC.search(text)
    return m.group(0).upper() if m else None


def _key(ref_type, ref_number):
    canon = canonical_number(ref_number)
    if canon:
        return "DOC:%s" % canon
    return "%s:%s" % ((ref_type or "AD").upper(), (ref_number or "").strip().upper())


def sweep_to_lead(ad):
    """A swept AD as a scanner-shaped, strictly UNVERIFIED lead record."""
    return {
        "id": "sweep-%s-%s" % (ad["regulator"].lower(), ad["ref_number"].lower()),
        "headline": "%s AD %s — %s" % (ad["regulator"], ad["ref_number"], ad.get("subject") or ""),
        "category": "fleet",
        "types_affected": list(ad.get("types_hint") or []),
        "event_date": ad.get("issue_date"),
        # Honest default: sweep_to_lead has no current_date/lookback_days to
        # judge this against (and must not — that filtering belongs solely to
        # the deterministic gate in validate_records.py). Now that event_date
        # is a real captured date rather than always None, the gate can
        # actually compute this; None here just means "not yet determined".
        "within_window": None,
        "developing_carryover": False,
        "summary": ad.get("subject") or "",
        "references": [{
            "ref_type": ad.get("ref_type") or "AD",
            "ref_number": ad["ref_number"],
            "revision": None,
            "ref_date": ad.get("issue_date"),
            "effective_date": None,
            "effectivity": None,
            "primary_source_url": None,
            "primary_source_domain": None,
            "fetched_text_snippet": None,
            "confidence": "UNVERIFIED",
        }],
        "oem_regulator_position": None,
        "root_cause": None,
        "quotes": [],
        "read_across": None,
        "lead_sources": [ad["source_url"]],
        "item_confidence": "UNVERIFIED",
        "verifier_notes": ("Enumerated deterministically from the %s publication listing; "
                           "reference number is authoritative but UNCONFIRMED — fetch the "
                           "primary document and confirm applicability against the tracked fleet."
                           % ad["regulator"]),
    }


def merge(sweep, scanner):
    """{'records': [...]} — field-level merge, not whole-record replacement.

    Where the sweep and the scanner name the same document, the *reference*
    wins (ref_number/ref_type are overwritten with the sweep's canonical,
    agency-sourced values) but the scanner's own record — its summary,
    category, lead_sources, read_across — survives untouched. Previously the
    whole scanner record was dropped and replaced by a sweep stub whenever its
    reference keys were a subset of the swept keys, discarding a researched
    scanner record (long summary, multiple lead_sources, a real category) in
    favour of a stub with just the FR title and a hard-coded category. A
    sweep-only AD still enters as a stub.
    """
    sweep_by_key = {}
    for ad in sweep.get("ads") or []:
        k = _key(ad.get("ref_type"), ad.get("ref_number"))
        sweep_by_key.setdefault(k, ad)

    enriched, consumed = [], set()
    for rec in scanner.get("records") or []:
        rec = dict(rec)
        refs = []
        for r in rec.get("references") or []:
            k = _key(r.get("ref_type"), r.get("ref_number"))
            ad = sweep_by_key.get(k)
            if ad is not None:
                r = dict(r, ref_type=ad.get("ref_type") or "AD", ref_number=ad["ref_number"])
                consumed.add(k)
            refs.append(r)
        rec["references"] = refs
        enriched.append(rec)

    records, seen = [], set()
    for ad in sweep.get("ads") or []:
        k = _key(ad.get("ref_type"), ad.get("ref_number"))
        if k in seen or k in consumed:
            continue
        seen.add(k)
        records.append(sweep_to_lead(ad))
    records.extend(enriched)
    return {"records": records}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Union swept ADs with scanner leads.")
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--scanner", required=True)
    args = ap.parse_args(argv)

    with open(args.sweep, encoding="utf-8") as fh:
        sweep = json.load(fh)
    with open(args.scanner, encoding="utf-8") as fh:
        scanner = json.load(fh)

    out = merge(sweep, scanner)
    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False))
    sys.stderr.write("[merge] %d swept + %d scanned -> %d lead(s)\n"
                     % (len(sweep.get("ads") or []), len(scanner.get("records") or []),
                        len(out["records"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
