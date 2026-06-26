#!/usr/bin/env python3
"""finalize_digest.py — deterministic post-render fixups for the digest Markdown.

The Writer is an LLM and cannot be relied on (by instruction alone) to honour
pure formatting rules — the 2026-06-26 proof run showed it still emitted
``&amp;`` and doubled the reference type even when explicitly told not to. This
runs AFTER the writer, the same way ``validate_records.py`` enforces the gate
after the verifier: structure over instruction.

It fixes three rendering defects (project gap G14):
  * HTML-escaped entities (``&amp;`` -> ``&``) the writer emits despite instructions;
  * a doubled reference type (``AD AD 2026-..`` when ``ref_number`` already
    starts with the type);
  * section order — forced to Directly Fleet-Relevant -> Read-Across -> Major
    Industry Events, with the trailing ``Sources & Confidence`` line kept last.

Stdlib only. Usage:
    python finalize_digest.py --infile digests/2026-06-26-weekly.md > clean.md
    python finalize_digest.py --infile digests/x.md --in-place
"""
import argparse
import re
import sys

SECTION_ORDER = [
    "## Directly Fleet-Relevant",
    "## Read-Across (Peer Types)",
    "## Major Industry Events",
]

# Longer/overlapping types first so e.g. 'EAD' is matched before 'AD'.
REF_TYPES = ["MSG-3", "EAD", "SIL", "SL", "SB", "AD"]

_ENTITIES = [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
             ("&#39;", "'"), ("&quot;", '"')]


def unescape_entities(text):
    """Turn the handful of HTML entities the writer over-escapes back into text."""
    for ent, ch in _ENTITIES:
        text = text.replace(ent, ch)
    return text


def dedupe_ref_type(text):
    """Collapse a doubled reference type, e.g. 'AD AD 2026-10-06' -> 'AD 2026-10-06'."""
    for t in REF_TYPES:
        text = re.sub(r"\b(%s)\s+\1\b" % re.escape(t), r"\1", text)
    return text


def reorder_sections(text):
    """Reorder the known H2 sections to SECTION_ORDER; keep the Sources line last."""
    footer = ""
    m = re.search(r"(?m)^\*\*Sources & Confidence.*", text, re.S)
    if m:
        footer = text[m.start():].strip()
        body = text[:m.start()]
    else:
        body = text

    parts = re.split(r"(?m)^(##\s+.*)$", body)
    preamble = parts[0]
    chunks = []
    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        content = parts[i + 1] if i + 1 < len(parts) else ""
        chunks.append((header, content))

    def rank(header):
        return SECTION_ORDER.index(header) if header in SECTION_ORDER else len(SECTION_ORDER)

    chunks.sort(key=lambda hc: rank(hc[0]))  # stable: unknown headers keep their order

    out = preamble.strip()
    for header, content in chunks:
        out += ("\n\n" if out else "") + header + "\n\n" + content.strip("\n")
    out = out.rstrip()
    if footer:
        out += "\n\n" + footer
    return out + "\n"


def finalize(text):
    """Apply all deterministic fixups in order."""
    return reorder_sections(dedupe_ref_type(unescape_entities(text)))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Deterministic digest post-render fixups.")
    ap.add_argument("--infile", required=True, help="Digest Markdown file.")
    ap.add_argument("--in-place", action="store_true",
                    help="Rewrite the file in place instead of printing to stdout.")
    args = ap.parse_args(argv)

    with open(args.infile, encoding="utf-8") as fh:
        text = fh.read()
    fixed = finalize(text)

    if args.in_place:
        with open(args.infile, "w", encoding="utf-8") as fh:
            fh.write(fixed)
    else:
        sys.stdout.write(fixed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
