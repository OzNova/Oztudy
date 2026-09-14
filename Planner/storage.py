"""Persistence: SQLite-backed doc store with one-time JSON migration.

Public API keeps the legacy doc-dict shape so routes keep working unchanged:
  LOCK, ROOT, DATA_DIR, DATA_FILE (legacy JSON, migration source only),
  DB_FILE, DEFAULT_SETTINGS, _settings_for, _load_doc, _save_doc, _load_plan,
  _add_missed, _repair_plan, _rollover, query_history_range

Durability/concurrency model: one SQLite database (WAL mode), a single
multi-statement transaction per save (BEGIN IMMEDIATE), per-operation
connections with a busy timeout. Readers never see torn writes, and a crash
mid-save rolls back instead of corrupting the store.
"""
import json
import logging
import logging.handlers
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime

from utils import REVIEW_BLOCK, _day_key, _minutes


logger = logging.getLogger("oztudy.storage")


def _log_file() -> str:
    """Launcher-compatible log path (``error.log`` next to the app by default)."""
    base = os.environ.get("OZTUDY_DATA_DIR", ROOT)
    return os.path.join(base, "error.log")


def _ensure_logger() -> logging.Logger:
    """Attach stderr + rotating-file handlers once (idempotent, test-safe)."""
    if getattr(_ensure_logger, "_configured_for", None) == _log_file() and logger.handlers:
        return logger
    # Drop stale file handlers when DATA_DIR changes (e.g. isolated tests).
    for h in list(logger.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
        logger.addHandler(logging.StreamHandler(sys.stderr))
    try:
        log_path = _log_file()
        if os.path.dirname(log_path) and os.path.dirname(log_path) != ROOT:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=512 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter("%(asctime)s [planner] %(message)s"))
        logger.addHandler(fh)
    except OSError:
        pass
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    _ensure_logger._configured_for = _log_file()  # type: ignore[attr-defined]
    return logger


ROOT = os.path.dirname(os.path.abspath(__file__))


DATA_DIR = os.environ.get("OZTUDY_DATA_DIR", os.path.join(ROOT, "userData"))


DATA_FILE = os.path.join(DATA_DIR, "planner.json")


DB_FILE = os.path.join(DATA_DIR, "planner.db")


LOCK = threading.RLock()


SCHEMA_VERSION = 1


DEFAULT_SETTINGS = {
    "study_min": 45,
    "break_min": 10,
    "win_start": "17:00",
    "win_end": "19:00",
    "duration_h": 2,
    "theme": "light",
    "timer_theme": "none",
}


TIMER_THEMES = ("none", "beach", "forest", "space")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    minutes INTEGER NOT NULL DEFAULT 0,
    questions INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0,
    ts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_history_day ON history(day);
