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

# Federal Register AD titles always name the manufacturer verbatim right after
# this boilerplate, e.g. "Airworthiness Directives; Airbus Helicopters" ->
# "Airbus Helicopters". Without this, coverage_ledger.classify() sees a `None`
# manufacturer_hint for every FAA AD whose abstract carries no model-code
# types_hint, and treats it as unknown -> unconditionally `unaccounted`.
_TITLE_BOILERPLATE = re.compile(r"^\s*Airworthiness Directives;\s*", re.IGNORECASE)

# The Federal Register document "type" field has been observed as both the
# human label ("Rule", "Proposed Rule") and the API's own filter code ("RULE",
# "PRORULE") — normalise away case and whitespace before mapping either form.
_REF_TYPE_MAP = {"RULE": "AD", "PRORULE": "NPRM", "PROPOSEDRULE": "NPRM"}


def _manufacturer_hint_from_title(title):
    return _TITLE_BOILERPLATE.sub("", title or "", count=1).strip()


def _ref_type_from_doc_type(doc_type):
    """Map the FR document type to the pipeline's ref_type. RULE -> AD (a final
    rule is how the FAA issues an AD); PRORULE -> NPRM (a proposed rule is not
    yet an AD, and must route to Standing Watch -> On the Horizon, not the core
    sections). Unrecognised/missing types default to AD, preserving prior
    behaviour for anything this map doesn't yet know about."""
    key = re.sub(r"\s+", "", (doc_type or "").strip().upper())
    return _REF_TYPE_MAP.get(key, "AD")


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
            "manufacturer_hint": _manufacturer_hint_from_title(doc.get("title")),
            "ref_type": _ref_type_from_doc_type(doc.get("type")),
            "source_url": full_text_url(num, pub),
            "issue_date": pub,
        })
    return out


def enumerate_faa(terms, start, end, fetch_json_fn):
    """(ads, coverage) for every FAA AD document matching any term in the window.

    Unlike the EASA sibling (which breaks on the first gap because its biweekly
    periods are sequential in time — the tail is genuinely unknown once one
    period is missing), FAA terms are independent manufacturer searches. One
    term failing tells you nothing about the others, so a failure here must
    NOT abandon the remaining terms — that would silently drop every
    manufacturer queried after the failing one. Continue past failures,
    collect ADs from every term that succeeded, and report which term(s)
    failed.
    """
    ads, seen, failures = [], set(), []
    for term in terms:
        try:
            payload = fetch_json_fn(fr_query_url(term, start, end))
            # A real Federal Register response always carries a "count" key,
            # including the well-formed zero-hit case, which omits "results"
            # entirely (observed live: {'description': ..., 'count': 0}).
            # Error bodies ({"error": "rate limited"}), a bare list, and None
            # all lack "count" and are rejected. `results`, when present, must
            # be a list — a "count" key alone doesn't rule out a malformed
            # results value (e.g. a string or dict) that would otherwise blow
            # up parse_fr_response's iteration below. Reading results as
            # `.get("results") or []` then correctly treats "no results key"
            # the same as "empty results list" for a payload we've already
            # confirmed is a genuine, well-shaped API response.
            if (not isinstance(payload, dict) or "count" not in payload
                    or not isinstance(payload.get("results", []), list)):
                raise ValueError("unexpected payload shape: %r" % (payload,))
            # Parsing happens inside the same guarded region as the fetch: a
            # payload can pass the shape check above yet still contain
            # per-record garbage that trips something in parse_fr_response
            # (or dedup below). That must attribute to this term as a
            # failure too, not escape enumerate_faa and discard every ad
            # already collected from earlier, successful terms.
            for ad in parse_fr_response(payload):
                if ad["ref_number"] in seen:
                    continue
                seen.add(ad["ref_number"])
                ads.append(ad)
        except Exception as exc:  # noqa: BLE001
            failures.append("term %r failed: %s" % (term, exc))
            continue
    complete = not failures
    coverage = {
        "regulator": "FAA",
        "from": start.isoformat(),
        "to": end.isoformat() if complete else None,
        "complete": complete,
        "reason": "; ".join(failures) if failures else None,
    }
    return ads, coverage
