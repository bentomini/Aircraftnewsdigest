#!/usr/bin/env python3
"""preflight.py — primary-source reachability probe (Step 0 of a digest run).

Answers ONE question deterministically before the pipeline spends anything:
can this environment fetch the primary-source endpoints the verifier/auditor
depend on? The July 2026 failure mode (three weeks of 0-VERIFIED digests,
cloud egress proxy silently blocking regulators) becomes visible at Step 0
and, via finalize_digest.py --preflight, flags the whole run [DEGRADED].

Probe targets are stable, permanent public documents (FR docs never move);
any stable public document on each host works — update if one ever 404s.
A redirect landing on unblock.federalregister.gov (the FR bot wall) counts
as BLOCKED even though the final status is 200.

Stdlib only. Usage:
    python preflight.py --outfile runs/2026-08-02/00_preflight.json
Exit 0 if at least one endpoint is reachable, 1 if all are blocked.
"""
import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone

BOT_WALL_HOSTS = ("unblock.federalregister.gov",)

ENDPOINTS = [
    ("fr_api", "https://www.federalregister.gov/api/v1/documents/2026-13481.json"),
    ("govinfo", "https://www.govinfo.gov/content/pkg/FR-2026-07-02/html/2026-13481.htm"),
    ("easa_ad", "https://ad.easa.europa.eu/ad/2026-0134"),
    ("ntsb", "https://www.ntsb.gov/investigations/Pages/aviation.aspx"),
]

# Some .gov hosts 403 the default urllib User-Agent; a plain descriptive UA passes.
_UA = "Mozilla/5.0 (aviation-digest-preflight)"


def evaluate(status, final_url):
    """True iff the fetch genuinely reached the document (200, not a bot wall)."""
    if status != 200:
        return False
    host = (final_url or "")
    return not any(w in host for w in BOT_WALL_HOSTS)


def probe(url, timeout=20):
    """GET the URL following redirects. Returns (status, final_url, error)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(512)  # touch the body; we only need proof of access
            return resp.status, resp.geturl(), None
    except Exception as exc:  # noqa: BLE001 — any failure is just "blocked"
        return None, None, str(exc)


def run(endpoints, probe_fn, checked_at):
    """Probe every endpoint; return the report dict (pure given probe_fn)."""
    results = []
    for name, url in endpoints:
        status, final_url, error = probe_fn(url)
        results.append({
            "name": name, "url": url, "status": status,
            "final_url": final_url, "ok": evaluate(status, final_url),
            "error": error,
        })
    return {
        "checked_at": checked_at,
        "endpoints": results,
        "any_ok": any(r["ok"] for r in results),
        "all_ok": all(r["ok"] for r in results),
    }


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Primary-source reachability probe.")
    ap.add_argument("--outfile", default="-", help="Report JSON path, or - for stdout.")
    ap.add_argument("--timeout", type=int, default=20, help="Per-endpoint timeout (s).")
    args = ap.parse_args(argv)

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = run(ENDPOINTS, lambda u: probe(u, timeout=args.timeout), checked_at)

    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.outfile == "-":
        sys.stdout.write(payload + "\n")
    else:
        with open(args.outfile, "w", encoding="utf-8") as fh:
            fh.write(payload)
    for r in report["endpoints"]:
        sys.stderr.write("[preflight] %-8s %s %s\n"
                         % (r["name"], "OK " if r["ok"] else "BLOCKED",
                            r["error"] or (r["final_url"] or "")))
    sys.stderr.write("[preflight] any_ok=%s all_ok=%s\n"
                     % (report["any_ok"], report["all_ok"]))
    return 0 if report["any_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
