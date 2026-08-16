#!/usr/bin/env python3
"""easa_biweekly.py — enumerate EASA ADs from the biweekly listing PDF.

EASA publishes every AD it issues in a 14-day "biweekly" PDF whose URL is
derivable from any date. Verified anchors: issue 06-2026 covers
2026-03-02..2026-03-15; issue 16-2026 covers 2026-07-20..2026-08-02.

See docs/superpowers/specs/2026-08-08-regulator-sweep-design.md
Stdlib only.
"""
import re
import zlib
from datetime import date, timedelta

# Issue 01-2026 starts here; 26 periods of 14 days = 364 days per AD-year.
ANCHOR_YEAR = 2026
ANCHOR_START = date(2025, 12, 22)
PERIOD_DAYS = 14
# YEAR_DAYS verified by live fetch of EASA biweekly PDFs 16-2025, 01-2026, 26-2025
# on 2026-08-16 — all returned HTTP 200 with valid PDF magic bytes.
YEAR_DAYS = 364


def year_anchor(year):
    """Start date of issue 01 for the given AD-year."""
    return ANCHOR_START + timedelta(days=YEAR_DAYS * (year - ANCHOR_YEAR))


def biweekly_period(d):
    """(issue_no, year, start, end) for the biweekly period containing date d."""
    for year in (d.year + 1, d.year, d.year - 1):
        anchor = year_anchor(year)
        delta = (d - anchor).days
        if 0 <= delta < YEAR_DAYS:
            idx = delta // PERIOD_DAYS
            start = anchor + timedelta(days=idx * PERIOD_DAYS)
            return idx + 1, year, start, start + timedelta(days=PERIOD_DAYS - 1)
    raise ValueError("no biweekly period for %s" % d)


def biweekly_url(issue_no, year, start, end):
    """The public PDF URL for a biweekly issue."""
    return ("https://ad.easa.europa.eu/blob/easa_biweekly_%s_%s_%02d-%d.pdf/biweekly"
            % (start.isoformat(), end.isoformat(), issue_no, year))


_STREAM = re.compile(rb"\d+ \d+ obj\s*(<<.*?>>)\s*stream\r?\n(.*?)endstream", re.S)
_SHOWN = re.compile(r"\((?:\\.|[^()\\])*\)", re.S)


def extract_pdf_text(raw):
    """Readable text from a PDF's content streams.

    Tries FlateDecode first; real EASA biweekly PDFs were found (2026-08-16
    live fetch) to ship literal, uncompressed content streams (no /Filter
    declared), so a stream that fails to inflate is used as-is ONLY when its
    own object dict declares no /Filter at all — that's the PDF-spec signal
    that the stream is meant to be literal. A stream whose dict DOES declare
    a filter (FlateDecode that still failed, or a binary filter like
    DCTDecode for an embedded image) is skipped rather than treated as text.
    """
    parts = []
    for obj_dict, content in _STREAM.findall(raw):
        try:
            parts.append(zlib.decompress(content))
        except zlib.error:
            if b"/Filter" not in obj_dict:
                parts.append(content)
    if not parts:
        return ""
    blob = b"\n".join(parts).decode("latin-1")
    text = "".join(t[1:-1] for t in _SHOWN.findall(blob))
    text = re.sub(
        r"\\(.)",
        lambda m: {"(": "(", ")": ")", "\\": "\\",
                   "n": " ", "r": " "}.get(m.group(1), m.group(1)),
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


# No trailing \b: real biweekly text glues the ref number directly onto the
# next column's digits with no separator (e.g. "...Inspection 2026-01442026-
# 07-21AIRBUS..."), so a digit-to-digit transition never satisfies \b.
# Leading lookbehind excludes only "<letter>-" immediately before the number
# — the specific shape of a letter-prefixed identifier (e.g. appliance AD
# "G-2026-0003") whose numeric tail must not be truncated into a fabricated
# standalone ref. A bare AD number is legitimately glued to arbitrary
# preceding text at page/column breaks (e.g. "...Subject2026-0145...",
# "...20262026-0153...") and must NOT be excluded just because a letter or
# digit precedes it — only excluding "<letter>-" fixes the alpha-prefix case
# without dropping those real ADs. The revision group requires a non-digit
# right after it (?!\d), so a revision glued to a following date (e.g.
# "2023-0148R12026-07-24") is never guessed — it falls back to the real base
# number "2023-0148" instead of fabricating "2023-0148R12".
_AD_NUM = re.compile(r"(?<![A-Za-z]-)(\d{4}-\d{4}(?:R\d{1,2}(?!\d))?(?:-E)?)")
# Lookaround instead of \b on both sides: same glued-text problem as _AD_NUM
# — a type token is often glued to the next column with no separator (e.g.
# "...A320, A321Wings - Main Landing..."). A glued *letter* is tolerated
# (still a valid boundary for a model code); a glued *digit* is not, since
# that would mean the match landed inside a longer number.
_TYPE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])A3[0-9]{2}(?![0-9])"
    r"|(?<![A-Za-z0-9])A2[0-9]{2}(?![0-9])"
    r"|(?<![A-Za-z0-9])B7[0-9]{2}(?![0-9])"
)
_LANG_TAG = re.compile(r"\b(?:en|fr|de)-[A-Z]{2}\b")
_SUBJECT_WINDOW = 160
_LEADING_DATE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}")
_LEADING_REV = re.compile(r"^R\d{1,2}(?=\d{4}-\d{2}-\d{2})")
_NEXT_REF = re.compile(r"\d{4}-\d{4}")
_MANUFACTURER_SPAN = 60


