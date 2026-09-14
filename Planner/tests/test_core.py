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
from scheduler import (
    _apply_feedback_step,
    _block_start_hour,
    _log_abandoned,
    apply_block_feedback,
)
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

    def test_timer_theme_default_and_validation(self):
        self.assertEqual(_settings_for({})["timer_theme"], "none")
        self.assertEqual(
            _settings_for({"settings": {"timer_theme": "forest"}})["timer_theme"],
            "forest",
        )
        for bad in ("ocean", "", None, 123):
            self.assertEqual(
                _settings_for({"settings": {"timer_theme": bad}})["timer_theme"],
                "none",
            )


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


class MalformedHistoryApiTests(unittest.TestCase):
    """Corrupt history entries must degrade gracefully, never 500."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OZTUDY_DATA_DIR"] = self.tmp.name
        import importlib

        import storage as storage_mod

        importlib.reload(storage_mod)
        import app as app_mod

        importlib.reload(app_mod)
        self.app_mod = app_mod
        self.storage_mod = storage_mod
        app_mod.app.config.update(TESTING=True)
        self.client = app_mod.app.test_client()
        with storage_mod.LOCK:
            doc = storage_mod._load_doc()
            doc["history"] = [
                {"day": "not-a-date", "subject": "MATH", "topic": "Bad",
                 "minutes": "NaN", "questions": "x", "pages": None, "ts": 1},
                {"day": "2026-13-99", "subject": "ENG", "topic": "Bad2",
                 "minutes": 10, "questions": 1, "pages": 1, "ts": 2},
                "not-a-dict",
                {"subject": "NoDay", "minutes": 5},
            ]
            storage_mod._save_doc(doc)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("OZTUDY_DATA_DIR", None)
        import importlib

        import storage as storage_mod

        importlib.reload(storage_mod)
        import app as app_mod  # noqa: F401

        importlib.reload(app_mod)

    def test_stats_survives_malformed_history(self):
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "success")

    def test_report_survives_malformed_history(self):
        for rng in ("week", "month", "all"):
            resp = self.client.get(f"/api/report?range={rng}")
            self.assertEqual(resp.status_code, 200, rng)
            self.assertEqual(resp.get_json()["status"], "success", rng)

    def test_weekday_helper_never_raises(self):
        self.assertEqual(_weekday_short("not-a-date"), "?")
        self.assertEqual(_weekday_short(None), "?")


class ExportApiTests(unittest.TestCase):
    """GET /api/export returns the full document without changing shape."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OZTUDY_DATA_DIR"] = self.tmp.name
        import importlib

        import storage as storage_mod

        importlib.reload(storage_mod)
        import app as app_mod

        importlib.reload(app_mod)
        self.app_mod = app_mod
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

    def test_export_returns_full_doc(self):
        resp = self.client.get("/api/export")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "success")
        self.assertIn("data", body)
        self.assertIsInstance(body["data"], dict)
        self.assertIn("version", body)
        self.assertIn("exported_at", body)


