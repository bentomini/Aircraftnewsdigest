#!/usr/bin/env python3
"""finalize_digest.py — deterministic post-render fixups for the digest Markdown.

The Writer is an LLM and cannot be relied on (by instruction alone) to honour
pure formatting rules — the 2026-06-26 proof run showed it still emitted
``&amp;`` and doubled the reference type even when explicitly told not to. This
runs AFTER the writer, the same way ``validate_records.py`` enforces the gate
after the verifier: structure over instruction.

It fixes four rendering/editorial rules:
  * HTML-escaped entities (``&amp;`` -> ``&``) the writer emits despite instructions;
  * a doubled reference type (``AD AD 2026-..`` when ``ref_number`` already
    starts with the type);
  * section order — forced to Directly Fleet-Relevant -> Read-Across -> Major
    Industry Events, with the trailing ``Sources & Confidence`` line kept last;
  * Read-Across suppression — if the Directly Fleet-Relevant section contains
    >= ``standing_watch.read_across_min_fleet_items`` items (default 5), the
    entire ## Read-Across (Peer Types) section is removed. A busy fleet week
    should not be diluted by peer-type items.

Stdlib only. Usage:
    python finalize_digest.py --infile digests/2026-06-26-weekly.md > clean.md
    python finalize_digest.py --infile digests/x.md --in-place
    python finalize_digest.py --infile digests/x.md --in-place --config config/fleet.yaml
"""
import argparse
import re
import sys

SECTION_ORDER = [
    "## Directly Fleet-Relevant",
    "## Read-Across (Peer Types)",
    "## Major Industry Events",
    "## Standing Watch",
]

# Longer/overlapping types first so e.g. 'EAD' is matched before 'AD'.
REF_TYPES = ["MSG-3", "EAD", "SIL", "SL", "SB", "AD"]

_ENTITIES = [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
             ("&#39;", "'"), ("&quot;", '"')]

_DEFAULT_MIN_FLEET = 5


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


def _read_fleet_threshold(config_path="config/fleet.yaml"):
    """Read read_across_min_fleet_items from fleet.yaml; return default if absent."""
    try:
        with open(config_path, encoding="utf-8") as fh:
            m = re.search(r"read_across_min_fleet_items\s*:\s*(\d+)", fh.read())
            return int(m.group(1)) if m else _DEFAULT_MIN_FLEET
    except OSError:
        return _DEFAULT_MIN_FLEET


def _count_fleet_items(text):
    """Count bold-headline items inside ## Directly Fleet-Relevant."""
    m = re.search(
        r"(?m)^## Directly Fleet-Relevant\s*\n(.*?)(?=^##|\Z)",
        text, re.S | re.MULTILINE,
    )
    if not m:
        return 0
    return len(re.findall(r"(?m)^\*\*", m.group(1)))


def suppress_readacross(text, config_path="config/fleet.yaml"):
    """Remove ## Read-Across section when fleet item count >= threshold.

    Prints a one-line notice to stderr if suppressed, so the operator can see why
    the section is absent (same pattern as other gate notices).
    """
    threshold = _read_fleet_threshold(config_path)
    fleet_count = _count_fleet_items(text)
    if fleet_count >= threshold:
        print(
            f"[finalise] Read-Across suppressed: {fleet_count} fleet items >= threshold {threshold}",
            file=sys.stderr,
        )
        text = re.sub(
            r"(?m)^## Read-Across \(Peer Types\)\n.*?(?=^##|\Z)",
            "",
            text,
            flags=re.S,
        )
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
        if not content.strip():
            continue  # skip empty sections (e.g. suppressed Read-Across)
        out += ("\n\n" if out else "") + header + "\n\n" + content.strip("\n")
    out = out.rstrip()
    if footer:
        out += "\n\n" + footer
    return out + "\n"


def finalize(text, config_path="config/fleet.yaml"):
    """Apply all deterministic fixups in order."""
    text = unescape_entities(text)
    text = dedupe_ref_type(text)
    text = suppress_readacross(text, config_path)
    text = reorder_sections(text)
    return text


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
    ap.add_argument("--config", default="config/fleet.yaml",
                    help="Path to fleet.yaml (default: config/fleet.yaml).")
    args = ap.parse_args(argv)

    with open(args.infile, encoding="utf-8") as fh:
        text = fh.read()
    fixed = finalize(text, config_path=args.config)

    if args.in_place:
        with open(args.infile, "w", encoding="utf-8") as fh:
            fh.write(fixed)
    else:
        sys.stdout.write(fixed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
