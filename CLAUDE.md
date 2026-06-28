# CLAUDE.md — Aviation Technical-Intelligence Digest

Project instructions for Claude Code. These override default behaviour for this repo.

## What this project is

A personal, recurring (default **weekly**) aviation technical-intelligence digest tracking the
HK carrier fleet using public sources only. Output is a verified, fleet-scoped Markdown report
covering ADs, EADs, SBs, SILs, service letters (SL), MSG-3 task changes, incidents, and major
industry events (the "UPS MD-11" class of event).

This is **engineer-grade** intelligence, not commercial airline news. Content is written at
expert level (technical services / line & base maintenance / reliability engineering) — do NOT define
AD, SB, SIL, SL, EAD, MSG-3, etc. in the output.

## Public-internet-only rule

**Use only information that can be found on the public internet.** No authenticated logins, no
credentialed OEM/airline customer portals, no paywalled or internal sources. If a primary
document is not publicly fetchable, the reference cannot be `[VERIFIED]` — mark it
`[UNVERIFIED — reference exists, text not accessed]` and move on. Never invent the missing text.

## The hard constraint (the whole point of the project)

**Every regulatory reference, revision, date, effectivity, and quote MUST be confirmed
against a live-fetched, publicly accessible primary source (regulator or OEM) before it
enters the report.**

- No reference goes in from memory, a headline, or a trade-press summary alone.
- Trade press generates **leads only** — it can NEVER be the sole source for a technical reference.
- Every quote comes from a document actually fetched, kept **under ~25 words**, with title, ref,
  revision/date, and URL attached. Gated/paywalled text is marked UNVERIFIED, never invented.
- Every technical claim carries a confidence tag:
  - `[VERIFIED — primary source]`
  - `[REPORTED — trade press, unconfirmed]`
  - `[UNVERIFIED — reference exists, text not accessed]`
- Current date is computed **at runtime**. Anything outside the lookback window is dropped or
  explicitly flagged as a carried-over developing event — never silently presented as new.

**A short verified digest always beats a long speculative one.**

## Never do

- Invent, guess, or "fill in" any reference number, revision, date, or quote.
- Present trade-press reporting as a confirmed regulatory/OEM reference.
- Issue your own airworthiness determinations — report the OEM/regulator position only.
- Pad the digest with commercial/route/financial news to hit a length. (The `## Standing Watch`
  section is NOT padding: it carries forward-looking *primary-source* intelligence — Compliance
  Radar of upcoming AD effective dates and On-the-Horizon proposed rules — plus a clearly walled-off
  curated Engineer's Corner. None of it asserts an unverified reference; all of it is structurally
  separated from the verified incident intelligence above it.)

## Fleet scope (see `config/` once built — this is the source of truth in prose form)

- **Operator:** Cathay Pacific
- **Fleet:** A330-300, A321neo, A350-900, A350-1000, 777-300ER
- **Engines:** Trent 700, Trent XWB-84/-97, PW1100G / LEAP-1A (A321neo), GE90-115B (777-300ER)
- **Peers / read-across:** A320neo family, A340, 787, 767, other Trent-powered widebodies
- **Cadence / window:** default weekly / last 7 days (parameterized)

## Architecture (decided in Stage 1)

**Multi-agent pipeline orchestrated by one slash command** — chosen because the verification
gate must be *structural*, not merely instructed:

1. **Orchestrator (slash command):** computes runtime date, loads fleet config, sets lookback
   window, dispatches stages.
2. **Scanner subagent(s):** run many targeted queries across source tiers; output candidate
   leads as structured records, all tagged UNVERIFIED. Forbidden from asserting reference
   numbers as fact (captures `claimed_ref` + URL to chase). May fan out in parallel.
3. **Verifier subagent:** fetches each primary document, confirms ref/rev/date/effectivity
   against actual text, extracts verbatim quotes only from fetched text, assigns confidence tags,
   and **drops/downgrades anything it cannot stand behind.**
4. **Gate** (`tools/validate_records.py`): deterministic. Mechanically downgrades any `[VERIFIED]`
   lacking an allowlisted `primary_source_url` + `fetched_text_snippet`; drops off-allowlist/over-
   length quotes; enforces the lookback window. → `03_sanitised.json`.
5. **Auditor subagent (independent re-fetch):** re-opens every still-`VERIFIED` URL **fresh**,
   independently confirms the reference number and quoted text genuinely appear there, and writes a
   real `audit` excerpt. It does NOT trust the verifier's snippet. → `04_audited.json`.
6. **Audit gate** (`validate_records.py --require-audit`): deterministic. Downgrades any
   `[VERIFIED]` the auditor did not `confirm` with a real excerpt. → `05_final.json`.