class TimerThemeApiTests(unittest.TestCase):
    """timer_theme persists via settings and falls back to 'none'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OZTUDY_DATA_DIR"] = self.tmp.name
        import importlib

        import storage as storage_mod

        importlib.reload(storage_mod)
        import app as app_mod

        importlib.reload(app_mod)
        self.app_mod = app_mod
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

    def test_default_is_none(self):
        body = self.client.get("/api/settings").get_json()
        self.assertEqual(body["settings"]["timer_theme"], "none")

    def test_save_and_reject_invalid(self):
        body = self.client.post(
            "/api/settings", json={"timer_theme": "space"}
        ).get_json()
        self.assertEqual(body["settings"]["timer_theme"], "space")
        body = self.client.post(
            "/api/settings", json={"timer_theme": "volcano"}
        ).get_json()
        self.assertEqual(body["settings"]["timer_theme"], "none")


def _isolated_client(testcase):
    """Point storage+app at a temp dir; return (client, storage_mod)."""
    import importlib

    import storage as storage_mod

    importlib.reload(storage_mod)
    import app as app_mod

    importlib.reload(app_mod)
    app_mod.app.config.update(TESTING=True)
    return app_mod.app.test_client(), storage_mod


def _seed_plan(storage_mod, blocks):
    with storage_mod.LOCK:
        doc = storage_mod._load_doc()
        doc["plan"] = {
            "id": "p1", "day": "2026-09-14", "created": "10:00",
            "created_at": 1,
            "input": {"topics": [], "start": "17:00", "end": "19:00",
                       "duration_h": 2, "mode": "practice", "criterion": "A"},
            "blocks": blocks, "meta": {}, "stats": {},
        }
        storage_mod._save_doc(doc)


def _study_block(bid="b1", conf="yellow", **kw):
    blk = {
        "id": bid, "start": 1020, "end": 1065, "time": "17:00-17:45",
        "duration": 45, "origDuration": 45, "type": "study",
        "subject": "Math", "topic": "Algebra", "confidence": conf,
        "status": "pending", "active": False, "elapsed": 0, "note": "",
    }
    blk.update(kw)
    return blk


class FeedbackTransitionTests(unittest.TestCase):
    def test_all_transitions(self):
        self.assertEqual(_apply_feedback_step("yellow", "easy"), "green")
        self.assertEqual(_apply_feedback_step("red", "easy"), "yellow")
        self.assertEqual(_apply_feedback_step("green", "easy"), "green")
        self.assertEqual(_apply_feedback_step("green", "hard"), "yellow")
        self.assertEqual(_apply_feedback_step("yellow", "hard"), "red")
        self.assertEqual(_apply_feedback_step("red", "hard"), "red")
        self.assertEqual(_apply_feedback_step("yellow", "medium"), "yellow")
        self.assertEqual(_apply_feedback_step("red", "medium"), "red")

    def test_block_and_topic_map(self):
        doc: dict = {}
        blk = _study_block(conf="yellow")
        self.assertEqual(apply_block_feedback(doc, blk, "hard"), "red")
        self.assertEqual(blk["confidence"], "red")
        self.assertEqual(blk["last_feedback"], "hard")
        self.assertEqual(doc["topic_confidence"], {"Math|Algebra": "red"})

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            apply_block_feedback({}, _study_block(), "extreme")
        with self.assertRaises(LookupError):
            apply_block_feedback({}, {"type": "break"}, "easy")

    def test_start_hour(self):
        self.assertEqual(_block_start_hour({"start": 1020}), 17)
        self.assertEqual(_block_start_hour({}), -1)
        self.assertEqual(_block_start_hour({"start": "bad"}), -1)

    def test_log_abandoned_guards(self):
        self.assertIsNone(_log_abandoned({}, _study_block(elapsed=0)))
        self.assertIsNone(_log_abandoned({}, _study_block(elapsed=5, status="done")))
        entry = _log_abandoned({}, _study_block(elapsed=120))
        self.assertIsNotNone(entry)
        self.assertEqual(entry["start_hour"], 17)


class FeedbackApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import os as _os

        _os.environ["OZTUDY_DATA_DIR"] = self.tmp.name
        self.client, self.storage_mod = _isolated_client(self)
        _seed_plan(self.storage_mod, [_study_block(), _study_block("b2", "green")])

    def tearDown(self):
        import os as _os
        import importlib

        self.tmp.cleanup()
        _os.environ.pop("OZTUDY_DATA_DIR", None)
        import storage as storage_mod

        importlib.reload(storage_mod)
        import app as app_mod  # noqa: F401

        importlib.reload(app_mod)

    def test_feedback_shifts_confidence(self):
        resp = self.client.post("/api/blocks/b1/feedback", json={"difficulty": "hard"})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["confidence"], "red")
        blk = next(b for b in body["plan"]["blocks"] if b["id"] == "b1")
        self.assertEqual(blk["last_feedback"], "hard")

    def test_feedback_persists_to_topic_map(self):
        self.client.post("/api/blocks/b1/feedback", json={"difficulty": "easy"})
        with self.storage_mod.LOCK:
            doc = self.storage_mod._load_doc()
        self.assertEqual(doc.get("topic_confidence", {}).get("Math|Algebra"), "green")

    def test_feedback_rejects_bad_difficulty(self):
        resp = self.client.post("/api/blocks/b1/feedback", json={"difficulty": "extreme"})
        self.assertEqual(resp.status_code, 400)

    def test_feedback_404_unknown_or_break(self):
        self.assertEqual(
            self.client.post("/api/blocks/nope/feedback", json={"difficulty": "easy"}).status_code, 404
        )
        with self.storage_mod.LOCK:
            doc = self.storage_mod._load_doc()
            doc["plan"]["blocks"].append({
                "id": "br1", "start": 1065, "end": 1075, "time": "17:45-17:55",
                "duration": 10, "origDuration": 10, "type": "break",
                "subject": "", "topic": "Mola", "confidence": "yellow",
                "status": "pending", "active": False, "elapsed": 0, "note": "",
            })
            self.storage_mod._save_doc(doc)
        self.assertEqual(
            self.client.post("/api/blocks/br1/feedback", json={"difficulty": "easy"}).status_code, 404
        )


class PeakApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import os as _os

        _os.environ["OZTUDY_DATA_DIR"] = self.tmp.name
        self.client, self.storage_mod = _isolated_client(self)

    def tearDown(self):
        import os as _os
        import importlib

        self.tmp.cleanup()
        _os.environ.pop("OZTUDY_DATA_DIR", None)
        import storage as storage_mod

        importlib.reload(storage_mod)
        import app as app_mod  # noqa: F401

        importlib.reload(app_mod)

    def test_empty_returns_no_data_message(self):
        body = self.client.get("/api/insights/peak").get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["best_hours"], [])
        self.assertEqual(body["worst_hours"], [])
        self.assertIn("yeterli veri yok", body["insight"])

    def test_computed_rates(self):
        with self.storage_mod.LOCK:
            doc = self.storage_mod._load_doc()
            doc["history"] = [
                {"day": "2026-09-10", "subject": "M", "topic": "T",
                 "minutes": 45, "questions": 0, "pages": 0, "ts": 1, "start_hour": 16},
                {"day": "2026-09-11", "subject": "M", "topic": "T",
                 "minutes": 45, "questions": 0, "pages": 0, "ts": 2, "start_hour": 16},
                {"day": "2026-09-12", "subject": "M", "topic": "T",
                 "minutes": 45, "questions": 0, "pages": 0, "ts": 3, "start_hour": 21},
            ]
            doc["abandoned"] = [
                {"day": "2026-09-12", "subject": "M", "topic": "T", "start_hour": 21, "ts": 4},
                {"day": "2026-09-13", "subject": "M", "topic": "T", "start_hour": 21, "ts": 5},
            ]
            self.storage_mod._save_doc(doc)
        body = self.client.get("/api/insights/peak").get_json()
        by_hour = {s["hour"]: s for s in body["hourly_stats"]}
        self.assertEqual(by_hour[16]["rate"], 100)
        self.assertEqual(by_hour[21]["rate"], 33)
        self.assertIn(16, body["best_hours"])
        self.assertIn(21, body["worst_hours"])

    def test_stop_logs_abandoned(self):
        import time as _time

        _seed_plan(self.storage_mod, [_study_block(
            status="pending", active=True,
            started_at=int(_time.time()) - 600, elapsed=0)])
        resp = self.client.post("/api/blocks/adjust", json={"action": "stop", "id": "b1"})
        self.assertEqual(resp.status_code, 200)
        with self.storage_mod.LOCK:
            doc = self.storage_mod._load_doc()
        self.assertEqual(len(doc.get("abandoned", [])), 1)
        self.assertEqual(doc["abandoned"][0]["start_hour"], 17)


class HeatmapApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import os as _os

        _os.environ["OZTUDY_DATA_DIR"] = self.tmp.name
        self.client, self.storage_mod = _isolated_client(self)

    def tearDown(self):
        import os as _os
        import importlib

        self.tmp.cleanup()
        _os.environ.pop("OZTUDY_DATA_DIR", None)
        import storage as storage_mod

        importlib.reload(storage_mod)
        import app as app_mod  # noqa: F401

        importlib.reload(app_mod)

    def test_empty_history_all_zeros(self):
        body = self.client.get("/api/stats/heatmap").get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["weeks"], 12)
        self.assertEqual(len(body["data"]), 84)
        self.assertTrue(all(d["minutes"] == 0 for d in body["data"]))
        self.assertEqual(body["data"][-1]["day"], date.today().isoformat())

    def test_sums_minutes_per_day(self):
        from datetime import timedelta as _td

        today = date.today().isoformat()
        yesterday = (date.today() - _td(days=1)).isoformat()
        with self.storage_mod.LOCK:
            doc = self.storage_mod._load_doc()
            doc["history"] = [
                {"day": today, "subject": "M", "topic": "A",
                 "minutes": 45, "questions": 0, "pages": 0, "ts": 1},
                {"day": today, "subject": "M", "topic": "B",
                 "minutes": 30, "questions": 0, "pages": 0, "ts": 2},
                {"day": yesterday, "subject": "M", "topic": "A",
                 "minutes": 20, "questions": 0, "pages": 0, "ts": 3},
            ]
            self.storage_mod._save_doc(doc)
        body = self.client.get("/api/stats/heatmap").get_json()
        by_day = {d["day"]: d["minutes"] for d in body["data"]}
        self.assertEqual(by_day[today], 75)
        self.assertEqual(by_day[yesterday], 20)


class AmbientSettingsTests(unittest.TestCase):
    def test_default_and_validation(self):
        self.assertEqual(_settings_for({})["ambient_sound"], "none")
        self.assertEqual(
            _settings_for({"settings": {"ambient_sound": "rain"}})["ambient_sound"], "rain"
        )
        self.assertEqual(
            _settings_for({"settings": {"ambient_sound": "ocean"}})["ambient_sound"], "none"
        )

    def test_save_via_post_and_put(self):
        tmp = tempfile.TemporaryDirectory()
        import os as _os

        _os.environ["OZTUDY_DATA_DIR"] = tmp.name
        try:
            client, _ = _isolated_client(self)
            self.assertEqual(
                client.post("/api/settings", json={"ambient_sound": "lofi"}).get_json()["settings"]["ambient_sound"],
                "lofi",
            )
            self.assertEqual(
                client.put("/api/settings", json={"ambient_sound": "rain"}).get_json()["settings"]["ambient_sound"],
                "rain",
            )
        finally:
            import importlib

            tmp.cleanup()
            _os.environ.pop("OZTUDY_DATA_DIR", None)
            import storage as storage_mod

            importlib.reload(storage_mod)
            import app as app_mod  # noqa: F401

            importlib.reload(app_mod)


if __name__ == "__main__":
    unittest.main()
