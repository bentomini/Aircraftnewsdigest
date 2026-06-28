# Aviation Technical-Intelligence Digest — Build Brief

## What I'm building
A personal recurring news digest tracking the HK carrier fleet using public sources.
The output is a verified, fleet-scoped Markdown report covering ADs, SBs, SILs, SLs,
service letters, MSG-3 task changes, incidents, and major industry events.

The hard constraint: every regulatory reference, revision, date, and quote in the
output MUST be confirmed against a primary source (regulator or OEM) fetched live.
No reference goes in the report from memory, from a headline, or from a trade-press
summary alone. A short verified digest always beats a long speculative one.

## Help me build this in stages — don't write the whole thing at once

### Stage 1 — Architecture decision (do this first, then stop and discuss)
Propose a structure. I'm undecided between:
- a single slash command that runs the whole scan→verify→write pipeline, vs.
- a multi-agent setup (scanner agent → verifier agent → writer agent) with the
  verifier as a hard gate that drops anything it can't confirm.

Give me your recommendation with reasoning, not a menu. Consider: token cost per run,
how to stop unverified claims leaking from scan into the final report, and how to make
the verification gate actually enforced rather than just instructed.

### Stage 2 — The fleet config
Externalize all the operator/fleet/engine scoping into a config file (YAML or JSON)
so the same pipeline works if the fleet changes. It needs:
- Operator: Cathay Pacific
- Fleet: A330-300, A321neo, A350-900, A350-1000, 777-300ER
- Engines: Trent 700, Trent XWB-84/-97, PW1100G / LEAP-1A (A321neo), GE90-115B (777-300ER)
- Peer/read-across types: A320neo family, A340, 787, 767, other Trent widebodies
- Source tiers (consult in priority order): regulators → OEMs → investigators → trade press
- Lookback window and cadence (parameterized, default weekly / 7 days)

### Stage 3 — The verification layer (most important — spend the most effort here)
This is the part that has to be bulletproof. Design it so that:
- Trade press can generate leads but can NEVER be the sole source for a technical reference.
- Every AD/SB/SIL/SL number, revision, date, and effectivity is re-fetched from the
  primary source and the actual document text is read before it's allowed into the report.
- Every quote is pulled from a document actually fetched, kept under ~25 words, with title,
  ref, revision/date, and URL attached. Gated/paywalled text is marked UNVERIFIED, never invented.
- Each technical claim carries a confidence tag: [VERIFIED — primary source] /
  [REPORTED — trade press, unconfirmed] / [UNVERIFIED — reference exists, text not accessed].
- The current date is computed at runtime, and any item outside the lookback window is
  dropped or explicitly flagged as a carried-over developing event — never silently
  presented as new.

### Stage 4 — Output format
Markdown, grouped: Directly Fleet-Relevant → Read-Across (Peer Types) → Major Industry Events.
Per item: triage headline + type/date; what happened (1–3 sentences); technical detail
(refs + rev, effectivity, root cause, OEM/regulator position, confidence tags inline);
verbatim quote if any with full source attribution; read-across line for peer events.
End with a Sources & Confidence summary (count of VERIFIED vs UNVERIFIED items).
Target ~1,000 words for the weekly.

## Constraints
- Never invent or guess a reference number, revision, date, or quote.
- Never present trade-press reporting as a confirmed regulatory/OEM reference.
- Never issue your own airworthiness determinations — report the OEM/regulator position only.
- No padding with commercial/route/financial news to hit a length.

## Source portals to wire in
FAA (Federal Register / DRS), EASA AD portal (ad.easa.europa.eu), HKCAD, Transport Canada,
CAAC, UK CAA; NTSB, AAIB, BEA, TSB, Hong Kong AAIA; Boeing/Airbus/Rolls-Royce/GE/P&W
service & press rooms. Trade press for leads only: Aviation Week, The Air Current, Leeham,
FlightGlobal, AeroTime, ch-aviation, Avherald.

Start with Stage 1. Give me your architecture recommendation and wait for my response
before building.