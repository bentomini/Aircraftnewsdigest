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
