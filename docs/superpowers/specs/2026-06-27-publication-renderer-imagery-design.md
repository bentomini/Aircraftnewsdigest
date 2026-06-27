# Publication Renderer + Imagery — Design Spec

**Date:** 2026-06-27
**Status:** Approved (brainstorm), ready for implementation planning
**Project:** Aviation Technical-Intelligence Digest (Cathay Pacific Engineering & Technical Services)

---

## 1. Goal

Add a **publication layer** that turns the verified Markdown digest into reader-facing artifacts
that attract and hold an engineer's attention without diluting the pipeline's verify-everything ethos:

- `digests/<date>-<cadence>.html` — a self-contained, magazine-style HTML publication.
- `digests/<date>-<cadence>.pdf` — a faithful PDF derived from that same HTML.

The existing `digests/<date>-<cadence>.md` remains the lightweight text artifact (writer output,
finalised). The HTML/PDF are the *published* deliverables, generated from the same gate-passed data.

Photos are part of this: each item may carry one relatable photo, sourced under a tiered,
copyright-aware policy (e.g. the NTSB aft-pylon lug exhibit for the UPS MD-11 item).

## 2. Non-negotiable principle (why this is designed the way it is)

The pipeline's entire value is that **every rendered reference, quote, date and fact traces to
gate-passed, independently-verified primary sources, and the renderer physically cannot fetch.**
The publication layer must preserve that:

- The renderer is **deterministic and fetch-free**. It assembles HTML from the same canonical JSON
  the writer consumes (`06_deduped.json` + `07_radar.json` + `08_corner.json`) plus a new
  resolved-images file. It can never introduce a reference, quote, or fact that did not pass the
  gates. This mirrors the writer's no-fetch wall.
- **Images get their own gate**, structurally identical to verify→gate: an `imagery` subagent uses
  judgment to find a candidate photo; a deterministic tool then enforces the copyright/provenance
  rule mechanically. Images are **non-evidentiary** — they never set or change a confidence tag.
- The **verified core is untouched.** New stages bolt on *after* `06_deduped.json`. The scanner,
  verifier, auditor, gates, dedup and Standing Watch logic (130 tests) are not modified.

## 3. Brand / visual direction (decided in brainstorm)

**"Flight Deck" — navy + teal, magazine layout (item layout "A", photo floated right).**

| Token | Value | Use |
|-------|-------|-----|
| `--ink` | `#0B2545` | masthead background, primary headings |
| `--ink-2` | `#13315C` | item headlines |
| `--accent` | `#15B8A0` (teal) | kicker, section rules, source links, accents |
| `--paper` | `#F7F9FC` | page background |
| `--body` | `#26303c` | body text |
| `--muted` | `#8a97a8` | meta / dates |
| VERIFIED tag | bg `#DCF6F1` / text `#0E7C6B` | confidence chip |
| REPORTED tag | warm-neutral chip (e.g. bg `#FFF4E5` / text `#B26A00`) | confidence chip |
| UNVERIFIED tag | grey chip | confidence chip |

Typography (web fonts with system fallback):
- Headlines / masthead title: **Fraunces** (serif) → fallback `Georgia, 'Times New Roman', serif`
- Labels / kicker / meta / UI: **Inter** (sans) → fallback `Arial, Helvetica, sans-serif`
- Body prose: **Source Serif 4** → fallback `Georgia, serif`

Item layout "A": item headline + confidence chip, meta line, then prose with a photo (when present)
floated to the right at ~140–170px wide with a caption beneath.

Masthead names Cathay Pacific as the **audience**, not the publisher — this is an *unofficial internal
digest*. Do not adopt Cathay's official logo/livery (trademark exposure for a solo operator).

## 4. Photo policy (decided in brainstorm)

Two-tier sourcing, copyright-aware embedding:

1. **Primary tier (embeddable):** a real figure from the item's allowlisted **primary source**.
   Embed inline **only** if the source domain is on the public-domain **image embed-allowlist**:
   `ntsb.gov`, `faa.gov`, `govinfo.gov`, `easa.europa.eu`. These are government/agency works that are
   public-domain or freely reproducible with attribution. Download → validate → optimise → base64-embed.
2. **Illustrative tier (link-only):** if no embeddable primary figure exists, an illustrative photo
   from the item's own `lead_source` (incl. trade press / avherald) or any OEM-press/off-allowlist
   domain. **Never downloaded or embedded** — rendered as a captioned attribution line:
   `Photo: <source> — illustrative [view original ↗]`, linking to the original.