CREATE TABLE IF NOT EXISTS game_days (
    day TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS habit_track (
    day TEXT NOT NULL,
    habit_id TEXT NOT NULL,
    PRIMARY KEY (day, habit_id)
);
CREATE TABLE IF NOT EXISTS habits (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    time TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '',
    pos INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS weak (
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    added INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject, topic)
);
CREATE TABLE IF NOT EXISTS tomorrow (
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    time TEXT,
    added INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject, topic)
);
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    target_day TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    added INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scheduled (
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    day TEXT NOT NULL DEFAULT '',
    minutes INTEGER NOT NULL DEFAULT 30,
    confidence TEXT NOT NULL DEFAULT 'yellow',
    reason TEXT NOT NULL DEFAULT '',
    created INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject, topic)
);
CREATE TABLE IF NOT EXISTS missed (
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    day TEXT,
    minutes INTEGER NOT NULL DEFAULT 30,
    source TEXT NOT NULL DEFAULT 'plan',
    confidence TEXT NOT NULL DEFAULT 'yellow',
    PRIMARY KEY (subject, topic, day)
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    day TEXT,
    created TEXT,
    created_at INTEGER,
    input_json TEXT NOT NULL DEFAULT '{}',
    meta_json TEXT NOT NULL DEFAULT '{}',
    extra_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS blocks (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    pos INTEGER NOT NULL DEFAULT 0,
    start INTEGER,
    end INTEGER,
    time TEXT,
    duration INTEGER,
    orig_duration INTEGER,
    type TEXT NOT NULL DEFAULT 'study',
    subject TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'yellow',
    status TEXT NOT NULL DEFAULT 'pending',
    active INTEGER NOT NULL DEFAULT 0,
    elapsed INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    extra_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_blocks_plan ON blocks(plan_id);
"""


_KNOWN_TOP = frozenset({
    "day", "settings", "exam_weeks", "habits", "habit_track", "history",
    "game", "weak", "tomorrow", "reviews", "scheduled", "missed", "plan",
    "stats_reset_ts",
})


_BLOCK_KNOWN = frozenset({
    "id", "start", "end", "time", "duration", "origDuration", "type",
    "subject", "topic", "confidence", "status", "active", "elapsed", "note",
})


_PLAN_KNOWN = frozenset({
    "id", "day", "created", "created_at", "input", "blocks", "meta", "stats",
})


def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _init_db(conn):
    conn.executescript(_SCHEMA)


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _legacy_json_load():
    """Read the pre-SQLite planner.json (with corrupt-file backup)."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except OSError as exc:
        _ensure_logger().warning("data file unreadable (%s); starting with empty doc", exc)
        return {}
    except ValueError as exc:
        try:
            if os.path.exists(DATA_FILE):
                backup = DATA_FILE + ".corrupt." + datetime.now().strftime("%Y%m%d-%H%M%S")
                os.replace(DATA_FILE, backup)
                _ensure_logger().warning("corrupt planner.json backed up to %s: %s", backup, exc)
            else:
                _ensure_logger().warning("corrupt planner.json: %s", exc)
        except OSError as backup_exc:
            _ensure_logger().warning("could not back up corrupt planner.json: %s", backup_exc)
        return {}


def _maybe_migrate(conn):
    row = conn.execute("SELECT value FROM kv WHERE key='schema_version'").fetchone()
    if row is not None:
        return
    data = _legacy_json_load()
    if data:
        _write_tx(conn, data)
        try:
            backup = DATA_FILE + ".migrated." + datetime.now().strftime("%Y%m%d-%H%M%S")
            os.replace(DATA_FILE, backup)
            _ensure_logger().info("migrated planner.json to SQLite; backup at %s", backup)
        except OSError as exc:
            _ensure_logger().warning("could not archive migrated planner.json: %s", exc)
    conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES('schema_version', ?)",
                 (str(SCHEMA_VERSION),))
    conn.commit()


def _read_doc(conn):
    kv = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM kv")}
    doc = {}
    if kv.get("day"):
        doc["day"] = kv["day"]
    if "settings" in kv:
        try:
            parsed = json.loads(kv["settings"])
            if isinstance(parsed, dict):
                doc["settings"] = parsed
        except ValueError:
            pass
    if "exam_weeks" in kv:
        try:
            parsed = json.loads(kv["exam_weeks"])
            if isinstance(parsed, list):
                doc["exam_weeks"] = parsed
        except ValueError:
            pass
    if "stats_reset_ts" in kv:
        try:
            doc["stats_reset_ts"] = int(kv["stats_reset_ts"])
        except (TypeError, ValueError):
            pass
    if any(k in kv for k in ("game_xp", "game_base_modules", "game_base_health", "game_badges")):
        try:
            badges = json.loads(kv.get("game_badges") or "[]")
            badges = badges if isinstance(badges, list) else []
        except ValueError:
            badges = []
        doc["game"] = {
            "xp": _to_int(kv.get("game_xp"), 0),
            "base_modules": _to_int(kv.get("game_base_modules"), 0),
            "badges": badges,
            "history_days": [r["day"] for r in
                             conn.execute("SELECT day FROM game_days ORDER BY day")],
            "base_health": kv.get("game_base_health") or "ok",
        }
    habits = [{
        "id": r["id"], "label": r["label"], "time": r["time"],
        "color": r["color"], "icon": r["icon"],
    } for r in conn.execute("SELECT id, label, time, color, icon FROM habits ORDER BY pos")]
    if habits:
        doc["habits"] = habits
    track = {}
    for r in conn.execute("SELECT day, habit_id FROM habit_track ORDER BY day, habit_id"):
        track.setdefault(r["day"], []).append(r["habit_id"])
    if track:
        doc["habit_track"] = track
    hist = [{
        "day": r["day"], "subject": r["subject"], "topic": r["topic"],
        "minutes": r["minutes"], "questions": r["questions"],
        "pages": r["pages"], "ts": r["ts"],
    } for r in conn.execute(
        "SELECT day, subject, topic, minutes, questions, pages, ts "
        "FROM history ORDER BY ts, id")]
    if hist:
        doc["history"] = hist
    weak = [{
        "subject": r["subject"], "topic": r["topic"], "added": r["added"],
    } for r in conn.execute("SELECT subject, topic, added FROM weak ORDER BY rowid")]
    if weak:
        doc["weak"] = weak
    tomorrow = [{
        "subject": r["subject"], "topic": r["topic"],
        "time": r["time"], "added": r["added"],
    } for r in conn.execute("SELECT subject, topic, time, added FROM tomorrow ORDER BY rowid")]
    if tomorrow:
        doc["tomorrow"] = tomorrow
    reviews = [{
        "id": r["id"], "subject": r["subject"], "topic": r["topic"],
        "targetDay": r["target_day"], "label": r["label"],
        "status": r["status"], "added": r["added"],
    } for r in conn.execute(
        "SELECT id, subject, topic, target_day, label, status, added "
        "FROM reviews ORDER BY rowid")]
    if reviews:
        doc["reviews"] = reviews
    scheduled = [{
        "subject": r["subject"], "topic": r["topic"], "day": r["day"],
        "minutes": r["minutes"], "confidence": r["confidence"],
        "reason": r["reason"], "created": r["created"],
    } for r in conn.execute(
        "SELECT subject, topic, day, minutes, confidence, reason, created "
        "FROM scheduled ORDER BY rowid")]
    if scheduled:
        doc["scheduled"] = scheduled
    missed = [{
        "subject": r["subject"], "topic": r["topic"], "day": r["day"],
        "minutes": r["minutes"], "source": r["source"],
        "confidence": r["confidence"],
    } for r in conn.execute(
        "SELECT subject, topic, day, minutes, source, confidence "
        "FROM missed ORDER BY rowid")]
    if missed:
        doc["missed"] = missed
    plan_row = conn.execute(
        "SELECT id, day, created, created_at, input_json, meta_json, extra_json "
        "FROM plans LIMIT 1").fetchone()
    if plan_row is not None:
        try:
            plan_input = json.loads(plan_row["input_json"] or "{}")
        except ValueError:
            plan_input = {}
        try:
            meta = json.loads(plan_row["meta_json"] or "{}")
        except ValueError:
            meta = {}
        try:
            extra = json.loads(plan_row["extra_json"] or "{}")
            extra = extra if isinstance(extra, dict) else {}
        except ValueError:
            extra = {}
        blocks = []
        for r in conn.execute(
                "SELECT id, start, end, time, duration, orig_duration, type, "
                "subject, topic, confidence, status, active, elapsed, note, extra_json "
                "FROM blocks WHERE plan_id=? ORDER BY pos", (plan_row["id"],)):
            try:
                bextra = json.loads(r["extra_json"] or "{}")
                bextra = bextra if isinstance(bextra, dict) else {}
            except ValueError:
                bextra = {}
            block = {
                "id": r["id"], "start": r["start"], "end": r["end"],
                "time": r["time"], "duration": r["duration"],
                "origDuration": r["orig_duration"], "type": r["type"],
                "subject": r["subject"], "topic": r["topic"],
                "confidence": r["confidence"], "status": r["status"],
                "active": bool(r["active"]), "elapsed": r["elapsed"],
                "note": r["note"],
            }
            block.update(bextra)
            blocks.append(block)
        plan = {
            "id": plan_row["id"], "created": plan_row["created"],
            "created_at": plan_row["created_at"], "day": plan_row["day"],
            "input": plan_input, "blocks": blocks, "meta": meta,
        }
        plan.update(extra)
        doc["plan"] = plan
    for key, value in kv.items():
        if key.startswith("extra:"):
            try:
                doc[key[len("extra:"):]] = json.loads(value)
            except ValueError:
                continue
    return doc


def _write_tx(conn, doc):
    """Persist the whole doc atomically (single BEGIN IMMEDIATE transaction)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.cursor()

        def kv_put(key, value):
            cur.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, value))

        def kv_del(key):
            cur.execute("DELETE FROM kv WHERE key=?", (key,))

        if doc.get("day"):
            kv_put("day", str(doc["day"]))
        else:
            kv_del("day")
        if isinstance(doc.get("settings"), dict):
            kv_put("settings", json.dumps(doc["settings"], ensure_ascii=False))
        else:
            kv_del("settings")
        if isinstance(doc.get("exam_weeks"), list):
            kv_put("exam_weeks", json.dumps(doc["exam_weeks"], ensure_ascii=False))
        else:
            kv_del("exam_weeks")
        if doc.get("stats_reset_ts") is not None:
            kv_put("stats_reset_ts", str(doc["stats_reset_ts"]))
        else:
            kv_del("stats_reset_ts")

        game = doc.get("game")
        if isinstance(game, dict):
            kv_put("game_xp", str(_to_int(game.get("xp"), 0)))
            kv_put("game_base_modules", str(_to_int(game.get("base_modules"), 0)))
            kv_put("game_base_health", str(game.get("base_health") or "ok"))
            badges = game.get("badges")
            kv_put("game_badges", json.dumps(badges if isinstance(badges, list) else [],
                                             ensure_ascii=False))
            cur.execute("DELETE FROM game_days")
            days = game.get("history_days") or []
            cur.executemany("INSERT OR IGNORE INTO game_days(day) VALUES(?)",
                            [(str(d),) for d in days if d])
        else:
            for key in ("game_xp", "game_base_modules", "game_base_health", "game_badges"):
                kv_del(key)
            cur.execute("DELETE FROM game_days")

        cur.execute("DELETE FROM history")
        hist_rows = []
        for h in doc.get("history") or []:
            if not isinstance(h, dict):
                continue
            hist_rows.append((
                str(h.get("day") or ""), str(h.get("subject") or ""),
                str(h.get("topic") or ""), _to_int(h.get("minutes"), 0),
                _to_int(h.get("questions"), 0), _to_int(h.get("pages"), 0),
                _to_int(h.get("ts"), 0),
            ))
        cur.executemany(
            "INSERT INTO history(day, subject, topic, minutes, questions, pages, ts)"
            " VALUES(?, ?, ?, ?, ?, ?, ?)", hist_rows)

        cur.execute("DELETE FROM habit_track")
        track_rows = []
        for day_key, ids in (doc.get("habit_track") or {}).items():
            for hid in ids or []:
                track_rows.append((str(day_key), str(hid)))
        cur.executemany("INSERT OR IGNORE INTO habit_track(day, habit_id) VALUES(?, ?)",
                        track_rows)

        cur.execute("DELETE FROM habits")
        habit_rows = []
        for pos, h in enumerate(doc.get("habits") or []):
            if not isinstance(h, dict):
                continue
            habit_rows.append((
                str(h.get("id") or ""), str(h.get("label") or ""),
                str(h.get("time") or ""), str(h.get("color") or ""),
                str(h.get("icon") or ""), pos,
            ))
        cur.executemany(
            "INSERT OR REPLACE INTO habits(id, label, time, color, icon, pos)"
            " VALUES(?, ?, ?, ?, ?, ?)", habit_rows)

        cur.execute("DELETE FROM weak")
        cur.executemany(
            "INSERT OR REPLACE INTO weak(subject, topic, added) VALUES(?, ?, ?)",
            [(str(w.get("subject") or ""), str(w.get("topic") or ""),
              _to_int(w.get("added"), 0))
             for w in doc.get("weak") or [] if isinstance(w, dict)])

        cur.execute("DELETE FROM tomorrow")
        cur.executemany(
            "INSERT OR REPLACE INTO tomorrow(subject, topic, time, added) VALUES(?, ?, ?, ?)",
            [(str(t.get("subject") or ""), str(t.get("topic") or ""),
              t.get("time"), _to_int(t.get("added"), 0))
             for t in doc.get("tomorrow") or [] if isinstance(t, dict)])

        cur.execute("DELETE FROM reviews")
        cur.executemany(
            "INSERT OR REPLACE INTO reviews(id, subject, topic, target_day, label, status, added)"
            " VALUES(?, ?, ?, ?, ?, ?, ?)",
            [(str(r.get("id") or ""), str(r.get("subject") or ""),
              str(r.get("topic") or ""), str(r.get("targetDay") or ""),
              str(r.get("label") or ""), str(r.get("status") or "pending"),
              _to_int(r.get("added"), 0))
             for r in doc.get("reviews") or [] if isinstance(r, dict)])

        cur.execute("DELETE FROM scheduled")
        cur.executemany(
            "INSERT OR REPLACE INTO scheduled(subject, topic, day, minutes, confidence, reason, created)"
            " VALUES(?, ?, ?, ?, ?, ?, ?)",
            [(str(s.get("subject") or ""), str(s.get("topic") or ""),
              str(s.get("day") or ""), _to_int(s.get("minutes"), 30),
              str(s.get("confidence") or "yellow"), str(s.get("reason") or ""),
              _to_int(s.get("created"), 0))
             for s in doc.get("scheduled") or [] if isinstance(s, dict)])

        cur.execute("DELETE FROM missed")
        cur.executemany(
            "INSERT OR REPLACE INTO missed(subject, topic, day, minutes, source, confidence)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            [(str(m.get("subject") or ""), str(m.get("topic") or ""),
              m.get("day"), _to_int(m.get("minutes"), 30),
              str(m.get("source") or "plan"), str(m.get("confidence") or "yellow"))
             for m in doc.get("missed") or [] if isinstance(m, dict)])

        cur.execute("DELETE FROM blocks")
        cur.execute("DELETE FROM plans")
        plan = doc.get("plan")
        if isinstance(plan, dict):
            plan_extra = {k: v for k, v in plan.items() if k not in _PLAN_KNOWN}
            cur.execute(
                "INSERT OR REPLACE INTO plans(id, day, created, created_at,"
                " input_json, meta_json, extra_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (str(plan.get("id") or ""), plan.get("day"),
                 plan.get("created"), plan.get("created_at"),
                 json.dumps(plan.get("input") if isinstance(plan.get("input"), dict) else {},
                            ensure_ascii=False),
                 json.dumps(plan.get("meta") if isinstance(plan.get("meta"), dict) else {},
                            ensure_ascii=False),
                 json.dumps(plan_extra, ensure_ascii=False)))
            block_rows = []
            for pos, b in enumerate(plan.get("blocks") or []):
                if not isinstance(b, dict):
                    continue
                bextra = {k: v for k, v in b.items() if k not in _BLOCK_KNOWN}
                try:
                    start = int(b["start"]) if b.get("start") is not None else None
                except (TypeError, ValueError):
                    start = None
                try:
                    end = int(b["end"]) if b.get("end") is not None else None
                except (TypeError, ValueError):
                    end = None
                block_rows.append((
                    str(b.get("id") or ""), str(plan.get("id") or ""), pos,
                    start, end, b.get("time"),
                    b.get("duration"), b.get("origDuration"),
                    str(b.get("type") or "study"), str(b.get("subject") or ""),
                    str(b.get("topic") or ""), str(b.get("confidence") or "yellow"),
                    str(b.get("status") or "pending"),
                    1 if b.get("active") else 0, _to_int(b.get("elapsed"), 0),
                    str(b.get("note") or ""), json.dumps(bextra, ensure_ascii=False),
                ))
            cur.executemany(
                "INSERT OR REPLACE INTO blocks(id, plan_id, pos, start, end, time,"
                " duration, orig_duration, type, subject, topic, confidence, status,"
                " active, elapsed, note, extra_json)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", block_rows)

        cur.execute("DELETE FROM kv WHERE key LIKE 'extra:%'")
        for key, value in doc.items():
            if key not in _KNOWN_TOP:
                kv_put("extra:" + str(key), json.dumps(value, ensure_ascii=False))

        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _settings_for(doc: dict) -> dict:
    """Merge stored settings over defaults with range validation.

    ``study_min`` is clamped to 15–120, ``break_min`` to 5–30,
    ``duration_h`` to 1–12; invalid window times, themes and timer themes
    fall back to defaults. Never raises on corrupt input.
    """
    st = dict(DEFAULT_SETTINGS)
    saved = doc.get("settings")
    if isinstance(saved, dict):
        st.update(saved)
    try:
        st["study_min"] = max(15, min(120, int(st["study_min"])))
    except (TypeError, ValueError):
        st["study_min"] = DEFAULT_SETTINGS["study_min"]
    try:
        st["break_min"] = max(5, min(30, int(st["break_min"])))
    except (TypeError, ValueError):
        st["break_min"] = DEFAULT_SETTINGS["break_min"]
    try:
        st["duration_h"] = max(1, min(12, int(st["duration_h"])))
    except (TypeError, ValueError):
        st["duration_h"] = DEFAULT_SETTINGS["duration_h"]
    if _minutes(str(st["win_start"])) is None:
        st["win_start"] = DEFAULT_SETTINGS["win_start"]
    if _minutes(str(st["win_end"])) is None:
        st["win_end"] = DEFAULT_SETTINGS["win_end"]
    if _minutes(str(st["win_start"])) >= _minutes(str(st["win_end"])):
        st["win_end"] = DEFAULT_SETTINGS["win_end"]
    if str(st.get("theme")) not in ("light", "dark"):
        st["theme"] = DEFAULT_SETTINGS["theme"]
    if str(st.get("timer_theme")) not in TIMER_THEMES:
        st["timer_theme"] = DEFAULT_SETTINGS["timer_theme"]
    return st


def _load_doc():
    # Deferred import: scheduler imports this module at load time.
    from scheduler import _stats
    with LOCK:
        conn = _connect()
        try:
            _init_db(conn)
            _maybe_migrate(conn)
            data = _read_doc(conn)
        finally:
            conn.close()
    if _rollover(data):
        _save_doc(data)
        return data
    plan = data.get("plan")
    if isinstance(plan, dict):
        _repair_plan(plan)
        plan["stats"] = _stats(plan.get("blocks", []))
        data["plan"] = plan
    return data


def _save_doc(doc):
    with LOCK:
        conn = _connect()
        try:
            _init_db(conn)
            _maybe_migrate(conn)
            _write_tx(conn, doc)
        finally:
            conn.close()


def _load_plan():
    plan = _load_doc().get("plan")
    return plan if isinstance(plan, dict) else None


def query_history_range(day_from, day_to):
    """Indexed history lookup (proves the SQL path; feeds future analytics)."""
    with LOCK:
        conn = _connect()
        try:
            _init_db(conn)
            _maybe_migrate(conn)
            rows = conn.execute(
                "SELECT day, subject, topic, minutes, questions, pages, ts "
                "FROM history WHERE day>=? AND day<=? ORDER BY ts, id",
                (day_from, day_to)).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]


def _add_missed(doc, item):
    missed = doc.setdefault("missed", [])
    for m in missed:
        if m["subject"] == item["subject"] and m["topic"] == item["topic"] and m.get("day") == item.get("day"):
            return missed
    missed.append({
        "subject": item["subject"],
        "topic": item["topic"],
        "day": item.get("day"),
        "minutes": int(item.get("minutes") or 30),
        "source": item.get("source") or "plan",
        "confidence": item.get("confidence") or "yellow",
    })
    return missed


def _repair_plan(plan: dict) -> dict:
    """Backfill and validate a stored plan in place; always returns ``plan``.

    Validates ``type`` (study/break), ``status`` (pending/done/pushed) and
    ``confidence`` (red/yellow/green), resetting invalid values to defaults
    so corrupt blocks can never crash stats or the timeline.
    """
    # Backfill plan identity fields for docs created before the midnight-
    # rollover fix (plan day / created_at).
    if not plan.get("day"):
        created_at = plan.get("created_at")
        try:
            plan["day"] = _day_key(int(created_at)) if created_at else None
        except (TypeError, ValueError):
            plan["day"] = None
    if not isinstance(plan.get("created_at"), int):
        try:
            plan["created_at"] = int(plan.get("created_at") or time.time())
        except (TypeError, ValueError):
            plan["created_at"] = int(time.time())
    _VALID_TYPES = ("study", "break")
    _VALID_STATUS = ("pending", "done", "pushed")
    _VALID_CONF = ("red", "yellow", "green")
    for b in plan.get("blocks", []) or []:
        if not isinstance(b, dict):
            continue
        win = None
        try:
            win = max(1, int(b.get("end", 0)) - int(b.get("start", 0)))
        except (TypeError, ValueError):
            win = None
        if not (isinstance(b.get("duration"), int) and b["duration"] >= 1):
            try:
                cand = int(b.get("duration") or 0)
                b["duration"] = cand if cand >= 1 else (win if win is not None else DEFAULT_SETTINGS["study_min"])
            except (TypeError, ValueError):
                b["duration"] = win if win is not None else DEFAULT_SETTINGS["study_min"]
        if not (isinstance(b.get("origDuration"), int) and b["origDuration"] >= 1):
            b["origDuration"] = b["duration"]
        if b.get("type") not in _VALID_TYPES:
            b["type"] = "study"
        if b.get("status") not in _VALID_STATUS:
            b["status"] = "pending"
        if b.get("confidence") not in _VALID_CONF:
            # Break blocks don't carry a confidence; leave them empty rather
            # than forcing a bogus value, but study blocks fall back to yellow.
            b["confidence"] = "yellow" if b.get("type") == "study" else b.get("confidence", "yellow")
            if b.get("confidence") not in _VALID_CONF:
                b["confidence"] = "yellow"
        b.setdefault("subject", "")
        b.setdefault("topic", "")
        b.setdefault("elapsed", 0)
        b.setdefault("note", "")
        if not isinstance(b.get("active"), bool):
            b["active"] = bool(b.get("active", False))
        # Pre-fix done blocks already granted XP once — mark them so a future
        # pending->done toggle does not award a second time.
        if b.get("status") == "done" and "xp_awarded" not in b:
            b["xp_awarded"] = True
    return plan


def _rollover(doc, now=None):
    """Archive yesterday's leftovers and clear the active plan on day change.

    Midnight grace: a study session spanning 23:30 -> 00:15 must NOT be wiped
    the moment the clock strikes midnight. Before 04:00 we treat the previous
    calendar day as still active and defer the rollover. The plan also carries
    its own ``day``/``created_at`` so a plan created after midnight is never
    mistaken for yesterday's plan.
    """
    now_dt = now or datetime.now()
    today = now_dt.date().isoformat()
    prev = doc.get("day")
    if prev == today:
        return False
    if now_dt.hour < 4:
        # Early-morning grace period — keep yesterday's plan alive.
        return False
    if prev:
        plan = doc.get("plan")
        if isinstance(plan, dict):
            # A plan created today (after midnight, doc day simply stale)
            # must survive the rollover.
            if plan.get("day") == today:
                doc["day"] = today
                return True
            for b in plan.get("blocks", []):
                if b["type"] != "study" or b.get("status") == "done":
                    continue
                if b.get("isReview"):
                    _add_missed(doc, {
                        "subject": b["subject"], "topic": b["topic"] + " (tekrar)",
                        "day": prev, "minutes": b.get("duration", REVIEW_BLOCK),
                        "source": "review", "confidence": "green",
                    })
                else:
                    _add_missed(doc, {
                        "subject": b["subject"], "topic": b["topic"],
                        "day": prev, "minutes": b.get("duration", 30),
                        "source": "plan", "confidence": b.get("confidence", "yellow"),
                    })
            for r in doc.setdefault("reviews", []):
                if r.get("status") == "scheduled" and r.get("targetDay") <= prev:
                    r["status"] = "pending"
        for r in doc.get("reviews", []):
            if r.get("status") == "pending" and r.get("targetDay") < today:
                _add_missed(doc, {
                    "subject": r["subject"], "topic": r["topic"] + " (" + r.get("label", "") + ")",
                    "day": r.get("targetDay"), "minutes": REVIEW_BLOCK,
                    "source": "review", "confidence": "green",
                })
        doc["plan"] = None
    doc["day"] = today
    return True
