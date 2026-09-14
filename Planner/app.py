import re
import time
import uuid
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request

from gamification import (
    HABIT_COLORS,
    _check_badges,
    _compute_streak,
    _game,
    _habit_days,
    _habit_icon,
    _habit_streak,
    _habit_track,
    _habits_for,
    _level_from_xp,
    _toggle_habit,
    _xp_for_level,
)
from scheduler import (
    REVIEW_BLOCK,
    _create_reviews,
    _find_block,
    _gather_overdue,
    _log_history,
    _merge_scheduled,
    _resize_block,
    _stats,
    _tomorrow_add,
    _tomorrow_remove,
    _upsert_weak,
    build_plan,
    drawer,
)
from school import _exams_for, _next_exam, _school_digest
from storage import LOCK, _load_doc, _load_plan, _repair_plan, _rollover, _save_doc, _settings_for
from utils import SECONDS_PER_DAY, _day_key, _hhmm, _minutes, _now_min, _weekday_short
from version import __version__


app = Flask(__name__)


def _safe_int(value, default: int = 0) -> int:
    """Best-effort int coercion for stored analytics fields (never raises)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _valid_day(value) -> bool:
    """Return True when ``value`` is a real ``YYYY-MM-DD`` calendar date."""
    try:
        datetime.strptime(str(value), "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/version")
def version():
    return jsonify({"status": "success", "name": "Oztudy", "version": __version__})


@app.get("/api/plan")
def get_plan():
    with LOCK:
        plan = _load_plan()
    return jsonify({"status": "success", "plan": plan})


@app.get("/api/school")
def school():
    return jsonify({"status": "success", "school": _school_digest(_day_key(int(time.time())))})


@app.get("/api/settings")
def get_settings():
    with LOCK:
        settings = _settings_for(_load_doc())
    return jsonify({"status": "success", "settings": settings})


@app.post("/api/settings")
def save_settings():
    body = request.get_json(silent=True) or {}
    with LOCK:
        doc = _load_doc()
        saved = dict(doc.get("settings") or {})
        for key in ("study_min", "break_min", "win_start", "win_end", "duration_h", "theme"):
            if key in body:
                saved[key] = body[key]
        doc["settings"] = saved
        _save_doc(doc)
        merged = _settings_for(doc)
    return jsonify({"status": "success", "settings": merged})


@app.get("/api/exams")
def get_exams():
    with LOCK:
        weeks = _exams_for(_load_doc())
    return jsonify({"status": "success", "exams": weeks, "next": _next_exam(weeks)})


@app.post("/api/exams")
def save_exams():
    body = request.get_json(silent=True) or {}
    raw = body.get("exams")
    if not isinstance(raw, list):
        return jsonify({"status": "error", "message": "Sınav listesi geçersiz."}), 400
    weeks = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            continue
        start = str(it.get("start") or "").strip()
        end = str(it.get("end") or "").strip()
        if not start or not end:
            continue
        try:
            start_d = datetime.strptime(start, "%Y-%m-%d").date()
            end_d = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"status": "error", "message": "Tarih biçimi YYYY-AA-GG olmalı."}), 400
        if start_d > end_d:
            return jsonify({"status": "error", "message": "Başlangıç tarihi bitişten sonra olamaz."}), 400
        weeks.append({
            "id": str(it.get("id") or ("exam%d" % i)),
            "label": str(it.get("label") or ("Sınav %d" % (i + 1))),
            "start": start,
            "end": end,
        })
    if not weeks:
        return jsonify({"status": "error", "message": "En az bir sınav girilmelidir."}), 400
    with LOCK:
        doc = _load_doc()
        doc["exam_weeks"] = weeks
        _save_doc(doc)
    return jsonify({"status": "success", "exams": weeks, "next": _next_exam(weeks)})


@app.get("/api/habits")
def get_habits():
    with LOCK:
        doc = _load_doc()
        habits = _habits_for(doc)
        today = datetime.now().date()
        payload = {
            "status": "success",
            "habits": habits,
            "track": _habit_track(doc),
            "days": _habit_days(today),
            "today": today.isoformat(),
            "streaks": _habit_streak(habits, _habit_track(doc), today),
        }
    return jsonify(payload)


@app.post("/api/habits/toggle")
def toggle_habit():
    body = request.get_json(silent=True) or {}
    hid = str(body.get("id") or "")
    day_key = str(body.get("date") or "")
    if not hid or not day_key:
        return jsonify({"status": "error", "message": "Habit eksik."}), 400
    with LOCK:
        doc = _load_doc()
        _toggle_habit(doc, hid, day_key)
        _save_doc(doc)
        habits = _habits_for(doc)
        today = datetime.now().date()
    return jsonify({
        "status": "success",
        "track": _habit_track(doc),
        "today": today.isoformat(),
        "streaks": _habit_streak(habits, _habit_track(doc), today),
    })


@app.post("/api/habits/save")
def save_habits():
    body = request.get_json(silent=True) or {}
    raw = body.get("habits")
    if not isinstance(raw, list):
        return jsonify({"status": "error", "message": "Alışkanlık listesi geçersiz."}), 400
    with LOCK:
        doc = _load_doc()
        existing = _habits_for(doc)
        by_label = {}
        for h in existing:
            by_label[h["label"].strip().lower()] = h
        seen = set()
        habits = []
        for i, it in enumerate(raw):
            if not isinstance(it, dict):
                continue
            label = str(it.get("label") or "").strip()
            time_s = str(it.get("time") or "").strip()
            if not label:
                continue
            if time_s and not re.match(r"^\d{2}:\d{2}$", time_s):
                return jsonify({"status": "error", "message": "Saat biçimi SS:DD olmalı (örn. 16:30)."}), 400
            key = label.lower()
            old = by_label.get(key)
            hid = old["id"] if old else "h%d" % (i + 1)
            if old is None and hid in seen:
                hid = "h%d" % (len(habits) + 100 + i)
            seen.add(hid)
            color = old["color"] if old else HABIT_COLORS[len(habits) % len(HABIT_COLORS)]
            icon = old["icon"] if (old and old.get("icon")) else _habit_icon(label)
            habits.append({"id": hid, "label": label, "time": time_s or "", "color": color, "icon": icon})
        existing_ids = {h["id"] for h in habits}
        track = _habit_track(doc)
        track = {date_key: [hid for hid in ids if hid in existing_ids] for date_key, ids in track.items()}
        track = {date_key: ids for date_key, ids in track.items() if ids}
        doc["habits"] = habits
        doc["habit_track"] = track
        _save_doc(doc)
        today = datetime.now().date()
    return jsonify({
        "status": "success",
        "habits": habits,
        "track": track,
        "days": _habit_days(today),
        "today": today.isoformat(),
        "streaks": _habit_streak(habits, track, today),
    })


@app.post("/api/plan")
def make_plan():
    body = request.get_json(silent=True) or {}
    topics = body.get("topics") or []
    try:
        duration_h = int(body.get("duration_h") or 4)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Süre değeri geçersiz."}), 400
    plan, err = build_plan(
        topics,
        str(body.get("start") or "17:00"),
        str(body.get("end") or "19:00"),
        duration_h,
        str(body.get("mode") or "practice"),
        str(body.get("criterion") or "A"),
    )
    if err:
        return jsonify({"status": "error", "message": err}), 400
    with LOCK:
        doc = _load_doc()
        for it in plan["input"]["topics"]:
            if it["confidence"] == "red":
                _upsert_weak(doc, it)
        _merge_scheduled(doc, plan, _day_key(int(time.time())))
        doc["plan"] = plan
        doc["day"] = plan.get("day") or _day_key(int(time.time()))
        _save_doc(doc)
    out = drawer(doc)
    out["status"] = "success"
    out["plan"] = plan
    return jsonify(out)


@app.post("/api/blocks")
def blocks():
    body = request.get_json(silent=True) or {}
    with LOCK:
        doc = _load_doc()
        plan = doc.get("plan")
        if body.get("clear"):
            doc["plan"] = None
            _save_doc(doc)
            return jsonify({"status": "success", "plan": None})
        if not plan:
            return jsonify({"status": "error", "message": "Blok bulunamadı."}), 404
        target = body.get("id")
        status = body.get("status")
        block = _find_block(plan, target)
        if not block:
            return jsonify({"status": "error", "message": "Blok bulunamadı."}), 404
        if block.get("type") == "study" and status in ("done", "pending"):
            was_done = block.get("status") == "done"
            if status == "done":
                if body.get("zen_abandon"):
                    block["zen_abandon"] = True
                block["status"] = "done"
            else:
                block["status"] = "pending"
            if status == "done" and not was_done:
                _log_history(doc, block)
        plan["stats"] = _stats(plan.get("blocks", []))
        doc["plan"] = plan
        _save_doc(doc)
    return jsonify({"status": "success", "plan": plan})


@app.post("/api/blocks/adjust")
def adjust():
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    with LOCK:
        doc = _load_doc()
        plan = doc.get("plan")

        if action == "shift":
            try:
                offset = int(body.get("offset") or 0)
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "Kaydırma değeri geçersiz."}), 400
            if not plan:
                return jsonify({"status": "error", "message": "Önce bir plan oluştur."}), 400
            if offset:
                for b in plan.get("blocks", []):
                    try:
                        b["start"] = int(b.get("start") or 0) + offset
                        b["end"] = int(b.get("end") or 0) + offset
                    except (TypeError, ValueError):
                        continue
                    b["time"] = f"{_hhmm(b['start'])}-{_hhmm(b['end'])}"
                plan["input"]["start"] = _hhmm(_minutes(plan["input"]["start"]) + offset)
                plan["meta"]["note"] = f"{plan['meta']['note']} · ⏩ +{offset} dk kaydırıldı"
                doc["plan"] = plan
                _save_doc(doc)
            out = drawer(doc)
            out["status"] = "success"
            out["plan"] = plan
            out["message"] = f"Tüm gün +{offset} dk kaydırıldı."
            return jsonify(out)

        if not plan:
            return jsonify({"status": "error", "message": "Önce bir plan oluştur."}), 400
        block = _find_block(plan, body.get("id"))
        if not block:
            return jsonify({"status": "error", "message": "Blok bulunamadı."}), 404

        if action == "start":
            now = int(time.time())
            for other in plan["blocks"]:
                if other["id"] == block["id"]:
                    continue
                if other.get("active"):
                    other["elapsed"] = int(other.get("elapsed", 0)) + max(
                        0, now - int(other.get("started_at", now))
                    )
                    other["active"] = False
                    other.pop("started_at", None)
            if block["type"] == "study" and not block.get("active"):
                block["active"] = True
                block["started_at"] = now
        elif action == "stop":
            if block.get("active"):
                now = int(time.time())
                block["elapsed"] = int(block.get("elapsed", 0)) + max(
                    0, now - int(block.get("started_at", now))
                )
            block["active"] = False
            block.pop("started_at", None)
        elif action == "reset":
            block["active"] = False
            block.pop("started_at", None)
            block.pop("elapsed", None)
            orig = block.get("origDuration")
            if isinstance(orig, int):
                _resize_block(plan, block, orig)
        elif action == "extend":
            try:
                orig = int(block.get("origDuration") or block.get("duration") or 45)
            except (TypeError, ValueError):
                orig = 45
            try:
                delta = int(body.get("delta", 5) or 5)
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "Süre değeri geçersiz."}), 400
            try:
                cur = int(block.get("duration", orig))
            except (TypeError, ValueError):
                cur = orig
            _resize_block(plan, block, max(1, min(cur + delta, orig + 60)))
        elif action == "done":
            was_done = block.get("status") == "done"
            if body.get("zen_abandon"):
                block["zen_abandon"] = True
            block["status"] = "done" if not was_done else "pending"
            block["active"] = False
            block.pop("started_at", None)
            block.pop("elapsed", None)
            if not was_done:
                if not block.get("isReview"):
                    _create_reviews(doc, block)
                _log_history(doc, block)
        elif action == "push":
            block["status"] = "pushed"
            block["active"] = False
            block.pop("started_at", None)
            block.pop("elapsed", None)
            _tomorrow_add(doc, block)
        elif action == "restore":
            block["status"] = "pending"
            block["active"] = False
            block.pop("started_at", None)
            block.pop("elapsed", None)
            _tomorrow_remove(doc, block["subject"], block["topic"])

        plan["stats"] = _stats(plan.get("blocks", []))
        doc["plan"] = plan
        _save_doc(doc)
    out = drawer(doc)
    out["status"] = "success"
    out["plan"] = plan
    return jsonify(out)


@app.post("/api/blocks/metrics")
def block_metrics():
    body = request.get_json(silent=True) or {}
    bid = body.get("id")

    def _to_int(v):
        try:
            return max(0, int(float(v)))
        except (TypeError, ValueError):
            return None

    with LOCK:
        doc = _load_doc()
        plan = doc.get("plan")
        if not plan:
            return jsonify({"status": "error", "message": "Blok bulunamadı."}), 404
        block = _find_block(plan, bid)
        if not block or block.get("type") != "study":
            return jsonify({"status": "error", "message": "Blok bulunamadı."}), 404
        changed = False
        if body.get("questions") is not None:
            q = _to_int(body.get("questions"))
            if q is not None:
                block["questions"] = q
                changed = True
        if body.get("pages") is not None:
            p = _to_int(body.get("pages"))
            if p is not None:
                block["pages"] = p
                changed = True
        if changed:
            g = _game(doc)
            credited = int(block.get("game_q") or 0)
            now_q = int(block.get("questions") or 0)
            delta = max(0, now_q - credited)
            if delta:
                g["xp"] = int(g.get("xp", 0)) + delta
            block["game_q"] = now_q
            today = _day_key(int(time.time()))
            subj = block.get("subject") or "Genel"
            topic = block.get("topic") or ""
            for h in doc.setdefault("history", []):
                if h.get("day") == today and h.get("subject") == subj and h.get("topic") == topic:
                    if "questions" in block:
                        h["questions"] = block.get("questions", 0)
                    if "pages" in block:
                        h["pages"] = block.get("pages", 0)
            doc["plan"] = plan
            _save_doc(doc)
    out = drawer(doc)
    out["status"] = "success"
    out["plan"] = plan
    return jsonify(out)


@app.get("/api/stats")
def stats():
    with LOCK:
        doc = _load_doc()
    now_ts = int(time.time())
    today = _day_key(now_ts)
    raw_hist = doc.get("history", [])
    hist = raw_hist if isinstance(raw_hist, list) else []
    today_dt = datetime.fromtimestamp(now_ts)
    week_monday = today_dt - timedelta(days=today_dt.weekday())
    day_keys = [(week_monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    day_keys_set = set(day_keys)

    plan = doc.get("plan")
    plan_blocks = plan.get("blocks", []) if isinstance(plan, dict) else []
    done_blocks = [
        b for b in plan_blocks if isinstance(b, dict)
        if b.get("type") == "study" and b.get("status") == "done"
    ]

    # A stats reset wipes history and excludes blocks completed before it, so
    # analytics restart from zero even though the timeline stays untouched.
    reset_ts = _safe_int(doc.get("stats_reset_ts") or 0)
    if reset_ts:
        done_blocks = [
            b for b in done_blocks
            if _safe_int(b.get("done_ts") or 0) > reset_ts
        ]

    def _sum(entries, field):
        total = 0
        for e in entries:
            if isinstance(e, dict):
                total += _safe_int(e.get(field, 0))
        return total

    def _history_minutes(entries):
        total = 0
        for h in entries:
            if isinstance(h, dict):
                total += _safe_int(h.get("minutes", 0))
        return total

    # Today: authoritative from live completed blocks (per-block sums, no
    # subject/topic collapse), falling back to the history log with no plan.
    use_plan_today = bool(done_blocks)
    if use_plan_today:
        today_min = sum(_safe_int(b.get("duration", 0)) for b in done_blocks)
        today_done = len(done_blocks)
        today_q = sum(_safe_int(b.get("questions") or 0) for b in done_blocks)
        today_p = sum(_safe_int(b.get("pages") or 0) for b in done_blocks)
    else:
        today_ents = [h for h in hist if isinstance(h, dict) and h.get("day") == today]
        today_min = _history_minutes(today_ents)
        today_done = len(today_ents)
        today_q = _sum(today_ents, "questions")
        today_p = _sum(today_ents, "pages")

    # 7-day series from history, with today overridden by live plan numbers.
    # Malformed history entries (bad day strings / non-numeric minutes) are
    # skipped so they can never 500 the endpoint.
    days = []
    for k in day_keys:
        ents = [h for h in hist if isinstance(h, dict) and h.get("day") == k]
        days.append({
            "day": _weekday_short(k),
            "done": len(ents),
            "minutes": _history_minutes(ents),
            "questions": _sum(ents, "questions"),
            "pages": _sum(ents, "pages"),
        })
    if use_plan_today:
        for i, k in enumerate(day_keys):
            if k == today:
                days[i] = {
                    "day": _weekday_short(today),
                    "done": today_done,
                    "minutes": today_min,
                    "questions": today_q,
                    "pages": today_p,
                }
                break

    week_min = sum(d["minutes"] for d in days)
    week_q = sum(d["questions"] for d in days)
    week_p = sum(d["pages"] for d in days)
    week_done = sum(d["done"] for d in days)

    # Subject distribution for the last 7 days (history), with today's
    # completed blocks folded in from the live plan to avoid schedule reuse.
    subjects = {}
    for h in hist:
        if not isinstance(h, dict):
            continue
        if h.get("day") not in day_keys_set:
            continue
        if use_plan_today and h.get("day") == today:
            continue
        s = h.get("subject") or "Genel"
        row = subjects.setdefault(s, {"minutes": 0, "questions": 0, "pages": 0})
        row["minutes"] += _safe_int(h.get("minutes", 0))
        row["questions"] += _safe_int(h.get("questions", 0))
        row["pages"] += _safe_int(h.get("pages", 0))
    if use_plan_today:
        for b in done_blocks:
            s = b.get("subject") or "Genel"
            row = subjects.setdefault(s, {"minutes": 0, "questions": 0, "pages": 0})
            row["minutes"] += _safe_int(b.get("duration", 0))
            row["questions"] += _safe_int(b.get("questions") or 0)
            row["pages"] += _safe_int(b.get("pages") or 0)
    subjects_list = [
        {
            "subject": k,
            "minutes": v["minutes"],
            "questions": v["questions"],
            "pages": v["pages"],
            "percent": round(v["minutes"] / week_min * 100, 1) if week_min else 0,
        }
        for k, v in sorted(subjects.items(), key=lambda kv: -kv[1]["minutes"])
    ]
    return jsonify({
        "status": "success",
        "today": {
            "minutes": today_min,
            "done": today_done,
            "questions": today_q,
            "pages": today_p,
        },
        "week": {
            "minutes": week_min,
            "done": week_done,
            "questions": week_q,
            "pages": week_p,
        },
        "subjects": subjects_list,
        "days": days,
    })


@app.post("/api/stats/reset")
def stats_reset():
    with LOCK:
        doc = _load_doc()
        now_ts = int(time.time())
        doc["history"] = []
        plan = doc.get("plan")
        if isinstance(plan, dict):
            for b in plan.get("blocks", []):
                if b.get("questions"):
                    b["questions"] = 0
                if b.get("pages"):
                    b["pages"] = 0
                if b.get("status") == "done":
                    b.setdefault("done_ts", now_ts)
            doc["plan"] = plan
        doc["stats_reset_ts"] = now_ts
        _save_doc(doc)
    return jsonify({"status": "success"})


_TR_MONTHS_SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz",
                    "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]


def _tr_date(dt):
    return f"{dt.day} {_TR_MONTHS_SHORT[dt.month - 1]}"


@app.get("/api/report")
def report():
    rng = (request.args.get("range") or "week").lower()
    if rng not in ("week", "month", "all"):
        rng = "week"
    with LOCK:
        doc = _load_doc()
    now_ts = int(time.time())
    today = _day_key(now_ts)
    today_dt = datetime.fromtimestamp(now_ts)
    raw_hist = doc.get("history", [])
    hist = raw_hist if isinstance(raw_hist, list) else []
    known: list[str] = []

    if rng == "week":
        monday = today_dt - timedelta(days=today_dt.weekday())
        day_from = monday.strftime("%Y-%m-%d")
        day_to = today
        day_keys = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    elif rng == "month":
        start = today_dt - timedelta(days=29)
        day_from = start.strftime("%Y-%m-%d")
        day_to = today
        day_keys = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
    else:
        # Only well-formed calendar dates participate in the all-time range;
        # malformed history days are ignored instead of poisoning day_from.
        known = sorted({
            h.get("day") for h in hist
            if isinstance(h, dict) and _valid_day(h.get("day"))
        })
        if known and known[0] < today:
            day_from = known[0]
        else:
            day_from = today
        day_to = today
        day_keys = None  # weekly buckets, built below

    # Today's live completed blocks are authoritative (per-block sums, no
    # subject/topic collapse), mirroring /api/stats.
    plan = doc.get("plan")
    blocks = plan.get("blocks", []) if isinstance(plan, dict) else []
    reset_ts = _safe_int(doc.get("stats_reset_ts") or 0)
    done_blocks = [
        b for b in blocks if isinstance(b, dict)
        if b.get("type") == "study" and b.get("status") == "done"
    ]
    if reset_ts:
        done_blocks = [
            b for b in done_blocks
            if _safe_int(b.get("done_ts") or 0) > reset_ts
        ]
    use_plan_today = bool(done_blocks) and day_from <= today <= day_to

    def _zero():
        return {"minutes": 0, "sessions": 0, "questions": 0, "pages": 0}

    def _add(row, minutes, questions, pages, sessions=1):
        row["minutes"] += _safe_int(minutes)
        row["sessions"] += _safe_int(sessions)
        row["questions"] += _safe_int(questions)
        row["pages"] += _safe_int(pages)

    per_day = {}
    subjects = {}
    topics = {}
    for h in hist:
        if not isinstance(h, dict):
            continue
        k = h.get("day")
        if not k or not _valid_day(k) or k < day_from or k > day_to:
            continue
        if use_plan_today and k == today:
            continue
        s = h.get("subject") or "Genel"
        t = h.get("topic") or "—"
        mins = _safe_int(h.get("minutes", 0))
        q = _safe_int(h.get("questions", 0))
        p = _safe_int(h.get("pages", 0))
        _add(per_day.setdefault(k, _zero()), mins, q, p)
        _add(subjects.setdefault(s, _zero()), mins, q, p)
        _add(topics.setdefault((s, t), _zero()), mins, q, p)
    if use_plan_today:
        for b in done_blocks:
            s = b.get("subject") or "Genel"
            t = b.get("topic") or "—"
            mins = _safe_int(b.get("duration", 0))
            q = _safe_int(b.get("questions") or 0)
            p = _safe_int(b.get("pages") or 0)
            _add(per_day.setdefault(today, _zero()), mins, q, p)
            _add(subjects.setdefault(s, _zero()), mins, q, p)
            _add(topics.setdefault((s, t), _zero()), mins, q, p)

    # Day series: daily buckets for week/month, weekly buckets for all-time.
    # Every strptime is guarded so a corrupt day key degrades to "?" / today
    # instead of raising a 500.
    def _day_num(key: str) -> str:
        try:
            return str(datetime.strptime(key, "%Y-%m-%d").day)
        except (ValueError, TypeError):
            return "?"

    days = []
    if day_keys is not None:
        for k in day_keys:
            row = dict(per_day.get(k, _zero()))
            row["date"] = k
            if rng == "week":
                row["label"] = _weekday_short(k)
            else:
                row["label"] = _day_num(k)
            days.append(row)
        try:
            first_dt = datetime.strptime(day_from, "%Y-%m-%d")
        except (ValueError, TypeError):
            first_dt = today_dt
            day_from = today
        try:
            last_dt = datetime.strptime(day_to, "%Y-%m-%d")
        except (ValueError, TypeError):
            last_dt = today_dt
            day_to = today
        days_total = (last_dt - first_dt).days + 1
    else:
        try:
            first_dt = datetime.strptime(day_from, "%Y-%m-%d")
        except (ValueError, TypeError):
            first_dt = today_dt
            day_from = today
        try:
            last_dt = datetime.strptime(day_to, "%Y-%m-%d")
        except (ValueError, TypeError):
            last_dt = today_dt
            day_to = today
        days_total = (last_dt - first_dt).days + 1
        weeks = {}
        cur = first_dt
        while cur <= last_dt:
            wk = (cur - timedelta(days=cur.weekday())).strftime("%Y-%m-%d")
            row = per_day.get(cur.strftime("%Y-%m-%d"))
            if row:
                _add(weeks.setdefault(wk, _zero()),
                     row["minutes"], row["questions"], row["pages"], row["sessions"])
            else:
                weeks.setdefault(wk, _zero())
            cur += timedelta(days=1)
        for wk in sorted(weeks):
            row = dict(weeks[wk])
            row["date"] = wk
            row["label"] = _tr_date(datetime.strptime(wk, "%Y-%m-%d"))
            days.append(row)

    total_min = sum(r["minutes"] for r in per_day.values())
    total_sessions = sum(r["sessions"] for r in per_day.values())
    total_q = sum(r["questions"] for r in per_day.values())
    total_p = sum(r["pages"] for r in per_day.values())
    days_active = sum(1 for r in per_day.values() if r["minutes"] > 0)

    subjects_list = [
        {
            "subject": k,
            "minutes": v["minutes"],
            "sessions": v["sessions"],
            "questions": v["questions"],
            "pages": v["pages"],
            "percent": round(v["minutes"] / total_min * 100, 1) if total_min else 0,
        }
        for k, v in sorted(subjects.items(), key=lambda kv: -kv[1]["minutes"])
    ]
    topics_list = [
        {
            "subject": k[0],
            "topic": k[1],
            "minutes": v["minutes"],
            "sessions": v["sessions"],
        }
        for k, v in sorted(topics.items(), key=lambda kv: -kv[1]["minutes"])[:8]
    ]

    best = max(days, key=lambda d: d["minutes"]) if days and total_min else None
    g = _game(doc) if isinstance(doc.get("game", {}), dict) or "game" not in doc else {}
    xp = _safe_int(g.get("xp", 0))
    raw_badges = g.get("badges", [])
    badges = sorted(raw_badges) if isinstance(raw_badges, list) else []

    first_lbl = _tr_date(first_dt)
    last_lbl = f"{_tr_date(last_dt)} {last_dt.year}"
    if rng == "week":
        title = f"{first_lbl} – {last_lbl}"
    elif rng == "month":
        title = f"Son 30 gün · {first_lbl} – {last_lbl}"
    elif total_min or known:
        title = f"Tüm zamanlar · {first_lbl} – {last_lbl}"
    else:
        title = "Tüm zamanlar · henüz veri yok"

    return jsonify({
        "status": "success",
        "range": {
            "key": rng,
            "title": title,
            "from": day_from,
            "to": day_to,
            "days_total": days_total,
            "days_active": days_active,
        },
        "totals": {
            "minutes": total_min,
            "sessions": total_sessions,
            "questions": total_q,
            "pages": total_p,
            "avg_minutes": round(total_min / days_active, 1) if days_active else 0,
        },
        "days": days,
        "subjects": subjects_list,
        "topics": topics_list,
        "highlights": {
            "best_day": {
                "date": best["date"],
                "label": best["label"],
                "minutes": best["minutes"],
            } if best else None,
            "top_subject": {
                "subject": subjects_list[0]["subject"],
                "minutes": subjects_list[0]["minutes"],
                "percent": subjects_list[0]["percent"],
            } if subjects_list else None,
            "consistency": round(days_active / days_total * 100, 1) if days_total else 0,
            "streak": _compute_streak(g),
            "xp": xp,
            "level": _level_from_xp(xp),
            "badges": badges,
        },
    })


@app.get("/api/game")
def get_game():
    with LOCK:
        doc = _load_doc()
    g = _game(doc)
    _check_badges(doc, g)
    lvl = _level_from_xp(int(g.get("xp", 0)))
    next_xp = _xp_for_level(lvl)
    streak = _compute_streak(g)
    badges = g.get("badges", [])
    return jsonify({
        "status": "success",
        "xp": int(g.get("xp", 0)),
        "level": lvl,
        "xp_next": next_xp,
        "base_modules": int(g.get("base_modules", 0)),
        "base_health": g.get("base_health", "ok"),
        "badges": badges,
        "streak": streak,
    })


@app.get("/api/drawer")
def get_drawer():
    with LOCK:
        doc = _load_doc()
    return jsonify({"status": "success", **drawer(doc)})


@app.get("/api/weak")
def get_weak():
    with LOCK:
        doc = _load_doc()
    return jsonify({"status": "success", "weak": doc.get("weak", [])})


@app.post("/api/weak")
def weak():
    body = request.get_json(silent=True) or {}
    with LOCK:
        doc = _load_doc()
        subject = str(body.get("subject") or "")
        topic = str(body.get("topic") or "")
        if body.get("remove"):
            doc["weak"] = [w for w in doc.get("weak", []) if not (w["subject"] == subject and w["topic"] == topic)]
        elif subject and topic:
            _upsert_weak(doc, {"subject": subject, "topic": topic})
        _save_doc(doc)
    return jsonify({"status": "success", "weak": doc.get("weak", [])})


@app.get("/api/tomorrow")
def get_tomorrow():
    with LOCK:
        doc = _load_doc()
    return jsonify({"status": "success", "tomorrow": doc.get("tomorrow", [])})


@app.post("/api/tomorrow")
def tomorrow():
    body = request.get_json(silent=True) or {}
    with LOCK:
        doc = _load_doc()
        subject = str(body.get("subject") or "")
        topic = str(body.get("topic") or "")
        if body.get("remove") and subject:
            _tomorrow_remove(doc, subject, topic)
        _save_doc(doc)
    return jsonify({"status": "success", "tomorrow": doc.get("tomorrow", [])})


@app.get("/api/reviews")
def get_reviews():
    with LOCK:
        doc = _load_doc()
    reviews = doc.get("reviews", [])
    today = _day_key(int(time.time()))
    pending = [r for r in reviews if r.get("targetDay") == today and r.get("status") == "pending"]
    return jsonify({"status": "success", "reviews": pending})


@app.post("/api/reviews/schedule")
def schedule_reviews():
    with LOCK:
        doc = _load_doc()
        plan = doc.get("plan")
        if not plan:
            return jsonify({"status": "error", "message": "Önce bir plan oluştur."}), 400

        reviews = doc.get("reviews", [])
        today = _day_key(int(time.time()))
        pending = [r for r in reviews if r.get("targetDay") == today and r.get("status") == "pending"]
        if not pending:
            return jsonify({"status": "error", "message": "Bugün için bekleyen review yok."}), 400

        blocks = plan.get("blocks", [])
        last_end = max((b["end"] for b in blocks), default=_minutes(plan["input"]["start"]))

        for r in pending:
            blk_id = uuid.uuid4().hex[:12]
            start = last_end
            end = start + REVIEW_BLOCK
            time_str = f"{_hhmm(start)}-{_hhmm(end)}"
            blocks.append({
                "id": blk_id,
                "start": start,
                "end": end,
                "time": time_str,
                "duration": REVIEW_BLOCK,
                "origDuration": REVIEW_BLOCK,
                "type": "study",
                "subject": r["subject"],
                "topic": r["topic"],
                "confidence": "green",
                "status": "pending",
                "active": False,
                "elapsed": 0,
                "note": f"Spaced Review · {r['label']}",
                "isReview": True,
                "reviewLabel": r["label"],
            })
            last_end = end
            r["status"] = "scheduled"

        plan["blocks"] = blocks
        plan["stats"] = _stats(blocks)
        doc["plan"] = plan
        _save_doc(doc)

    out = drawer(doc)
    out["status"] = "success"
    out["plan"] = plan
    out["message"] = f"{len(pending)} spaced review eklendi."
    return jsonify(out)


@app.get("/api/overdue")
def overdue():
    with LOCK:
        doc = _load_doc()
    items = _gather_overdue(doc)
    return jsonify({"status": "success", "overdue": items, "count": len(items)})


@app.post("/api/catchup")
def catchup():
    with LOCK:
        doc = _load_doc()
        today = _day_key(int(time.time()))
        overdue = _gather_overdue(doc)

        summary = {"days": {}, "missed": len(overdue), "note": "Mevcut plan ve timeline korundu — gecikmiş konular sonraki günlere kuyruğa eklendi."}

        if overdue:
            rank = {"red": 0, "yellow": 1, "green": 2}
            scheduled_keys = {(s["subject"], s["topic"]) for s in doc.get("scheduled", [])}
            ordered = [
                it for it in sorted(overdue, key=lambda x: rank.get(x.get("confidence", "yellow"), 1))
                if (it["subject"], it["topic"]) not in scheduled_keys
            ]
            byday = {}
            queued = []
            for i, it in enumerate(ordered):
                offset = 1 + (i % 3)
                day = _day_key(int(time.time()) + offset * SECONDS_PER_DAY)
                doc.setdefault("scheduled", []).append({
                    "subject": it["subject"], "topic": it["topic"],
                    "day": day, "minutes": max(REVIEW_BLOCK, min(int(it.get("minutes") or 30), 60)),
                    "confidence": it.get("confidence", "yellow"),
                    "reason": "catchup", "created": int(time.time()),
                })
                queued.append((it["subject"], it["topic"]))
                byday.setdefault(day, []).append(it["subject"] + " · " + it["topic"])
            summary["days"] = {
                d: {"label": _weekday_short(d), "topics": v} for d, v in byday.items()
            }
            summary["missed"] = len(ordered)
            if queued:
                doc["missed"] = [
                    m for m in doc.get("missed", [])
                    if (m["subject"], m["topic"]) not in queued
                ]
                doc["tomorrow"] = [
                    t for t in doc.get("tomorrow", [])
                    if (t["subject"], t["topic"]) not in queued
                ]

        _save_doc(doc)

    out = drawer(doc)
    out["status"] = "success"
    out["plan"] = doc.get("plan")
    out["summary"] = summary
    return jsonify(out)


@app.get("/api/export")
def export_data():
    """Return the full planner document as JSON (backup / portability)."""
    with LOCK:
        doc = _load_doc()
    return jsonify({
        "status": "success",
        "version": __version__,
        "exported_at": _day_key(int(time.time())),
        "data": doc,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)