def _clean(fragment):
    return re.sub(r"\s+", " ", _LANG_TAG.sub(" ", fragment)).strip()


def _manufacturer_hint(window):
    """Rough manufacturer text following the ref/date, truncated before the
    next row's reference number so a single hint never blends two
    manufacturers together. A revision suffix (R1, R2, ...) that _AD_NUM
    declined to attach to the preceding ref number (see
    test_real_fixture_never_fabricates_glued_revision_number) can be left
    glued to the front of the window, immediately before the date — stripped
    here too, so it never gets read as part of the manufacturer name.
    Membership test only."""
    tail = _LEADING_REV.sub("", window, count=1)
    tail = _LEADING_DATE.sub("", tail, count=1)
    next_ref = _NEXT_REF.search(tail)
    end = next_ref.start() if next_ref else len(tail)
    return tail[:min(end, _MANUFACTURER_SPAN)].strip()


def parse_biweekly(text):
    """AD entries found in extracted biweekly text.

    Deliberately loose: the sweep only needs a reference number to chase. The
    verifier confirms everything downstream, and an empty types_hint is treated
    as fleet-matching by the coverage ledger, so a parse miss fails loud.
    """
    out, seen = [], set()
    for m in _AD_NUM.finditer(text):
        ref = m.group(1)
        if ref in seen:
            continue
        seen.add(ref)
        window = _clean(text[m.end():m.end() + _SUBJECT_WINDOW])
        out.append({
            "ref_number": ref,
            "subject": window,
            "types_hint": sorted(set(_TYPE_TOKEN.findall(window))),
            "manufacturer_hint": _manufacturer_hint(window),
        })
    return out


def _periods_spanning(start, end):
    """Every biweekly period touching [start, end], in chronological order."""
    periods, cursor = [], start
    while cursor <= end:
        issue, year, p_start, p_end = biweekly_period(cursor)
        periods.append((issue, year, p_start, p_end))
        cursor = p_end + timedelta(days=1)
    return periods


def enumerate_easa(start, end, fetch_fn):
    """(ads, coverage) for every EASA AD published in [start, end].

    Stops at the first unpublished period and reports the covered boundary —
    the biweekly publishes in arrears, so the tail of a window is routinely
    unavailable and must never be silently claimed as covered.

    Deliberately break-on-first-failure, unlike the FAA sibling (which
    continues past a failing term): these periods are sequential in time, so
    once one is missing the tail of the window is genuinely unknown and
    `covered_to` records exactly how far enumeration got. FAA's terms are
    independent manufacturer searches with no such ordering, so one failing
    there says nothing about the rest — do not "harmonise" these two loops.
    """
    ads, covered_to, reason = [], None, None
    for issue, year, p_start, p_end in _periods_spanning(start, end):
        url = biweekly_url(issue, year, p_start, p_end)
        try:
            raw = fetch_fn(url)
        except Exception as exc:  # noqa: BLE001 — any failure ends coverage here
            reason = "biweekly %02d-%d not available (%s)" % (issue, year, exc)
            break
        # A soft-404 (HTTP 200 with an HTML interstitial, maintenance page, or
        # truncated body) does not raise, but isn't a PDF either. Treated as
        # covered, it would report zero ADs as a fully-covered window — the
        # exact false comfort this function exists to prevent. A genuinely
        # quiet period (valid PDF, zero ADs) must still count as covered.
        if not raw or raw.lstrip()[:4] != b"%PDF":
            reason = "biweekly %02d-%d did not return a PDF (got %d bytes)" % (
                issue, year, len(raw or b""))
            break
        ads.extend(parse_biweekly(extract_pdf_text(raw)))
        covered_to = min(p_end, end)
    coverage = {
        "regulator": "EASA",
        "from": start.isoformat(),
        "to": covered_to.isoformat() if covered_to else None,
        "complete": covered_to is not None and covered_to >= end,
        "reason": reason,
    }
    return ads, coverage
