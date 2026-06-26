#!/usr/bin/env python3
"""validate_records.py — the deterministic verification gate.

Runs between the Verifier subagent and the Writer subagent. It mechanically
re-checks the verification gate the Verifier was *instructed* to honour, and
SANITISES the records so an illegal claim can never reach the Writer:

  * a [VERIFIED] reference must have a primary_source_url whose domain is on the
    allowlist AND a non-trivial fetched_text_snippet — else it is downgraded to
    [UNVERIFIED];
  * a quote must come from an allowlisted URL and be within the word limit —
    else it is dropped;
  * item_confidence is recomputed as the highest surviving reference confidence;
  * records outside the lookback window are dropped unless flagged as developing
    carry-overs (which are kept and marked within_window=False).

This is the structural enforcement: the gate holds even if the Verifier LLM is
wrong or adversarial, because the Writer only ever sees this script's output.

Stdlib only. Usage:
    python validate_records.py --config ../config/fleet.yaml \\
        --current-date 2026-06-25 --lookback-days 7 < verifier_output.json > clean.json
Exit code 0 if nothing changed, 1 if any sanitisation occurred (run is flagged).
"""
import argparse
import json
import re
import sys
from datetime import date
from urllib.parse import urlparse

MIN_SNIPPET_LEN = 20  # a fetched_text_snippet shorter than this is not real proof
_CONF_RANK = {"UNVERIFIED": 0, "REPORTED": 1, "VERIFIED": 2}
_RANK_CONF = {0: "UNVERIFIED", 1: "REPORTED", 2: "VERIFIED"}


def extract_domain(url):
    """Host of a URL, lower-cased, with a leading 'www.' stripped. '' if none."""
    if not url:
        return ""
    netloc = urlparse(url).netloc.lower()
    if not netloc:  # urlparse puts bare 'host/path' in path, not netloc
        netloc = urlparse("//" + url).netloc.lower()
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def domain_in_allowlist(domain, allowlist):
    """True if domain equals or is a subdomain of an allowlisted domain.

    'drs.faa.gov' matches 'faa.gov'; 'evilfaa.gov' and 'faa.gov.evil.com' do not.
    """
    domain = (domain or "").lower()
    for allowed in allowlist:
        allowed = allowed.lower()
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def sanitize_reference(reference, allowlist):
    """Return (reference, violations). Downgrade an illegal VERIFIED to UNVERIFIED."""
    r = dict(reference)
    violations = []
    url = r.get("primary_source_url")
    r["primary_source_domain"] = extract_domain(url) if url else None

    if r.get("confidence") == "VERIFIED":
        snippet = (r.get("fetched_text_snippet") or "").strip()
        reasons = []
        if not url:
            reasons.append("no primary_source_url")
        elif not domain_in_allowlist(r["primary_source_domain"], allowlist):
            reasons.append("primary_source_url domain '%s' not on allowlist"
                           % r["primary_source_domain"])
        if len(snippet) < MIN_SNIPPET_LEN:
            reasons.append("missing/trivial fetched_text_snippet")
        if reasons:
            r["confidence"] = "UNVERIFIED"
            violations.append("ref %s downgraded VERIFIED->UNVERIFIED: %s"
                              % (r.get("ref_number", "?"), "; ".join(reasons)))
    return r, violations


def sanitize_quote(quote, allowlist, max_words):
    """Return (keep: bool, reason). A quote must be allowlisted and within limit."""
    url = quote.get("url") or ""
    if not url:
        return False, "quote dropped: missing url"
    if not domain_in_allowlist(extract_domain(url), allowlist):
        return False, "quote dropped: url domain not on allowlist (%s)" % url
    words = len((quote.get("text") or "").split())
    if words > max_words:
        return False, "quote dropped: %d words exceeds max_words=%d" % (words, max_words)
    if words == 0:
        return False, "quote dropped: empty text"
    return True, ""


def rollup_confidence(references, has_event_reporting):
    """Highest confidence among references; REPORTED if none but a real event; else UNVERIFIED."""
    if references:
        best = max(_CONF_RANK.get(r.get("confidence", "UNVERIFIED"), 0) for r in references)
        return _RANK_CONF[best]
    return "REPORTED" if has_event_reporting else "UNVERIFIED"


