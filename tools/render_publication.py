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
