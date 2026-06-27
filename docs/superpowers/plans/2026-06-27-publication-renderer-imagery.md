# Publication Renderer + Imagery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a publication layer that renders the verified digest into a branded, magazine-style HTML file (+ derived PDF) with copyright-aware photos, without touching the verified core or breaking the no-fetch guarantee.

**Architecture:** Three new stages bolt on *after* dedup (`06_deduped.json`): an `imagery` subagent (judgment, returns photo metadata only) → a deterministic image gate `fetch_images.py` (enforces an embed-allowlist; embeds public-domain primary figures, link-only for everything else) → a deterministic, fetch-free `render_publication.py` that assembles the "Flight Deck" HTML from the same gate-passed JSON the writer uses + the resolved images, then `html_to_pdf.py` prints it via headless Chromium. The existing Markdown writer path is unchanged.

**Tech Stack:** Python 3 (stdlib + Pillow for image validation/resize), Playwright/Chromium for PDF, JSON inter-stage contracts, unittest/pytest. Matches existing `tools/*.py` conventions (argparse CLI, `sys.stdout.reconfigure("utf-8")`, atomic writes, regex-parsed `config/fleet.yaml`, no YAML lib).

**Spec:** `docs/superpowers/specs/2026-06-27-publication-renderer-imagery-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `config/fleet.yaml` (modify) | New `imagery:` block: `enabled`, `embed_allowlist`, `max_width_px`, `max_bytes`, `download_timeout_s`. |
| `tools/fetch_images.py` (create) | Image GATE. Recompute domain from URL; embed only allowlisted primary-tier images (download→validate→resize→base64); everything else link-only. Never raises. |
| `tools/test_fetch_images.py` (create) | Unit tests for the gate (allowlist enforcement, embed/link decision, failure → safe link). |
| `.claude/agents/imagery.md` (create) | IMAGERY subagent spec. Finds one candidate photo per record; returns metadata only; never embeds. |
| `tools/render_publication.py` (create) | Deterministic HTML renderer. Assembles Flight Deck HTML from JSON bundle + resolved images. Fetch-free. |
| `tools/test_render_publication.py` (create) | Unit tests for grouping/ordering/item/image/suppression + verified-content guarantee. |
| `tools/html_to_pdf.py` (create) | HTML→PDF via Playwright/Chromium. Failure-tolerant (exit 0 if unavailable). |
| `.claude/commands/digest.md` (modify) | Wire Steps 4g/4h (imagery + gate) and 7/7b (render + PDF) + guardrails. |
| `roadmap.md` (modify) | New stage entry + decisions-log rows. |

Tests import the module directly (e.g. `import fetch_images as fi`), matching the existing suite, so **run pytest from the `tools/` directory**.

---

## Phase 1 — Config + Image Gate

### Task 1: Add the `imagery` config block

**Files:**
- Modify: `config/fleet.yaml` (append a new top-level block after `quotes:`)

- [ ] **Step 1: Add the block**

Append to `config/fleet.yaml`:

```yaml

# -----------------------------------------------------------------------------
# Imagery — publication-layer photos. Two tiers, copyright-aware:
#   * primary  : a real figure from the item's primary source. Embedded inline
#                ONLY if its domain is on embed_allowlist (public-domain agencies).
#   * illustrative / off-allowlist / OEM press : link-only attribution, never
#                downloaded or embedded.
# Non-evidentiary: images never set or change a confidence tag.
# -----------------------------------------------------------------------------
imagery:
  enabled: true
  embed_allowlist:        # public-domain / freely-reproducible agency domains only
    - ntsb.gov
    - faa.gov
    - govinfo.gov
    - easa.europa.eu
  max_width_px: 480       # downscale wider images before embedding
  max_bytes: 5000000      # reject anything larger than ~5 MB
  download_timeout_s: 15
```

- [ ] **Step 2: Commit**

```bash
git add config/fleet.yaml
git commit -m "feat(imagery): add imagery config block to fleet.yaml"
```

---

### Task 2: Image-gate config parser + domain helpers

**Files:**
- Create: `tools/fetch_images.py`
- Create: `tools/test_fetch_images.py`

- [ ] **Step 1: Write the failing test**

Create `tools/test_fetch_images.py`:

```python
#!/usr/bin/env python3
"""Tests for tools/fetch_images.py — the deterministic image gate."""
import unittest

import fetch_images as fi


class TestConfigParser(unittest.TestCase):
    YAML = """
quotes:
  max_words: 25

imagery:
  enabled: true
  embed_allowlist:
    - ntsb.gov
    - faa.gov
    - govinfo.gov
    - easa.europa.eu
  max_width_px: 480
  max_bytes: 5000000
  download_timeout_s: 15

verified_domains:
  - ntsb.gov
"""

    def test_parses_all_fields(self):
        cfg = fi.parse_imagery_config(self.YAML)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["embed_allowlist"],
                         ["ntsb.gov", "faa.gov", "govinfo.gov", "easa.europa.eu"])
        self.assertEqual(cfg["max_width_px"], 480)
        self.assertEqual(cfg["max_bytes"], 5000000)
        self.assertEqual(cfg["download_timeout_s"], 15)

    def test_missing_block_uses_defaults(self):
        cfg = fi.parse_imagery_config("verified_domains:\n  - ntsb.gov\n")
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["embed_allowlist"], [])

    def test_enabled_false_parsed(self):
        cfg = fi.parse_imagery_config("imagery:\n  enabled: false\n")
        self.assertFalse(cfg["enabled"])