7. **Writer subagent:** receives ONLY `05_final.json`, emits the Markdown. Has **no web/fetch
   tools** — it physically cannot cite a reference the pipeline didn't supply. Renders a clickable
   `[source]` link on every reference.

**A reference keeps `[VERIFIED]` only if BOTH the Verifier and the independent Auditor confirmed it
from the live primary source, and both deterministic gates passed.**

### What makes the gates enforced, not instructed
- **Context isolation:** the writer never sees unverified data.
- **Tool restriction:** the writer cannot fetch, so it cannot invent/fill references.
- **Schema contract:** every inter-stage record must carry `primary_source_url` +
  `fetched_text_snippet`; records missing them are auto-dropped before the writer runs.
- **Provenance allowlist:** `primary_source_url` must match a regulator/OEM domain allowlist,
  so trade-press URLs can never satisfy a `[VERIFIED]` claim.
- **Independent re-fetch:** a separate auditor must re-confirm the cited text from the live source;
  a single agent's hallucination cannot survive two independent fetches + two deterministic gates.

## Source tiers (consult in priority order)

1. **Regulators / ADs:** FAA (Federal Register / DRS), EASA AD portal (ad.easa.europa.eu),
   HKCAD, Transport Canada, CAAC, UK CAA.
2. **OEMs:** Boeing, Airbus, Rolls-Royce, GE Aerospace, Pratt & Whitney service & press rooms
   (SB/SL access may be gated — mark UNVERIFIED if text not accessible).
3. **Investigators:** NTSB, AAIB, BEA, TSB Canada, Hong Kong AAIA.
4. **Trade press (leads / corroboration only, never sole source for a technical ref):**
   Aviation Week, The Air Current, Leeham News, FlightGlobal, AeroTime, ch-aviation, Avherald.

## Output format

Markdown, grouped:
`## Directly Fleet-Relevant` → `## Read-Across (Peer Types)` → `## Major Industry Events`

**Categorisation rules (relevance to the tracked fleet is the priority):**
- **Fleet** = same aircraft TYPE or engine TYPE as a `fleet` entry, **regardless of operator**.
  A JAL A350-1000 / Trent XWB-97 event is *fleet*, not read-across — the tracked fleet flies that exact type. An
  AD that explicitly lists a tracked variant in its effectivity is *fleet* even if discovered as a peer
  lead.
- **Read-Across** = peer type only (shared system / engine family / OEM design / regulatory
  precedent). **Deliberately lower priority:** the `## Read-Across` section is **suppressed entirely
  when there are ≥ `standing_watch.read_across_min_fleet_items` (default 5) fleet items** that week,
  so a busy fleet week is never diluted by peer-type noise. Enforced mechanically in
  `tools/finalize_digest.py`, not by writer instruction.
- **Major Industry Events** = NOT fleet-type bounded — a channel for big worldwide events (the
  "UPS MD-11" class) whatever the aircraft type.
- **Standing Watch** (Compliance Radar + On the Horizon) is **fleet-scoped**: only NPRMs/PADs/ADs
  for the tracked fleet types appear — proposed rules on aircraft not in the fleet are excluded.

Per item:
- **[Triage headline]** — type(s), date.
- *What happened:* 1-3 sentences.
- *Technical detail:* references (AD/SB/SIL/SL + rev), effectivity, root cause, OEM/regulator
  position; confidence tags inline.
- *Verbatim (if any):* "<short quote>" — Document title, ref, rev/date, [URL].
- *Read-across to fleet:* one line (omit if N/A).

End with a **Sources & Confidence** line (count of VERIFIED vs UNVERIFIED items).
Target ~1,000 words for the weekly. Brevity over completeness.

After the three core sections (and before the Sources line) a `## Standing Watch` section may
appear: **Compliance Radar** (upcoming AD effective dates, from `tools/compliance_radar.py`),
**On the Horizon** (NPRM/PAD proposed rules, gate-verified like any reference), and **Engineer's
Corner** (curated evergreen explainer from `config/engineers_corner.json`, shown only on thin
weeks). It guarantees a minimum of substantial reading without diluting the verified core.

## Working agreement

- Build in **stages** — do not write the whole pipeline at once. See `roadmap.md` for status.
- Stop at stage boundaries and confirm before proceeding.
- Keep `roadmap.md` updated as we complete work so progress is checkable later.

## Open questions to resolve

- _(resolved 2026-06-25)_ Gated OEM docs → **public internet only**. Mark UNVERIFIED and move on;
  never wire an authenticated/credentialed portal. See *Public-internet-only rule* above.
