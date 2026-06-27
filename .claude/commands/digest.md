---
description: Run the Aviation Technical-Intelligence Digest pipeline (scan → verify → gate → audit → audit-gate → write) and produce the digest. Optional arg: daily | weekly (default from config).
argument-hint: "[daily|weekly]"
---

You are the **orchestrator** for the Aviation Technical-Intelligence Digest. Execute the pipeline
below step by step, in order. Two structural gates protect the output and must never be skipped:
the deterministic **gate** (Step 4) and, after an independent re-fetch by the **auditor**, the
deterministic **audit gate** (Step 4c). A reference only keeps `[VERIFIED]` if BOTH the Verifier
and the independent Auditor confirmed it from the live primary source. Never let the Writer see
anything but the final audited output.

Read `CLAUDE.md` and `config/fleet.yaml` first if not already in context.

Cadence argument: `$ARGUMENTS` (if empty, use `run.cadence` from `config/fleet.yaml`; default weekly).

## Subagent dispatch mechanism (read first)
Each stage runs as a SEPARATE subagent so context stays isolated (this is part of the gate).
Dispatch each stage with the Agent tool using whichever of these your Claude Code supports:
- **Preferred:** `subagent_type: scanner` / `verifier` / `auditor` / `writer` (works if project
  agents in `.claude/agents/` are registered as dispatchable types).
- **Fallback (portable):** if those types are "not found", dispatch `subagent_type: general-purpose`
  and begin the prompt with: *"Read and follow EXACTLY the instructions in
  `.claude/agents/<stage>.md`, then …"*.
- For the **writer fallback only**, add an explicit line: *"Do NOT use WebFetch or WebSearch;
  render only what is in the sanitised JSON."* — because general-purpose is not tool-restricted
  and the writer's no-fetch guarantee would otherwise be lost. (With a registered `writer` agent,
  the tool restriction is enforced structurally and this line is unnecessary.)

## Step 1 — Runtime parameters (Bash)
- Compute today's date: `date +%F` → call this `CURRENT_DATE`. **Always compute at runtime; never hardcode.**
- Set `NOMINAL_LOOKBACK`: from `config/fleet.yaml` `run.lookback_days` (7 for weekly, 1 for daily) — override if the cadence arg differs.
- Create the run directory: `mkdir -p runs/$CURRENT_DATE digests`.
- **Compute the self-healing window (deterministic, never skip).** The lookback grows to cover the
  gap since the last successful run of this cadence (capped by `run.cold_start_lookback_days`), so a
  first run or a skipped run never silently misses recent items:
  ```
  LOOKBACK_DAYS=$(python tools/compute_window.py \
    --config config/fleet.yaml \
    --current-date $CURRENT_DATE \
    --cadence {cadence} \
    --nominal-lookback-days $NOMINAL_LOOKBACK \
    --state runs/_state.json)
  ```
  The tool prints the effective integer to stdout and a one-line reason to stderr
  (`cold start -> 30d` / `14d gap -> healed to 14d` / `steady state -> 7d`).
- Use `LOOKBACK_DAYS` (the **effective** window) for every downstream step — the scanner prompt, the
  verifier prompt, and both gates' `--lookback-days`.
- Tell the user the run parameters before proceeding: `CURRENT_DATE`, cadence, `NOMINAL_LOOKBACK` vs
  the effective `LOOKBACK_DAYS`, and the reason line from the tool's stderr.

## Step 2 — SCAN (dispatch the `scanner` subagent)
Use the Agent tool with `subagent_type: scanner`. In the prompt, pass:
> current_date = {CURRENT_DATE}, lookback_days = {LOOKBACK_DAYS}, cadence = {cadence}.
> Scan per your instructions and the scope in config/fleet.yaml. Return ONLY `{ "records": [...] }`.

