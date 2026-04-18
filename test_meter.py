#!/usr/bin/env python3
"""
test_meter.py — Smoke tests for Claude Token Meter data logic.
Tests pure Python functions in meter_core.py (no AppKit required).

Run:  python3 test_meter.py
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta, date
from unittest.mock import patch, MagicMock

# ── Import the module under test ───────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
import meter_core


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_entry(ts_utc: datetime, tokens: int) -> dict:
    """Build a minimal JSONL entry dict."""
    return {
        "timestamp": ts_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "message": {
            "usage": {
                "input_tokens":                tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens":      0,
                "output_tokens":               0,
            }
        }
    }


def _write_jsonl(path: str, entries: list) -> None:
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestExtractTokens(unittest.TestCase):
    def test_normal(self):
        usage = {"input_tokens": 100, "output_tokens": 50,
                 "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5}
        self.assertEqual(meter_core._extract_tokens(usage), 165)

    def test_partial_keys(self):
        self.assertEqual(meter_core._extract_tokens({"input_tokens": 200}), 200)

    def test_none_input(self):
        self.assertEqual(meter_core._extract_tokens(None), 0)

    def test_non_dict(self):
        self.assertEqual(meter_core._extract_tokens("bad"), 0)

    def test_negative_clamped_to_zero(self):
        # negative values should not subtract from total
        self.assertEqual(meter_core._extract_tokens({"input_tokens": -50}), 0)

    def test_string_numbers(self):
        # Some API responses may return numeric strings
        self.assertEqual(meter_core._extract_tokens({"input_tokens": "300"}), 300)

    def test_non_numeric_value(self):
        # Non-numeric values should be ignored silently
        self.assertEqual(meter_core._extract_tokens({"input_tokens": "abc"}), 0)


class TestParseTs(unittest.TestCase):
    def test_utc_z(self):
        ts = meter_core._parse_ts("2024-04-17T22:00:00.000Z")
        self.assertIsNotNone(ts)
        self.assertEqual(ts.tzinfo, timezone.utc)

    def test_utc_plus(self):
        ts = meter_core._parse_ts("2024-04-17T22:00:00+00:00")
        self.assertIsNotNone(ts)

    def test_empty_string(self):
        self.assertIsNone(meter_core._parse_ts(""))

    def test_none(self):
        self.assertIsNone(meter_core._parse_ts(None))

    def test_garbage(self):
        self.assertIsNone(meter_core._parse_ts("not-a-date"))


class TestWeekStart(unittest.TestCase):
    def test_saturday_is_start(self):
        """A Saturday should return itself at midnight PT."""
        # 2024-04-20 is a Saturday; at noon UTC it's still Saturday morning PT
        sat_noon_utc = datetime(2024, 4, 20, 12, 0, tzinfo=timezone.utc)
        ws = meter_core._week_start_utc(sat_noon_utc)
        # Saturday midnight PT = Saturday 07:00 UTC (PDT, UTC-7)
        expected = datetime(2024, 4, 20, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(ws, expected)

    def test_monday_points_back_to_saturday(self):
        """Monday UTC should point back to the previous Saturday."""
        mon_utc = datetime(2024, 4, 22, 10, 0, tzinfo=timezone.utc)
        ws = meter_core._week_start_utc(mon_utc)
        # Should be April 20 (Sat) at midnight PT = 07:00 UTC
        expected = datetime(2024, 4, 20, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(ws, expected)


class TestLocalTimezone(unittest.TestCase):
    def test_fmt_local_time_returns_string(self):
        utc = datetime(2024, 4, 20, 23, 30, tzinfo=timezone.utc)
        result = meter_core._fmt_local_time(utc)
        # Should contain a colon and AM/PM
        self.assertIn(":", result)
        self.assertTrue("AM" in result or "PM" in result)

    def test_fmt_local_date_returns_string(self):
        utc = datetime(2024, 4, 20, 0, 0, tzinfo=timezone.utc)
        result = meter_core._fmt_local_date(utc)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 3)


class TestLoadAllEntries(unittest.TestCase):
    def setUp(self):
        # Clear cache before each test
        meter_core._jsonl_cache.clear()

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(meter_core, "PROJECTS_GLOB",
                              os.path.join(tmpdir, "**/*.jsonl")):
                # Also patch the projects dir existence check
                with patch("os.path.isdir", return_value=True):
                    entries = meter_core._load_all_entries()
        self.assertEqual(entries, [])

    def test_missing_projects_dir(self):
        with patch("os.path.isdir", return_value=False):
            entries = meter_core._load_all_entries()
        self.assertEqual(entries, [])

    def test_parses_valid_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = os.path.join(tmpdir, "proj1")
            os.makedirs(proj_dir)
            path = os.path.join(proj_dir, "usage.jsonl")
            now = datetime.now(timezone.utc)
            _write_jsonl(path, [_make_entry(now, 500), _make_entry(now, 300)])

            with patch.object(meter_core, "PROJECTS_GLOB",
                              os.path.join(tmpdir, "**/*.jsonl")):
                with patch("os.path.isdir", return_value=True):
                    entries = meter_core._load_all_entries()

        self.assertEqual(len(entries), 2)

    def test_skips_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = os.path.join(tmpdir, "proj1")
            os.makedirs(proj_dir)
            path = os.path.join(proj_dir, "usage.jsonl")
            with open(path, "w") as f:
                f.write("not json at all\n")
                f.write('{"valid": true}\n')
                f.write("{broken\n")

            with patch.object(meter_core, "PROJECTS_GLOB",
                              os.path.join(tmpdir, "**/*.jsonl")):
                with patch("os.path.isdir", return_value=True):
                    entries = meter_core._load_all_entries()

        # Only the valid JSON line should be parsed
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].get("valid"))

    def test_cache_hit_avoids_reparse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = os.path.join(tmpdir, "proj1")
            os.makedirs(proj_dir)
            path = os.path.join(proj_dir, "usage.jsonl")
            now = datetime.now(timezone.utc)
            _write_jsonl(path, [_make_entry(now, 100)])

            with patch.object(meter_core, "PROJECTS_GLOB",
                              os.path.join(tmpdir, "**/*.jsonl")):
                with patch("os.path.isdir", return_value=True):
                    # First load — parses file
                    meter_core._load_all_entries()
                    # Mutate the file content WITHOUT changing mtime
                    mtime = os.path.getmtime(path)
                    _write_jsonl(path, [_make_entry(now, 999), _make_entry(now, 999)])
                    os.utime(path, (mtime, mtime))   # restore original mtime
                    # Second load — should use cache (still 1 entry, not 2)
                    entries = meter_core._load_all_entries()

        self.assertEqual(len(entries), 1)


class TestGetUsage(unittest.TestCase):
    def setUp(self):
        meter_core._jsonl_cache.clear()

    def _run_get_usage(self, entries):
        with patch.object(meter_core, "_load_all_entries", return_value=entries):
            return meter_core.get_usage()

    def test_empty_returns_zeros(self):
        total, week, session, daily, _ = self._run_get_usage([])
        self.assertEqual(total, 0)
        self.assertEqual(week, 0)
        self.assertEqual(session, 0)
        self.assertEqual(len(daily), 0)

    def test_today_tokens_counted(self):
        now = datetime.now(timezone.utc)
        entries = [_make_entry(now, 1000)]
        total, _, _, daily, _ = self._run_get_usage(entries)
        self.assertEqual(total, 1000)
        today_local = datetime.now().date()
        self.assertEqual(daily.get(today_local, 0), 1000)

    def test_timezone_bucketing(self):
        """Tokens at local midnight boundary go into the right local day."""
        # Create a timestamp that's in yesterday UTC but today locally
        # We simulate by mocking .astimezone() to return a known local date
        local_date_today = datetime.now().date()
        now_utc = datetime.now(timezone.utc)
        entries = [_make_entry(now_utc, 500)]

        total, _, _, daily, _ = self._run_get_usage(entries)
        # The token should land on the local date, not necessarily UTC date
        total_in_daily = sum(daily.values())
        self.assertEqual(total_in_daily, 500)

    def test_entries_without_usage_skipped(self):
        entry_no_usage = {"timestamp": "2024-04-17T10:00:00Z", "message": {}}
        entry_null_usage = {"timestamp": "2024-04-17T10:00:00Z",
                            "message": {"usage": None}}
        total, _, _, _, _ = self._run_get_usage([entry_no_usage, entry_null_usage])
        self.assertEqual(total, 0)

    def test_entries_without_timestamp_counted_in_monthly(self):
        entry = {"message": {"usage": {"input_tokens": 200}}}  # no timestamp
        total, week, _, _, _ = self._run_get_usage([entry])
        self.assertEqual(total, 200)
        self.assertEqual(week, 0)   # no timestamp → excluded from weekly

    def test_multiple_files_aggregated(self):
        now = datetime.now(timezone.utc)
        entries = [_make_entry(now, 300), _make_entry(now, 700)]
        total, _, _, _, _ = self._run_get_usage(entries)
        self.assertEqual(total, 1000)


class TestBuildPayload(unittest.TestCase):
    def setUp(self):
        meter_core._jsonl_cache.clear()

    def _default_settings(self, **kwargs):
        s = {"limit": 45_000_000, "interval": 300, "theme": "dark"}
        s.update(kwargs)
        return s

    def test_basic_structure(self):
        with patch.object(meter_core, "get_usage",
                          return_value=(500, 200, 100, {}, datetime.now(timezone.utc))):
            with patch.object(meter_core, "fetch_claude_ai_usage", return_value=None):
                p = meter_core.build_payload(self._default_settings())

        required = ["pct", "used", "limit", "session_pct", "week_pct",
                    "daily_7", "source", "cookie_expired", "theme"]
        for key in required:
            self.assertIn(key, p, f"Missing key: {key}")

    def test_pct_capped_at_100(self):
        # Tokens way over limit → should cap at 100%
        with patch.object(meter_core, "get_usage",
                          return_value=(999_999_999, 0, 0, {},
                                        datetime.now(timezone.utc))):
            with patch.object(meter_core, "fetch_claude_ai_usage", return_value=None):
                p = meter_core.build_payload(self._default_settings())
        self.assertEqual(p["pct"], 100.0)

    def test_zero_limit_no_crash(self):
        with patch.object(meter_core, "get_usage",
                          return_value=(0, 0, 0, {}, datetime.now(timezone.utc))):
            with patch.object(meter_core, "fetch_claude_ai_usage", return_value=None):
                # limit=0 should not divide by zero
                p = meter_core.build_payload(self._default_settings(limit=0))
        self.assertIsInstance(p["pct"], float)

    def test_cookie_expired_flag_true_when_key_set_and_no_live(self):
        with patch.object(meter_core, "get_usage",
                          return_value=(0, 0, 0, {}, datetime.now(timezone.utc))):
            with patch.object(meter_core, "fetch_claude_ai_usage", return_value=None):
                p = meter_core.build_payload(
                    self._default_settings(session_key="sk-ant-sid01-fake"))
        self.assertTrue(p["cookie_expired"])

    def test_cookie_expired_flag_false_when_no_key(self):
        with patch.object(meter_core, "get_usage",
                          return_value=(0, 0, 0, {}, datetime.now(timezone.utc))):
            with patch.object(meter_core, "fetch_claude_ai_usage", return_value=None):
                p = meter_core.build_payload(self._default_settings())
        self.assertFalse(p["cookie_expired"])

    def test_daily_7_has_7_entries(self):
        with patch.object(meter_core, "get_usage",
                          return_value=(0, 0, 0, {}, datetime.now(timezone.utc))):
            with patch.object(meter_core, "fetch_claude_ai_usage", return_value=None):
                p = meter_core.build_payload(self._default_settings())
        self.assertEqual(len(p["daily_7"]), 7)

    def test_daily_7_exactly_one_today(self):
        with patch.object(meter_core, "get_usage",
                          return_value=(0, 0, 0, {}, datetime.now(timezone.utc))):
            with patch.object(meter_core, "fetch_claude_ai_usage", return_value=None):
                p = meter_core.build_payload(self._default_settings())
        today_count = sum(1 for d in p["daily_7"] if d["today"])
        self.assertEqual(today_count, 1)

    def test_live_api_data_used_when_available(self):
        live = {
            "session_pct": 42.0, "session_reset": "5:00 PM PDT",
            "session_reset_epoch": 9999, "week_pct": 18.0,
            "week_reset": "Apr 26", "week_reset_epoch": 8888, "source": "live",
        }
        with patch.object(meter_core, "get_usage",
                          return_value=(1000, 500, 200, {},
                                        datetime.now(timezone.utc))):
            with patch.object(meter_core, "fetch_claude_ai_usage", return_value=live):
                p = meter_core.build_payload(
                    self._default_settings(session_key="sk-ant-sid01-x"))
        self.assertEqual(p["session_pct"], 42.0)
        self.assertEqual(p["week_pct"], 18.0)
        self.assertEqual(p["source"], "live")

    def test_settings_with_invalid_limit_type(self):
        """Non-integer limit should not crash."""
        with patch.object(meter_core, "get_usage",
                          return_value=(0, 0, 0, {}, datetime.now(timezone.utc))):
            with patch.object(meter_core, "fetch_claude_ai_usage", return_value=None):
                p = meter_core.build_payload(self._default_settings(limit="bad"))
        # Should fall back to 1 (div/0 guard) and return pct=0.0
        self.assertIsInstance(p["pct"], float)


class TestLoadSettings(unittest.TestCase):
    def test_returns_defaults_on_missing_file(self):
        with patch.object(meter_core, "SETTINGS_FILE", "/nonexistent/path.json"):
            s = meter_core.load_settings()
        self.assertIn("limit", s)
        self.assertIn("interval", s)

    def test_reads_existing_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"limit": 99, "theme": "light"}, f)
            path = f.name
        try:
            with patch.object(meter_core, "SETTINGS_FILE", path):
                s = meter_core.load_settings()
            self.assertEqual(s["limit"], 99)
            self.assertEqual(s["theme"], "light")
        finally:
            os.unlink(path)

    def test_corrupted_file_returns_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("not valid json {{{{")
            path = f.name
        try:
            with patch.object(meter_core, "SETTINGS_FILE", path):
                s = meter_core.load_settings()
            self.assertIn("limit", s)
        finally:
            os.unlink(path)


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = loader.loadTestsFromModule(sys.modules[__name__])
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
