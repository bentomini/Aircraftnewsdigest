# HK Aviation Fleet Watch

A personal aviation technical-intelligence digest, produced weekly from public internet sources only.

It tracks airworthiness directives, safety bulletins, incidents, and regulatory developments for a
specific fleet of aircraft — the types operated by the Hong Kong carrier — and produces a verified,
structured report that could be useful to anyone following aviation safety and engineering.

---

## What it produces

A weekly digest in three formats:

- **Markdown** (`.md`) — clean, readable anywhere
- **HTML** (`.html`) — styled, magazine-layout publication
- **PDF** (`.pdf`) — print-ready

Each digest covers the past 7 days of relevant developments, grouped as:

1. **Directly Fleet-Relevant** — ADs, service bulletins, incidents directly affecting the tracked aircraft types
2. **Read-Across (Peer Types)** — events on related aircraft that may have implications for the fleet
3. **Major Industry Events** — significant worldwide events regardless of aircraft type
4. **Standing Watch** — upcoming compliance deadlines, proposed rules on the horizon, and background explainers

Every item carries a confidence label: `[VERIFIED — primary source]`, `[REPORTED — trade press, unconfirmed]`, or `[UNVERIFIED — reference exists, text not accessed]`.

---

## Fleet tracked

| Aircraft | Engines |
|---|---|
| A330-300 | Rolls-Royce Trent 700 |
| A321neo | CFM LEAP-1A |
| A350-900 | Rolls-Royce Trent XWB-84 |
| A350-1000 | Rolls-Royce Trent XWB-97 |
| 777-300ER | GE90-115B |

Peer types (A320neo family, A340, 787, 767, other Trent-powered widebodies) are monitored for read-across.

---

## How it works

Running `/digest` kicks off an automated pipeline. Here is what happens, in plain terms:

### 1. Scanner — finding the news

An AI agent searches across dozens of official and trade-press sources: the FAA and EASA regulatory portals, airline accident investigators (NTSB, AAIB, BEA), and aviation trade press (Aviation Week, FlightGlobal, etc.). It casts a wide net, covering each aircraft type, engine variant, and regulator separately.

The scanner intentionally *never* asserts anything as fact — it produces a list of leads tagged `UNVERIFIED`.

### 2. Verifier — checking against primary sources

A second AI agent takes each lead and fetches the actual official document — the regulatory directive, the investigation report, the OEM notice — directly from the government or regulator's website. It reads the document and confirms whether the reference number, date, and technical details actually appear there.

If the document is behind a paywall, gated, or simply not publicly available, the item is marked `UNVERIFIED` and nothing is invented to fill the gap. Trade-press articles can generate leads but can never, on their own, be the basis for a verified claim.

### 3. Deterministic gate — a rule-enforcing script

After the Verifier, a Python script (not an AI — a deterministic program with fixed rules) mechanically checks every record. Any item that claims to be `VERIFIED` but lacks:
- a URL from an approved list of government/regulator domains, and
- an actual excerpt of text fetched from that URL

…is automatically downgraded. No exceptions, no overrides.

### 4. Auditor — independent re-check

A third AI agent, working completely independently and without seeing the Verifier's notes, re-opens every URL that still carries a `VERIFIED` tag and checks the document again from scratch. It is a second pair of eyes that does not trust the first.

A second gate script then enforces the auditor's findings: if the Auditor could not confirm a claim, it is downgraded — even if the Verifier said it was fine.

**A reference only keeps `[VERIFIED]` if both the Verifier and the independent Auditor confirmed it from the live source, and both gate scripts agreed.**

### 5. Supporting tools (Python scripts)

Several small automated programs handle bookkeeping:

- **Dedup ledger** — remembers what was reported last week so unchanged items are not repeated; if a reference is updated (new revision, new date), it is re-surfaced and tagged `[UPDATED]`
- **Compliance Radar** — tracks upcoming AD effective dates and surfaces them in the "Standing Watch" section before they become overdue
- **Engineer's Corner** — on quiet weeks, selects a short curated explainer from a bank of evergreen technical background pieces

### 6. Writer — producing the digest

A final AI agent, which has no ability to search the internet or fetch any URLs, receives only the cleaned, gate-passed records and writes the digest in the agreed format. Because it physically cannot look anything up, it cannot invent a reference, a date, or a quote that was not already confirmed upstream.

### 7. Publication

Python scripts then build the HTML and PDF versions from the same gate-passed data, and the imagery scout finds one relevant photo per item where available. Public-domain images (from NTSB, FAA, EASA) may be embedded directly; copyrighted trade-press or OEM photos appear as attribution links only.

---

## Why so many checks?

Aviation regulatory references are specific — an AD number, revision, effectivity range, compliance date. Getting any of these wrong is worse than saying nothing. The multi-stage verification pipeline exists to make it structurally impossible for a hallucinated or misquoted reference to reach the final output, not just instructionally discouraged.

A short verified digest always beats a long speculative one.

---

## Sources used

All information is drawn from publicly accessible sources only. No authenticated portals, no paywalled documents.

**Tier 1 — Regulators (primary source for `VERIFIED` claims)**
FAA (Federal Register / govinfo.gov), EASA, HKCAD, Transport Canada, CAAC, UK CAA

**Tier 2 — OEMs (primary source when publicly fetchable)**
Boeing, Airbus, Rolls-Royce, GE Aerospace, Pratt & Whitney

**Tier 3 — Accident investigators (primary source)**
NTSB, AAIB, BEA, TSB Canada, Hong Kong AAIA

**Tier 4 — Trade press (leads only, never sole source)**
Aviation Week, The Air Current, Leeham News, FlightGlobal, AeroTime, ch-aviation, The Aviation Herald

---

## Project structure

```
config/         Fleet scope, source tiers, pipeline settings
digests/        Weekly output files (.md, .html, .pdf)
runs/           Per-run pipeline artifacts (scanner → verifier → gates → writer)
schema/         Record format contract enforced between pipeline stages
tools/          Deterministic Python scripts (gates, dedup, radar, renderer, PDF)
.claude/
  agents/       AI agent instructions (scanner, verifier, auditor, writer, imagery)
  commands/     The /digest slash command that runs the whole pipeline
```

---

*Personal project. Uses public internet sources only. Not affiliated with any airline or regulator.*
