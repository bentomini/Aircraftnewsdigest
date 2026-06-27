# Aviation Technical-Intelligence Digest — Specification

**Status:** Spec written from intent (idea.md + prompt.md). Build is ~95% complete (Stages 1–4.5 done; Stage 5 live-validation pending).
**Date:** 2026-06-26
**Owner:** Joseph (solo founder)
**Source of truth for scope:** `config/fleet.yaml`. **Source of truth for contract:** `CLAUDE.md`.

This document specifies the system as *intended* by `idea.md` and `prompt.md`. Section 8 (Gap
Analysis) flags every place the current build diverges from this intent.

---

## 1. Purpose

A recurring (default **weekly**) aviation technical-intelligence digest for **Cathay Pacific's
engineering and technical-services team**. Output is a verified, fleet-scoped Markdown report
covering ADs, EADs, SBs, SILs, service letters (SL), MSG-3 task changes, incidents, and major
industry events (the "UPS MD-11" class).

The audience is expert (technical services / line & base maintenance / reliability engineering).
Acronyms (AD, SB, SIL, SL, EAD, MSG-3) are **not** defined in the output.

**This is engineer-grade intelligence, not commercial airline news.**

## 2. The hard constraint (the reason the project exists)

> Every regulatory reference, revision, date, effectivity, and quote MUST be confirmed against a
> live-fetched, publicly accessible primary source (regulator or OEM) before it enters the report.

- No reference enters from memory, a headline, or a trade-press summary alone.
- Trade press generates **leads only** — never the sole source for a technical reference.
- Every quote comes from a document actually fetched, kept **≤25 words**, with title, ref,
  revision/date, and URL. Gated/paywalled text is marked `UNVERIFIED`, never invented.
- Every technical claim carries a confidence tag: `[VERIFIED — primary source]` /
  `[REPORTED — trade press, unconfirmed]` / `[UNVERIFIED — reference exists, text not accessed]`.
- Current date is computed **at runtime**. Items outside the lookback window are dropped or
  explicitly flagged as carried-over developing events — never silently presented as new.

**A short verified digest always beats a long speculative one.**

### Public-internet-only rule (resolved 2026-06-25)
Use only information findable on the public internet. No authenticated logins, no credentialed
OEM/airline portals, no paywalled sources. If a primary document is not publicly fetchable, it
cannot be `[VERIFIED]` — mark it `[UNVERIFIED — reference exists, text not accessed]` and move on.

## 3. Scope