Save the subagent's returned JSON verbatim to `runs/$CURRENT_DATE/01_scanner.json`
(strip any ``` code fences if present). If it returned no records, report that and stop.

## Step 3 — VERIFY (dispatch the `verifier` subagent)
Use the Agent tool with `subagent_type: verifier`. In the prompt, pass:
> current_date = {CURRENT_DATE}, lookback_days = {LOOKBACK_DAYS}.
> Verify the leads in `runs/{CURRENT_DATE}/01_scanner.json` per your instructions. Read that file.
> Fetch primary sources, confirm references, assign confidence, enforce the window.
> Return ONLY the cleaned `{ "records": [...] }`.

Save the returned JSON verbatim to `runs/$CURRENT_DATE/02_verifier.json`.

## Step 4 — GATE (Bash — deterministic, never skip)
Run the deterministic gate. The Writer must only ever see this output:
```
python tools/validate_records.py \
  --config config/fleet.yaml \
  --current-date $CURRENT_DATE \
  --lookback-days $LOOKBACK_DAYS \
  --infile runs/$CURRENT_DATE/02_verifier.json \
  > runs/$CURRENT_DATE/03_sanitised.json \
  2> runs/$CURRENT_DATE/03_gate_report.txt
```
Then read `runs/$CURRENT_DATE/03_gate_report.txt` and surface its summary line and any
downgrades/drops to the user. A non-zero exit means the gate had to sanitise something — that is
expected and healthy, not an error. (Note: this tool runs Bash; on this Windows host use the Bash
tool which provides `date`, `mkdir`, and `python`.)

## Step 4b — AUDIT (dispatch the `auditor` subagent — independent re-fetch)
Use the Agent tool with `subagent_type: auditor`. In the prompt, pass:
> Independently audit `runs/{CURRENT_DATE}/03_sanitised.json` per your instructions. Read that file.
> For EVERY reference still tagged VERIFIED, re-fetch its cited `primary_source_url` fresh and
> confirm the reference number and any quoted text actually appear there. Add an `audit` object to
> each. Return ONLY the records JSON `{ "records": [...] }`.

Save the returned JSON verbatim to `runs/$CURRENT_DATE/04_audited.json`. The auditor is a second,
independent pair of eyes — it does not trust the verifier's snippet.

## Step 4c — AUDIT GATE (Bash — deterministic, never skip)
Enforce the audit result mechanically. Any VERIFIED reference the auditor did not `confirm` (with a
real excerpt) is downgraded here:
```
python tools/validate_records.py \
  --config config/fleet.yaml \
  --current-date $CURRENT_DATE \
  --lookback-days $LOOKBACK_DAYS \
  --require-audit \
  --infile runs/$CURRENT_DATE/04_audited.json \
  > runs/$CURRENT_DATE/05_final.json \
  2> runs/$CURRENT_DATE/05_audit_report.txt
```
Read `runs/$CURRENT_DATE/05_audit_report.txt` and surface its summary and any audit downgrades to
the user. A non-zero exit is expected and healthy. `05_final.json` now feeds the dedup step (4d);
the deduped `06_deduped.json` is what the Writer sees.

## Step 4d — DEDUP (Bash — deterministic, never skip)
Suppress items already reported in a previous run, and tag any that changed since (new
revision/date). The Writer must only ever see this output:
```
python tools/dedup_ledger.py \
  --ledger runs/_seen.json \
  --current-date $CURRENT_DATE \
  --infile runs/$CURRENT_DATE/05_final.json \
  > runs/$CURRENT_DATE/06_deduped.json \
  2> runs/$CURRENT_DATE/06_dedup_report.txt
```
Read `runs/$CURRENT_DATE/06_dedup_report.txt` and surface its summary line
(`kept / suppressed / new / updated`) to the user. A non-zero exit just means something was
suppressed — expected and healthy. The ledger is **read-only here**; it is only written in Step 6c
after the digest succeeds. **`06_deduped.json` is the only thing the Writer may see.**

## Step 5 — WRITE (dispatch the `writer` subagent)
Use the Agent tool with `subagent_type: writer`. In the prompt, pass:
> Render the digest from `runs/{CURRENT_DATE}/06_deduped.json` per your instructions.
> cadence = {cadence}. Read that file (and config/fleet.yaml for operator/fleet names).
> Return ONLY the finished Markdown.

Save the returned Markdown to `digests/$CURRENT_DATE-{cadence}.md`.

## Step 5b — FINALISE (Bash — deterministic, never skip)
The Writer is an LLM and does not reliably honour pure formatting rules by instruction (it
re-escapes `&` as `&amp;`, doubles the reference type, and can mis-order sections). Enforce them
mechanically — structure over instruction, the same principle as the gates:
```
python tools/finalize_digest.py --infile digests/$CURRENT_DATE-{cadence}.md --in-place
```
This unescapes HTML entities, collapses a doubled ref type (`AD AD …` → `AD …`), and forces section
order (Directly Fleet-Relevant → Read-Across → Major Industry Events) with the Sources line last. It
only reformats text already in the file — it never adds or changes a reference, quote, or fact.

## Step 6 — Report to the user
Print:
- The output path: `digests/$CURRENT_DATE-{cadence}.md`.
- The gate summary (step 4) AND the audit-gate summary (step 4c): kept / downgraded / violations.
- The Sources & Confidence tally from the digest.
- A one-line reminder that gated OEM SB/SIL/SL are marked UNVERIFIED (public-internet-only rule),
  that every VERIFIED item was confirmed by two independent fetches, and that the per-stage
  artifacts in `runs/$CURRENT_DATE/` (01→05) are kept for audit.

## Step 6b — Record the run (Bash — deterministic, never skip)
The digest now exists, so advance the per-cadence run marker. This makes the *next* run's window
self-heal (it measures the gap since this run). Record only here, after a successful digest — never
earlier, so a failed or empty run does not move the marker and cause the next run to under-cover:
```
python tools/compute_window.py --record \
  --state runs/_state.json \
  --current-date $CURRENT_DATE \
  --cadence {cadence}
```
This writes `runs/_state.json` (`{cadence}.last_run_date = $CURRENT_DATE`), preserving the other
cadence's marker. It records the run only; it never touches the digest.

## Step 6c — Record reported items (Bash — deterministic, never skip)
The digest now exists, so remember what it reported so the *next* run can suppress unchanged
repeats. Record only here, after a successful digest — never earlier, so a failed or empty run
does not poison the ledger:
```
python tools/dedup_ledger.py --record \
  --ledger runs/_seen.json \
  --current-date $CURRENT_DATE \
  --infile runs/$CURRENT_DATE/06_deduped.json
```
This upserts each shown item's identity (reference TYPE:NUMBER + version, or event slug) into
`runs/_seen.json` with an atomic write. It records only; it never touches the digest.

## Guardrails (do not violate)
- Never write a reference, quote, date, or revision into the digest that is not in `05_final.json`.
- Never let the Writer fetch the web or "fill in" a gap — it has no fetch tools; keep it that way.
- Never present a trade-press item as a confirmed regulatory/OEM reference.
- Never skip Step 4 or Step 4c (the two deterministic gates) or Step 6b (the run marker that makes
  the next window self-heal). Record the marker (6b) only after the digest is written.
- Never skip Step 4d (dedup) or Step 6c (the ledger update). Update the ledger (6c) only after the
  digest is written, mirroring the Step 6b run marker.
- A reference reaching the digest as VERIFIED must have passed BOTH the verifier and the independent
  auditor. If only one confirmed it, it is UNVERIFIED.
