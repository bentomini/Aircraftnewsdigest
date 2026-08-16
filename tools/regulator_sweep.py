#!/usr/bin/env python3
"""regulator_sweep.py — deterministic enumeration of EASA + FAA ADs in a window.

Gives the pipeline a recall floor: the scanner may miss an AD that no search
engine indexed, but the agency's own listing cannot. Emits UNVERIFIED leads
only — confidence is decided downstream by the verifier and auditor.

See docs/superpowers/specs/2026-08-08-regulator-sweep-design.md
Stdlib only.
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import date, timedelta

import easa_biweekly
import faa_register

_UA = "Mozilla/5.0 (aviation-digest-preflight)"
_OEM = re.compile(r"^\s+oem:\s*(.+?)\s*$", re.MULTILINE)


def oem_terms(config_text):
    """Manufacturer search terms from fleet.yaml, so the sweep re-scopes with the fleet."""
    seen, out = set(), []
    for m in _OEM.finditer(config_text):
        term = m.group(1).strip()
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    return out


def easa_portal_url(ref_number):
    """Canonical human/fetchable page for an EASA AD."""
    return "https://ad.easa.europa.eu/ad/%s" % ref_number


def build_sweep(easa_result, faa_result):
    """Assemble {'coverage': [...], 'ads': [...]} from both adapters."""
    easa_ads, easa_cov = easa_result
    faa_ads, faa_cov = faa_result
    ads = []
    for ad in easa_ads:
        ads.append(dict(ad, regulator="EASA", ref_type="AD",
                        source_url=ad.get("source_url") or easa_portal_url(ad["ref_number"])))
    for ad in faa_ads:
        ads.append(dict(ad, regulator="FAA", ref_type="AD"))
    return {"coverage": [easa_cov, faa_cov], "ads": ads}


def recall_partial(sweep):
    """True when any regulator failed to cover the whole window."""
    return not all(c.get("complete") for c in sweep.get("coverage", []))


def fetch_bytes(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_json(url, timeout=45):
    return json.loads(fetch_bytes(url, timeout).decode("utf-8"))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Deterministic EASA + FAA AD enumeration.")
    ap.add_argument("--config", default="config/fleet.yaml")
    ap.add_argument("--current-date", required=True, help="Runtime date, YYYY-MM-DD.")
    ap.add_argument("--lookback-days", type=int, required=True)
    ap.add_argument("--outfile", required=True)
    args = ap.parse_args(argv)

    end = date.fromisoformat(args.current_date)
    start = end - timedelta(days=args.lookback_days)

    with open(args.config, encoding="utf-8") as fh:
        terms = oem_terms(fh.read())

    sweep = build_sweep(
        easa_biweekly.enumerate_easa(start, end, fetch_bytes),
        faa_register.enumerate_faa(terms, start, end, fetch_json),
    )

    with open(args.outfile, "w", encoding="utf-8") as fh:
        json.dump(sweep, fh, indent=2, ensure_ascii=False)

    for cov in sweep["coverage"]:
        if cov["complete"]:
            sys.stderr.write("[sweep] %-4s enumerated %s -> %s  (complete)\n"
                             % (cov["regulator"], cov["from"], cov["to"]))
        else:
            sys.stderr.write("[sweep] %-4s enumerated %s -> %s  (%s)\n"
                             % (cov["regulator"], cov["from"], cov["to"], cov["reason"]))
            sys.stderr.write("[sweep] %s %s -> %s is SEARCH-ONLY - recall not guaranteed\n"
                             % (cov["regulator"], cov["to"] or cov["from"], args.current_date))
    sys.stderr.write("[sweep] %d AD(s) enumerated\n" % len(sweep["ads"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
