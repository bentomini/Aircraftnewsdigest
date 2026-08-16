#!/usr/bin/env python3
"""faa_register.py — enumerate FAA ADs via the Federal Register API.

The canonical federalregister.gov/documents/<date>/<slug> URL 302-redirects to
a bot wall; the full_text/html form fetches reliably and is allowlisted in
config/fleet.yaml verified_domains.

See docs/superpowers/specs/2026-08-08-regulator-sweep-design.md
Stdlib only.
"""
import re
import urllib.parse

API = "https://www.federalregister.gov/api/v1/documents.json"
# Boeing commercial models are 7-x-7 (737, 747, 757, 767, 777, 787), so requiring the
# trailing 7 excludes engine designations like "Trent 700" that would otherwise produce
# a spurious types_hint. A wrong hint is worse than none: empty hints fail loud downstream.
#
# Format note for downstream fleet matching: the two branches below capture different
# granularity. Airbus branches capture only the family code ("A321"), never the full
# variant. The Boeing branch captures the full variant when present ("777-300ER"), not
# just the family ("777"). Neither branch prefixes with a manufacturer letter. The EASA
# sibling (tools/easa_biweekly.py) differs on both counts: it captures only family codes
# for Airbus AND Boeing, and prefixes Boeing with "B" ("B777"). A later reconciliation
# task must tolerate both a bare family code and a full variant, and both "777" and
# "B777" spellings, when comparing hints across the two adapters.
_TYPE_TOKEN = re.compile(r"\bA3[0-9]{2}\b|\bA2[0-9]{2}\b|\b7[0-9]7(?:-[0-9A-Za-z]+)?\b")
FIELDS = ("document_number", "publication_date", "title", "type", "abstract")


def fr_query_url(term, start, end):
    """Federal Register API URL for AD documents matching `term` in the window.

    Restricted to RULE and PRORULE document types per the approved design doc —
    without this filter the query matches every Federal Register document
    containing the term (notices, meeting announcements, funding opportunities),
    not just ADs, which pollutes the coverage ledger with unrelated entries.
    """
    q = [("conditions[term]", term),
         ("conditions[publication_date][gte]", start.isoformat()),
         ("conditions[publication_date][lte]", end.isoformat()),
         ("conditions[type][]", "RULE"),
         ("conditions[type][]", "PRORULE"),
         ("per_page", "100"), ("order", "newest")]
    q.extend(("fields[]", f) for f in FIELDS)
    return API + "?" + urllib.parse.urlencode(q)


def full_text_url(document_number, publication_date):
    """The fetchable full-text form, e.g. .../full_text/html/2026/07/29/2026-15308.html"""
    y, m, d = publication_date.split("-")
    return ("https://www.federalregister.gov/documents/full_text/html/%s/%s/%s/%s.html"
            % (y, m, d, document_number))


def parse_fr_response(payload):
    """AD entries from one API response."""
    out = []
    for doc in payload.get("results") or []:
        num = doc.get("document_number")
        pub = doc.get("publication_date")
        if not num or not pub:
            continue
        blob = "%s %s" % (doc.get("title") or "", doc.get("abstract") or "")
        out.append({
            "ref_number": num,
            "subject": (doc.get("title") or "").strip(),
            "types_hint": sorted(set(_TYPE_TOKEN.findall(blob))),
            "source_url": full_text_url(num, pub),
            "issue_date": pub,
        })
    return out


def enumerate_faa(terms, start, end, fetch_json_fn):
    """(ads, coverage) for every FAA AD document matching any term in the window."""
    ads, seen, reason = [], set(), None
    for term in terms:
        try:
            payload = fetch_json_fn(fr_query_url(term, start, end))
            # A 200 response with an unexpected shape (rate-limit error body,
            # {"errors": [...]}, a bare list, None) must not be silently read as
            # "zero results" — that would report complete=True for a term that
            # actually failed. Validated inside the guarded region so a non-dict
            # payload can't raise AttributeError past this function either.
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("unexpected payload shape: %r" % (payload,))
        except Exception as exc:  # noqa: BLE001
            reason = "term %r failed: %s" % (term, exc)
            break
        for ad in parse_fr_response(payload):
            if ad["ref_number"] in seen:
                continue
            seen.add(ad["ref_number"])
            ads.append(ad)
    coverage = {
        "regulator": "FAA",
        "from": start.isoformat(),
        "to": end.isoformat() if reason is None else None,
        "complete": reason is None,
        "reason": reason,
    }
    return ads, coverage