Include an item only if it satisfies **at least one**:
1. Directly involves a **fleet** type (operator's own), OR
2. Involves a **peer** type with read-across to the fleet (shared system, engine, OEM design
   philosophy, or regulatory precedent), OR
3. Is a **major industry event** of broad significance (hull loss, fleet-wide AD/grounding, cert
   or production milestone, significant regulatory action).

**Exclude:** routine commercial/route/financial news, marketing, minor incidents with no technical
read-across.

**Fleet:** A330-300, A321neo, A350-900, A350-1000, 777-300ER.
**Engines:** Trent 700, Trent XWB-84/-97, PW1100G / LEAP-1A (A321neo), GE90-115B (777-300ER).
**Peers / read-across:** A320neo family, A340, 787, 767, other Trent-powered widebodies.
**Cadence / window:** default weekly / last 7 days (parameterized; daily / 1 day supported).

## 4. Architecture — multi-agent pipeline with structural gates

Chosen because the verification gate must be **structural, not merely instructed**. A single
prompt that says "verify everything" cannot stop an unverified claim from leaking into the output;
isolated stages + tool restriction + deterministic scripts can.

```
/digest (orchestrator)
  └─ Step 1  Runtime params (date, lookback)            [Bash]
  └─ Step 2  SCANNER   → 01_scanner.json                [agent: WebSearch/WebFetch/Read]
  └─ Step 3  VERIFIER  → 02_verifier.json               [agent: WebFetch/WebSearch/Read]
  └─ Step 4  GATE 1    → 03_sanitised.json (+report)    [validate_records.py — deterministic]
  └─ Step 4b AUDITOR   → 04_audited.json                [agent: WebFetch/Read — independent re-fetch]
  └─ Step 4c GATE 2    → 05_final.json (+report)        [validate_records.py --require-audit]
  └─ Step 5  WRITER    → digests/<date>-<cadence>.md    [agent: Read ONLY — no fetch tools]
```

### Stage responsibilities

| Stage | Tools | Job | Cannot do |
|-------|-------|-----|-----------|
| **Scanner** | WebSearch, WebFetch, Read | Wide net of candidate leads; all `UNVERIFIED`, provenance `null` | Assert any ref number as fact |
| **Verifier** | WebFetch, WebSearch, Read | Fetch each primary source; confirm ref/rev/date/effectivity against text; extract quotes; assign confidence | `VERIFIED` without fetched snippet or allowlisted URL |
| **Gate 1** | (script) | Mechanically downgrade illegal `VERIFIED`, drop bad quotes, enforce window, re-roll confidence | — |
| **Auditor** | WebFetch, Read | **Independently re-fetch** every still-`VERIFIED` URL; confirm number + quote appear; write real excerpt | Trust the verifier's snippet |
| **Gate 2** | (script) | Downgrade any `VERIFIED` the auditor didn't `confirm` with a real excerpt | — |
| **Writer** | Read | Render Markdown from `05_final.json` only; clickable `[source]` per ref | Fetch, infer, or add anything |

### The five enforcement levers (what makes the gate structural)
1. **Context isolation** — the writer never sees unverified data.
2. **Tool restriction** — the writer has no fetch tools; it physically cannot invent a reference.
3. **Schema contract** — every inter-stage record must carry `primary_source_url` +
   `fetched_text_snippet`; records missing them are auto-dropped before the writer runs.
4. **Provenance allowlist** — `primary_source_url` must match a regulator/OEM domain allowlist, so
   trade-press URLs can never satisfy a `VERIFIED` claim.
5. **Independent re-fetch** — a separate auditor must re-confirm the cited text from the live
   source; one agent's hallucination cannot survive two independent fetches + two deterministic
   gates.

**A reference keeps `[VERIFIED]` only if BOTH the Verifier and the independent Auditor confirmed it
from the live primary source, AND both deterministic gates passed.**

## 5. Self-audit / verification chain (the most important part)

This is the implementation of the brief's "SELF-AUDIT (mandatory gate)" — and it goes further than
the brief asked (the brief wanted the *same* analyst to re-check; the build uses an *independent*
auditor + two mechanical gates).

1. **Verifier** assigns `VERIFIED` only with: allowlisted `primary_source_url` + genuine
   `fetched_text_snippet` + confirmed ref/rev/date.
2. **Gate 1** (`validate_records.py`): downgrades any `VERIFIED` lacking allowlisted URL or
   snippet (`MIN_SNIPPET_LEN = 20`); drops quotes off-allowlist or over the word limit; drops
   out-of-window non-carryovers; recomputes `item_confidence` as the highest surviving reference
   confidence.
3. **Auditor** re-opens each cited URL **fresh**, confirms the ref number + quoted text appear,
   and writes a real `audit.excerpt`. Strict: when in doubt, `not_found` / `fetch_failed`.
4. **Gate 2** (`--require-audit`): downgrades any `VERIFIED` whose `audit.status != "confirmed"`
   or whose excerpt is < 20 chars; drops any quote whose backing reference lost `VERIFIED`.

The auditor is an LLM, but its output is mechanically enforced downstream — its honest
`not_found` / `fetch_failed` verdicts are what protect the digest.

## 6. Data contract (`schema/record.schema.json`)

Inter-stage record (abridged): `id`, `headline`, `category` (`fleet|read_across|industry`),
`types_affected`, `event_date`, `within_window`, `developing_carryover`, `summary`,
`references[]`, `oem_regulator_position`, `root_cause`, `quotes[]`, `read_across`, `lead_sources[]`,
`item_confidence`, `verifier_notes`.

- **reference**: `ref_type` (`AD|EAD|SB|SIL|SL|MSG-3|other`), `ref_number`, `revision`, `ref_date`,
  `effectivity`, `primary_source_url`, `primary_source_domain`, `fetched_text_snippet`,
  `confidence`, `audit{status, checked_url, excerpt, ref_number_found, notes}`.
- **quote**: `text` (≤25 words), `doc_title`, `ref_number`, `revision_or_date`, `url`.
- **lead_sources**: discovery URLs (incl. trade press) — *never* proof.

## 7. Output format

Markdown, grouped: `## Directly Fleet-Relevant` → `## Read-Across (Peer Types)` →
`## Major Industry Events`. Per item: triage headline + type/date; *What happened* (1–3 sentences);
*Technical detail* (refs + rev, effectivity, root cause, OEM/regulator position, inline confidence
tags, clickable `[source]`); *Verbatim* quote with full attribution; *Read-across* line. Ends with
a **Sources & Confidence** tally (VERIFIED / REPORTED / UNVERIFIED). Target ~1,000 words (weekly).

## 8. Gap Analysis — intended spec vs. current build

### 8.1 Where the build exceeds the brief
- Independent **auditor** + **second** deterministic gate (brief asked only for single-agent self-audit).
- **Provenance allowlist** as a hard mechanical check, not an instruction.
- **No-fetch writer** + context isolation make the gate structural.
- Clickable `[source]` link on every reference (full traceability).

### 8.2 Flagged divergences (intent → build)

| # | Gap | Severity | Evidence | Recommendation |
|---|-----|----------|----------|----------------|
| **G1** | **Headline use case may be undeliverable as VERIFIED.** idea.md's worked example is a *verbatim Boeing Service Letter quote*. Boeing SLs are portal-gated → forced to `UNVERIFIED`; quote can't be extracted. Only VERIFIED path: a public **regulator AD or investigator docket that reproduces/cites the SL** — opportunistic. | **HIGH** | idea.md L4; verifier.md L14-17 | Make this explicit in CLAUDE.md scope. Add an intended *capture path*: verifier should prefer regulator ADs / NTSB-AAIB dockets that quote OEM correspondence. Reset expectation: SL verbatim quotes are a bonus, not a guarantee. |
| **G2** | **Gates have never run live.** 37 unit tests pass; no live internet run. Unknown: can WebFetch read FAA DRS / EASA AD PDFs as text? Will auditor over-trigger `fetch_failed`? Scanner coverage vs token cost? | **HIGH** | roadmap.md Stage 5 ⬜ | Run Stage 5: one live `/digest` on a recent window, then manually audit `01→05`. Highest-value next action. |
| **G3** | **MSG-3 task changes in scope but rarely public.** Public-internet rule makes them near-permanently `UNVERIFIED`. | **MED** | CLAUDE.md scope; idea.md L5 | Down-rank MSG-3 to "best-effort, expect UNVERIFIED"; note the only public route (regulator MRB reports). |
| **G4** | **No cross-run memory / dedup.** `developing_carryover` works within a run only; nothing remembers prior digests. Developing events re-surface weekly with no "already reported" awareness. | **MED** | `enforce_window`; no state store | Add a lightweight per-run ledger (e.g. `runs/_seen.json` of ref_numbers + event ids) the scanner/writer can consult. |
| **G5** | **Writer no-fetch wall is conditional.** Structural only when `.claude/agents/writer.md` is registered; the portable fallback dispatches `general-purpose` (full tools) + an *instruction* not to fetch — reintroduces the leak. Currently mitigated (registration verified) but environment-dependent. | **MED** | digest.md L26-28 | Add a startup assertion that the `writer` agent type resolves; refuse the fallback for the writer (fail loudly) rather than silently degrading. |
| **G6** | **Auditor independence is re-fetch-only, not model-diverse.** Verifier + auditor both `model: sonnet`; a shared misreading of a real page survives both. | **LOW** | agent frontmatter | State the bounded guarantee in the spec. Optionally set auditor to a different model. |
| **G7** | **Gate parses YAML by regex.** Reformatting `fleet.yaml` can silently empty the allowlist → exit 2 (fail-closed, but fragile). | **LOW** | validate_records.py L258-271 | Either add a tiny YAML dependency or a unit test asserting the allowlist parses to N>0 domains from the real config. |
| **G8** | **Undated items bypass the window** (`_in_window` returns True on null date). | **LOW** | validate_records.py L126-135 | Acceptable; document the behaviour, or require the writer to flag undated items. |
| **G9** | **Daily cadence under-specified.** Only a weekly `target_words`; writer just says "daily shorter." | **LOW** | fleet.yaml L101; writer.md L62 | Add `target_words_daily` (e.g. 400) to config. |
| **G10** | **Quote attribution not fully gate-enforced.** Config requires `[doc_title, ref_number, revision_or_date, url]`; gate checks only url + word count. | **LOW** | `sanitize_quote`; fleet.yaml L211-214 | Extend `sanitize_quote` to drop quotes missing required attribution fields. |

### 8.3 Recommended priority order
1. **G2** — run the live validation (everything else is theoretical until this passes).
2. **G1** — reset the expectation + wire the regulator/docket capture path; this is the project's core promise.
3. **G5**, **G4** — close the structural-leak and dedup gaps.
4. **G3, G6–G10** — documentation + small hardening.

## 9. Non-goals / Never-do
- Invent, guess, or "fill in" any reference number, revision, date, or quote.
- Present trade-press reporting as a confirmed regulatory/OEM reference.
- Issue own airworthiness determinations — report the OEM/regulator position only.
- Pad the digest with commercial/route/financial news to hit a length.

## 10. Open questions
- _(none currently open in roadmap.md; G1's expectation-reset and G4's dedup approach are the two
  worth a decision.)_

## 11. Live validation run — 2026-06-26 (first run; closes G2)

The first end-to-end live `/digest weekly` run executed scan → verify → gate → audit-gate → write
on the real internet. **The gates held** (no unverified claim reached the writer), but the run
surfaced one blocking bug and three structural findings. Artifacts kept under `runs/2026-06-26/`
(`01`→`05`) and `digests/2026-06-26-weekly.md`.

**Outcome:** 18 scanner leads → 8 verified-stage records → **2** survived the gate (both `REPORTED`
carry-overs: UPS MD-11 investigation, Cathay CX156 turbulence). **0 VERIFIED** this cycle.

| # | Finding | Status |
|---|---------|--------|
| **G11** | **Blocking encoding bug.** `validate_records.py` read files as `utf-8` but wrote via `sys.stdout`, which on Windows is cp1252; non-ASCII output (em-dashes) made the audit gate crash (`UnicodeDecodeError: 0x97`). 37 unit tests missed it — none exercised the real Windows file→stdout→file→read round trip. | **FIXED** 2026-06-26: `sys.stdout/stderr.reconfigure(encoding="utf-8")` in `main()` + regression test `TestOutputEncoding` (38 tests pass). |
| **G12** | **`federalregister.gov` blocks automated fetch** (302 loop to `unblock.federalregister.gov`). The verifier could only read authoritative FAA AD text via **`govinfo.gov`** (GPO's official Federal Register publisher), which was not on `verified_domains` — so genuine, fully-read government text was correctly held at UNVERIFIED. This is **G1**, observed live. | **PARTLY ADDRESSED** 2026-06-26: `govinfo.gov` added to `verified_domains`. The VERIFIED path is now *unblocked* but still **unproven end-to-end** (no in-window VERIFIED item this run — see G13). |
| **G13** | **Cold-start window.** The 3 genuinely relevant CX-fleet ADs the verifier confirmed (Trent 700 AD 2026-10-06; A350 actuators AD 2026-12-05; A350 O₂-clamp AD 2026-09-14, published Jun 12–18) fell just outside the strict 7-day window (Jun 19–26) and were dropped. In steady-state weekly ops the prior week catches them; a first run has no prior week. | **RESOLVED** 2026-06-26: self-healing window — `tools/compute_window.py` sets `effective = min(cold_start_cap, max(nominal, gap_since_last_run))` (per-cadence `runs/_state.json`, cap 30d); orchestrator Step 1 computes it, Step 6b records the run. G4 dedup half still open. |
| **G14** | **Writer disobeyed its own spec.** (a) Rendered *Major Industry Events* before *Directly Fleet-Relevant* — `writer.md` mandates Fleet → Read-Across → Industry. (b) Emitted `&amp;` instead of a literal `&` in the Sources line — the exact escaping `writer.md` forbids (regression of the Stage-4 fix). | **OPEN** (documented, not changed). `writer.md` needs hardening or a deterministic post-render check; digest saved verbatim with both defects for the record. |

**The VERIFIED path is now PROVEN (2026-06-26, focused re-verify).** After fixing the encoding bug
(G11) and allowlisting `govinfo.gov` (G12), a focused run on the 3 confirmable CX-fleet ADs (Trent
700 AD 2026-10-06; A350 actuators AD 2026-12-05; A350 O₂-clamp AD 2026-09-14) with a 21-day
cold-start window passed end-to-end:
- **Verifier** independently re-fetched the 3 `govinfo.gov` PDFs, confirmed every
  ref/amendment/docket/date/effectivity, assigned `VERIFIED`.
- **Gate 1** kept all 3 `VERIFIED` and **dropped 2 over-length quotes** (27 & 29 words > 25), keeping
  the one quote that fit — the quote-length rule enforcing itself live.
- **Auditor** independently re-fetched all 3 again and `confirmed` each with a real page-cited excerpt.
- **Audit gate** `kept=3 dropped=0 violations=0`; all 3 stayed `VERIFIED`.
- **Writer** rendered 3 `[VERIFIED — primary source]` items with clickable `[source]` links + 1
  verbatim quote → `digests/2026-06-26-verifyproof.md`, artifacts in `runs/2026-06-26-verifyproof/`.

So the full **two-independent-fetch + two-deterministic-gate** chain works, *and* the gates correctly
reject (the canonical run) — both directions validated. **G2 is closed.**

**New defect from the proof run (G14a):** even with an explicit instruction to emit a literal `&`,
the writer STILL output `&amp;` (in "Sources & Confidence" and "Rolls-Royce ... Ltd & Co KG"), and
also doubled the ref type ("AD AD 2026-10-06" because `ref_number` already begins with "AD "). This
confirmed G14 **cannot be fixed by prompt instruction alone**.

**G14 — FIXED** 2026-06-26: added `tools/finalize_digest.py`, a deterministic post-render step
(12 tests) that unescapes HTML entities, collapses a doubled ref type, and forces section order
(Fleet → Read-Across → Industry, Sources line last). It only reformats text already present — never
adds or changes a reference, quote, or fact. Wired into the orchestrator as **Step 5b** (runs after
the writer, "never skip"), the same structure-over-instruction principle as the gates. Both existing
digests reprocessed clean.

## 12. Second live run + self-audit — 2026-06-27 (G15/G16 found and fixed)

A second `/digest weekly` ran clean end-to-end (cold-start 30d window; 18 leads → 9 in digest; the
audit gate caught a non-verbatim quote and downgraded it; Standing Watch rendered live for the first
time). Artifacts in `runs/2026-06-27/`, digest `digests/2026-06-27-weekly.md`. A scope self-audit then
produced the categorisation + Read-Across-suppression changes (see roadmap decision log 2026-06-27) and
two render/window follow-ups, both since **FIXED** test-first on 2026-06-27:

| # | Finding | Status |
|---|---------|--------|
| **G15** | **Writer render leaks.** (a) `ref_type:"other"` printed literally (`other NTSB docket …`). (b) `AD FAA AD 2025-…` double-prefix slipped past `finalize_digest.dedupe_ref_type` — its old `\b(AD)\s+\1\b` matched only the adjacent `AD AD` form, not `AD <regulator> AD` where the `ref_number` itself contains the type. | **FIXED.** `dedupe_ref_type` now collapses the regulator-in-between form with a **letters-only bridge** (excludes digits/slash/brackets) so a distinct later reference like `FAA AD … / EASA AD …` keeps both ADs; new `strip_other_ref_type` drops the `other` placeholder (anchored to `*Technical detail:*`/`; ` + following-capital guard). 8 new tests incl. 2 over-collapse regressions caught by re-finalising the live digest; applied in place. `writer.md` also nudged. |
| **G16** | **Window enforced on publication date only.** The gate dropped the directly-fleet A330 standby-fuel-pump AD because its pub date (2026-05-22) fell 6 days before a 30d window, even though its *effective* date (2026-06-08) was well inside. | **FIXED.** `validate_records.enforce_window` now keeps an item whose `event_date` predates the window if any reference *became effective* inside the backward window (new `_effective_in_window`), marking it `within_window=True` (current, not carry-over). A *future* effective date does not rescue it — that stays Compliance Radar's job. 3 new tests. |

Also cleared the deferred Stage-6 nit: `compliance_radar.record_store` now **warns** (stderr or an
injectable `warn`) on an unparseable `effective_date` instead of silently storing one that `select()`
skips forever (still stored, never dropped; 2 new tests). **152 tool tests pass.** The living status
is tracked in `roadmap.md`; this section is the point-in-time record.
