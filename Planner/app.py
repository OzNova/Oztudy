import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "userData")
DATA_FILE = os.path.join(DATA_DIR, "planner.json")
LOCK = threading.Lock()

BREAK_MIN = 10

DEFAULT_SETTINGS = {
    "study_min": 45,
    "break_min": 10,
    "win_start": "17:00",
    "win_end": "19:00",
    "duration_h": 2,
    "theme": "light",
}

EXAM_DEFAULTS = [
    {"id": "t1w1", "label": "1. Dönem 1. Sınav", "start": "2026-10-30", "end": "2026-11-06"},
    {"id": "t1w2", "label": "1. Dönem 2. Sınav", "start": "2026-12-24", "end": "2026-12-31"},
    {"id": "t2w1", "label": "2. Dönem 1. Sınav", "start": "2027-03-25", "end": "2027-04-01"},
    {"id": "t2w2", "label": "2. Dönem 2. Sınav", "start": "2027-05-31", "end": "2027-06-04"},
]


def _exams_for(doc):
    weeks = doc.get("exam_weeks")
    if not isinstance(weeks, list) or not weeks:
        return [dict(w) for w in EXAM_DEFAULTS]
    return weeks


def _next_exam(weeks, today=None):
    today = today or datetime.now().date()
    today_key = today.isoformat()
    best = None
    for w in weeks:
        start = str(w.get("start") or "")
        end = str(w.get("end") or "")
        if not start or not end:
            continue
        if end < today_key:
            continue
        if best is None or start < best["start"]:
            best = {"label": w.get("label") or "Sınav", "start": start, "end": end}
    if best is None:
        return None
    days = (datetime.strptime(best["start"], "%Y-%m-%d").date() - today).days
    ongoing = days <= 0 and datetime.strptime(best["end"], "%Y-%m-%d").date() >= today
    best["days_until"] = max(0, days)
    best["ongoing"] = ongoing
    return best

HABIT_DEFAULTS = [
    {"id": "gym", "label": "Gym", "time": "16:30", "color": "#4F46E5", "icon": "gym"},
    {"id": "okuma", "label": "Okuma", "time": "21:00", "color": "#059669", "icon": "book"},
    {"id": "uyku", "label": "Uyku", "time": "22:30", "color": "#0891B2", "icon": "moon"},
]
HABIT_COLORS = ["#4F46E5", "#059669", "#D97706", "#DB2777", "#0891B2", "#7C3AED"]


def _habit_icon(label):
    low = str(label or "").lower()
    if any(k in low for k in ("gym", "spor", "antrenman", "egzersiz", "fitness")):
        return "gym"
    if any(k in low for k in ("uyku", "uyu", "sleep")):
        return "moon"
    if any(k in low for k in ("oku", "okuma", "kitap", "read")):
        return "book"
    return "flag"


def _habits_for(doc):
    habits = doc.get("habits")
    if not isinstance(habits, list) or not habits:
        return [dict(h) for h in HABIT_DEFAULTS]
    out = []
    for h in habits:
        item = dict(h)
        if not item.get("icon"):
            item["icon"] = _habit_icon(item.get("label"))
        if not item.get("color"):
            item["color"] = HABIT_COLORS[len(out) % len(HABIT_COLORS)]
        out.append(item)
    return out


def _habit_track(doc):
    track = doc.get("habit_track")
    return track if isinstance(track, dict) else {}


