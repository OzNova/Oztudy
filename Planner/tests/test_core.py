"""Core unit tests: build_plan, _next_exam, _habit_streak, _stats, date helpers.

Run from repo root::

    python3 -m unittest discover -s Planner/tests -v

or from ``Planner/``::

    python3 -m unittest discover -s tests -v

No external dependencies; stdlib ``unittest`` only. ``build_plan`` reads
settings from the store, so tests patch ``scheduler._load_doc`` to avoid
touching the real database.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduler
from gamification import _compute_streak, _habit_streak
from school import _next_exam
from storage import _repair_plan, _settings_for
from utils import (
    MIN_PLAN_WINDOW_MIN,
    REVIEW_BLOCK,
    _day_key,
    _hhmm,
    _minutes,
    _weekday_short,
)


def _topics(n=2):
    return [
        {"subject": f"MATH{i}", "topic": f"Topic {i}", "confidence": "yellow"}
        for i in range(n)
    ]


class DateHelperTests(unittest.TestCase):
    def test_minutes_roundtrip(self):
        self.assertEqual(_minutes("00:00"), 0)
        self.assertEqual(_minutes("17:00"), 1020)
        self.assertEqual(_minutes("23:59"), 1439)
        self.assertEqual(_hhmm(1020), "17:00")
        self.assertEqual(_hhmm(24 * 60), "00:00")  # wraps past midnight

    def test_minutes_invalid(self):
        self.assertIsNone(_minutes("not-a-time"))
        self.assertIsNone(_minutes(""))
        self.assertIsNone(_minutes(None))
        self.assertIsNone(_minutes("25:00"))
        self.assertIsNone(_minutes("12:60"))

    def test_day_key_format(self):
        ts = int(datetime(2026, 9, 14, 12, 0, 0).timestamp())
        self.assertEqual(_day_key(ts), "2026-09-14")

    def test_weekday_short_known_monday(self):
        # 2026-09-14 is a Monday.
        self.assertEqual(_weekday_short("2026-09-14"), "Pzt")
        self.assertEqual(_weekday_short("2026-09-13"), "Paz")


class StatsTests(unittest.TestCase):
    def test_breaks_excluded(self):
        blocks = [
            {"type": "study", "duration": 45, "status": "done"},
            {"type": "break", "duration": 10, "status": "done"},
            {"type": "study", "duration": 45, "status": "pending"},
        ]
        stats = scheduler._stats(blocks)
        self.assertEqual(stats, {"total_min": 90, "done_min": 45, "done_count": 1})

    def test_pushed_excluded(self):
        blocks = [
            {"type": "study", "duration": 45, "status": "pushed"},
            {"type": "study", "duration": 30, "status": "pending"},
        ]
        stats = scheduler._stats(blocks)
        self.assertEqual(stats["total_min"], 30)

    def test_malformed_blocks_do_not_crash(self):
        blocks = [
            {"status": "pending"},  # missing type/duration
            {"type": "study"},  # missing duration -> 0
            {"type": "study", "duration": "bad", "status": "done"},
            {"type": "study", "duration": -5, "status": "done"},
            None,
            "not-a-dict",
        ]
        stats = scheduler._stats(blocks)
        self.assertEqual(stats, {"total_min": 0, "done_min": 0, "done_count": 2})

    def test_non_list_input(self):
        self.assertEqual(
            scheduler._stats(None),
            {"total_min": 0, "done_min": 0, "done_count": 0},
        )


class NextExamTests(unittest.TestCase):
    def test_upcoming(self):
        weeks = [{"label": "S1", "start": "2026-10-30", "end": "2026-11-06"}]
        nxt = _next_exam(weeks, today=date(2026, 9, 14))
        self.assertEqual(nxt["days_until"], 46)
        self.assertFalse(nxt["ongoing"])

    def test_ongoing_clamped_to_zero(self):
        weeks = [{"label": "S1", "start": "2026-09-01", "end": "2026-09-20"}]
        nxt = _next_exam(weeks, today=date(2026, 9, 14))
        self.assertEqual(nxt["days_until"], 0)
        self.assertTrue(nxt["ongoing"])

    def test_past_exams_ignored(self):
        weeks = [
            {"label": "Old", "start": "2026-01-01", "end": "2026-01-05"},
            {"label": "New", "start": "2026-12-01", "end": "2026-12-05"},
        ]
        nxt = _next_exam(weeks, today=date(2026, 9, 14))
        self.assertEqual(nxt["label"], "New")

    def test_no_future_returns_none(self):
        weeks = [{"label": "Old", "start": "2026-01-01", "end": "2026-01-05"}]
        self.assertIsNone(_next_exam(weeks, today=date(2026, 9, 14)))

    def test_malformed_entries_skipped(self):
        weeks = [
            {"label": "Bad", "start": "not-a-date", "end": "2026-12-05"},
            {"label": "Missing"},
            "not-a-dict",
            {"label": "Good", "start": "2026-12-01", "end": "2026-12-05"},
        ]
        nxt = _next_exam(weeks, today=date(2026, 9, 14))
        self.assertEqual(nxt["label"], "Good")


class HabitStreakTests(unittest.TestCase):
    def setUp(self):
        self.habits = [{"id": "gym", "label": "Gym"}]

    def test_consecutive_streak(self):
        track = {
            "2026-09-12": ["gym"],
            "2026-09-13": ["gym"],
            "2026-09-14": ["gym"],
        }
        self.assertEqual(
            _habit_streak(self.habits, track, today=date(2026, 9, 14))["gym"], 3
        )

    def test_stale_streak_resets_to_zero(self):
        # Last activity days ago -> 0, not the old count.
        track = {"2026-09-10": ["gym"], "2026-09-11": ["gym"]}
        self.assertEqual(
            _habit_streak(self.habits, track, today=date(2026, 9, 14))["gym"], 0
        )

    def test_yesterday_only_counts_one(self):
        track = {"2026-09-13": ["gym"]}
        self.assertEqual(
            _habit_streak(self.habits, track, today=date(2026, 9, 14))["gym"], 1
        )

    def test_no_activity_is_zero(self):
        self.assertEqual(
            _habit_streak(self.habits, {}, today=date(2026, 9, 14))["gym"], 0
        )

    def test_game_streak_ghosting(self):
        g = {"history_days": ["2026-09-10", "2026-09-11"]}
        self.assertEqual(_compute_streak(g, today=date(2026, 9, 14)), 0)
        g2 = {"history_days": ["2026-09-13", "2026-09-14"]}
        self.assertEqual(_compute_streak(g2, today=date(2026, 9, 14)), 2)


class RepairPlanTests(unittest.TestCase):
    def test_invalid_enum_values_reset(self):
        plan = {
            "blocks": [
                {
                    "id": "1",
                    "type": "bogus",
                    "status": "weird",
                    "confidence": "purple",
                    "start": 0,
                    "end": 45,
                }
            ]
        }
        _repair_plan(plan)
        b = plan["blocks"][0]
        self.assertEqual(b["type"], "study")
        self.assertEqual(b["status"], "pending")
        self.assertEqual(b["confidence"], "yellow")
        self.assertGreaterEqual(b["duration"], 1)
        self.assertGreaterEqual(b["origDuration"], 1)

    def test_break_type_preserved(self):
        plan = {
            "blocks": [
                {
                    "id": "1",
                    "type": "break",
                    "status": "pending",
                    "confidence": "yellow",
                    "start": 0,
                    "end": 10,
                    "duration": 10,
                    "origDuration": 10,
                }
            ]
        }
        _repair_plan(plan)
        self.assertEqual(plan["blocks"][0]["type"], "break")


class BuildPlanTests(unittest.TestCase):
    def _patch_settings(self, study_min=45, break_min=10):
        return patch.object(
            scheduler,
            "_load_doc",
            return_value={"settings": {"study_min": study_min, "break_min": break_min}},
        )

    def test_requires_topics(self):
        with self._patch_settings():
            plan, err = scheduler.build_plan([], "17:00", "19:00", 2, "practice", "A")
        self.assertIsNone(plan)
        self.assertIn("konu", err)

    def test_rejects_bad_window(self):
        with self._patch_settings():
            plan, err = scheduler.build_plan(_topics(1), "19:00", "17:00", 2, "practice", "A")
            self.assertIsNone(plan)
            self.assertIsNotNone(err)
            plan, err = scheduler.build_plan(_topics(1), "17:00", "17:10", 2, "practice", "A")
            self.assertIsNone(plan)
            self.assertIn(str(MIN_PLAN_WINDOW_MIN), err)

    def test_blocks_use_settings_durations(self):
        with self._patch_settings(study_min=30, break_min=5):
            plan, err = scheduler.build_plan(
                _topics(3), "17:00", "19:00", 2, "practice", "A"
            )
        self.assertIsNone(err)
        studies = [b for b in plan["blocks"] if b["type"] == "study"]
        breaks = [b for b in plan["blocks"] if b["type"] == "break"]
        self.assertEqual(len(studies), 3)
        self.assertEqual(len(breaks), 2)  # n-1 breaks
        self.assertTrue(all(b["duration"] == 30 for b in studies))
        self.assertTrue(all(b["duration"] == 5 for b in breaks))
        self.assertIn("30 dk", plan["meta"]["note"])
        self.assertIn("5 dk", plan["meta"]["note"])

    def test_invalid_confidence_defaults_to_yellow(self):
        topics = [{"subject": "MATH", "topic": "Algebra", "confidence": "purple"}]
        with self._patch_settings():
            plan, err = scheduler.build_plan(topics, "17:00", "19:00", 2, "practice", "A")
        self.assertIsNone(err)
        self.assertEqual(plan["blocks"][0]["confidence"], "yellow")

    def test_review_block_constant_sane(self):
        self.assertGreaterEqual(REVIEW_BLOCK, 1)


class SettingsValidationTests(unittest.TestCase):
    def test_clamps(self):
        st = _settings_for({"settings": {"study_min": 999, "break_min": 0}})
        self.assertEqual(st["study_min"], 120)
        self.assertEqual(st["break_min"], 5)
        st = _settings_for({"settings": {"study_min": "bad"}})
        self.assertEqual(st["study_min"], 45)


class SaveExamsApiTests(unittest.TestCase):
    """Exercise POST /api/exams with an isolated SQLite store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OZTUDY_DATA_DIR"] = self.tmp.name
        import importlib

        import storage as storage_mod

        importlib.reload(storage_mod)
        import app as app_mod

        importlib.reload(app_mod)
        # Rebind local names to the reloaded modules.
        self.app_mod = app_mod
        self.storage_mod = storage_mod
        app_mod.app.config.update(TESTING=True)
        self.client = app_mod.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("OZTUDY_DATA_DIR", None)
        import importlib

        import storage as storage_mod

        importlib.reload(storage_mod)
        import app as app_mod  # noqa: F401

        importlib.reload(app_mod)

    def test_rejects_start_after_end(self):
        resp = self.client.post(
            "/api/exams",
            json={"exams": [{"start": "2026-12-10", "end": "2026-12-01"}]},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["status"], "error")

    def test_accepts_valid_range(self):
        resp = self.client.post(
            "/api/exams",
            json={
                "exams": [
                    {"label": "Final", "start": "2026-12-01", "end": "2026-12-10"}
                ]
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["exams"][0]["start"], "2026-12-01")


if __name__ == "__main__":
    unittest.main()