def enforce_window(record, current_date, lookback_days):
    """Return (keep: bool, reason). Set record['within_window']. Drop stale non-carryovers."""
    ev = record.get("event_date")
    within = _in_window(ev, current_date, lookback_days)
    record["within_window"] = within
    if not within:
        if record.get("developing_carryover"):
            return True, ""
        return False, ("record %s dropped: event_date %s outside %d-day window"
                       % (record.get("id", "?"), ev, lookback_days))
    return True, ""


def _in_window(event_date, current_date, lookback_days):
    if not event_date:
        return True  # undated items are kept; the writer/human can judge
    try:
        ev = date.fromisoformat(event_date)
        now = date.fromisoformat(current_date)
    except (ValueError, TypeError):
        return True
    delta = (now - ev).days
    return 0 <= delta <= lookback_days


def sanitize_record(record, allowlist, max_words, current_date, lookback_days):
    """Return (record_or_None, violations) after applying every gate to one record."""
    violations = []
    keep, reason = enforce_window(record, current_date, lookback_days)
    if not keep:
        return None, [reason]

    clean_refs = []
    for reference in record.get("references", []):
        r, viol = sanitize_reference(reference, allowlist)
        violations.extend(viol)
        clean_refs.append(r)
    record["references"] = clean_refs

    kept_quotes = []
    for q in record.get("quotes", []):
        keep_q, reason = sanitize_quote(q, allowlist, max_words)
        if keep_q:
            kept_quotes.append(q)
        else:
            violations.append("%s: %s" % (record.get("id", "?"), reason))
    record["quotes"] = kept_quotes

    has_event = bool((record.get("summary") or "").strip()) or bool(record.get("lead_sources"))
    new_conf = rollup_confidence(clean_refs, has_event_reporting=has_event)
    if new_conf != record.get("item_confidence"):
        violations.append("%s: item_confidence %s->%s (recomputed)"
                          % (record.get("id", "?"), record.get("item_confidence"), new_conf))
    record["item_confidence"] = new_conf
    return record, violations


def sanitize(payload, allowlist, max_words, current_date, lookback_days):
    """Sanitise a {'records': [...]} payload. Return (cleaned_payload, report)."""
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    cleaned, all_violations, dropped = [], [], 0
    for rec in records:
        out, viol = sanitize_record(dict(rec), allowlist, max_words, current_date, lookback_days)
        all_violations.extend(viol)
        if out is None:
            dropped += 1
        else:
            cleaned.append(out)
    report = {"violations": all_violations, "dropped": dropped, "kept": len(cleaned)}
    return {"records": cleaned}, report


MIN_AUDIT_EXCERPT_LEN = 20  # an independent-audit excerpt shorter than this is not proof


def enforce_audit_reference(reference):
    """Downgrade a VERIFIED reference unless an independent re-fetch confirmed it.

    Requires reference['audit'] = {status: 'confirmed', excerpt: <real re-fetched text>}.
    Any other status (not_found / fetch_failed / missing) → UNVERIFIED.
    """
    r = dict(reference)
    violations = []
    url = r.get("primary_source_url")
    if url and not r.get("primary_source_domain"):
        r["primary_source_domain"] = extract_domain(url)

    if r.get("confidence") == "VERIFIED":
        audit = r.get("audit") or {}
        excerpt = (audit.get("excerpt") or "").strip()
        reasons = []
        if audit.get("status") != "confirmed":
            reasons.append("audit status %r != 'confirmed'" % audit.get("status"))
        if len(excerpt) < MIN_AUDIT_EXCERPT_LEN:
            reasons.append("missing/trivial independent-audit excerpt")
        if reasons:
            r["confidence"] = "UNVERIFIED"
            violations.append("ref %s downgraded VERIFIED->UNVERIFIED (audit): %s"
                              % (r.get("ref_number", "?"), "; ".join(reasons)))
    return r, violations


