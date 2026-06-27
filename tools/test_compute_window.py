"""Tests for compute_window.py — the self-healing lookback window.

Run: python tools/test_compute_window.py   (from project root)
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import compute_window as cw

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)


class TestRule(unittest.TestCase):
    def test_cold_start_returns_cap(self):
        self.assertEqual(cw.compute_effective_lookback(None, 7, 30), 30)

    def test_gap_below_nominal_returns_nominal(self):
        self.assertEqual(cw.compute_effective_lookback(2, 7, 30), 7)

    def test_gap_between_returns_gap(self):
        self.assertEqual(cw.compute_effective_lookback(14, 7, 30), 14)

    def test_gap_above_cap_returns_cap(self):
        self.assertEqual(cw.compute_effective_lookback(90, 7, 30), 30)

    def test_gap_equals_nominal_boundary(self):
        self.assertEqual(cw.compute_effective_lookback(7, 7, 30), 7)

    def test_gap_equals_cap_boundary(self):
        self.assertEqual(cw.compute_effective_lookback(30, 7, 30), 30)

    def test_negative_gap_returns_nominal(self):
        self.assertEqual(cw.compute_effective_lookback(-3, 7, 30), 7)

    def test_cap_below_nominal_never_undercovers(self):
        # misconfigured cap < nominal must not produce a window narrower than nominal
        self.assertEqual(cw.compute_effective_lookback(None, 7, 3), 7)


class TestConfigParse(unittest.TestCase):
    def test_parses_cold_start(self):
        text = "run:\n  cadence: weekly\n  cold_start_lookback_days: 30\n"
        self.assertEqual(cw.parse_cold_start_from_yaml(text), 30)

    def test_default_when_absent(self):
        self.assertEqual(cw.parse_cold_start_from_yaml("run:\n  cadence: weekly\n"), 30)


class TestStateIO(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_missing_file_is_cold_start(self):
        self.assertEqual(cw.load_state(os.path.join(PROJECT_ROOT, "does", "not", "exist.json")), {})

    def test_malformed_json_is_cold_start(self):
        path = self._write("{ not valid json")
        try:
            self.assertEqual(cw.load_state(path), {})
        finally:
            os.remove(path)

    def test_non_dict_top_level_is_cold_start(self):
        path = self._write(json.dumps([1, 2, 3]))
        try:
            self.assertEqual(cw.load_state(path), {})
        finally:
            os.remove(path)

    def test_last_run_date_per_cadence(self):
        state = {"weekly": {"last_run_date": "2026-06-19"},
                 "daily": {"last_run_date": "2026-06-25"}}
        self.assertEqual(cw.last_run_date(state, "weekly"), "2026-06-19")
        self.assertEqual(cw.last_run_date(state, "daily"), "2026-06-25")
        self.assertIsNone(cw.last_run_date(state, "monthly"))

    def test_gap_days(self):
        self.assertEqual(cw.gap_days("2026-06-19", "2026-06-26"), 7)
        self.assertIsNone(cw.gap_days(None, "2026-06-26"))
        self.assertIsNone(cw.gap_days("not-a-date", "2026-06-26"))

    def test_record_preserves_other_bucket(self):
        path = self._write(json.dumps({"daily": {"last_run_date": "2026-06-25"}}))
        try:
            cw.record_run(path, "weekly", "2026-06-26")
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(state["weekly"]["last_run_date"], "2026-06-26")
            self.assertEqual(state["daily"]["last_run_date"], "2026-06-25")
        finally:
            os.remove(path)

    def test_record_creates_file_when_absent(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        try:
            cw.record_run(path, "weekly", "2026-06-26")
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(state["weekly"]["last_run_date"], "2026-06-26")
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestRealConfig(unittest.TestCase):
    def test_real_config_declares_cold_start(self):
        import re
        with open(os.path.join(PROJECT_ROOT, "config", "fleet.yaml"), encoding="utf-8") as f:
            text = f.read()
        # parse must find a real value, not fall back to the default-by-absence path
        self.assertIsNotNone(re.search(r"^\s+cold_start_lookback_days:\s*\d+", text, re.MULTILINE))
        self.assertEqual(cw.parse_cold_start_from_yaml(text), 30)


class TestCLI(unittest.TestCase):
    def _run(self, args):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "compute_window.py")] + args,
            capture_output=True, text=True, cwd=PROJECT_ROOT)

    def _tmp_state_path(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)  # start absent
        return path

    def test_compute_cold_start_prints_cap(self):
        path = self._tmp_state_path()
        try:
            r = self._run(["--config", "config/fleet.yaml", "--current-date", "2026-06-26",
                           "--cadence", "weekly", "--nominal-lookback-days", "7", "--state", path])
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), "30")        # clean integer on stdout
            self.assertIn("cold start", r.stderr)            # explanation on stderr
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_record_then_compute_steady_state(self):
        path = self._tmp_state_path()
        try:
            rec = self._run(["--record", "--state", path, "--current-date", "2026-06-26",
                             "--cadence", "weekly"])
            self.assertEqual(rec.returncode, 0)
            comp = self._run(["--config", "config/fleet.yaml", "--current-date", "2026-07-03",
                              "--cadence", "weekly", "--nominal-lookback-days", "7", "--state", path])
            self.assertEqual(comp.stdout.strip(), "7")       # 7-day gap -> nominal
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_record_then_compute_healed(self):
        path = self._tmp_state_path()
        try:
            self._run(["--record", "--state", path, "--current-date", "2026-06-12",
                       "--cadence", "weekly"])
            comp = self._run(["--config", "config/fleet.yaml", "--current-date", "2026-06-26",
                              "--cadence", "weekly", "--nominal-lookback-days", "7", "--state", path])
            self.assertEqual(comp.stdout.strip(), "14")      # 14-day gap -> healed to 14
            self.assertIn("healed", comp.stderr)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_missing_config_exits_nonzero_with_empty_stdout(self):
        path = self._tmp_state_path()
        try:
            r = self._run(["--config", "does/not/exist.yaml", "--current-date", "2026-06-26",
                           "--cadence", "weekly", "--nominal-lookback-days", "7", "--state", path])
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")   # stdout must stay clean — orchestrator captures it
            self.assertIn("ERROR", r.stderr)
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
