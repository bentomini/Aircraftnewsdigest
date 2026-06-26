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
- [ ] Window/cold-start decision (G13) — one-time wider first-run lookback vs. accept steady-state cadence
- [x] Writer defects (G14) FIXED: added deterministic `tools/finalize_digest.py` (unescape entities,
      collapse doubled ref type, force section order) + 12 tests; wired into orchestrator as Step 5b
- [ ] Adversarial test: confirm a trade-press-only "lead" cannot reach `[VERIFIED]`
- [ ] Confirm auditor's live fetch reliability (watch for over-conservative `fetch_failed` downgrades)
- [ ] Tune scanner query set for coverage vs. token cost

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

## Open questions

_(none open)_
