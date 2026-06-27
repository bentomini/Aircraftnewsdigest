#!/usr/bin/env python3
"""render_publication.py — deterministic HTML publication renderer for the digest.

Assembles the "Flight Deck" branded, magazine-style HTML from the SAME gate-passed
JSON the Markdown writer consumes (06_deduped.json + 07_radar.json + 08_corner.json)
plus the gate-passed images (10_images.json). It is FETCH-FREE: like the writer, it
can never introduce a reference, quote, or fact that did not pass the gates. Images
are the only external content and they already passed the image gate.

Reproduces the writer's documented routing: group by category; all-NPRM/PAD records
-> On the Horizon; suppress Read-Across when fleet items >= threshold; order
VERIFIED-first then newest; carry-over + dedup tags. Stdlib only.
"""
import argparse
import json
import re
import sys


def html_escape(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


CONF_CLASS = {"VERIFIED": "v", "REPORTED": "r", "UNVERIFIED": "u"}
CONF_BRACKET = {
    "VERIFIED": "[VERIFIED — primary source]",
    "REPORTED": "[REPORTED — trade press, unconfirmed]",
    "UNVERIFIED": "[UNVERIFIED — reference exists, text not accessed]",
}
PROPOSED_TYPES = {"NPRM", "PAD"}


def chip(confidence):
    c = (confidence or "UNVERIFIED").upper()
    return '<span class="tag %s">%s</span>' % (CONF_CLASS.get(c, "u"), html_escape(c))


def conf_inline(confidence, ref_type=None):
    if (ref_type or "").upper() in PROPOSED_TYPES:
        return "[PROPOSED — not yet final]"
    return CONF_BRACKET.get((confidence or "UNVERIFIED").upper(), CONF_BRACKET["UNVERIFIED"])


def render_quote(q):
    text = html_escape(q.get("text"))
    attrib = ", ".join([x for x in [html_escape(q.get("doc_title")),
                                    html_escape(q.get("ref_number")),
                                    html_escape(q.get("revision_or_date"))] if x])
    url = q.get("url")
    link = ' <a class="src" href="%s">[source]</a>' % html_escape(url) if url else ""
    return '<p class="verbatim">&ldquo;%s&rdquo; — %s%s</p>' % (text, attrib, link)
