# Roadmap — Aviation Technical-Intelligence Digest

Living progress tracker. Update status as we complete work. See `CLAUDE.md` for full project context.

**Status legend:** ✅ done · 🚧 in progress · ⏳ next up · ⬜ not started · ❓ needs decision

---

## Stage 1 — Architecture decision ✅

**Goal:** Decide single-command vs. multi-agent, with the verification gate enforced structurally.

**Decision:** Multi-agent pipeline (orchestrator → scanner → verifier → writer), enforced by
context isolation + tool restriction + schema contract + provenance allowlist. Full rationale in
`CLAUDE.md` → *Architecture*.

- [x] Compare single-command vs. multi-agent against the leak-prevention requirement
- [x] Define the four enforcement levers that make the gate structural
- [x] Record decision in `CLAUDE.md`

---

## Stage 2 — Fleet config ✅

**Goal:** Externalize all operator/fleet/engine/source scoping so the same pipeline works if the
fleet changes. Delivered: `config/fleet.yaml`.

- [x] Choose format (YAML vs JSON) and location → `config/fleet.yaml`
- [x] Operator: Cathay Pacific (+ ICAO/IATA/home regulator)
- [x] Fleet: A330-300, A321neo, A350-900, A350-1000, 777-300ER (with source-matching aliases)
- [x] Engines: Trent 700, Trent XWB-84/-97, PW1100G / LEAP-1A, GE90-115B (mapped to types)
- [x] Peer/read-across types: A320neo family, A340, 787, 767, other Trent widebodies (+ read-across notes)
- [x] Source tiers in priority order (regulators → OEMs → investigators → trade press)
- [x] Per-source URLs
- [x] Provenance allowlist (`verified_domains`: only these can satisfy a `[VERIFIED]` claim)
- [x] Parameterized cadence + lookback window (default: weekly / 7 days)
- [x] Quote rules block (max 25 words, fetched-text-only, required attribution)

---

## Stage 3 — Verification layer ✅ (the core — built test-first)

**Goal:** Make the gate bulletproof. Delivered: schema contract + scanner/verifier agent specs +
a deterministic gate script that mechanically enforces the rules (27 unit tests, all passing).

Files: `schema/record.schema.json`, `.claude/agents/scanner.md`, `.claude/agents/verifier.md`,
`tools/validate_records.py`, `tools/test_validate_records.py`.

- [x] Define the inter-stage record schema (incl. `primary_source_url`, `fetched_text_snippet`)
- [x] Scanner: emit UNVERIFIED leads only; forbid asserting reference numbers as fact
- [x] Verifier: re-fetch primary source, confirm ref/rev/date/effectivity against actual text
- [x] Verifier: drop/downgrade records missing primary source URL or fetched text (mechanical gate)
- [x] Verifier: trade-press URL can never satisfy `[VERIFIED]` (provenance allowlist check)
- [x] Quote handling: only from fetched text, ≤25 words, full attribution; off-allowlist → dropped
- [x] Confidence tagging: VERIFIED / REPORTED / UNVERIFIED applied per claim + roll-up
- [x] Runtime date computation + lookback-window enforcement (drop or flag carried-over events)
- [x] **Deterministic gate** (`validate_records.py`): runs verifier→writer, sanitises illegal
      VERIFIED claims so the writer can never see one. Verified end-to-end with adversarial input.
- [x] ✅ Gated-OEM-docs handling resolved: **public internet only** → mark UNVERIFIED, never use portals
- [x] _(done in Stage 4)_ Writer: consumes sanitised records only. No-fetch is structural when a
      `writer` agent is registered; by instruction under the general-purpose fallback.

---

## Stage 4 — Output format + Writer + orchestrator ✅

**Goal:** Produce the digest in the agreed Markdown structure and wire the pipeline into one
runnable command. Delivered: `.claude/agents/writer.md`, `.claude/commands/digest.md`.
Writer verified via dry-run on `runs/_sample/03_sanitised.json` — format correct, no fetching.