This realises the user's decision: *embed public-domain primary figures; for copyrighted photos,
attribute and link, never redistribute the bytes.* OEM press photos are treated as link-only for now
(copyrighted; press-use embedding is a possible future option, deliberately deferred).

**Density:** at most **one photo per item**, and only where a candidate genuinely helps (incidents,
structural failures, investigative hearings, engine events). Pure-text AD compliance items stay
photo-less. No separate masthead/hero image (YAGNI; easy to add later).

## 5. Architecture — new pipeline stages

Inserted after the existing dedup step (`06_deduped.json`); the writer/markdown path is unchanged.

### Stage 4g — IMAGERY (new `imagery` subagent)
- **Input:** `runs/<date>/06_deduped.json` (records carry `primary_source_url`, `lead_sources`,
  `category`, `types_affected`, `headline`, `id`).
- **Task:** for each record, judge whether a photo materially helps; if so, find the single best
  candidate. Prefer a real figure on the item's allowlisted primary source (tier `primary`); else an
  illustrative photo from the item's own lead source (tier `illustrative`). May fetch pages to locate
  a candidate image URL (it has WebFetch), but returns **metadata only** — it never embeds.
- **Output (`runs/<date>/09_imagery.json`):** per record id, zero or one image candidate:
  ```json
  { "images": [ {
      "record_id": "ntsb-ups-md11-investigation-update",
      "tier": "primary",
      "image_url": "https://www.ntsb.gov/.../exhibit.jpg",
      "source_domain": "ntsb.gov",
      "source_label": "NTSB DCA26MA024 hearing exhibit",
      "caption": "Fractured aft-pylon spherical bearing lug (P/N S00399-1).",
      "alt_text": "Close-up of fractured aft-pylon spherical bearing lug",
      "link_url": "https://www.ntsb.gov/.../exhibit.jpg"
  } ] }
  ```
  Records with no useful photo simply have no entry. The agent never asserts a technical fact; image
  captions describe the photo only and carry no reference/quote claims.

### Stage 4h — IMAGE GATE (new deterministic `tools/fetch_images.py`)
The image analog of `validate_records.py`. Mechanically enforces the copyright/provenance rule;
no agent judgment can bypass it.
- **Input:** `09_imagery.json` (+ `config/fleet.yaml` for the embed-allowlist).
- **Logic per candidate:**
  - Recompute `source_domain` from `image_url` (www-stripped) — never trust the agent's field.
    Allowlist matching is **suffix-based** so subdomains qualify (e.g. `ad.easa.europa.eu` matches
    `easa.europa.eu`), matching how `verified_domains` is treated elsewhere in the pipeline.
  - If `tier == primary` AND domain ∈ embed-allowlist: download with a timeout/size cap; verify
    content-type is an image and it decodes (Pillow); reject SVG/HTML/oversized; resize to a max
    width and re-encode (JPEG/PNG); store under `runs/<date>/assets/<record_id>.<ext>`; compute a
    base64 data URI. Mark `embed: true`.
  - Otherwise (illustrative, off-allowlist, OEM press, or any download/validation failure):
    `embed: false`, keep `link_url` + `source_label` for an attribution line. Never download bytes
    for these.
- **Output (`runs/<date>/10_images.json`):** per record id, a resolved image directive:
  `{ embed: true, asset_path, data_uri, caption, alt_text, source_label, link_url }` **or**
  `{ embed: false, source_label, link_url, caption }` **or** absent (no photo).
- **Config:** new `imagery` block in `config/fleet.yaml` — `embed_allowlist` (domains),
  `max_width_px`, `max_bytes`, `download_timeout_s`, `enabled` (master switch).
- **Failure is always safe:** any error → drop to link-only or omit; never raise, never block.

### Stage 7 — RENDER (new deterministic `tools/render_publication.py`)
- **Input:** `06_deduped.json`, `07_radar.json`, `08_corner.json`, `10_images.json`,
  `config/fleet.yaml` (operator/fleet names, cadence, run date).
- **Logic:** assemble the Flight Deck HTML deterministically from the JSON fields — the same
  field→section mapping the writer uses (Directly Fleet-Relevant → Read-Across → Major Industry
  Events → Standing Watch → Sources & Confidence). For each item, attach its `10_images.json`
  directive by `record_id`: a float-right embedded `<img>` (data URI) with caption, or an
  attribution line, or nothing. Respect the existing suppression rules already applied upstream
  (e.g. Read-Across suppressed when ≥5 fleet items is already reflected in the JSON / handled the
  same way the writer does).