def enforce_audit_record(record):
    """Apply the audit gate to one record: downgrade refs, drop unbacked quotes, re-roll up."""
    violations = []
    clean_refs = []
    for reference in record.get("references", []):
        r, viol = enforce_audit_reference(reference)
        violations.extend(viol)
        clean_refs.append(r)
    record["references"] = clean_refs

    # A quote may only survive if a reference from its own document remained VERIFIED.
    surviving = {r.get("primary_source_domain")
                 for r in clean_refs if r.get("confidence") == "VERIFIED"}
    surviving.discard(None)
    kept_quotes = []
    for q in record.get("quotes", []):
        if extract_domain(q.get("url")) in surviving:
            kept_quotes.append(q)
        else:
            violations.append("%s: quote dropped (audit): no audit-confirmed backing reference"
                              % record.get("id", "?"))
    record["quotes"] = kept_quotes

    has_event = bool((record.get("summary") or "").strip()) or bool(record.get("lead_sources"))
    new_conf = rollup_confidence(clean_refs, has_event_reporting=has_event)
    if new_conf != record.get("item_confidence"):
        violations.append("%s: item_confidence %s->%s (post-audit)"
                          % (record.get("id", "?"), record.get("item_confidence"), new_conf))
    record["item_confidence"] = new_conf
    return record, violations


def enforce_audit(payload):
    """Apply the independent-audit gate to a {'records': [...]} payload."""
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    cleaned, all_violations = [], []
    for rec in records:
        out, viol = enforce_audit_record(dict(rec))
        all_violations.extend(viol)
        cleaned.append(out)
    return {"records": cleaned}, {"violations": all_violations, "kept": len(cleaned), "dropped": 0}


def parse_allowlist_from_yaml(text):
    """Extract the list under 'verified_domains:' from fleet.yaml without a YAML lib."""
    domains, in_block = [], False
    for line in text.splitlines():
        if re.match(r"^verified_domains:\s*$", line):
            in_block = True
            continue
        if in_block:
            if re.match(r"^\S", line):  # next top-level key ends the block
                break
            m = re.match(r"^\s+-\s+(\S+)", line)
            if m:
                domains.append(m.group(1).strip())
    return domains


def parse_max_words_from_yaml(text, default=25):
    m = re.search(r"^\s+max_words:\s*(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else default


def main(argv=None):
    # Always emit UTF-8 regardless of the OS locale. The orchestrator redirects
    # this script's stdout to a file that a later stage reads back as UTF-8; on
    # Windows a redirected stdout otherwise defaults to cp1252 and a non-ASCII
    # byte (e.g. an em-dash) would make that next read fail.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Deterministic verification gate.")
    ap.add_argument("--config", default="config/fleet.yaml",
                    help="Path to fleet.yaml (for verified_domains + quotes.max_words).")
    ap.add_argument("--current-date", required=True, help="Runtime date, YYYY-MM-DD.")
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--infile", default="-", help="Records JSON path, or - for stdin.")
    ap.add_argument("--require-audit", action="store_true",
                    help="Also enforce the independent re-fetch audit gate: a VERIFIED reference "
                         "is downgraded unless reference['audit'].status == 'confirmed' with a real "
                         "excerpt. Run this AFTER the auditor agent has added audit fields.")
    args = ap.parse_args(argv)

    with open(args.config, encoding="utf-8") as f:
        cfg_text = f.read()
    allowlist = parse_allowlist_from_yaml(cfg_text)
    max_words = parse_max_words_from_yaml(cfg_text)
    if not allowlist:
        sys.stderr.write("ERROR: no verified_domains found in %s\n" % args.config)
        return 2

    raw = sys.stdin.read() if args.infile == "-" else open(args.infile, encoding="utf-8").read()
    payload = json.loads(raw)

    cleaned, report = sanitize(
        payload, allowlist, max_words, args.current_date, args.lookback_days)

    label = "gate"
    if args.require_audit:
        cleaned, audit_report = enforce_audit(cleaned)
        report["violations"].extend(audit_report["violations"])
        label = "gate+audit"

    sys.stdout.write(json.dumps(cleaned, indent=2, ensure_ascii=False))

    sys.stderr.write("\n[%s] kept=%d dropped=%d violations=%d\n"
                     % (label, report["kept"], report["dropped"], len(report["violations"])))
    for vmsg in report["violations"]:
        sys.stderr.write("  - %s\n" % vmsg)
    return 1 if (report["violations"] or report["dropped"]) else 0


if __name__ == "__main__":
    sys.exit(main())
