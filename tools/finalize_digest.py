#!/usr/bin/env python3
"""finalize_digest.py — deterministic post-render fixups for the digest Markdown.

The Writer is an LLM and cannot be relied on (by instruction alone) to honour
pure formatting rules — the 2026-06-26 proof run showed it still emitted
``&amp;`` and doubled the reference type even when explicitly told not to. This
runs AFTER the writer, the same way ``validate_records.py`` enforces the gate
after the verifier: structure over instruction.

It fixes these rendering/editorial rules:
  * HTML-escaped entities (``&amp;`` -> ``&``) the writer emits despite instructions;
  * a doubled reference type — both the adjacent ``AD AD 2026-..`` form and the
    ``AD FAA AD 2025-..`` regulator-in-between form (G15), when ``ref_number``
    already names the type;
  * the placeholder ref_type ``other`` printed literally (``other NTSB docket …``);
  * section order — forced to Directly Fleet-Relevant -> Read-Across -> Major
    Industry Events, with the trailing ``Sources & Confidence`` line kept last;
  * Read-Across suppression — if the Directly Fleet-Relevant section contains
    >= ``standing_watch.read_across_min_fleet_items`` items (default 5), the
    entire ## Read-Across (Peer Types) section is removed. A busy fleet week
    should not be diluted by peer-type items;
  * a recall-gap notice — when ``--coverage`` (12_coverage.json) shows a
    non-zero ``unaccounted`` count, a notice naming the count and reference
    numbers is inserted and ``unaccounted_count``/``unaccounted_refs`` are
    written to ``--health-out`` so the email subject (Step 6f) can carry
    ``[RECALL GAP: N]`` — otherwise the coverage ledger's alarm never reaches
    a delivered artifact on an unattended run.

Stdlib only. Usage:
    python finalize_digest.py --infile digests/2026-06-26-weekly.md > clean.md
    python finalize_digest.py --infile digests/x.md --in-place
    python finalize_digest.py --infile digests/x.md --in-place --config config/fleet.yaml
"""
import argparse
import json
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
    """Collapse a redundant leading reference type.

    The writer renders ``{ref_type} {ref_number}``. When ``ref_number`` already
    names the type, the prepended type is noise. Two forms are collapsed, both
    bounded to a single reference clause — the bridge ``[^,;\\n]*?`` never crosses
    a comma, semicolon or newline, so an unrelated later reference is left intact:

      * adjacent duplicate   ``AD AD 2026-10-06``      -> ``AD 2026-10-06``
      * regulator-in-between  ``AD FAA AD 2025-25-12``  -> ``FAA AD 2025-25-12``
        (ref_type ``AD`` + ref_number ``FAA AD 2025-25-12``; G15)

    The bridge between the two types is letters-only (a regulator name, optionally
    ``emergency``): it never spans a digit, slash, comma, semicolon, bracket or
    newline. That keeps it from reaching across to a *distinct* later reference —
    e.g. ``FAA AD 2025-24-51 / EASA AD 2025-0268-E`` must keep both ``AD`` tokens.

    ``EAD EASA AD …`` is left alone: ``\\bAD\\b`` does not match inside ``EAD``,
    and there is no second ``EAD``, so it never triggers.
    """
    for t in REF_TYPES:
        esc = re.escape(t)
        text = re.sub(r"\b(%s)\s+([^,;\n\d/()\[\]]*?\b%s\b)" % (esc, esc), r"\2", text)
    return text


def strip_other_ref_type(text):
    """Remove the placeholder ref_type ``other`` the writer prints literally.

    ``other`` is the schema catch-all (NTSB dockets, etc.) and must not appear in
    the prose. The writer renders ``{ref_type} {ref_number}`` -> ``other NTSB
    docket …``. Strip a lowercase ``other`` only where the writer places a
    reference — right after the ``*Technical detail:*`` marker or after a ``; ``
    reference separator — and only when the following token is capitalised (a
    regulator/doc name), so the ordinary word ``other`` in prose is never touched.
    """
    text = re.sub(r"(\*Technical detail:\*\s+)other\s+(?=[A-Z])", r"\1", text)
    text = re.sub(r"(;\s+)other\s+(?=[A-Z])", r"\1", text)
    return text


DEGRADED_REF_TYPES = {"AD", "EAD", "NPRM", "PAD"}
_BANNER_FMT = "**[DEGRADED — verification pipeline impaired: %s]**"