def _habit_days(today=None, n=7):
    today = today or datetime.now().date()
    return [(today - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def _habit_streak(habits, track, today=None):
    today = today or datetime.now().date()
    streaks = {}
    for h in habits:
        hid = h["id"]
        done_days = set()
        for day_key, ids in (track or {}).items():
            try:
                if hid in (ids or []):
                    datetime.strptime(str(day_key), "%Y-%m-%d")
                    done_days.add(str(day_key))
            except (ValueError, TypeError):
                continue
        if not done_days:
            streaks[hid] = 0
            continue
        latest_key = max(done_days)
        try:
            latest = datetime.strptime(latest_key, "%Y-%m-%d").date()
        except ValueError:
            streaks[hid] = 0
            continue
        # Ghosting fix: a streak is only alive if the most recent completion
        # is today or yesterday. Mon+Tue done but today Fri -> 0, not 2.
        if (today - latest).days > 1:
            streaks[hid] = 0
            continue
        n = 0
        d = latest
        while d.isoformat() in done_days:
            n += 1
            d -= timedelta(days=1)
        streaks[hid] = n
    return streaks


def _toggle_habit(doc, hid, day_key):
    track = _habit_track(doc)
    done = list(track.get(day_key) or [])
    if hid in done:
        done.remove(hid)
    else:
        done.append(hid)
    if done:
        track[day_key] = done
    elif day_key in track:
        del track[day_key]
    doc["habit_track"] = track


def _settings_for(doc):
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
    return st

# Study blocks use the user's settings (study_min / break_min): every selected
# topic is placed in full — nothing is shortened, scaled, queued or dropped;
# the timeline simply extends past the Focus Window end to fit all topics.

# Spaced-review/catch-up blocks are fixed human blocks too — never below this.
REVIEW_BLOCK = 25

CONFIDENCES = ("red", "yellow", "green")
CONF_RANK = {"red": 0, "yellow": 1, "green": 2}
CONF_TAGS = {
    "red": "Zorlanıyorum / Deep Focus",
    "yellow": "Orta / Practice",
    "green": "Hakimim / Quick Review",
}


def _conf_note(confidence, study_min):
    notes = {
        "red": "derin odak + aktif hatırlama: kapat-anlat, zorlanan noktayı işaretle",
        "yellow": "pratik: soru çözümü + yanlış analizi",
        "green": "hızlı tekrar: özet tara + kavram kartları",
    }
    return f"{study_min} dk {notes.get(confidence, notes['yellow'])}"

CRITERIA = {
    "A": "Criterion A: Knowing and Understanding",
    "BC": "Criterion B/C: Investigating & Communicating",
    "D": "Criterion D: Applying Mathematics/Science in Real-World Contexts",
}
CRITERION_SHORT = {"A": "Kriter A", "BC": "Kriter B/C", "D": "Kriter D"}

MODE_LABELS = {
    "exam": "Criterion A/D Exam Prep",
    "practice": "Soru Çözümü / Practice",
    "recall": "Active Recall / Review",
}
MODE_SUFFIX = {
    "exam": "kriter yazım odaklı çalış",
    "practice": "soru odaklı çalış",
    "recall": "tekrar odaklı çalış",
}

# ============ 9-A weekly timetable ============
# Python weekday(): Mon=0 .. Sun=6 -> subjects of that day (periods 08:00-15:30).
TIMETABLE = {
    0: ["MATH", "ENG", "TUR", "BIO", "VIA/MUS"],
    1: ["ENG", "MATH", "GER", "PHY", "TUR"],
    2: ["CHE", "ENG", "HIS", "PHY", "DT", "GER"],
    3: ["TUR", "BIO", "MATH", "HIS", "ECL"],
    4: ["GER", "PE", "R&E", "CHE", "GEO", "ENG"],
    5: [],
    6: [],
}

# Fixed non-study school blocks: 08:00-15:30 on schooldays, lunch 12:35-13:20.
SCHOOL_START = 480   # 08:00
SCHOOL_END = 930     # 15:30
LUNCH = (755, 800)   # 12:35 - 13:20
PERIOD_SLOTS = [
    (480, 525, "P1"), (530, 575, "P2"), (580, 625, "P3"),
    (625, 640, "Ara"), (640, 685, "P4"), (690, 735, "P5"),
    (735, 755, "Boş"), (755, 800, "Öğle Yemeği"),
    (800, 850, "P6"), (855, 905, "P7"), (910, 930, "P8"),
]

# Weekend study programme (editable): subjects worked on Saturday/Sunday.
# Planned like school days: the planner prioritises subjects of the current
# weekend day, then the next day of the programme / next school day.
WEEKEND_PROGRAM = {
    5: ["MATH", "ENG", "PHY", "TUR", "GER"],            # Cumartesi
    6: ["BIO", "CHE", "HIS", "GEO", "DT", "ECL"],       # Pazar
}

# Timetable code -> accepted subject names (match planner topic subjects).
SUBJECT_ALIASES = {
    "MATH": ["math", "mathematics", "matematik"],
    "ENG": ["english", "english literature", "ingilizce", "iel"],
    "TUR": ["turkish", "turkish language", "türkçe", "türk dili"],
    "BIO": ["biology", "biyoloji"],
    "CHE": ["chemistry", "kimya"],
    "PHY": ["physics", "fizik"],
    "GER": ["german", "almanca"],
    "HIS": ["history", "tarih"],
    "GEO": ["geography", "coğrafya"],
    "DT": ["design", "design and technology", "design & technology", "tasarım"],
    "ECL": ["ecl", "english as a co-language", "english class"],
    "VIA/MUS": ["visual arts", "visual arts/music", "music", "müzik", "sanat", "art", "via/mus"],
    "PE": ["pe", "physical education", "physical ed", "beden eğitimi", "beden"],
    "R&E": ["research", "research & enquiry", "araştırma", "rea", "r&e"],
}

# ============ 2026-2027 academic calendar (Turkey, editable) ============
ACADEMIC_YEAR = "2026-2027"
TERM_1 = ("2026-09-08", "2027-01-22")
SEMESTER_BREAK = ("2027-01-23", "2027-02-05")
TERM_2 = ("2027-02-08", "2027-06-18")
SUMMER_START = "2027-06-19"

# Single no-school days (national holidays / ceremonies).
NO_SCHOOL_DAYS = {
    "2026-09-09": "Uyum Haftası (okul yok)",
    "2026-10-29": "Cumhuriyet Bayramı",
    "2027-01-01": "Yılbaşı",
    "2027-04-23": "23 Nisan Ulusal Egemenlik ve Çocuk Bayramı",
    "2027-05-19": "19 Mayıs Atatürk'ü Anma, Gençlik ve Spor Bayramı",
}

# No-school ranges (midterm breaks, semester break, bayram, summer).
NO_SCHOOL_RANGES = [
    ("2026-11-16", "2026-11-20", "Ara Tatil (Kasım)"),
    ("2027-01-23", "2027-02-05", "Yarıyıl Tatili"),
    ("2027-04-05", "2027-04-09", "Ara Tatil (Nisan)"),
    ("2027-04-11", "2027-04-12", "Ramazan Bayramı"),
    ("2027-05-18", "2027-05-21", "Kurban Bayramı"),
    ("2027-06-19", "2027-09-30", "Yaz Tatili"),
]

app = Flask(__name__)


def _minutes(hhmm):
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _hhmm(total):
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def _load_doc():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data = data if isinstance(data, dict) else {}
        if _rollover(data):
            _save_doc(data)
            return data
        plan = data.get("plan")
        if isinstance(plan, dict):
            _repair_plan(plan)
            plan["stats"] = _stats(plan.get("blocks", []))
            data["plan"] = plan
        return data
    except FileNotFoundError:
        return {}
    except (OSError,) as exc:
        print(f"[planner] data file unreadable ({exc}); starting with empty doc",
              file=sys.stderr)
        return {}
    except ValueError as exc:
        # Corrupt JSON (e.g. torn write from a crash). Back it up instead of
        # silently wiping the semester's history/XP.
        try:
            if os.path.exists(DATA_FILE):
                backup = DATA_FILE + ".corrupt." + datetime.now().strftime("%Y%m%d-%H%M%S")
                os.replace(DATA_FILE, backup)
                print(f"[planner] WARNING: corrupt planner.json backed up to {backup}: {exc}",
                      file=sys.stderr)
            else:
                print(f"[planner] WARNING: corrupt planner.json: {exc}", file=sys.stderr)
        except OSError as backup_exc:
            print(f"[planner] WARNING: could not back up corrupt planner.json: {backup_exc}",
                  file=sys.stderr)
        return {}


def _save_doc(doc):
    os.makedirs(DATA_DIR, exist_ok=True)
    # Atomic write: write to a temp file on the same filesystem, then swap it
    # in with os.replace() so a crash/power-loss can never leave a half-written
    # planner.json behind.
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix="planner.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp_path, DATA_FILE)
    except BaseException:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_plan():
    plan = _load_doc().get("plan")
    return plan if isinstance(plan, dict) else None


def _stats(blocks):
    active = [b for b in blocks if b["type"] == "study" and b.get("status") != "pushed"]
    total = sum(b["duration"] for b in active)
    done = sum(b["duration"] for b in active if b["status"] == "done")
    return {"total_min": total, "done_min": done, "done_count": sum(1 for b in active if b["status"] == "done")}


def _repair_plan(plan):
    # Backfill plan identity fields for docs created before the midnight-
    # rollover fix (plan day / created_at).
    if not plan.get("day"):
        created_at = plan.get("created_at")
        try:
            plan["day"] = _day_key(int(created_at)) if created_at else None
        except (TypeError, ValueError):
            plan["day"] = None
    if not isinstance(plan.get("created_at"), int):
        plan["created_at"] = int(time.time())
    for b in plan.get("blocks", []):
        if not isinstance(b, dict):
            continue
        win = None
        try:
            win = max(1, int(b.get("end", 0)) - int(b.get("start", 0)))
        except (TypeError, ValueError):
            win = None
        if not (isinstance(b.get("duration"), int) and b["duration"] >= 1):
            b["duration"] = win if win is not None else 45
        if not (isinstance(b.get("origDuration"), int) and b["origDuration"] >= 1):
            b["origDuration"] = b["duration"]
        b.setdefault("type", "study")
        b.setdefault("status", "pending")
        b.setdefault("subject", "")
        b.setdefault("topic", "")
        b.setdefault("confidence", "yellow")
        b.setdefault("elapsed", 0)
        b.setdefault("note", "")
        if "active" not in b:
            b["active"] = False
        # Pre-fix done blocks already granted XP once — mark them so a future
        # pending->done toggle does not award a second time.
        if b.get("status") == "done" and "xp_awarded" not in b:
            b["xp_awarded"] = True


def _upsert_weak(doc, item):
    weak = doc.setdefault("weak", [])
    for w in weak:
        if w["subject"] == item["subject"] and w["topic"] == item["topic"]:
            return weak
    weak.insert(0, {"subject": item["subject"], "topic": item["topic"], "added": int(time.time())})
    return weak


def _tomorrow_add(doc, block):
    items = doc.setdefault("tomorrow", [])
    for it in items:
        if it["subject"] == block["subject"] and it["topic"] == block["topic"]:
            it["time"] = block["time"]
            it["added"] = int(time.time())
            return items
    items.insert(0, {
        "subject": block["subject"],
        "topic": block["topic"],
        "time": block["time"],
        "added": int(time.time()),
    })
    return items


def _tomorrow_remove(doc, subject, topic):
    items = doc.setdefault("tomorrow", [])
    doc["tomorrow"] = [it for it in items if not (it["subject"] == subject and it["topic"] == topic)]
    return doc["tomorrow"]


def _day_key(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _create_reviews(doc, block):
    reviews = doc.setdefault("reviews", [])
    now_ts = int(time.time())
    today = _day_key(now_ts)
    existing = {(r["subject"], r["topic"], r["targetDay"]) for r in reviews}
    offsets = [(3, "Hızlı Tekrar"), (7, "Kendini Sınama")]
    for days, label in offsets:
        target = _day_key(now_ts + days * 86400)
        if (block["subject"], block["topic"], target) in existing:
            continue
        reviews.append({
            "id": uuid.uuid4().hex[:12],
            "subject": block["subject"],
            "topic": block["topic"],
            "targetDay": target,
            "label": label,
            "status": "pending",
            "added": now_ts,
        })


def _log_history(doc, block):
    now_ts = int(time.time())
    block["done_ts"] = now_ts
    day = _day_key(now_ts)
    subj = block.get("subject") or "Genel"
    topic = block.get("topic") or ""
    minutes = max(1, int(block.get("duration") or (block.get("end", 0) - block.get("start", 0)) or 1))
    q = max(0, int(block.get("questions") or 0))
    p = max(0, int(block.get("pages") or 0))
    already_awarded = bool(block.get("xp_awarded"))
    if not already_awarded:
        block["xp_awarded"] = True
    hist = doc.setdefault("history", [])
    for h in hist:
        if h.get("day") == day and h.get("subject") == subj and h.get("topic") == topic:
            h["minutes"] = minutes
            if q or p:
                h["questions"] = q
                h["pages"] = p
            if already_awarded:
                # History entry updated, but XP/base already granted for this
                # block — do NOT call _game_end() again (prevents Done-toggle
                # farming and double-XP when two blocks share a topic).
                return
            _game_record_day(doc, day)
            _game_end(doc, block)
            return
    hist.append({
        "day": day, "subject": subj, "topic": topic,
        "minutes": minutes, "questions": q, "pages": p, "ts": now_ts,
    })
    if already_awarded:
        return
    _game_record_day(doc, day)
    _game_end(doc, block)


def _game(doc):
    return doc.setdefault("game", {
        "xp": 0, "base_modules": 0, "badges": [],
        "history_days": [], "base_health": "ok",
    })


def _xp_for_level(lvl):
    return 100 + (lvl - 1) * 50


def _level_from_xp(xp):
    lvl = 1
    xp = max(0, min(int(xp), 10 ** 9))
    while xp >= _xp_for_level(lvl) and lvl < 20000:
        lvl += 1
    return lvl


def _game_record_day(doc, day):
    g = _game(doc)
    days = g.setdefault("history_days", [])
    if day not in days:
        days.append(day)
        if len(days) > 200:
            del days[: len(days) - 200]


def _game_award_xp(doc, block):
    g = _game(doc)
    dur = max(1, int(block.get("duration") or 1))
    xp = (dur // 15) * 10
    g["xp"] = int(g.get("xp", 0)) + xp
    g["base_health"] = "ok"
    return xp


def _game_build(doc):
    g = _game(doc)
    g["base_modules"] = min(int(g.get("base_modules", 0)) + 1, 12)
    _check_badges(doc, g)


def _game_damage(doc):
    g = _game(doc)
    g["base_modules"] = max(int(g.get("base_modules", 0)) - 1, 0)
    g["base_health"] = "damaged"


def _game_end(doc, block):
    if block.get("zen_abandon"):
        _game_damage(doc)
    else:
        _game_award_xp(doc, block)
        _game_build(doc)


def _compute_streak(g, today=None):
    days = sorted(g.get("history_days", []), reverse=True)
    if not days:
        return 0
    today = today or datetime.now().date()
    try:
        latest = datetime.strptime(days[0], "%Y-%m-%d").date()
    except ValueError:
        return 0
    # Ghosting fix: if the most recent active day is older than yesterday,
    # the streak is dead — return 0 instead of the stale count.
    if (today - latest).days > 1:
        return 0
    streak = 1
    cur = datetime.strptime(days[0], "%Y-%m-%d")
    for i in range(1, len(days)):
        try:
            prev = datetime.strptime(days[i], "%Y-%m-%d")
        except ValueError:
            break
        if (cur - prev).days == 1:
            streak += 1
            cur = prev
        else:
            break
    return streak


def _check_badges(doc, g):
    hist = doc.get("history", [])
    sessions = len(hist)
    streak = _compute_streak(g)
    known = set(g.get("badges", []))
    if sessions >= 1:
        known.add("Space Cadet")
    if sessions >= 5:
        known.add("IB Survivor")
    if streak >= 10:
        known.add("10-Day Streak")
    if streak >= 5:
        known.add("Week Warrior")
    if streak >= 3:
        known.add("Comet Chaser")
    if int(g.get("xp", 0)) >= 500:
        known.add("Star Navigator")
    if int(g.get("base_modules", 0)) >= 8:
        known.add("Outpost Architect")
    today = datetime.now().date()
    recent = set()
    for h in hist:
        try:
            d = datetime.strptime(str(h.get("day") or ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - d).days < 7:
            recent.add(str(h.get("day")))
    if len(recent) >= 5:
        known.add("Weekly Target")
    g["badges"] = sorted(known)


def _now_min():
    now = datetime.now()
    return now.hour * 60 + now.minute


def _weekday_short(daykey):
    names = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
    return names[datetime.strptime(daykey, "%Y-%m-%d").weekday()]


def _day_status(daykey):
    d = datetime.strptime(daykey, "%Y-%m-%d")
    if daykey in NO_SCHOOL_DAYS:
        return "holiday", NO_SCHOOL_DAYS[daykey]
    for start, end, label in NO_SCHOOL_RANGES:
        if start <= daykey <= end:
            return "holiday", label
    if d.weekday() >= 5:
        return "weekend", "Hafta sonu"
    return "school", "Okul günü"


def _subjects_for_day(daykey):
    d = datetime.strptime(daykey, "%Y-%m-%d")
    status, label = _day_status(daykey)
    if status == "weekend":
        return WEEKEND_PROGRAM.get(d.weekday(), []), status, label
    if status != "school":
        return [], status, label
    return TIMETABLE.get(d.weekday(), []), status, label


def _next_program_day(daykey, step=1):
    cur = datetime.strptime(daykey, "%Y-%m-%d")
    for _ in range(40):
        cur += timedelta(days=step)
        key = cur.strftime("%Y-%m-%d")
        if _subjects_for_day(key)[0]:
            return key
    return daykey


def _subject_code(name):
    norm = str(name or "").strip().lower()
    for code, names in SUBJECT_ALIASES.items():
        if norm in names:
            return code
    return None


def _period_table(weekday):
    n = len(TIMETABLE.get(weekday, []))
    return [
        {"period": label, "start": _hhmm(a), "end": _hhmm(b)}
        for (a, b, label) in PERIOD_SLOTS[:n]
    ]


def _school_digest(day_key):
    d = datetime.strptime(day_key, "%Y-%m-%d")
    status, label = _day_status(day_key)
    subjects, _, _ = _subjects_for_day(day_key)
    next_key = _next_program_day(day_key)
    next_subjects = _subjects_for_day(next_key)[0]
    return {
        "academic_year": ACADEMIC_YEAR,
        "date": day_key,
        "day": _weekday_short(day_key),
        "status": status,
        "status_label": label,
        "school_day": status == "school",
        "school_window": f"{_hhmm(SCHOOL_START)}-{_hhmm(SCHOOL_END)}",
        "lunch": f"{_hhmm(LUNCH[0])}-{_hhmm(LUNCH[1])}",
        "subjects_today": subjects,
        "subjects_next_day": next_subjects,
        "weekend_program": {
            "sat": WEEKEND_PROGRAM.get(5, []),
            "sun": WEEKEND_PROGRAM.get(6, []),
        },
        "periods": _period_table(d.weekday()),
    }


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


def _gather_overdue(doc):
    today = _day_key(int(time.time()))
    items = []
    seen = set()

    def push(subject, topic, minutes, day, source, confidence):
        key = (subject, topic)
        if key in seen:
            return
        seen.add(key)
        items.append({
            "subject": subject, "topic": topic,
            "minutes": int(minutes or 30), "day": day,
            "source": source, "confidence": confidence or "yellow",
        })

    for m in doc.get("missed", []):
        push(m["subject"], m["topic"], m.get("minutes", 30), m.get("day"), m.get("source", "plan"), m.get("confidence"))
    for it in doc.get("tomorrow", []):
        push(it["subject"], it["topic"], 30, today, "push", "yellow")
    for e in doc.get("scheduled", []):
        if e.get("day") and e.get("day") < today:
            push(e["subject"], e["topic"], e.get("minutes", 30), e["day"], "scheduled", e.get("confidence", "yellow"))
    plan = doc.get("plan")
    if isinstance(plan, dict):
        now_min = _now_min()
        for b in plan.get("blocks", []):
            if b.get("type") == "study" and b.get("status") != "done" and b.get("end") is not None and b["end"] <= now_min:
                push(b["subject"], b["topic"], b.get("duration", 30), today, "plan", b.get("confidence", "yellow"))
    return items


def _merge_scheduled(doc, plan, today):
    sched = doc.setdefault("scheduled", [])
    due = [s for s in sched if s.get("day") == today]
    if not due:
        return
    blocks = plan.get("blocks", [])
    cursor = max((b["end"] for b in blocks), default=_minutes(plan["input"]["start"]))
    for s in due:
        mins = max(REVIEW_BLOCK, int(s.get("minutes", 30)))
        end = cursor + mins
        blocks.append({
            "id": uuid.uuid4().hex[:12],
            "start": cursor,
            "end": end,
            "time": f"{_hhmm(cursor)}-{_hhmm(end)}",
            "duration": mins,
            "origDuration": mins,
            "type": "study",
            "subject": s["subject"],
            "topic": s["topic"],
            "confidence": s.get("confidence", "yellow"),
            "status": "pending",
            "active": False,
            "elapsed": 0,
            "note": "Catch-Up · önceki günlerden",
            "isRescheduled": True,
        })
        cursor = end
    plan["blocks"] = blocks
    plan["stats"] = _stats(blocks)
    doc["scheduled"] = [s for s in sched if s.get("day") != today]


def _find_block(plan, bid):
    for b in plan.get("blocks", []):
        if b.get("id") == bid:
            return b
    return None


def _resize_block(plan, block, new_duration):
    """Resize ``block`` and cascade the delta to all later blocks.

    Fixes the timeline-overlap bug where "+5 min" grew ``duration`` but left
    ``end``/``time`` stale so the extended block visually overlapped the next
    one. Returns the applied delta (0 when nothing changed).
    """
    try:
        new_duration = max(1, int(new_duration))
    except (TypeError, ValueError):
        return 0
    try:
        old_duration = int(block.get("duration") or 0)
    except (TypeError, ValueError):
        old_duration = 0
    delta = new_duration - old_duration
    if delta == 0:
        return 0
    block["duration"] = new_duration
    try:
        start = int(block.get("start") or 0)
    except (TypeError, ValueError):
        return delta
    block["end"] = start + new_duration
    block["time"] = f"{_hhmm(start)}-{_hhmm(start + new_duration)}"
    blocks = plan.get("blocks", [])
    try:
        idx = next(i for i, b in enumerate(blocks) if b.get("id") == block.get("id"))
    except StopIteration:
        return delta
    for later in blocks[idx + 1:]:
        try:
            later["start"] = int(later.get("start") or 0) + delta
            later["end"] = int(later.get("end") or 0) + delta
            later["time"] = f"{_hhmm(later['start'])}-{_hhmm(later['end'])}"
        except (TypeError, ValueError):
            continue
    return delta


def build_plan(topics, start, end, duration_h, mode, criterion):
    if not topics:
        return None, "En az bir konu seçmelisin."
    s, e = _minutes(start), _minutes(end)
    if s is None or e is None:
        return None, "Geçerli bir saat formatı kullan (HH:MM)."
    if e <= s:
        return None, "Bitiş saati başlangıçtan sonra olmalı."
    if e - s < 30:
        return None, "Zaman penceresi en az 30 dakika olmalı."

    st = _settings_for(_load_doc())
    study_min = st["study_min"]
    break_min = st["break_min"]

    today_key = _day_key(int(time.time()))
    day_status, day_label = _day_status(today_key)

    # School hours 08:00-15:30 are fixed non-study blocks: on a school day,
    # push an overlapping planning window to start after school (15:30).
    # Free-gap aware: a window fully inside a free period (lunch 12:35-13:20,
    # "Ara"/"Boş" gaps from PERIOD_SLOTS) is allowed as-is so students CAN
    # study at lunch. Anything overlapping a teaching period is clamped.
    # Note: sub-45-min gaps can't fit a full study_min block, which is why we
    # don't try to pack the whole plan into scattered free periods.
    school_clamped = False
    if day_status == "school":
        free_gaps = [
            (a, b) for (a, b, label) in PERIOD_SLOTS
            if label in ("Ara", "Boş", "Öğle Yemeği")
        ]
        in_free_gap = any(s >= a and e <= b and e - s >= 30 for (a, b) in free_gaps)
        if not in_free_gap and s < SCHOOL_END and e > SCHOOL_START:
            length = e - s
            s = max(s, SCHOOL_END)
            e = min(s + length, 24 * 60 - 1)
            if e - s < 30:
                e = min(s + 30, 24 * 60 - 1)
            school_clamped = True
        if e <= s:
            return None, "Okul günü çalışma penceresi 15:30 sonrasına kaydırılamıyor — pencereyi güncelle."

    mode = mode if mode in MODE_LABELS else "practice"
    criterion = criterion if criterion in CRITERIA else "A"
    duration_h = max(1, int(duration_h or 0))

    items = []
    for t in topics:
        conf = str(t.get("confidence") or "yellow")
        if conf not in CONFIDENCES:
            conf = "yellow"
        items.append({"subject": str(t.get("subject")), "topic": str(t.get("topic")), "confidence": conf})

    # Every selected topic is scheduled in the order the user picked them, as
    # a study_min block separated by break_min breaks (both from settings).
    # Nothing is capped, dropped, hidden or limited by the focus window: if the
    # total duration exceeds the window, the master timeline extends past it.
    blocks = []
    cursor = s
    for it in items:
        if blocks:
            blocks.append({
                "id": uuid.uuid4().hex[:12],
                "start": cursor,
                "end": cursor + break_min,
                "time": f"{_hhmm(cursor)}-{_hhmm(cursor + break_min)}",
                "duration": break_min,
                "origDuration": break_min,
                "type": "break",
                "subject": "",
                "topic": "Mola",
                "status": "pending",
                "note": f"Mola: su + zihni boşaltma ({break_min} dk)",
            })
            cursor += break_min
        cursor_end = cursor + study_min
        blocks.append({
            "id": uuid.uuid4().hex[:12],
            "start": cursor,
            "end": cursor_end,
            "time": f"{_hhmm(cursor)}-{_hhmm(cursor_end)}",
            "duration": study_min,
            "origDuration": study_min,
            "type": "study",
            "subject": it["subject"],
            "topic": it["topic"],
            "confidence": it["confidence"],
            "status": "pending",
            "active": False,
            "elapsed": 0,
            "note": f"{_conf_note(it['confidence'], study_min)} · {MODE_SUFFIX[mode]} · {CRITERION_SHORT[criterion]}",
        })
        cursor = cursor_end

    placed = sum(1 for b in blocks if b["type"] == "study")
    if not placed:
        return None, "En az bir konu seçmelisin."

    plan = {
        "id": uuid.uuid4().hex[:8],
        "created": datetime.now().strftime("%H:%M"),
        "created_at": int(time.time()),
        "day": today_key,
        "input": {
            "topics": items,
            "start": start,
            "end": end,
            "duration_h": duration_h,
            "mode": mode,
            "criterion": criterion,
        },
        "blocks": blocks,
        "meta": {
            "note": f"{study_min} dk odak blokları · {break_min} dk molalar",
            "mode": MODE_LABELS.get(mode, mode),
            "criterion": CRITERIA[criterion],
            "school": {
                "clamped_to_after_school": school_clamped,
                **_school_digest(today_key),
            },
        },
    }
    plan["stats"] = _stats(blocks)
    return plan, None


def drawer(doc):
    return {
        "weak": doc.get("weak", []),
        "tomorrow": doc.get("tomorrow", []),
    }


@app.get("/")
def index():
    return render_template("index.html")


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
            datetime.strptime(start, "%Y-%m-%d")
            datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            return jsonify({"status": "error", "message": "Tarih biçimi YYYY-AA-GG olmalı."}), 400
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
    plan, err = build_plan(
        topics,
        str(body.get("start") or "17:00"),
        str(body.get("end") or "19:00"),
        int(body.get("duration_h") or 4),
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
            offset = int(body.get("offset") or 0)
            if not plan:
                return jsonify({"status": "error", "message": "Önce bir plan oluştur."}), 400
            if offset:
                for b in plan["blocks"]:
                    b["start"] += offset
                    b["end"] += offset
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
            orig = int(block.get("origDuration") or block.get("duration") or 45)
            delta = int(body.get("delta", 5) or 5)
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
    hist = doc.get("history", [])
    today_dt = datetime.fromtimestamp(now_ts)
    week_monday = today_dt - timedelta(days=today_dt.weekday())
    day_keys = [(week_monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    day_keys_set = set(day_keys)

    plan = doc.get("plan")
    plan_blocks = plan.get("blocks", []) if isinstance(plan, dict) else []
    done_blocks = [
        b for b in plan_blocks
        if b.get("type") == "study" and b.get("status") == "done"
    ]

    # A stats reset wipes history and excludes blocks completed before it, so
    # analytics restart from zero even though the timeline stays untouched.
    reset_ts = int(doc.get("stats_reset_ts") or 0)
    if reset_ts:
        done_blocks = [
            b for b in done_blocks
            if int(b.get("done_ts") or 0) > reset_ts
        ]

    def _sum(entries, field):
        return sum(int(e.get(field, 0)) for e in entries)

    # Today: authoritative from live completed blocks (per-block sums, no
    # subject/topic collapse), falling back to the history log with no plan.
    use_plan_today = bool(done_blocks)
    if use_plan_today:
        today_min = sum(int(b.get("duration", 0)) for b in done_blocks)
        today_done = len(done_blocks)
        today_q = sum(int(b.get("questions") or 0) for b in done_blocks)
        today_p = sum(int(b.get("pages") or 0) for b in done_blocks)
    else:
        today_ents = [h for h in hist if h.get("day") == today]
        today_min = sum(int(h.get("minutes", 0)) for h in today_ents)
        today_done = len(today_ents)
        today_q = _sum(today_ents, "questions")
        today_p = _sum(today_ents, "pages")

    # 7-day series from history, with today overridden by live plan numbers.
    days = []
    for k in day_keys:
        ents = [h for h in hist if h.get("day") == k]
        days.append({
            "day": _weekday_short(k),
            "done": len(ents),
            "minutes": sum(int(h.get("minutes", 0)) for h in ents),
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
        if h.get("day") not in day_keys_set:
            continue
        if use_plan_today and h.get("day") == today:
            continue
        s = h.get("subject") or "Genel"
        row = subjects.setdefault(s, {"minutes": 0, "questions": 0, "pages": 0})
        row["minutes"] += int(h.get("minutes", 0))
        row["questions"] += int(h.get("questions", 0))
        row["pages"] += int(h.get("pages", 0))
    if use_plan_today:
        for b in done_blocks:
            s = b.get("subject") or "Genel"
            row = subjects.setdefault(s, {"minutes": 0, "questions": 0, "pages": 0})
            row["minutes"] += int(b.get("duration", 0))
            row["questions"] += int(b.get("questions") or 0)
            row["pages"] += int(b.get("pages") or 0)
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
                day = _day_key(int(time.time()) + offset * 86400)
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)