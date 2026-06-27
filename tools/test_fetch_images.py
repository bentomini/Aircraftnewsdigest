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

    def test_null_images_does_not_raise(self):
        # A poorly-behaved agent could emit {"images": null}; the gate must not crash.
        out = fi.process({"images": None}, ALLOW, _ok_downloader)
        self.assertEqual(out["images"], {})


class TestMainResilience(unittest.TestCase):
    def test_main_survives_malformed_json(self):
        import io
        import contextlib
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            f.write("{not valid json")
            bad = f.name
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = fi.main(["--config", "no-such-config.yaml", "--infile", bad])
            self.assertEqual(rc, 0)
            self.assertIn('"images"', buf.getvalue())
        finally:
            os.unlink(bad)


try:
    import PIL  # noqa: F401
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


@unittest.skipUnless(_HAS_PIL, "Pillow not installed")
class TestRealDownloader(unittest.TestCase):
    def test_resize_and_jpeg_encode_without_network(self):
        import io
        import urllib.request
        from PIL import Image

        # Build a 600x400 raster in memory (wider than the 480px cap).
        src = Image.new("RGB", (600, 400), (10, 120, 90))
        buf = io.BytesIO()
        src.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        class _FakeResp:
            def __init__(self, data):
                self._data = data
                self.headers = self

            def get_content_type(self):
                return "image/png"

            def read(self, n=-1):
                return self._data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: _FakeResp(png_bytes)
        try:
            download = fi.make_downloader(max_bytes=5000000, max_width_px=480, timeout=15)
            got = download("https://www.ntsb.gov/x/fig.png")
        finally:
            urllib.request.urlopen = original

        self.assertTrue(got["data_uri"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(got["ext"], "jpg")
        # Re-decode the returned JPEG to confirm it was downscaled to the 480px cap.
        out_img = Image.open(io.BytesIO(got["raw_bytes"]))
        self.assertEqual(out_img.width, 480)
        self.assertEqual(out_img.height, 320)


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


if __name__ == "__main__":
    unittest.main()