class TestDomain(unittest.TestCase):
    def test_subdomain_matches(self):
        self.assertTrue(fi.domain_in_allowlist("ad.easa.europa.eu", ["easa.europa.eu"]))

    def test_lookalike_rejected(self):
        self.assertFalse(fi.domain_in_allowlist("ntsb.gov.evil.com", ["ntsb.gov"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools && python -m pytest test_fetch_images.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fetch_images'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/fetch_images.py`:

```python
#!/usr/bin/env python3
"""fetch_images.py — deterministic IMAGE GATE for the digest publication layer.

The image analog of validate_records.py. Takes the imagery subagent's candidate
photos (09_imagery.json) and mechanically enforces the copyright/provenance rule:

  * tier 'primary' AND source domain on the public-domain embed-allowlist
    (ntsb.gov / faa.gov / govinfo.gov / easa.europa.eu) -> download, validate it
    is a real raster image, resize/optimise, base64-embed.
  * everything else (illustrative tier, off-allowlist, OEM press, or ANY
    download/validation failure) -> link-only attribution; bytes never fetched.

Domain is recomputed from the URL here (never trusted from the agent) and suffix-
matched so subdomains qualify. Failure is always safe: drop to link-only, never
raise, never block the digest. Stdlib + Pillow.
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse


def extract_domain(url):
    """Host of a URL, lower-cased, leading 'www.' stripped. '' if none."""
    if not url:
        return ""
    netloc = urlparse(url).netloc.lower()
    if not netloc:
        netloc = urlparse("//" + url).netloc.lower()
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def domain_in_allowlist(domain, allowlist):
    """True if domain equals or is a subdomain of an allowlisted domain."""
    domain = (domain or "").lower()
    for allowed in allowlist:
        allowed = allowed.lower()
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def parse_imagery_config(text):
    """Read the `imagery:` block from fleet.yaml without a YAML lib."""
    cfg = {"enabled": True, "embed_allowlist": [],
           "max_width_px": 480, "max_bytes": 5000000, "download_timeout_s": 15}
    in_block = False
    in_list = False
    for line in text.splitlines():
        if re.match(r"^imagery:\s*$", line):
            in_block, in_list = True, False
            continue
        if not in_block:
            continue
        if re.match(r"^\S", line):        # next top-level key ends the block
            break
        m = re.match(r"^\s+enabled:\s*(true|false)", line, re.I)
        if m:
            cfg["enabled"] = m.group(1).lower() == "true"; in_list = False; continue
        m = re.match(r"^\s+max_width_px:\s*(\d+)", line)
        if m:
            cfg["max_width_px"] = int(m.group(1)); in_list = False; continue
        m = re.match(r"^\s+max_bytes:\s*(\d+)", line)
        if m:
            cfg["max_bytes"] = int(m.group(1)); in_list = False; continue
        m = re.match(r"^\s+download_timeout_s:\s*(\d+)", line)
        if m:
            cfg["download_timeout_s"] = int(m.group(1)); in_list = False; continue
        if re.match(r"^\s+embed_allowlist:\s*$", line):
            in_list = True; continue
        m = re.match(r"^\s+-\s*(\S+)", line)
        if m and in_list:
            cfg["embed_allowlist"].append(m.group(1).strip())
    return cfg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tools && python -m pytest test_fetch_images.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/fetch_images.py tools/test_fetch_images.py
git commit -m "feat(imagery): image-gate config parser + domain helpers"
```

---

### Task 3: The embed/link decision (`resolve_image`)

**Files:**
- Modify: `tools/fetch_images.py`
- Modify: `tools/test_fetch_images.py`

- [ ] **Step 1: Write the failing test**

Append to `tools/test_fetch_images.py` (before the `if __name__` line):

```python
def _candidate(record_id="r1", tier="primary",
               image_url="https://www.ntsb.gov/x/exhibit.jpg", **kw):
    base = {
        "record_id": record_id, "tier": tier, "image_url": image_url,
        "link_url": kw.get("link_url", image_url),
        "caption": kw.get("caption", "A lug"),
        "alt_text": kw.get("alt_text", "lug photo"),
        "source_label": kw.get("source_label", "NTSB exhibit"),
    }
    return base


def _ok_downloader(url):
    return {"data_uri": "data:image/jpeg;base64,QUJD", "ext": "jpg", "raw_bytes": b"ABC"}


def _boom_downloader(url):
    raise RuntimeError("network down")


ALLOW = ["ntsb.gov", "faa.gov", "govinfo.gov", "easa.europa.eu"]


class TestResolve(unittest.TestCase):
    def test_primary_allowlisted_embeds(self):
        d = fi.resolve_image(_candidate(), ALLOW, _ok_downloader)
        self.assertTrue(d["embed"])
        self.assertEqual(d["data_uri"], "data:image/jpeg;base64,QUJD")
        self.assertEqual(d["_raw_bytes"], b"ABC")

    def test_illustrative_tier_is_link_only(self):
        d = fi.resolve_image(_candidate(tier="illustrative"), ALLOW, _ok_downloader)
        self.assertFalse(d["embed"])
        self.assertNotIn("data_uri", d)

    def test_primary_off_allowlist_is_link_only(self):
        c = _candidate(image_url="https://avherald.com/p.jpg")
        d = fi.resolve_image(c, ALLOW, _ok_downloader)
        self.assertFalse(d["embed"])

    def test_subdomain_easa_embeds(self):
        c = _candidate(image_url="https://ad.easa.europa.eu/f/fig.jpg")
        d = fi.resolve_image(c, ALLOW, _ok_downloader)
        self.assertTrue(d["embed"])

    def test_download_failure_falls_back_to_link(self):
        d = fi.resolve_image(_candidate(), ALLOW, _boom_downloader)
        self.assertFalse(d["embed"])
        self.assertEqual(d["link_url"], "https://www.ntsb.gov/x/exhibit.jpg")

    def test_domain_recomputed_not_trusted(self):
        # Agent lies that an avherald URL is on ntsb.gov via a stray field; URL wins.
        c = _candidate(image_url="https://avherald.com/p.jpg")
        c["source_domain"] = "ntsb.gov"
        d = fi.resolve_image(c, ALLOW, _ok_downloader)
        self.assertFalse(d["embed"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools && python -m pytest test_fetch_images.py::TestResolve -v`
Expected: FAIL — `AttributeError: module 'fetch_images' has no attribute 'resolve_image'`

- [ ] **Step 3: Write minimal implementation**

Add to `tools/fetch_images.py`:

```python
def resolve_image(candidate, embed_allowlist, downloader):
    """Decide embed-vs-link for one candidate. Pure: download is injected.

    Returns a directive dict. Embedded directives carry a transient '_raw_bytes'
    that process() persists and strips. Never raises.
    """
    rid = candidate.get("record_id")
    image_url = candidate.get("image_url") or ""
    link_url = candidate.get("link_url") or image_url
    tier = (candidate.get("tier") or "").lower()
    domain = extract_domain(image_url)   # recomputed; agent fields ignored

    link_directive = {
        "record_id": rid, "embed": False,
        "caption": candidate.get("caption"),
        "source_label": candidate.get("source_label"),
        "link_url": link_url,
    }
    if tier != "primary" or not domain_in_allowlist(domain, embed_allowlist):
        return link_directive
    try:
        got = downloader(image_url)
    except Exception:
        return link_directive
    return {
        "record_id": rid, "embed": True,
        "data_uri": got["data_uri"], "ext": got["ext"], "_raw_bytes": got["raw_bytes"],
        "caption": candidate.get("caption"), "alt_text": candidate.get("alt_text"),
        "source_label": candidate.get("source_label"), "link_url": link_url,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tools && python -m pytest test_fetch_images.py::TestResolve -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/fetch_images.py tools/test_fetch_images.py
git commit -m "feat(imagery): resolve_image embed/link decision (URL-recomputed domain)"
```

---

### Task 4: `process()` — iterate candidates, persist embedded assets

**Files:**
- Modify: `tools/fetch_images.py`
- Modify: `tools/test_fetch_images.py`

- [ ] **Step 1: Write the failing test**

Append to `tools/test_fetch_images.py` (before `if __name__`):

```python
import os
import tempfile
import json as _json


class TestProcess(unittest.TestCase):
    def test_keys_output_by_record_id_and_strips_raw_bytes(self):
        payload = {"images": [_candidate(record_id="a"),
                              _candidate(record_id="b", tier="illustrative")]}
        out = fi.process(payload, ALLOW, _ok_downloader)
        self.assertIn("a", out["images"])
        self.assertIn("b", out["images"])
        self.assertTrue(out["images"]["a"]["embed"])
        self.assertNotIn("_raw_bytes", out["images"]["a"])
        self.assertFalse(out["images"]["b"]["embed"])

    def test_writes_asset_file_when_dir_given(self):
        payload = {"images": [_candidate(record_id="a")]}
        with tempfile.TemporaryDirectory() as d:
            out = fi.process(payload, ALLOW, _ok_downloader, assets_dir=d)
            path = out["images"]["a"]["asset_path"]
            self.assertTrue(os.path.isfile(path))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"ABC")

    def test_candidate_without_record_id_skipped(self):
        payload = {"images": [_candidate(record_id=None)]}
        out = fi.process(payload, ALLOW, _ok_downloader)
        self.assertEqual(out["images"], {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools && python -m pytest test_fetch_images.py::TestProcess -v`
Expected: FAIL — `AttributeError: module 'fetch_images' has no attribute 'process'`

- [ ] **Step 3: Write minimal implementation**

Add to `tools/fetch_images.py`:

```python
def process(payload, embed_allowlist, downloader, assets_dir=None):
    """Resolve every candidate -> {"images": {record_id: directive}}."""
    candidates = payload.get("images", []) if isinstance(payload, dict) else (payload or [])
    out = {}
    for cand in candidates:
        d = resolve_image(cand, embed_allowlist, downloader)
        rid = d.get("record_id")
        if not rid:
            continue
        if d.get("embed") and assets_dir:
            os.makedirs(assets_dir, exist_ok=True)
            path = os.path.join(assets_dir, "%s.%s" % (rid, d.get("ext", "jpg")))
            with open(path, "wb") as f:
                f.write(d["_raw_bytes"])
            d["asset_path"] = path
        d.pop("_raw_bytes", None)
        out[rid] = d
    return {"images": out}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tools && python -m pytest test_fetch_images.py::TestProcess -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/fetch_images.py tools/test_fetch_images.py
git commit -m "feat(imagery): process() resolves candidates + persists embedded assets"
```

---

### Task 5: Real downloader + CLI `main()`

**Files:**
- Modify: `tools/fetch_images.py`

- [ ] **Step 1: Add the real downloader and `main()`**

Add to `tools/fetch_images.py`:

```python
def make_downloader(max_bytes, max_width_px, timeout):
    """Build a real network downloader. Validates it is a raster image, downscales,
    re-encodes JPEG, returns {data_uri, ext, raw_bytes}. Raises on anything wrong."""
    import base64
    import io
    import urllib.request
    from PIL import Image

    def download(url):
        req = urllib.request.Request(url, headers={"User-Agent": "digest-imagery/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get_content_type() or "").lower()
            if not ctype.startswith("image/") or ctype == "image/svg+xml":
                raise ValueError("not a raster image: %s" % ctype)
            raw = resp.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("image exceeds max_bytes")
        img = Image.open(io.BytesIO(raw))
        img.load()                       # force decode; raises on truncated/garbage
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if img.width > max_width_px:
            h = round(img.height * max_width_px / img.width)
            img = img.resize((max_width_px, h))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        data = buf.getvalue()
        b64 = base64.b64encode(data).decode("ascii")
        return {"data_uri": "data:image/jpeg;base64," + b64, "ext": "jpg", "raw_bytes": data}

    return download


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Image gate: enforce embed-allowlist; embed or link.")
    ap.add_argument("--config", default="config/fleet.yaml")
    ap.add_argument("--infile", default="-", help="09_imagery.json path, or - for stdin.")
    ap.add_argument("--assets-dir", default=None, help="Directory to write embedded image files.")
    args = ap.parse_args(argv)

    cfg_text = ""
    try:
        cfg_text = open(args.config, encoding="utf-8").read()
    except OSError:
        pass
    icfg = parse_imagery_config(cfg_text)

    raw = sys.stdin.read() if args.infile == "-" else open(args.infile, encoding="utf-8").read()
    payload = json.loads(raw) if raw.strip() else {"images": []}

    if not icfg["enabled"]:
        sys.stdout.write(json.dumps({"images": {}}, indent=2, ensure_ascii=False))
        sys.stderr.write("\n[images] imagery disabled -> no photos\n")
        return 0

    downloader = make_downloader(icfg["max_bytes"], icfg["max_width_px"], icfg["download_timeout_s"])
    result = process(payload, icfg["embed_allowlist"], downloader, assets_dir=args.assets_dir)
    embedded = sum(1 for d in result["images"].values() if d.get("embed"))
    linked = len(result["images"]) - embedded
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False))
    sys.stderr.write("\n[images] %d embedded, %d link-only\n" % (embedded, linked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test the CLI with disabled imagery (no network)**

Run:
```bash
cd "C:/Users/josep/Project_AircraftNews" && echo '{"images":[]}' | python tools/fetch_images.py --config config/fleet.yaml
```
Expected: prints `{"images": {}}` and stderr `[images] 0 embedded, 0 link-only`.

- [ ] **Step 3: Confirm Pillow is installed**

Run: `python -c "import PIL; print(PIL.__version__)"`
Expected: a version string. If it errors: `pip install pillow`.

- [ ] **Step 4: Commit**

```bash
git add tools/fetch_images.py
git commit -m "feat(imagery): real Pillow downloader + image-gate CLI"
```

---

## Phase 2 — Imagery subagent

### Task 6: Write the `imagery` agent spec

**Files:**
- Create: `.claude/agents/imagery.md`

- [ ] **Step 1: Create the agent spec**

Create `.claude/agents/imagery.md` (model `imagery.md` on the existing `auditor.md`/`writer.md` frontmatter style):

```markdown
---
name: imagery
description: Aviation digest IMAGERY scout. For each gate-passed record, finds at most ONE relatable photo and returns metadata only — it never downloads or embeds. Prefers a real figure from the item's primary source (public-domain agency); else an illustrative photo from the item's own lead source. Public internet only.
tools: WebFetch, WebSearch, Read
model: sonnet
---

You are the **Imagery scout** for the Aviation Technical-Intelligence Digest. You run AFTER the
verified core is final. Your photos are **decoration, never evidence** — they must never assert or
imply a technical fact, reference, revision, date, or quote.

## Input
You receive a path to `06_deduped.json` (`{ "records": [...] }`). Read it. Each record has `id`,
`headline`, `category`, `types_affected`, `summary`, `references[].primary_source_url`, and
`lead_sources[]`.

## Your job
For EACH record, decide whether a photo would genuinely help a reader relate to the item (incidents,
structural failures, investigative hearings, engine events benefit most; routine text-only AD
compliance items often need none — skip those). If yes, find the single best candidate image URL:

1. **Primary tier (preferred):** a real, relevant figure on the record's own `primary_source_url`
   domain (e.g. an NTSB hearing exhibit photo of the failed part). Fetch the primary page and look
   for a genuine content image (a diagram, exhibit, or photo — not a logo, icon, banner, or ad).
   Tag `tier: "primary"`.
2. **Illustrative tier (fallback):** if no primary figure exists, a representative photo from the
   item's OWN `lead_sources` page (trade press / avherald — e.g. a photo of the accident aircraft).
   Tag `tier: "illustrative"`.

Pick AT MOST ONE image per record. If nothing suitable exists, emit no entry for that record.

## Hard rules
- **Return metadata only. Never download, embed, or base64 an image.** The downstream gate decides
  what may be embedded; you only point at a URL.
- **Public internet only.** No logins/paywalls.
- The `caption` and `alt_text` describe the PHOTO ONLY (what is visibly shown). Never put a
  reference number, revision, date, or any unverified claim in them.
- `image_url` must be a direct link to the image file or the page it sits on; `link_url` is where a
  reader should be sent to view the original (defaults to `image_url`).
- Do not guess a URL. If you did not actually see the image while fetching, do not emit it.

## Output (return ONLY this JSON, no prose, no code fences)
```json
{ "images": [
  {
    "record_id": "<record id, exactly as in input>",
    "tier": "primary | illustrative",
    "image_url": "<direct image or page URL you actually saw>",
    "link_url": "<where to view the original>",
    "source_domain": "<host of image_url>",
    "source_label": "<short human label, e.g. 'NTSB DCA26MA024 hearing exhibit'>",
    "caption": "<one line describing what the photo shows>",
    "alt_text": "<short screen-reader description>"
  }
] }
```
Records you skipped simply have no entry. `source_domain` is advisory only — the gate recomputes it
from `image_url` and will not embed anything off its public-domain allowlist, so be honest about the
tier and never route a copyrighted photo through `tier: "primary"`.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/agents/imagery.md
git commit -m "feat(imagery): add imagery subagent spec (metadata-only photo scout)"
```

---

## Phase 3 — HTML renderer

The renderer assembles the Flight Deck HTML from the **same gate-passed JSON the writer consumes**
(`06_deduped.json` + `07_radar.json` + `08_corner.json`) plus `10_images.json`. It is **fetch-free**
and reproduces the writer's documented routing rules deterministically: group by `category`; route
all-NPRM/PAD records to *On the Horizon*; suppress `## Read-Across` when fleet items ≥ the configured
threshold; order VERIFIED-first then newest; carry-over + dedup tags.

### Task 7: Escaping, confidence tags, quotes

**Files:**
- Create: `tools/render_publication.py`
- Create: `tools/test_render_publication.py`

- [ ] **Step 1: Write the failing test**

Create `tools/test_render_publication.py`:

```python
#!/usr/bin/env python3
"""Tests for tools/render_publication.py — the deterministic HTML renderer."""
import unittest

import render_publication as rp


class TestPrimitives(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(rp.html_escape("A & B <x>"), "A &amp; B &lt;x&gt;")
        self.assertEqual(rp.html_escape(None), "")

    def test_chip_verified(self):
        html = rp.chip("VERIFIED")
        self.assertIn("VERIFIED", html)
        self.assertIn('class="tag v"', html)

    def test_conf_inline_proposed_for_nprm(self):
        self.assertEqual(rp.conf_inline("VERIFIED", "NPRM"), "[PROPOSED — not yet final]")
        self.assertEqual(rp.conf_inline("VERIFIED", "AD"), "[VERIFIED — primary source]")

    def test_quote_renders_attribution_and_source(self):
        q = {"text": "loss of control", "doc_title": "AD; Airbus",
             "ref_number": "FAA AD 1", "revision_or_date": "2026-01-01",
             "url": "https://govinfo.gov/x"}
        html = rp.render_quote(q)
        self.assertIn("loss of control", html)
        self.assertIn("FAA AD 1", html)
        self.assertIn('href="https://govinfo.gov/x"', html)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools && python -m pytest test_render_publication.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'render_publication'`

- [ ] **Step 3: Write minimal implementation**

Create `tools/render_publication.py`:

```python
#!/usr/bin/env python3
"""render_publication.py — deterministic HTML publication renderer for the digest.

Assembles the "Flight Deck" branded, magazine-style HTML from the SAME gate-passed
JSON the Markdown writer consumes (06_deduped.json + 07_radar.json + 08_corner.json)
plus the gate-passed images (10_images.json). It is FETCH-FREE: like the writer, it
can never introduce a reference, quote, or fact that did not pass the gates. Images
are the only external content and they already passed the image gate.

Reproduces the writer's documented routing: group by category; all-NPRM/PAD records
-> On the Horizon; suppress Read-Across when fleet items >= threshold; order
VERIFIED-first then newest; carry-over + dedup tags. Stdlib only.
"""
import argparse
import json
import re
import sys


def html_escape(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


CONF_CLASS = {"VERIFIED": "v", "REPORTED": "r", "UNVERIFIED": "u"}
CONF_BRACKET = {
    "VERIFIED": "[VERIFIED — primary source]",
    "REPORTED": "[REPORTED — trade press, unconfirmed]",
    "UNVERIFIED": "[UNVERIFIED — reference exists, text not accessed]",
}
PROPOSED_TYPES = {"NPRM", "PAD"}


def chip(confidence):
    c = (confidence or "UNVERIFIED").upper()
    return '<span class="tag %s">%s</span>' % (CONF_CLASS.get(c, "u"), html_escape(c))


def conf_inline(confidence, ref_type=None):
    if (ref_type or "").upper() in PROPOSED_TYPES:
        return "[PROPOSED — not yet final]"
    return CONF_BRACKET.get((confidence or "UNVERIFIED").upper(), CONF_BRACKET["UNVERIFIED"])


def render_quote(q):
    text = html_escape(q.get("text"))
    attrib = ", ".join([x for x in [html_escape(q.get("doc_title")),
                                    html_escape(q.get("ref_number")),
                                    html_escape(q.get("revision_or_date"))] if x])
    url = q.get("url")
    link = ' <a class="src" href="%s">[source]</a>' % html_escape(url) if url else ""
    return '<p class="verbatim">&ldquo;%s&rdquo; — %s%s</p>' % (text, attrib, link)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tools && python -m pytest test_render_publication.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/render_publication.py tools/test_render_publication.py
git commit -m "feat(render): escaping, confidence chips, quote rendering"
```

---

### Task 8: Image block + technical detail + full item

**Files:**
- Modify: `tools/render_publication.py`
- Modify: `tools/test_render_publication.py`

- [ ] **Step 1: Write the failing test**

Append to `tools/test_render_publication.py` (before `if __name__`):

```python
def _record(**kw):
    base = {
        "id": "r1", "headline": "GE90 disk AD", "category": "fleet",
        "types_affected": ["777-300ER"], "event_date": "2026-01-02",
        "item_confidence": "VERIFIED",
        "summary": "Disk replacement required.",
        "root_cause": "Iron inclusion in powder metal.",
        "oem_regulator_position": None,
        "references": [{
            "ref_type": "AD", "ref_number": "FAA AD 2025-25-07",
            "confidence": "VERIFIED", "effective_date": "2026-02-06",
            "primary_source_url": "https://www.govinfo.gov/x.htm",
        }],
        "quotes": [], "read_across": None,
    }
    base.update(kw)
    return base


class TestImageBlock(unittest.TestCase):
    def test_embed_renders_img_with_data_uri(self):
        d = {"embed": True, "data_uri": "data:image/jpeg;base64,QQ==",
             "alt_text": "lug", "caption": "A lug", "source_label": "NTSB"}
        cell, attr = rp.render_image(d)
        self.assertIn("data:image/jpeg;base64,QQ==", cell)
        self.assertEqual(attr, "")

    def test_link_only_renders_attribution_no_img(self):
        d = {"embed": False, "caption": "aircraft", "source_label": "Avherald",
             "link_url": "https://avherald.com/p"}
        cell, attr = rp.render_image(d)
        self.assertEqual(cell, "")
        self.assertIn("Avherald", attr)
        self.assertIn("https://avherald.com/p", attr)
        self.assertNotIn("data:image", attr)

    def test_none_directive_empty(self):
        self.assertEqual(rp.render_image(None), ("", ""))


class TestItem(unittest.TestCase):
    def test_item_has_headline_chip_source_link(self):
        html = rp.render_item(_record(), None)
        self.assertIn("GE90 disk AD", html)
        self.assertIn('class="tag v"', html)
        self.assertIn('href="https://www.govinfo.gov/x.htm"', html)
        self.assertIn("[VERIFIED — primary source]", html)

    def test_carryover_labelled(self):
        html = rp.render_item(_record(developing_carryover=True), None)
        self.assertIn("carried-over developing event", html)

    def test_embedded_image_item_uses_table(self):
        d = {"embed": True, "data_uri": "data:image/jpeg;base64,QQ==",
             "alt_text": "x", "caption": "c", "source_label": "NTSB"}
        html = rp.render_item(_record(), d)
        self.assertIn("<table", html)
        self.assertIn("data:image/jpeg;base64,QQ==", html)

    def test_dedup_updated_tag(self):
        rec = _record(dedup={"status": "updated", "previously_reported": "2026-06-01"})
        html = rp.render_item(rec, None)
        self.assertIn("[UPDATED since 2026-06-01]", html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools && python -m pytest test_render_publication.py::TestItem -v`
Expected: FAIL — `AttributeError: module 'render_publication' has no attribute 'render_item'`

- [ ] **Step 3: Write minimal implementation**

Add to `tools/render_publication.py`:

```python
def render_image(directive):
    """Return (image_cell_html, attribution_html). At most one is non-empty."""
    if not directive:
        return "", ""
    cap = html_escape(directive.get("caption"))
    src = html_escape(directive.get("source_label"))
    if directive.get("embed") and directive.get("data_uri"):
        alt = html_escape(directive.get("alt_text"))
        caption_line = " &middot; ".join([x for x in [cap, src] if x])
        cell = ('<img src="%s" alt="%s" style="width:160px;border:1px solid #c4ccd4;'
                'border-radius:4px;display:block">'
                '<div class="cap">%s</div>') % (directive["data_uri"], alt, caption_line)
        return cell, ""
    link = html_escape(directive.get("link_url") or "")
    label = src or "source"
    attribution = ('<p class="photo-attr">Photo: %s — illustrative '
                   '<a href="%s">[view original &#8599;]</a></p>') % (label, link)
    return "", attribution


def render_technical(record):
    parts = []
    for r in (record.get("references") or []):
        rt = (r.get("ref_type") or "").strip()
        num = (r.get("ref_number") or "").strip()
        label = html_escape(("%s %s" % (rt, num)).strip()) if rt and rt != "other" else html_escape(num)
        tag = conf_inline(r.get("confidence"), rt)
        eff = (" — effective %s" % html_escape(r.get("effective_date"))) if r.get("effective_date") else ""
        url = r.get("primary_source_url")
        src = ' <a class="src" href="%s">[source]</a>' % html_escape(url) if url else ""
        seg = " ".join([s for s in [label, tag] if s]) + eff + src
        parts.append(seg.strip())
    bits = []
    if parts:
        bits.append("; ".join(parts))
    if record.get("root_cause"):
        bits.append(html_escape(record["root_cause"]))
    if record.get("oem_regulator_position"):
        bits.append(html_escape(record["oem_regulator_position"]))
    if not bits:
        return ""
    return '<p><span class="lbl">Technical detail:</span> %s.</p>' % "; ".join(bits)


def render_item(record, directive):
    head = html_escape(record.get("headline"))
    types = html_escape(", ".join(record.get("types_affected") or []))
    date = html_escape(record.get("event_date"))
    conf = chip(record.get("item_confidence"))

    carry = ""
    if record.get("developing_carryover") or record.get("within_window") is False:
        carry = ' <span class="carry">[carried-over developing event]</span>'
    dedup = record.get("dedup") or {}
    updated = ""
    if dedup.get("status") == "updated":
        updated = ' <span class="updated">[UPDATED since %s]</span>' % \
                  html_escape(dedup.get("previously_reported") or "")

    img_cell, attribution = render_image(directive)

    body = []
    if record.get("summary"):
        body.append('<p><span class="lbl">What happened:</span> %s</p>' % html_escape(record["summary"]))
    tech = render_technical(record)
    if tech:
        body.append(tech)
    for q in (record.get("quotes") or []):
        body.append(render_quote(q))
    if record.get("category") != "fleet" and record.get("read_across"):
        body.append('<p><span class="lbl">Read-across to fleet:</span> %s</p>'
                    % html_escape(record["read_across"]))
    if attribution:
        body.append(attribution)
    body_html = "\n".join(body)

    head_block = ('<div class="hl">%s %s%s%s</div>\n<div class="meta">%s%s</div>'
                  % (head, conf, carry, updated, types, (" &middot; " + date if date else "")))

    if img_cell:
        return ('<div class="item"><table class="itemtbl" cellpadding="0" cellspacing="0"><tr>'
                '<td class="txt" valign="top">%s\n%s</td>'
                '<td class="imgcell" valign="top">%s</td></tr></table></div>'
                % (head_block, body_html, img_cell))
    return '<div class="item">%s\n%s</div>' % (head_block, body_html)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tools && python -m pytest test_render_publication.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add tools/render_publication.py tools/test_render_publication.py
git commit -m "feat(render): image block, technical detail, full item rendering"
```

---

### Task 9: Grouping, ordering, Read-Across suppression

**Files:**
- Modify: `tools/render_publication.py`
- Modify: `tools/test_render_publication.py`

- [ ] **Step 1: Write the failing test**

Append to `tools/test_render_publication.py` (before `if __name__`):

```python
class TestGrouping(unittest.TestCase):
    def test_all_nprm_record_is_horizon(self):
        rec = _record(references=[{"ref_type": "NPRM", "ref_number": "X",
                                   "confidence": "VERIFIED"}])
        self.assertTrue(rp.is_horizon(rec))

    def test_mixed_refs_not_horizon(self):
        rec = _record(references=[{"ref_type": "NPRM", "ref_number": "X", "confidence": "VERIFIED"},
                                  {"ref_type": "AD", "ref_number": "Y", "confidence": "VERIFIED"}])
        self.assertFalse(rp.is_horizon(rec))

    def test_order_verified_before_reported_then_newest(self):
        a = _record(id="a", item_confidence="REPORTED", event_date="2026-06-20")
        b = _record(id="b", item_confidence="VERIFIED", event_date="2026-06-01")
        c = _record(id="c", item_confidence="VERIFIED", event_date="2026-06-10")
        ordered = [r["id"] for r in rp.order_records([a, b, c])]
        self.assertEqual(ordered, ["c", "b", "a"])

    def test_read_across_suppressed_when_fleet_threshold_met(self):
        by_cat = {"fleet": [1, 2, 3, 4, 5], "read_across": [9], "industry": []}
        rp.suppress_read_across(by_cat, 5)
        self.assertEqual(by_cat["read_across"], [])

    def test_read_across_kept_below_threshold(self):
        by_cat = {"fleet": [1, 2], "read_across": [9], "industry": []}
        rp.suppress_read_across(by_cat, 5)
        self.assertEqual(by_cat["read_across"], [9])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools && python -m pytest test_render_publication.py::TestGrouping -v`
Expected: FAIL — `AttributeError: module 'render_publication' has no attribute 'is_horizon'`

- [ ] **Step 3: Write minimal implementation**

Add to `tools/render_publication.py`:

```python
CONF_RANK = {"VERIFIED": 0, "REPORTED": 1, "UNVERIFIED": 2}


def is_horizon(record):
    """A proposed rule: has references and ALL of them are NPRM/PAD."""
    refs = record.get("references") or []
    return bool(refs) and all((r.get("ref_type") or "").upper() in PROPOSED_TYPES for r in refs)


def order_records(records):
    """VERIFIED first, then newest event_date first. Stable two-pass sort."""
    recs = sorted(records, key=lambda r: r.get("event_date") or "", reverse=True)
    recs.sort(key=lambda r: CONF_RANK.get((r.get("item_confidence") or "UNVERIFIED").upper(), 3))
    return recs


def group_records(records):
    """Split into horizon (proposed) + core categories. Unknown categories fall to industry."""
    by_cat = {"fleet": [], "read_across": [], "industry": [], "_horizon": []}
    for rec in records:
        if is_horizon(rec):
            by_cat["_horizon"].append(rec)
            continue
        cat = (rec.get("category") or "industry").lower()
        if cat not in ("fleet", "read_across", "industry"):
            cat = "industry"
        by_cat[cat].append(rec)
    return by_cat


def suppress_read_across(by_cat, min_fleet_items):
    """Mechanically suppress ## Read-Across on a busy fleet week (structure over instruction)."""
    if len(by_cat.get("fleet", [])) >= min_fleet_items:
        by_cat["read_across"] = []
    return by_cat
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tools && python -m pytest test_render_publication.py::TestGrouping -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/render_publication.py tools/test_render_publication.py
git commit -m "feat(render): grouping, ordering, Read-Across suppression"
```

---

### Task 10: Config parsing + Standing Watch + Sources line

**Files:**
- Modify: `tools/render_publication.py`
- Modify: `tools/test_render_publication.py`

- [ ] **Step 1: Write the failing test**

Append to `tools/test_render_publication.py` (before `if __name__`):

```python
class TestConfigAndWatch(unittest.TestCase):
    YAML = """
operator:
  name: Cathay Pacific
fleet:
  - type: A330-300
  - type: 777-300ER
standing_watch:
  read_across_min_fleet_items: 5
"""

    def test_parse_operator_and_fleet(self):
        self.assertEqual(rp.parse_operator_name(self.YAML), "Cathay Pacific")
        self.assertEqual(rp.parse_fleet_types(self.YAML), ["A330-300", "777-300ER"])

    def test_parse_min_fleet_default(self):
        self.assertEqual(rp.parse_scalar(self.YAML, "read_across_min_fleet_items", 5), 5)
        self.assertEqual(rp.parse_scalar("", "read_across_min_fleet_items", 5), 5)

    def test_radar_block_renders_entries(self):
        radar = [{"ref_number": "FAA AD 1", "effective_date": "2026-07-30",
                  "headline": "Trent blade", "url": "https://govinfo.gov/y"}]
        html = rp.render_radar(radar)
        self.assertIn("FAA AD 1", html)
        self.assertIn("2026-07-30", html)
        self.assertIn("https://govinfo.gov/y", html)

    def test_radar_empty_is_blank(self):
        self.assertEqual(rp.render_radar([]), "")

    def test_sources_line_counts(self):
        recs = [_record(item_confidence="VERIFIED"),
                _record(item_confidence="REPORTED"),
                _record(item_confidence="VERIFIED")]
        line = rp.render_sources_line(recs)
        self.assertIn("3 items", line)
        self.assertIn("2 VERIFIED", line)
        self.assertIn("1 REPORTED", line)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools && python -m pytest test_render_publication.py::TestConfigAndWatch -v`
Expected: FAIL — `AttributeError: module 'render_publication' has no attribute 'parse_operator_name'`

- [ ] **Step 3: Write minimal implementation**

Add to `tools/render_publication.py`:

```python
def parse_scalar(text, key, default):
    m = re.search(r"(?m)^\s*%s:\s*(\d+)" % re.escape(key), text)
    return int(m.group(1)) if m else default


def parse_operator_name(text):
    m = re.search(r"(?m)^operator:\s*\n(?:\s+.*\n)*?\s+name:\s*(.+)$", text)
    return m.group(1).strip() if m else "Operator"


def parse_fleet_types(text):
    """Collect 'type:' values under the top-level 'fleet:' block."""
    types, in_block = [], False
    for line in text.splitlines():
        if re.match(r"^fleet:\s*$", line):
            in_block = True
            continue
        if in_block:
            if re.match(r"^\S", line):
                break
            m = re.match(r"^\s+-?\s*type:\s*(.+)$", line)
            if m:
                types.append(m.group(1).strip())
    return types


def render_radar(radar):
    if not radar:
        return ""
    rows = []
    for e in radar:
        ref = html_escape(e.get("ref_number"))
        eff = html_escape(e.get("effective_date"))
        head = html_escape(e.get("headline"))
        url = e.get("url")
        link = ' <a class="src" href="%s">[source]</a>' % html_escape(url) if url else ""
        rows.append('<li><strong>%s</strong> — effective %s · %s%s</li>' % (ref, eff, head, link))
    return ('<h3 class="watch-h">Compliance Radar</h3>\n<ul class="radar">\n%s\n</ul>'
            % "\n".join(rows))


def render_corner(corner):
    if not corner:
        return ""
    title = html_escape(corner.get("title"))
    body = html_escape(corner.get("body"))
    return ('<h3 class="watch-h">Engineer\'s Corner</h3>\n'
            '<div class="corner"><h4>%s</h4><p>%s</p></div>' % (title, body))


def render_sources_line(records):
    counts = {"VERIFIED": 0, "REPORTED": 0, "UNVERIFIED": 0}
    for r in records:
        c = (r.get("item_confidence") or "UNVERIFIED").upper()
        counts[c] = counts.get(c, 0) + 1
    total = len(records)
    return ('<p class="sources"><strong>Sources &amp; Confidence:</strong> %d items — '
            '%d VERIFIED, %d REPORTED, %d UNVERIFIED. Public-internet sources only; '
            'gated OEM documents marked UNVERIFIED.</p>'
            % (total, counts["VERIFIED"], counts["REPORTED"], counts["UNVERIFIED"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tools && python -m pytest test_render_publication.py::TestConfigAndWatch -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/render_publication.py tools/test_render_publication.py
git commit -m "feat(render): config parsing, Compliance Radar / Corner, Sources line"
```

---

### Task 11: Document assembly (Flight Deck template) + CLI

**Files:**
- Modify: `tools/render_publication.py`
- Modify: `tools/test_render_publication.py`

- [ ] **Step 1: Write the failing test**

Append to `tools/test_render_publication.py` (before `if __name__`):

```python
class TestDocument(unittest.TestCase):
    BUNDLE = {
        "records": [
            _record(id="f1", category="fleet", headline="GE90 AD"),
            _record(id="ra1", category="read_across", headline="Trent 1000 AD",
                    read_across="Trent family read-across."),
            _record(id="ind1", category="industry", headline="UPS MD-11 hearing"),
            _record(id="h1", category="fleet", headline="777 MLG NPRM",
                    references=[{"ref_type": "NPRM", "ref_number": "FAA-1",
                                 "confidence": "VERIFIED",
                                 "primary_source_url": "https://govinfo.gov/n"}]),
        ],
        "radar": [{"ref_number": "FAA AD 9", "effective_date": "2026-07-30",
                   "headline": "blade", "url": "https://govinfo.gov/y"}],
        "corner": None,
        "images": {"ind1": {"embed": False, "caption": "wreck",
                            "source_label": "Avherald", "link_url": "https://avherald.com/p"}},
        "operator": "Cathay Pacific",
        "fleet_types": ["A330-300", "777-300ER"],
        "min_fleet_items": 5,
        "date_label": "Week of 2026-06-27",
    }

    def test_document_has_sections_and_masthead(self):
        html = rp.render_document(self.BUNDLE)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Cathay Pacific", html)
        self.assertIn("Directly Fleet-Relevant", html)
        self.assertIn("Read-Across", html)
        self.assertIn("Major Industry Events", html)
        self.assertIn("Standing Watch", html)
        self.assertIn("On the Horizon", html)

    def test_horizon_record_not_in_core_sections(self):
        html = rp.render_document(self.BUNDLE)
        # The NPRM headline appears once, under On the Horizon, tagged PROPOSED.
        self.assertIn("777 MLG NPRM", html)
        self.assertIn("[PROPOSED — not yet final]", html)

    def test_illustrative_image_is_link_not_embedded(self):
        html = rp.render_document(self.BUNDLE)
        self.assertIn("avherald.com/p", html)
        self.assertNotIn("data:image", html)

    def test_no_unknown_reference_smuggled(self):
        # Verified-content guarantee: a ref number not in the bundle never appears.
        html = rp.render_document(self.BUNDLE)
        self.assertNotIn("FAA AD 2099", html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tools && python -m pytest test_render_publication.py::TestDocument -v`
Expected: FAIL — `AttributeError: module 'render_publication' has no attribute 'render_document'`

- [ ] **Step 3: Write minimal implementation**

Add to `tools/render_publication.py`. First the CSS/template constants:

```python
STYLE = """
:root{--ink:#0B2545;--ink2:#13315C;--accent:#15B8A0;--paper:#F7F9FC;--body:#26303c;--muted:#8a97a8;}
*{box-sizing:border-box;}
body{margin:0;background:var(--paper);color:var(--body);
  font-family:'Source Serif 4',Georgia,serif;font-size:14px;line-height:1.55;}
.wrap{max-width:720px;margin:0 auto;background:#fff;}
.mast{background:var(--ink);color:#fff;padding:22px 26px;}
.mast .kick{font-family:'Inter',Arial,sans-serif;font-size:10px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--accent);font-weight:600;}
.mast .ttl{font-family:'Fraunces',Georgia,serif;font-size:26px;font-weight:600;margin:6px 0 0;}
.mast .sub{font-family:'Inter',Arial,sans-serif;font-size:12px;color:#aebfd6;margin-top:6px;}
h2.sect{font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink);border-bottom:2px solid var(--accent);
  margin:26px 26px 4px;padding-bottom:6px;}
.item{padding:12px 26px 18px;border-bottom:1px solid #eef1f5;}
.itemtbl{width:100%;} .itemtbl td.txt{vertical-align:top;padding-right:14px;}
.itemtbl td.imgcell{vertical-align:top;width:160px;}
.hl{font-family:'Fraunces',Georgia,serif;font-size:17px;font-weight:600;color:var(--ink2);line-height:1.3;}
.meta{font-family:'Inter',Arial,sans-serif;font-size:10px;text-transform:uppercase;
  letter-spacing:.05em;color:var(--muted);margin:3px 0 8px;}
p{margin:6px 0;} .lbl{font-style:italic;color:#5b6b7d;}
.tag{font-family:'Inter',Arial,sans-serif;font-size:10px;font-weight:700;padding:1px 6px;
  border-radius:3px;margin-left:4px;}
.tag.v{background:#DCF6F1;color:#0E7C6B;} .tag.r{background:#FFF4E5;color:#B26A00;}
.tag.u{background:#eceff3;color:#5a6470;}
.src{font-family:'Inter',Arial,sans-serif;color:#0E8C7A;font-size:12px;text-decoration:none;}
.carry,.updated{font-family:'Inter',Arial,sans-serif;font-size:10px;color:#B26A00;}
.verbatim{font-style:italic;color:#3a4654;border-left:3px solid var(--accent);
  padding-left:10px;margin-left:2px;}
.cap{font-family:'Inter',Arial,sans-serif;font-size:10px;color:#888;margin-top:3px;}
.photo-attr{font-family:'Inter',Arial,sans-serif;font-size:11px;color:#6b7785;}
.watch{background:#F2F5F9;margin-top:10px;padding:4px 0 14px;}
.watch-intro{font-family:'Inter',Arial,sans-serif;font-size:11px;color:#6b7785;
  font-style:italic;padding:10px 26px 0;}
.watch-h{font-family:'Inter',Arial,sans-serif;font-size:12px;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--ink);margin:14px 26px 4px;}
.radar,.corner{margin:0 26px;font-size:13px;}
.sources{font-family:'Inter',Arial,sans-serif;font-size:12px;color:#5a6470;padding:14px 26px 26px;}
@media print{body{background:#fff;} .item{break-inside:avoid;}}
"""

FONT_LINK = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
             'family=Inter:wght@400;600;700&family=Fraunces:opsz,wght@9..144,500;9..144,600&'
             'family=Source+Serif+4:opsz,wght@8..60,400&display=swap">')

SECTION_TITLES = [("fleet", "Directly Fleet-Relevant"),
                  ("read_across", "Read-Across (Peer Types)"),
                  ("industry", "Major Industry Events")]
```

Then the assembly + CLI:

```python
def render_section(title, records, images):
    if not records:
        return ""
    items = "\n".join(render_item(r, images.get(r.get("id"))) for r in order_records(records))
    return '<h2 class="sect">%s</h2>\n%s' % (html_escape(title), items)


def render_standing_watch(radar, horizon_records, corner, images):
    radar_html = render_radar(radar)
    horizon_html = ""
    if horizon_records:
        items = "\n".join(render_item(r, images.get(r.get("id")))
                          for r in order_records(horizon_records))
        horizon_html = '<h3 class="watch-h">On the Horizon</h3>\n%s' % items
    corner_html = render_corner(corner)
    if not (radar_html or horizon_html or corner_html):
        return ""
    inner = "\n".join(x for x in [radar_html, horizon_html, corner_html] if x)
    return ('<h2 class="sect">Standing Watch</h2>\n'
            '<p class="watch-intro">Forward-looking and background items — not this week\'s '
            'verified incident intelligence.</p>\n<div class="watch">%s</div>' % inner)


def render_document(bundle):
    records = bundle.get("records", [])
    images = bundle.get("images", {}) or {}
    by_cat = group_records(records)
    suppress_read_across(by_cat, bundle.get("min_fleet_items", 5))

    core_shown = []
    sections = []
    for key, title in SECTION_TITLES:
        recs = by_cat.get(key, [])
        core_shown.extend(recs)
        sections.append(render_section(title, recs, images))

    watch = render_standing_watch(bundle.get("radar", []), by_cat.get("_horizon", []),
                                  bundle.get("corner"), images)
    sources = render_sources_line(core_shown)

    operator = html_escape(bundle.get("operator", "Operator"))
    fleet = html_escape(" · ".join(bundle.get("fleet_types", [])))
    date_label = html_escape(bundle.get("date_label", ""))
    body = "\n".join(x for x in (sections + [watch, sources]) if x)

    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '%s\n<style>%s</style>\n<title>%s — Technical-Intelligence Digest</title></head>\n'
        '<body><div class="wrap">\n'
        '<div class="mast"><div class="kick">%s · Engineering &amp; Technical Services</div>'
        '<div class="ttl">Technical-Intelligence Digest</div>'
        '<div class="sub">%s · %s</div></div>\n'
        '%s\n</div></body></html>'
        % (FONT_LINK, STYLE, operator, operator, date_label, fleet, body)
    )


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Render the Flight Deck HTML publication.")
    ap.add_argument("--records", required=True, help="06_deduped.json")
    ap.add_argument("--radar", default=None, help="07_radar.json")
    ap.add_argument("--corner", default=None, help="08_corner.json")
    ap.add_argument("--images", default=None, help="10_images.json")
    ap.add_argument("--config", default="config/fleet.yaml")
    ap.add_argument("--date-label", required=True, help='e.g. "Week of 2026-06-27"')
    ap.add_argument("--outfile", required=True)
    args = ap.parse_args(argv)

    cfg = ""
    try:
        cfg = open(args.config, encoding="utf-8").read()
    except OSError:
        pass

    records = (_load(args.records) or {}).get("records", [])
    radar = (_load(args.radar) or {}).get("radar", []) if args.radar else []
    corner = (_load(args.corner) or {}).get("corner") if args.corner else None
    images = (_load(args.images) or {}).get("images", {}) if args.images else {}

    bundle = {
        "records": records, "radar": radar, "corner": corner, "images": images,
        "operator": parse_operator_name(cfg), "fleet_types": parse_fleet_types(cfg),
        "min_fleet_items": parse_scalar(cfg, "read_across_min_fleet_items", 5),
        "date_label": args.date_label,
    }
    html = render_document(bundle)
    with open(args.outfile, "w", encoding="utf-8") as f:
        f.write(html)
    sys.stderr.write("[render] wrote %s (%d records, %d images)\n"
                     % (args.outfile, len(records), len(images)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the full renderer test suite**

Run: `cd tools && python -m pytest test_render_publication.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Real-data smoke test**

Run (uses the existing 2026-06-27 run artifacts):
```bash
cd "C:/Users/josep/Project_AircraftNews" && python tools/render_publication.py \
  --records runs/2026-06-27/06_deduped.json \
  --radar runs/2026-06-27/07_radar.json \
  --corner runs/2026-06-27/08_corner.json \
  --config config/fleet.yaml \
  --date-label "Week of 2026-06-27" \
  --outfile digests/2026-06-27-weekly.html
```
Expected: writes `digests/2026-06-27-weekly.html`. Open it in a browser and confirm the masthead,
sections, confidence chips, and `[source]` links render (no images yet — that's fine).

- [ ] **Step 6: Commit**

```bash
git add tools/render_publication.py tools/test_render_publication.py digests/2026-06-27-weekly.html
git commit -m "feat(render): Flight Deck document assembly + CLI"
```

---

## Phase 4 — PDF

### Task 12: `html_to_pdf.py` (Playwright/Chromium, failure-tolerant)

**Files:**
- Create: `tools/html_to_pdf.py`

- [ ] **Step 1: Create the script**

Create `tools/html_to_pdf.py`:

```python
#!/usr/bin/env python3
"""html_to_pdf.py — render a digest HTML file to PDF via headless Chromium (Playwright).

Failure-tolerant by design: if Playwright/Chromium is unavailable or errors, print a
warning and exit 0 so the HTML still ships. Run AFTER render_publication.py.
"""
import argparse
import pathlib
import sys


def html_to_pdf(html_path, pdf_path):
    from playwright.sync_api import sync_playwright
    url = pathlib.Path(html_path).resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.pdf(path=pdf_path, format="A4", print_background=True,
                 margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"})
        browser.close()


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="HTML -> PDF via Playwright/Chromium.")
    ap.add_argument("--infile", required=True)
    ap.add_argument("--outfile", required=True)
    args = ap.parse_args(argv)
    try:
        html_to_pdf(args.infile, args.outfile)
    except Exception as e:
        sys.stderr.write("[pdf] WARNING: PDF step skipped (%s). HTML still produced.\n" % e)
        return 0
    sys.stderr.write("[pdf] wrote %s\n" % args.outfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Install Playwright + Chromium (one-time)**

Run:
```bash
pip install playwright && python -m playwright install chromium
```
Expected: installs the `playwright` package and the Chromium browser binary.

- [ ] **Step 3: Smoke-test the PDF on the rendered HTML**

Run:
```bash
cd "C:/Users/josep/Project_AircraftNews" && python tools/html_to_pdf.py \
  --infile digests/2026-06-27-weekly.html --outfile digests/2026-06-27-weekly.pdf
```
Expected: stderr `[pdf] wrote digests/2026-06-27-weekly.pdf`. Open the PDF and confirm the magazine
layout paginates cleanly (items don't split awkwardly across pages).

- [ ] **Step 4: Verify graceful failure**

Temporarily rename the browser cache or run with Playwright uninstalled in a throwaway env to confirm
the script prints the warning and exits 0 (HTML still present). If you can't easily simulate this,
inspect the `except` path by eye — it must `return 0`, never raise.

- [ ] **Step 5: Commit**

```bash
git add tools/html_to_pdf.py digests/2026-06-27-weekly.pdf
git commit -m "feat(pdf): html_to_pdf via Playwright/Chromium (failure-tolerant)"
```

---

## Phase 5 — Orchestrator wiring + adversarial integration test + roadmap

### Task 13: Adversarial integration test — copyrighted photo can never embed

**Files:**
- Modify: `tools/test_fetch_images.py`

This is the image analog of the existing trade-press adversarial test: a hostile candidate that
spoofs `tier: "primary"` and a fake `source_domain` over a copyrighted (avherald) URL must end up
link-only, with no bytes fetched.

- [ ] **Step 1: Write the failing test**

Append to `tools/test_fetch_images.py` (before `if __name__`):

```python
class TestAdversarial(unittest.TestCase):
    def test_spoofed_primary_over_copyrighted_url_never_embeds(self):
        downloaded = []

        def spy_downloader(url):
            downloaded.append(url)
            return {"data_uri": "data:image/jpeg;base64,QQ==", "ext": "jpg", "raw_bytes": b"A"}

        hostile = {
            "record_id": "x", "tier": "primary",                  # lies
            "image_url": "https://avherald.com/photo.jpg",        # copyrighted, off-allowlist
            "source_domain": "ntsb.gov",                          # spoofed
            "link_url": "https://avherald.com/h",
            "caption": "wreck", "source_label": "Avherald",
        }
        out = fi.process({"images": [hostile]}, ALLOW, spy_downloader)
        self.assertFalse(out["images"]["x"]["embed"])
        self.assertEqual(downloaded, [])   # gate never even attempted a download

    def test_lookalike_domain_never_embeds(self):
        c = _candidate(image_url="https://ntsb.gov.evil.com/x.jpg")
        d = fi.resolve_image(c, ALLOW, _ok_downloader)
        self.assertFalse(d["embed"])
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd tools && python -m pytest test_fetch_images.py -v`
Expected: PASS (these assert existing behaviour — the gate recomputes the domain from the URL, so the
spoofed fields are inert; if either fails, the gate has a provenance hole — fix `resolve_image` before
proceeding).

- [ ] **Step 3: Commit**

```bash
git add tools/test_fetch_images.py
git commit -m "test(imagery): adversarial — spoofed primary over copyrighted URL stays link-only"
```

---

### Task 14: Wire the orchestrator (`digest.md`)

**Files:**
- Modify: `.claude/commands/digest.md`

- [ ] **Step 1: Add the imagery build steps after Step 4f**

In `.claude/commands/digest.md`, immediately after the Step 4f block (BUILD ENGINEER'S CORNER) and
before `## Step 5 — WRITE`, insert:

````markdown
## Step 4g — SCOUT IMAGERY (dispatch the `imagery` subagent)
Find at most one relatable photo per item. Use the Agent tool with `subagent_type: imagery`
(fallback: `general-purpose` + "Read and follow EXACTLY `.claude/agents/imagery.md`"). In the prompt:
> Find imagery for the records in `runs/{CURRENT_DATE}/06_deduped.json` per your instructions.
> Read that file. Return ONLY metadata `{ "images": [...] }` — never download or embed an image.

Save the returned JSON verbatim to `runs/$CURRENT_DATE/09_imagery.json`. If imagery is disabled in
config or the agent returns nothing, save `{ "images": [] }`.

## Step 4h — IMAGE GATE (Bash — deterministic, never skip)
Enforce the copyright/provenance rule. Embeds are restricted to the public-domain
`imagery.embed_allowlist`; everything else becomes link-only. Bytes for off-allowlist photos are
never fetched.
```
python tools/fetch_images.py \
  --config config/fleet.yaml \
  --infile runs/$CURRENT_DATE/09_imagery.json \
  --assets-dir runs/$CURRENT_DATE/assets \
  > runs/$CURRENT_DATE/10_images.json \
  2> runs/$CURRENT_DATE/10_images_report.txt
```
Read `runs/$CURRENT_DATE/10_images_report.txt` and surface the `[images] N embedded, M link-only`
summary. This step never blocks the digest — any download/validation failure degrades to link-only.
````

- [ ] **Step 2: Add the render + PDF steps after the finalise step**

After the Step 5b block (FINALISE) and before `## Step 6 — Report to the user`, insert:

````markdown
## Step 7 — RENDER PUBLICATION (Bash — deterministic)
Build the branded HTML publication from the SAME gate-passed JSON the writer used, plus the
gate-passed images. Like the writer, this tool has no fetch capability and can add nothing new.
```
python tools/render_publication.py \
  --records runs/$CURRENT_DATE/06_deduped.json \
  --radar runs/$CURRENT_DATE/07_radar.json \
  --corner runs/$CURRENT_DATE/08_corner.json \
  --images runs/$CURRENT_DATE/10_images.json \
  --config config/fleet.yaml \
  --date-label "Week of $CURRENT_DATE" \
  --outfile digests/$CURRENT_DATE-{cadence}.html
```

## Step 7b — RENDER PDF (Bash — deterministic, failure-tolerant)
Print the HTML to PDF via headless Chromium. If Playwright/Chromium is unavailable it warns and the
HTML still ships — never treat a PDF miss as a pipeline failure.
```
python tools/html_to_pdf.py \
  --infile digests/$CURRENT_DATE-{cadence}.html \
  --outfile digests/$CURRENT_DATE-{cadence}.pdf
```
````

- [ ] **Step 3: Add guardrails**

In `## Step 6 — Report to the user`, add the HTML/PDF paths to what gets printed. In the
`## Guardrails (do not violate)` list, add:

```markdown
- The publication renderer (Step 7) may only consume gate-passed JSON (`06_deduped.json`,
  `07_radar.json`, `08_corner.json`) + gate-passed images (`10_images.json`). It has no fetch tools;
  it can never add a reference, quote, or fact — exactly like the Writer.
- Images may be EMBEDDED only via `10_images.json` (the image gate). Embedding is restricted to the
  public-domain `imagery.embed_allowlist`; copyrighted/illustrative photos are link-only and must
  never be downloaded or inlined. The imagery subagent returns metadata only.
- The publication layer (Steps 4g/4h/7/7b) must NEVER block the verified Markdown digest. Any imagery
  or PDF failure degrades gracefully (link-only / HTML-only); the digest still completes.
```

- [ ] **Step 4: Commit**

```bash
git add .claude/commands/digest.md
git commit -m "feat: wire imagery (4g/4h) + publication render (7/7b) into orchestrator"
```

---

### Task 15: Update the roadmap

**Files:**
- Modify: `roadmap.md`

- [ ] **Step 1: Add a Stage 7 section**

After the Stage 6 section in `roadmap.md`, add:

```markdown
## Stage 7 — Publication renderer + imagery ✅/🚧

**Goal:** Turn the verified digest into a branded, magazine-style HTML (+ derived PDF) with
copyright-aware photos, without touching the verified core or the no-fetch guarantee.
Spec: `docs/superpowers/specs/2026-06-27-publication-renderer-imagery-design.md`.
Plan: `docs/superpowers/plans/2026-06-27-publication-renderer-imagery.md`.

- [x] `imagery` config block in `config/fleet.yaml` (enabled, embed_allowlist, sizing)
- [x] Image gate `tools/fetch_images.py` (+ tests): URL-recomputed domain, embed only public-domain
      primary figures, everything else link-only, failure-safe; adversarial spoof test
- [x] `imagery` subagent `.claude/agents/imagery.md` (metadata only, never embeds)
- [x] Renderer `tools/render_publication.py` (+ tests): Flight Deck HTML from gate-passed JSON +
      images; reproduces writer routing (horizon/suppression/order/carry-over/dedup); fetch-free
- [x] PDF `tools/html_to_pdf.py` (Playwright/Chromium, failure-tolerant)
- [x] Orchestrator Steps 4g/4h (imagery) + 7/7b (render/PDF) + guardrails
- [ ] Live `/digest` smoke with real fetched imagery (the lug photo end-to-end)
```

- [ ] **Step 2: Add decisions-log rows**

Add to the Decisions log table in `roadmap.md`:

```markdown
| 2026-06-27 | Publication renderer + imagery (Flight Deck) | New layer after dedup: `imagery` subagent (judgment, metadata only) → deterministic image gate (`fetch_images.py`, embed-allowlist) → fetch-free `render_publication.py` (Flight Deck navy+teal magazine HTML) → Playwright PDF. Verified core untouched; renderer can't add facts (no fetch), images are the only external content and pass their own gate |
| 2026-06-27 | Photos: embed primary, link illustrative | Embed only public-domain agency images (ntsb/faa/govinfo/easa); copyrighted trade-press/avherald/OEM-press photos are attribution-link only, never redistributed. Domain recomputed from the URL in the gate, suffix-matched — no agent field can spoof it. One photo per item, only where it helps |
```

- [ ] **Step 3: Commit**

```bash
git add roadmap.md
git commit -m "docs: roadmap Stage 7 (publication renderer + imagery)"
```

---

## Final verification

- [ ] **Run the full tool test suite**

Run: `cd tools && python -m pytest -v`
Expected: all suites green (existing 130 + new `test_fetch_images.py` + `test_render_publication.py`).

- [ ] **End-to-end dry run on existing artifacts**

Re-render the 2026-06-27 digest with images wired (the image gate over an empty `09_imagery.json`
returns `{"images":{}}`, so this exercises the full render path even before a live imagery run):
```bash
cd "C:/Users/josep/Project_AircraftNews" && echo '{"images":[]}' \
  | python tools/fetch_images.py --config config/fleet.yaml > runs/2026-06-27/10_images.json
python tools/render_publication.py \
  --records runs/2026-06-27/06_deduped.json --radar runs/2026-06-27/07_radar.json \
  --corner runs/2026-06-27/08_corner.json --images runs/2026-06-27/10_images.json \
  --config config/fleet.yaml --date-label "Week of 2026-06-27" \
  --outfile digests/2026-06-27-weekly.html
python tools/html_to_pdf.py --infile digests/2026-06-27-weekly.html \
  --outfile digests/2026-06-27-weekly.pdf
```
Expected: HTML + PDF produced; open both and confirm the Flight Deck look (navy masthead, teal
accents, Fraunces headlines, confidence chips, `[source]` links) and clean pagination.

- [ ] **Commit any regenerated artifacts**

```bash
git add digests/2026-06-27-weekly.html digests/2026-06-27-weekly.pdf runs/2026-06-27/10_images.json
git commit -m "chore: regenerate 2026-06-27 publication artifacts"
```
