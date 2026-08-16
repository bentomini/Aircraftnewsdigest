#!/usr/bin/env python3
"""easa_biweekly.py — enumerate EASA ADs from the biweekly listing PDF.

EASA publishes every AD it issues in a 14-day "biweekly" PDF whose URL is
derivable from any date. Verified anchors: issue 06-2026 covers
2026-03-02..2026-03-15; issue 16-2026 covers 2026-07-20..2026-08-02.

See docs/superpowers/specs/2026-08-08-regulator-sweep-design.md
Stdlib only.
"""
from datetime import date, timedelta

# Issue 01-2026 starts here; 26 periods of 14 days = 364 days per AD-year.
ANCHOR_YEAR = 2026
ANCHOR_START = date(2025, 12, 22)
PERIOD_DAYS = 14
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
