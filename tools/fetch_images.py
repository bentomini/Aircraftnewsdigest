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