def strip_radar_artifacts(text):
    """Remove writer routing artifacts like ', Compliance Radar — effective Aug 14'
    from bold item headlines (seen in the 2026-07-26 digest). Applies only to
    lines starting with '**' so the '### Compliance Radar' heading and radar
    bullet lines are never touched."""
    def _clean(m):
        line = m.group(0)
        line = re.sub(r",\s*Compliance Radar\s*—[^),\n]*(?=\))", "", line)
        line = re.sub(r"\(\s*Compliance Radar\s*—[^)\n]*\)\s*", "", line)
        return line
    return re.sub(r"(?m)^\*\*.*$", _clean, text)


def compute_degraded(preflight, records):
    """(degraded, reason). Degraded iff no primary endpoint was reachable at run
    start, or >=1 regulatory reference (AD/EAD/NPRM/PAD) is present yet none is
    VERIFIED. A quiet week with no regulatory references is NOT degraded."""
    if preflight is not None and not preflight.get("any_ok", True):
        return True, "no primary-source endpoint reachable at run start"
    refs = [r for rec in records for r in (rec.get("references") or [])
            if (r.get("ref_type") or "").upper() in DEGRADED_REF_TYPES]
    if refs and not any((r.get("confidence") or "").upper() == "VERIFIED" for r in refs):
        return True, "%d regulatory reference(s) in scope, 0 VERIFIED" % len(refs)
    return False, None


def insert_degraded_banner(text, reason):
    """Insert the banner after the '**Week of …**' header line when present
    (first 5 lines), else after the H1. Formatting-only: adds a flag line,
    never touches a reference or fact."""
    lines = text.split("\n")
    idx = 0
    for i, line in enumerate(lines[:5]):
        if line.startswith("# "):
            idx = i
        if line.startswith("**Week of"):
            idx = i
            break
    lines.insert(idx + 1, "")
    lines.insert(idx + 2, _BANNER_FMT % reason)
    return "\n".join(lines)


def compute_recall_partial(sweep):
    """(recall_partial, reason). True when a regulator did not cover the whole window.

    Distinct from ``degraded``: the verification pipeline is sound here, only the
    enumeration window was not fully swept (e.g. EASA's biweekly listing not yet
    published for the tail of the window). Never touches the email subject."""
    if not sweep:
        return False, None
    gaps = [c for c in (sweep.get("coverage") or []) if not c.get("complete")]
    if not gaps:
        return False, None
    reason = "; ".join(
        "%s enumerated only to %s (%s)" % (c.get("regulator"), c.get("to") or "n/a",
                                           c.get("reason") or "unknown")
        for c in gaps)
    return True, reason


_RECALL_NOTICE = ("> **Recall note:** regulator enumeration was incomplete for this window — %s. "
                  "Items above are unaffected; coverage of that gap relied on search alone.\n\n")


def insert_recall_notice(text, reason):
    """Place the notice immediately before the Sources & Confidence line."""
    notice = _RECALL_NOTICE % reason
    marker = "**Sources & Confidence:**"
    idx = text.find(marker)
    if idx == -1:
        return text.rstrip() + "\n\n" + notice
    return text[:idx] + notice + text[idx:]


def compute_unaccounted(coverage):
    """(count, refs) from a loaded 12_coverage.json. refs is the list of
    ref_number strings, capped at 10, for the notice/health payload.

    Distinct from both `degraded` and `recall_partial`: the coverage ledger
    proves enumeration SUCCEEDED and found N fleet-matching ADs the digest
    never reported — a recall gap, not an enumeration gap. Nothing previously
    consumed 12_coverage.json downstream of Step 4g, so this alarm never
    reached a delivered artifact on an unattended run."""
    if not coverage:
        return 0, []
    items = coverage.get("unaccounted") or []
    refs = [str(e.get("ref_number")) for e in items if e.get("ref_number")]
    return len(items), refs[:10]


_RECALL_GAP_NOTICE = ("> **Recall gap:** the regulator sweep enumerated %d fleet-matching AD(s) "
                     "that this run never reported — %s. Investigate before treating this "
                     "digest as complete.\n\n")


def insert_recall_gap_notice(text, count, refs):
    """Place the notice immediately before the Sources & Confidence line,
    adjacent to the recall-partial notice (if any)."""
    ref_list = ", ".join(refs) if refs else "see runs/DATE/12_coverage.json"
    notice = _RECALL_GAP_NOTICE % (count, ref_list)
    marker = "**Sources & Confidence:**"
    idx = text.find(marker)
    if idx == -1:
        return text.rstrip() + "\n\n" + notice
    return text[:idx] + notice + text[idx:]


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
    text = strip_other_ref_type(text)
    text = strip_radar_artifacts(text)
    text = suppress_readacross(text, config_path)
    text = reorder_sections(text)
    return text


MAX_DROPPED_LISTED = 10
HEADLINE_MATCH_CHARS = 40