- [x] Grouping: Directly Fleet-Relevant → Read-Across (Peer Types) → Major Industry Events
- [x] Per-item template (headline, what happened, technical detail, verbatim, read-across)
- [x] Sources & Confidence summary line (VERIFIED / REPORTED / UNVERIFIED counts)
- [x] Target ~1,000 words for the weekly; brevity over completeness
- [x] Wire the slash command end-to-end (`/digest [daily|weekly]` orchestrator)
- [x] Portable subagent dispatch (named agent → general-purpose fallback)
- [x] _minor_ Fixed: writer `&amp;` escaping + doubled-punctuation join (writer.md polish, 2026-06-25)
- [x] Verified: custom agents (`scanner`/`verifier`/`writer`) ARE registered in this CLI → writer's
      no-fetch wall is structural (real `writer` dry-run used only 2 Read calls, no web access)

### How to run it
`/digest` (weekly default) or `/digest daily`. Artifacts land in `runs/<date>/`:
`01_scanner.json` → `02_verifier.json` → `03_sanitised.json` (+ `03_gate_report.txt`) →
`04_audited.json` → `05_final.json` (+ `05_audit_report.txt`) → digest in `digests/<date>-<cadence>.md`.

---

## Stage 4.5 — Independent re-fetch audit ("Full audit" hardening) ✅

**Goal (user-requested, 2026-06-26):** be *absolutely sure* a VERIFIED item is real. Close the one
residual hole — the deterministic gate confirmed provenance fields existed but did not re-open the
URL to confirm the cited text genuinely appears there (verifier-hallucination risk).

**Delivered:** an independent **auditor** stage + a second deterministic gate. A reference keeps
`[VERIFIED]` only if BOTH the Verifier AND the independent Auditor confirmed it from the live source.
Also: every reference now renders a clickable `[source]` link, so every claim is traceable.

Files: `.claude/agents/auditor.md`, `enforce_audit*` in `tools/validate_records.py`
(+ `--require-audit` CLI flag), schema `audit` object, writer source-link, orchestrator Steps 4b/4c.

- [x] Auditor agent: re-fetch each cited URL fresh, confirm ref number + quoted text, write real excerpt
- [x] `audit` object in schema (status: confirmed / not_found / fetch_failed / not_audited)
- [x] Deterministic audit gate (`--require-audit`): downgrade any VERIFIED lacking `confirmed` + excerpt
- [x] Quote dropped if its backing reference fails the audit
- [x] Every reference renders a clickable `[source](url)` link in the output
- [x] 10 new unit tests (37 total, all passing) + CLI smoke test caught a simulated hallucination
- [x] Orchestrator wired: scan → verify → gate → **audit → audit-gate** → write

---

## Stage 5 — Test & validate 🚧 (first live run done 2026-06-26)

**Goal:** Prove the gates hold in a live run before relying on them.
**First live run (2026-06-26):** `/digest weekly` ran end-to-end. Gates held (0 leaks). 18 leads →
8 verified-stage → 2 survived (both REPORTED carry-overs), 0 VERIFIED this cycle. Full write-up in
`docs/superpowers/specs/2026-06-26-aviation-digest-spec.md` §11. Artifacts in `runs/2026-06-26/`.

- [x] Live `/digest` run executed; per-stage artifacts (01→05) + digest produced
- [x] **Found + fixed blocking bug (G11):** gate wrote cp1252 on Windows → audit gate crashed on
      UTF-8 read. Fixed via `sys.stdout/stderr.reconfigure("utf-8")` + regression test (38 tests pass)
- [x] **Found provenance gap (G12/G1):** `federalregister.gov` blocks bots; verifier read FAA text
      via `govinfo.gov` → not allowlisted → held UNVERIFIED. **Added `govinfo.gov` to `verified_domains`**
- [x] **VERIFIED path PROVEN** (focused re-verify, 21-day window): 3 CX-fleet FAA ADs passed
      verifier→gate→auditor→audit-gate→writer as `VERIFIED` with `[source]` links + 1 verbatim quote;
      audit gate `kept=3 dropped=0 violations=0`. Artifacts: `runs/2026-06-26-verifyproof/`,
      `digests/2026-06-26-verifyproof.md`. **G2 closed.**
- [x] Window/cold-start decision (G13) RESOLVED + SHIPPED: self-healing window via
      `tools/compute_window.py` (`effective = min(cold_start_cap, max(nominal, gap))`, per-cadence
      `runs/_state.json`, cap 30d). 22 unit/CLI tests pass. Orchestrator Step 1 computes it; Step 6b
      records the run on success. Built test-first via subagent-driven dev; final review = ready to
      merge (incl. `os.makedirs` guard on the state path). Spec + plan in
      `docs/superpowers/{specs,plans}/2026-06-26-g13-self-healing-window*.md`. G4 dedup half still open.
