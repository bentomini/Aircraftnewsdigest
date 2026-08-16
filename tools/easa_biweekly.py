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
# Leading lookbehind excludes a preceding letter/digit/hyphen so a glued
# alpha-prefixed number (e.g. appliance AD "G-2026-0003") isn't truncated
# into a fabricated standalone ref. The revision group requires a non-digit
# right after it (?!\d), so a revision glued to a following date (e.g.
# "2023-0148R12026-07-24") is never guessed — it falls back to the real base
# number "2023-0148" instead of fabricating "2023-0148R12".
_AD_NUM = re.compile(r"(?<![A-Za-z0-9-])(\d{4}-\d{4}(?:R\d{1,2}(?!\d))?(?:-E)?)")
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


def _clean(fragment):
    return re.sub(r"\s+", " ", _LANG_TAG.sub(" ", fragment)).strip()


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
        })
    return out