def _norm_text(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def compute_dropped_records(raw_markdown, records):
    """Records the writer received but never rendered. Returns (count, ids).

    The deterministic HTML renderer builds from the same records the writer gets,
    so a silently omitted record makes the Markdown and the published HTML
    disagree about what the digest contains. Run this against the writer's RAW
    output, before suppress_readacross() legitimately removes a whole section.

    Matches on the HEADLINE, not on reference numbers. A reference number can
    appear in prose that *explains why an item was left out* — a note reading
    "NPRM 2026-16956 ... excluded here" would otherwise satisfy a check meant to
    prove the item was included. Only a rendered item carries its headline.
    """
    haystack = _norm_text(raw_markdown)
    missing = []
    for rec in records or []:
        needle = _norm_text(rec.get("headline"))[:HEADLINE_MATCH_CHARS]
        if not needle or needle not in haystack:
            missing.append(rec.get("id") or "?")
    return len(missing), missing[:MAX_DROPPED_LISTED]


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
    ap.add_argument("--preflight", default=None,
                    help="00_preflight.json from tools/preflight.py (optional).")
    ap.add_argument("--records", default=None,
                    help="06_deduped.json final records (optional; enables the degraded rule).")
    ap.add_argument("--sweep", default=None,
                    help="00_sweep.json (optional; enables the recall_partial rule).")
    ap.add_argument("--coverage", default=None,
                    help="12_coverage.json (optional; enables the unaccounted/recall-gap rule).")
    ap.add_argument("--health-out", default=None,
                    help="Write {degraded, reason} JSON here for downstream steps.")
    args = ap.parse_args(argv)

    with open(args.infile, encoding="utf-8") as fh:
        text = fh.read()
    fixed = finalize(text, config_path=args.config)

    def _load_json(path, rule):
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as fh2:
                return json.load(fh2)
        except (OSError, ValueError):
            print("[finalise] WARNING: --%s %s supplied but unreadable — "
                  "%s degraded rule partially disabled" % (rule, path, rule), file=sys.stderr)
            return None

    preflight = _load_json(args.preflight, "preflight")
    payload = _load_json(args.records, "records")
    records = (payload or {}).get("records", []) if isinstance(payload, dict) else (payload or [])
    dropped_count, dropped_ids = compute_dropped_records(text, records)
    if dropped_count:
        print("[finalise] WRITER DROPPED %d record(s) the HTML will still contain — "
              "Markdown and HTML now disagree: %s"
              % (dropped_count, ", ".join(dropped_ids)), file=sys.stderr)

    degraded, reason = compute_degraded(preflight, records)
    if degraded:
        fixed = insert_degraded_banner(fixed, reason)
        print("[finalise] DEGRADED: %s" % reason, file=sys.stderr)

    sweep = None
    if args.sweep:
        try:
            with open(args.sweep, encoding="utf-8") as fh3:
                sweep = json.load(fh3)
        except (OSError, ValueError):
            print("[finalise] WARNING: cannot read sweep %s; recall rule disabled" % args.sweep,
                  file=sys.stderr)
    recall, recall_reason = compute_recall_partial(sweep)
    if recall:
        fixed = insert_recall_notice(fixed, recall_reason)
        print("[finalise] RECALL PARTIAL: %s" % recall_reason, file=sys.stderr)

    coverage = None
    if args.coverage:
        try:
            with open(args.coverage, encoding="utf-8") as fh4:
                coverage = json.load(fh4)
        except (OSError, ValueError):
            print("[finalise] WARNING: cannot read coverage %s; recall-gap rule disabled"
                  % args.coverage, file=sys.stderr)
    unaccounted_count, unaccounted_refs = compute_unaccounted(coverage)
    if unaccounted_count:
        fixed = insert_recall_gap_notice(fixed, unaccounted_count, unaccounted_refs)
        print("[finalise] RECALL GAP: %d unaccounted AD(s): %s"
              % (unaccounted_count, ", ".join(unaccounted_refs)), file=sys.stderr)

    if args.health_out:
        with open(args.health_out, "w", encoding="utf-8") as fh2:
            json.dump({"degraded": degraded, "reason": reason,
                       "recall_partial": recall, "recall_reason": recall_reason,
                       "unaccounted_count": unaccounted_count,
                       "unaccounted_refs": unaccounted_refs,
                       "dropped_count": dropped_count,
                       "dropped_ids": dropped_ids,
                       "inputs": {"preflight": preflight is not None,
                                  "records": payload is not None}}, fh2)

    if args.in_place:
        with open(args.infile, "w", encoding="utf-8") as fh:
            fh.write(fixed)
    else:
        sys.stdout.write(fixed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