- [x] Writer defects (G14) FIXED: added deterministic `tools/finalize_digest.py` (unescape entities,
      collapse doubled ref type, force section order) + 12 tests; wired into orchestrator as Step 5b
- [x] G4 cross-run dedup SHIPPED (2026-06-26): `tools/dedup_ledger.py` (apply + `--record`), ledger
      `runs/_seen.json`, orchestrator Steps 4d/6c, schema `dedup`/`dedup_status` fields, writer
      `[UPDATED since …]` tag. Policy: suppress exact, re-surface on change (revision/date). Atomic
      ledger write. 24 tests pass. Built test-first per
      `docs/superpowers/plans/2026-06-26-g4-dedup-ledger.md`.
- [x] Adversarial test: confirm a trade-press-only "lead" cannot reach `[VERIFIED]` (2026-06-26).
      4 new tests (`TestAdversarialTradePressOnly`, 42 total) drive the REAL CLI end-to-end
      (sanitize gate → `--require-audit` audit gate) on a record where a hostile verifier faked
      every controllable field — spoofed `primary_source_domain: faa.gov`, fabricated
      `fetched_text_snippet`, fabricated `audit.status=confirmed` excerpt — over a trade-press URL.
      Result: downgraded to UNVERIFIED (reason = provenance/allowlist, not audit), backing quote
      dropped, lookalike `faa.gov.avherald.com` also rejected. Gate recomputes the domain from the
      URL, so no verifier-supplied field can spoof it.
- [x] **2nd live run done (2026-06-27):** `/digest weekly`, cold-start 30d window. 18 leads →
      11 final, 9 in digest (7 VERIFIED / 1 REPORTED / 1 UNVERIFIED). Gates held; audit gate caught a
      non-verbatim quote (A330 oxygen NPRM) and downgraded it. Standing Watch rendered live for the
      first time. Artifacts in `runs/2026-06-27/`, digest `digests/2026-06-27-weekly.md`.
- [x] **Self-audit findings actioned (2026-06-27):** (a) categorisation fixed so same-TYPE-any-operator
      = fleet [scanner.md]; (b) Read-Across suppressed when ≥5 fleet items [finalize_digest.py +
      `read_across_min_fleet_items` config]; (c) Standing Watch confirmed fleet-scoped [scanner.md].
- [x] **G15 FIXED (2026-06-27):** `finalize_digest.py` now (a) collapses the `AD FAA AD 2025-…`
      regulator-in-between double-prefix (bridge is letters-only — never spans digits/slash/brackets,
      so `FAA AD … / EASA AD …` keeps both ADs), and (b) strips the literal `other` ref_type via new
      `strip_other_ref_type` (only after the `*Technical detail:*` marker or a `; ` separator, guarded
      by a following capital so prose "other" is untouched). Writer.md also nudged. 8 new tests
      (incl. 2 over-collapse regressions caught by re-finalising the live digest). Applied in place to
      `digests/2026-06-27-weekly.md`.
- [x] **G16 FIXED (2026-06-27):** `validate_records.enforce_window` now keeps an item whose
      publication `event_date` predates the window if any reference *became effective* inside the
      backward window (new `_effective_in_window`); such items are marked `within_window=True` (current,
      not carry-over). A *future* effective date does not rescue an item — that stays Compliance Radar's
      job. 3 new tests.
- [ ] Confirm auditor's live fetch reliability (watch for over-conservative `fetch_failed` downgrades)
- [ ] Tune scanner query set for coverage vs. token cost

---

## Stage 6 — Standing Watch content layer ✅ (filler / minimum-entry rule)

**Goal:** make thin weeks read as substantial (≥4 blocks) without diluting the verified core.
Spec: `docs/superpowers/specs/2026-06-26-standing-watch-content-layer-design.md`.
Plan: `docs/superpowers/plans/2026-06-27-standing-watch-content-layer.md`.