- **Email-safe build:** inline CSS (no external stylesheet); base64 images; the float-right photo
  implemented as a **two-cell table** (robust across clients) rather than CSS float; web fonts via a
  `<link>` that degrades to the system fallback stack when stripped; confidence chips as inline-styled
  spans.
- **Output:** `digests/<date>-<cadence>.html`.
- **Guarantee:** consumes only gate-passed JSON + gate-passed images. Like the writer, it cannot add
  a reference, quote or fact. The only external content is images, which passed the image gate.

### Stage 7b — PDF (deterministic, Playwright/Chromium)
- Render `digests/<date>-<cadence>.html` to `digests/<date>-<cadence>.pdf` by loading the file in
  headless Chromium (Playwright) and using its print-to-PDF, with print CSS (`@page` margins,
  page-break-avoid on items). Chosen over WeasyPrint for faithful rendering of the magazine layout
  and clean Windows install.
- **Failure is non-fatal:** if Playwright/Chromium is unavailable or errors, log and continue — the
  HTML still ships.

## 6. Orchestrator wiring (`.claude/commands/digest.md`)

Insert after Step 4f (build Engineer's Corner) and renumber the render step:
- **Step 4g** — dispatch `imagery` subagent → `09_imagery.json`.
- **Step 4h** — `python tools/fetch_images.py` → `10_images.json` (+ report to stderr).
- **Step 7 (after the Markdown writer + finalise)** — `python tools/render_publication.py` → `.html`.
- **Step 7b** — HTML → PDF via the Playwright runner.
- Add guardrails: the renderer must only ever see gate-passed JSON; images must only come from
  `10_images.json`; embedding is restricted to the image embed-allowlist; the publication layer must
  never block the existing Markdown digest from completing.

The imagery stages are **opt-out** via `imagery.enabled: false` — a run with imagery disabled still
produces a (photo-less) HTML/PDF, and the existing Markdown path is entirely independent of them.

## 7. New / changed files

- `.claude/agents/imagery.md` — new subagent spec (WebFetch, Read; returns metadata only).
- `tools/fetch_images.py` + `tools/test_fetch_images.py` — image gate (allowlist enforcement,
  embed/link decision, validation, optimisation) — **test-first**.
- `tools/render_publication.py` + `tools/test_render_publication.py` — HTML assembly from JSON +
  image directives — **test-first**.
- `tools/html_to_pdf.py` (or a documented Playwright invocation) — HTML→PDF; thin, failure-tolerant.
- `config/fleet.yaml` — new `imagery` block (`enabled`, `embed_allowlist`, `max_width_px`,
  `max_bytes`, `download_timeout_s`).
- `.claude/commands/digest.md` — Steps 4g/4h/7/7b + guardrails.
- `roadmap.md` — new stage entry + decisions-log rows.
- Dependencies: `Pillow` (image validation/resize), `playwright` (+ `playwright install chromium`).

## 8. Testing strategy

Deterministic tools are unit-tested (project norm, built test-first):
- **`fetch_images.py`:** domain recomputed from URL (no spoofing); embed only for allowlisted
  public-domain domains; illustrative/off-allowlist/OEM ⇒ link-only, never downloaded; non-image /
  oversized / decode-failure ⇒ safe drop to link-only; output schema correct. Network is mocked.
- **`render_publication.py`:** correct section order; item with embedded image renders a data-URI
  `<img>` + caption; item with illustrative photo renders an attribution link, no `<img>` data URI;
  item with no photo renders text-only; confidence chips correct; **no content appears that is not in
  the input JSON** (the verified-content guarantee, asserted as a test); email-safe structure
  (inline styles, table-based photo cell).
- **Integration:** a record whose only image is a copyrighted/off-allowlist photo can never produce
  an embedded `<img>` data URI — it is link-only end-to-end (the image analog of the trade-press
  adversarial test).

## 9. Scope guards (YAGNI / out of scope)

- "Email-ready" = renders well in modern webmail (Gmail / Apple Mail / browser) and as a forwardable
  file. **Full Outlook-desktop bulletproofing is out of scope.**
- No image hosting/CDN; embeds are inline, illustrative photos are links.
- No masthead/hero image; at most one photo per item.
- No OEM-press image embedding (link-only for now).
- No change to the verified core, the gates, dedup, or Standing Watch logic.
- The Markdown digest is never blocked by, or dependent on, the publication layer.

## 10. Open items (deferred, not blocking)

- OEM-press image embedding under press-use terms (currently link-only).
- A masthead/hero image and per-section cover imagery.
- Live `/digest` smoke of the rendered HTML/PDF with real fetched imagery.
