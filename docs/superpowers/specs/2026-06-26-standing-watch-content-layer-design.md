# Spec — "Standing Watch" content layer (filler / minimum-entry rule)

**Date:** 2026-06-26
**Status:** Design approved, ready for implementation plan
**Sub-project 1 of 2** (the other: publication renderer + imagery — separate spec)

## 1. Problem

Some weeks are thin. The 2026-06-26 weekly digest carried only 2 items, both REPORTED
carry-overs. A digest the author won't keep reading is wasted effort. Goal: a weekly
should read as substantial (~4–5 things to read minimum), **without** diluting the
verification trust model that is the whole point of the project.

The hard constraint (CLAUDE.md): *"No padding with commercial/route/financial news to
hit a length. A short verified digest always beats a long speculative one."* This spec
does not break that rule — it adds **forward-looking primary-source intelligence** and a
**clearly walled-off educational block**, neither of which is the prohibited padding.

## 2. Scope

In scope — three new blocks, grouped under one new section:

1. **Compliance Radar** — upcoming AD effective dates / compliance deadlines for the CX
   fleet, derived from already-VERIFIED ADs. Standing (every week), capped ≤3 nearest.
2. **On the Horizon** — FAA NPRMs / EASA PADs (proposed rules) affecting the fleet.
   Standing, capped ≤3. Flows through the existing verify→audit gates.
3. **Engineer's Corner** — one short explainer grounded in a VERIFIED source, with a
   curated evergreen fallback. **Conditional:** renders only when core items < 4.

Out of scope (deferred / rejected): reliability/incident roundup (reads as filler);
free-generated explainers (hallucination risk); anything that changes the verification
gates, the writer no-fetch wall, or the "never invent a reference" contract.

## 3. Structure & placement

The three existing intelligence sections are unchanged — they are the trust core. A new
top-level section is **appended below** them:

```
## Directly Fleet-Relevant        ← unchanged (VERIFIED intelligence)
## Read-Across (Peer Types)       ← unchanged
## Major Industry Events          ← unchanged
## Standing Watch                 ← NEW, walled off, own intro line
   ### Compliance Radar           (standing, ≤3 nearest by date)
   ### On the Horizon             (proposed rules, standing, ≤3)
   ### Engineer's Corner          (conditional: only when core items < 4)
```

Section/block names are placeholders and may be renamed (e.g. "Tech Watch", "The
Hangar") with no design impact. The `## Standing Watch` header + a one-line intro is the
**trust mechanism**: the reader instantly sees where verified incident/AD intelligence
ends and forward-looking / educational material begins.

## 4. Compliance Radar — persistent date store

This is the one architectural addition. To surface *"AD 2026-12-05 becomes effective
July 20"* weeks after that AD was first reported, the pipeline must remember verified ADs
and their dates.

- **Store:** extend the existing dedup ledger (`runs/_seen.json`, written by
  `tools/dedup_ledger.py --record`) — or a parallel `runs/_compliance.json` if cleaner —
  to capture, for every VERIFIED AD shown: `reference`, `effective_date`, and any
  `compliance_deadline` dates, plus the `primary_source_url`.
- **Selection:** at render time, show items whose effective/compliance date falls inside
  a forward window (default **90 days**), nearest first, capped ≤3.
- **Trust:** every Radar entry traces back to an already-VERIFIED AD and keeps its
  `[source]` link. No new verification path; it re-displays previously verified facts.
- **Decision needed in plan:** extend `_seen.json` vs. new `_compliance.json`. Leaning
  extend, to keep one ledger and one atomic write.

## 5. On the Horizon — extends the scanner

FAA NPRMs / EASA PADs are themselves primary-source documents on allowlisted domains, so
they flow through the **exact same** verify → gate → audit → audit-gate chain as any AD.

- **Scanner:** add NPRM/PAD queries to the scanner query set.
- **Rendering:** confirmed proposed rules render in this block with a
  `[PROPOSED — not yet final]` framing so they are never mistaken for active ADs.
- **No gate changes:** a proposed rule that cannot be confirmed from its primary source is
  dropped/held exactly like any other unconfirmed lead.

## 6. Engineer's Corner — grounded with fallback

- **Default:** a short explainer tied to a source already VERIFIED this run or present in
  the ledger (facts anchored to a fetched document — e.g. "why Trent 700 HPT blades
  corrode" when a Trent 700 AD is in play).
- **Fallback:** if nothing suitable exists, pull a topic from a small hand-seeded evergreen
  bank, `config/engineers_corner.yaml`, seeded once by the author.
- **Conditional render:** only when core intelligence items < 4 (see §7).
- **Trust:** the block carries no `[VERIFIED]` regulatory claims of its own; it explains
  already-verified material or seeded evergreen content. It must not introduce new
  reference numbers, revisions, dates, or quotes that did not pass the gates.

## 7. Trigger logic (the "minimum 4–5" rule)

- **Radar + On the Horizon:** always render (each capped ≤3). Real intelligence; they
  naturally bulk a thin week.
- **Engineer's Corner:** renders **only if** substantive items in the three core sections
  < 4.
- **"Substantive"** = VERIFIED + REPORTED items in the three core sections. Standing-Watch
  blocks are bonus and **do not** count toward the floor — so a 2-AD week still triggers
  the Corner and reads as full.

## 8. What does not change

The verification gates, the writer's no-fetch wall, and the "never invent a reference"
contract are untouched. CLAUDE.md's "no padding" rule gets a **one-line amendment**
clarifying that forward-looking primary-source intelligence and a walled-off educational
block are explicitly permitted, and are not the prohibited commercial/route/financial
padding.

## 9. Touch points (for the plan)

- `config/fleet.yaml` — forward-window default (90d), block caps (≤3), Engineer's Corner
  trigger threshold (<4).
- `config/engineers_corner.yaml` — NEW, seeded evergreen topic bank.
- `tools/dedup_ledger.py` (or new `tools/compliance_store.py`) — persist AD effective /
  compliance dates; query the forward window. Test-first, stdlib only, mirrors existing
  ledger atomic-write pattern.
- `.claude/agents/scanner.md` — add NPRM/PAD queries.
- `.claude/agents/writer.md` — render `## Standing Watch` and its three blocks; apply the
  trigger logic; `[PROPOSED — not yet final]` framing.
- `.claude/commands/digest.md` (orchestrator) — wire the Radar selection step and the
  Corner trigger; record AD dates into the store after a successful run.
- `CLAUDE.md` — one-line amendment to the "no padding" rule + document the new section.
- `roadmap.md` — new stage entry.

## 10. Success criteria

- A thin week (< 4 core items) renders Radar + On the Horizon + Engineer's Corner and
  reads as ≥4 substantial blocks.
- A fruitful week renders the core sections in full + capped standing blocks, and **omits**
  Engineer's Corner.
- Every Compliance Radar and On-the-Horizon entry carries a working `[source]` link to an
  allowlisted primary source.
- No new reference, revision, date, or quote reaches the output without passing the
  existing gates. Adversarial test: a Standing-Watch block cannot smuggle an unverified
  reference into the digest.