- [x] Schema: `NPRM`/`PAD` ref_type + reference `effective_date`
- [x] On the Horizon: scanner queries + verifier handling + writer routing (rides existing gates)
- [x] Compliance Radar: `tools/compliance_radar.py` + `runs/_compliance.json` store (test-first)
- [x] Engineer's Corner: `config/engineers_corner.json` bank + `tools/engineers_corner.py` picker (test-first)
- [x] Writer: `## Standing Watch` section (radar / horizon / corner)
- [x] `finalize_digest.py`: Standing Watch in enforced section order
- [x] Orchestrator: build Steps 4e/4f + record Steps 6d/6e
- [x] Integration test: Standing Watch cannot smuggle an unverified reference
- [x] _(done 2026-06-27)_ radar `record_store` now warns (stderr, or injectable `warn` callable) on
      an unparseable `effective_date` at write time — still stored (never dropped), but the operator is
      told it will never surface until corrected. 2 new tests. (`select()` still fail-safe-skips it.)
- [ ] _deferred_: live `/digest` smoke of the rendered `## Standing Watch` section (LLM-driven writer)

---

## Stage 7 — Publication renderer + imagery ✅

**Goal:** Turn the verified digest into a branded, magazine-style HTML (+ derived PDF) with
copyright-aware photos, without touching the verified core or the no-fetch guarantee.
Spec: `docs/superpowers/specs/2026-06-27-publication-renderer-imagery-design.md`.
Plan: `docs/superpowers/plans/2026-06-27-publication-renderer-imagery.md`.

- [x] `imagery` config block in `config/fleet.yaml` (enabled, embed_allowlist, sizing)
- [x] Image gate `tools/fetch_images.py` (+ tests): URL-recomputed domain, embed only public-domain
      primary figures, everything else link-only, failure-safe; adversarial spoof test (17 tests)
- [x] `imagery` subagent `.claude/agents/imagery.md` (metadata only, never embeds)
- [x] Renderer `tools/render_publication.py` (+ tests): Flight Deck HTML from gate-passed JSON +
      images; reproduces writer routing (horizon/suppression/order/carry-over/dedup); fetch-free;
      escaping invariant on every JSON value; no chip on proposed-rule items (29 tests)
- [x] PDF `tools/html_to_pdf.py` (Playwright/Chromium, failure-tolerant)
- [x] Orchestrator Steps 4g/4h (imagery) + 7/7b (render/PDF) + guardrails
- [ ] Live `/digest` smoke with real fetched imagery (the lug photo end-to-end)

---

## Decisions log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-06-25 | Multi-agent pipeline over single command | Gate must be structural, not instructed |
| 2026-06-25 | Public internet sources only | No logins/portals/paywalls; non-public docs → UNVERIFIED |
| 2026-06-25 | Deterministic gate script (not LLM-only) | `validate_records.py` mechanically sanitises verifier output so the writer can never see an illegal VERIFIED claim |
| 2026-06-25 | Orchestrator = `/digest` slash command (Option 1) | Manual, auditable run in Claude Code; per-stage artifacts saved under `runs/<date>/` |
| 2026-06-25 | Portable subagent dispatch | Some environments don't register `.claude/agents/*` as dispatchable types; orchestrator falls back to `general-purpose` + "read the spec file". Writer fallback loses the hard no-fetch wall → mitigated by explicit instruction + the gate running first |
| 2026-06-26 | "Full audit" hardening (independent re-fetch) | User wanted to be *absolutely sure* news is real. Added auditor stage + `--require-audit` gate: VERIFIED requires two independent confirmations from the live source. Every reference now carries a clickable source link |
| 2026-06-26 | `govinfo.gov` added to `verified_domains` | First live run: `federalregister.gov` (allowlisted) blocks automated fetch; the readable authoritative copy is `govinfo.gov`, GPO's official Federal Register publisher. Treated as a Tier-1 primary source so FAA AD text read there can satisfy VERIFIED |
| 2026-06-26 | Deterministic output finaliser (`finalize_digest.py`, orchestrator Step 5b) | Live run showed the writer LLM ignores pure-formatting instructions (`&amp;`, doubled ref type, section order). Enforce mechanically after the writer — structure over instruction, same principle as the gates. Only reformats existing text; never adds/changes a reference, quote, or fact |
| 2026-06-26 | Gate I/O forced to UTF-8 | Windows redirected stdout defaults to cp1252; the gate wrote a file the audit gate could not read back. `reconfigure(encoding="utf-8")` in both scripts' `main()` + regression test |
| 2026-06-26 | G13 — self-healing lookback window | Effective window = `min(cold_start_cap, max(nominal, gap_since_last_run))`; per-cadence state in `runs/_state.json`; cold-start/cap default 30d. New deterministic tool `tools/compute_window.py`; orchestrator records the run marker only on success (Step 6b). Fixes cold-start + skipped-run coverage; structural over manual override. G4 dedup stays open. |
| 2026-06-26 | G4 — cross-run dedup ledger | Suppress an item only if every reference was already reported at the same version; re-surface tagged `updated` on a new revision/date. Identity = reference `TYPE:NUMBER` + version (revision else ref_date); ref-less events keyed on a headline slug + event_date (best-effort). New deterministic tool `tools/dedup_ledger.py`; ledger `runs/_seen.json` written atomically only after a successful digest (Step 6c), mirroring the G13 run marker. |
| 2026-06-27 | Standing Watch content layer | Filler for thin weeks via forward-looking primary-source intel (Compliance Radar, On-the-Horizon NPRM/PAD) + a walled-off curated Engineer's Corner. On-the-Horizon rides the existing gates (NPRM/PAD added to ref_type enum; gate ignores ref_type). Corner is deterministic + author-curated (JSON bank) — no LLM-generated facts, preserving the verify-everything ethos even in the entertaining block |
| 2026-06-27 | G15 render fixes + G16 window-on-effective-date | G15: deterministic `finalize_digest.py` collapses the `AD FAA AD` double-prefix (letters-only bridge so distinct later ADs survive) and strips the literal `other` ref_type — rendering-only, structure over instruction. G16: `enforce_window` lets a reference that became effective inside the lookback window keep an item whose publication predates it (marked current, not carry-over); future effective dates stay Compliance Radar's remit. Both test-first |
| 2026-06-27 | Categorisation + Read-Across suppression (post-self-audit) | Audience is CX/HK, so CX-fleet relevance is the priority. (1) **Fleet = same aircraft/engine TYPE, any operator** (a JAL A350-1000 event is fleet, not read-across) — scanner `category` rules rewritten. (2) **Read-Across suppressed entirely when ≥ `read_across_min_fleet_items` (=5) fleet items** — enforced in `finalize_digest.py`, structure over instruction, also fixes word-overflow on busy weeks. (3) **Major Industry Events stays NOT fleet-bounded** — a worldwide-event channel. (4) **Standing Watch (Radar + Horizon) is fleet-scoped** — no proposed rules for non-CX types. New config `standing_watch.read_across_min_fleet_items`. Writer unchanged (already routes by `category` + omits empty sections) |
| 2026-06-27 | Publication renderer + imagery (Flight Deck) | New layer after dedup: `imagery` subagent (judgment, metadata only) → deterministic image gate (`fetch_images.py`, embed-allowlist) → fetch-free `render_publication.py` (Flight Deck navy+teal magazine HTML) → Playwright PDF. Verified core untouched; renderer can't add facts (no fetch), images are the only external content and pass their own gate |
| 2026-06-27 | Photos: embed primary, link illustrative | Embed only public-domain agency images (ntsb/faa/govinfo/easa); copyrighted trade-press/avherald/OEM-press photos are attribution-link only, never redistributed. Domain recomputed from the URL in the gate, suffix-matched — no agent field can spoof it. One photo per item, only where it helps |

## Open questions

- **G4 — cross-run memory / dedup (CLOSED 2026-06-26):** both halves now done. G13 closed the
  *window-coverage* half (`runs/_state.json` persists per-cadence `last_run_date`); the *dedup* half
  is now shipped as `tools/dedup_ledger.py` + the `runs/_seen.json` ledger. Apply (Step 4d) suppresses
  items already reported at the same version and tags changed refs `updated`; record (Step 6c) upserts
  shown items after a successful digest. Remaining limitation (minor): ref-less event items are keyed
  on a headline slug, so a re-worded headline for the same incident can dodge dedup — acceptable, since
  ref-bearing items (ADs/SBs/SILs/SLs) are the robust path and the main re-surfacing problem.
- **G13 follow-up (minor, deferred):** `record_run` writes `runs/_state.json` non-atomically. A crash
  mid-write degrades to cold start on the next run (correct fail-safe direction), so low severity;
  a temp-file-and-rename would make it crash-proof if ever wanted.
- **Output density (minor):** the writer's `*Technical detail:*` line packs ref + effectivity + OEM
  position + root cause into one semicolon-joined run-on. Per current `writer.md` template; could be
  tightened. Not a defect.

See `docs/superpowers/specs/2026-06-26-aviation-digest-spec.md` §8 + §11 for the full gap analysis.
