import json
import os
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

CONF_BLOCKS = {"red": 60, "yellow": 45, "green": 30}
CONF_RANK = {"red": 0, "yellow": 1, "green": 2}
CONF_TAGS = {
    "red": "Zorlanıyorum / Deep Focus",
    "yellow": "Orta / Practice",
    "green": "Hakimim / Quick Review",
}
CONF_NOTE = {
    "red": "60 dk derin odak + aktif hatırlama: kapat-anlat, zorlanan noktayı işaretle",
    "yellow": "45 dk pratik: soru çözümü + yanlış analizi",
    "green": "30 dk hızlı tekrar: özet tara + kavram kartları",
}

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
    except (OSError, ValueError):
        return {}


def _save_doc(doc):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)


def _load_plan():
    plan = _load_doc().get("plan")
    return plan if isinstance(plan, dict) else None


def _stats(blocks):
    active = [b for b in blocks if b["type"] == "study" and b.get("status") != "pushed"]
    total = sum(b["duration"] for b in active)
    done = sum(b["duration"] for b in active if b["status"] == "done")
    return {"total_min": total, "done_min": done, "done_count": sum(1 for b in active if b["status"] == "done")}


def _repair_plan(plan):
    for b in plan.get("blocks", []):
        window = max(1, b.get("end", 0) - b.get("start", 0))
        b["duration"] = window
        b["origDuration"] = window
        b["active"] = False
        b.pop("started_at", None)


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
    hist = doc.setdefault("history", [])
    for h in hist:
        if h.get("day") == day and h.get("subject") == subj and h.get("topic") == topic:
            h["minutes"] = minutes
            if q or p:
                h["questions"] = q
                h["pages"] = p
            _game_record_day(doc, day)
            _game_end(doc, block)
            return
    hist.append({
        "day": day, "subject": subj, "topic": topic,
        "minutes": minutes, "questions": q, "pages": p, "ts": now_ts,
    })
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
    while xp >= _xp_for_level(lvl):
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


def _compute_streak(g):
    days = sorted(g.get("history_days", []), reverse=True)
    if not days:
        return 0
    streak = 1
    cur = datetime.strptime(days[0], "%Y-%m-%d")
    for i in range(1, len(days)):
        prev = datetime.strptime(days[i], "%Y-%m-%d")
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
    if streak >= 3:
        known.add("Comet Chaser")
    if int(g.get("xp", 0)) >= 500:
        known.add("Star Navigator")
    if int(g.get("base_modules", 0)) >= 8:
        known.add("Outpost Architect")
    g["badges"] = sorted(known)


def _now_min():
    now = datetime.now()
    return now.hour * 60 + now.minute


def _weekday_short(daykey):
    names = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
    return names[datetime.strptime(daykey, "%Y-%m-%d").weekday()]


def _day_status(daykey):
    d = datetime.strptime(daykey, "%Y-%m-%d")
    if d.weekday() >= 5:
        return "weekend", "Hafta sonu"
    if daykey in NO_SCHOOL_DAYS:
        return "holiday", NO_SCHOOL_DAYS[daykey]
    for start, end, label in NO_SCHOOL_RANGES:
        if start <= daykey <= end:
            return "holiday", label
    return "school", "Okul günü"


def _subjects_for_day(daykey):
    d = datetime.strptime(daykey, "%Y-%m-%d")
    status, label = _day_status(daykey)
    if status != "school":
        return [], status, label
    return TIMETABLE.get(d.weekday(), []), status, label


def _next_school_day(daykey, step=1):
    cur = datetime.strptime(daykey, "%Y-%m-%d")
    for _ in range(40):
        cur += timedelta(days=step)
        key = cur.strftime("%Y-%m-%d")
        if _day_status(key)[0] == "school":
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


def _rollover(doc):
    today = _day_key(int(time.time()))
    prev = doc.get("day")
    if prev == today:
        return False
    if prev:
        plan = doc.get("plan")
        if isinstance(plan, dict):
            for b in plan.get("blocks", []):
                if b["type"] != "study" or b.get("status") == "done":
                    continue
                if b.get("isReview"):
                    _add_missed(doc, {
                        "subject": b["subject"], "topic": b["topic"] + " (tekrar)",
                        "day": prev, "minutes": b.get("duration", 15),
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
                    "day": r.get("targetDay"), "minutes": 15,
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
        end = cursor + int(s.get("minutes", 30))
        blocks.append({
            "id": uuid.uuid4().hex[:12],
            "start": cursor,
            "end": end,
            "time": f"{_hhmm(cursor)}-{_hhmm(end)}",
            "duration": int(s.get("minutes", 30)),
            "origDuration": int(s.get("minutes", 30)),
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

    today_key = _day_key(int(time.time()))
    day_status, day_label = _day_status(today_key)
    today_subjects, _, _ = _subjects_for_day(today_key)
    next_key = _next_school_day(today_key)
    tomorrow_subjects, _, _ = _subjects_for_day(next_key)

    # School hours 08:00-15:30 are fixed non-study blocks: on a school day,
    # push an overlapping planning window to start after school (15:30).
    school_clamped = False
    if day_status == "school":
        if s < SCHOOL_END and e > SCHOOL_START:
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
        if conf not in CONF_BLOCKS:
            conf = "yellow"
        items.append({"subject": str(t.get("subject")), "topic": str(t.get("topic")), "confidence": conf})

    # Prioritize homework/revision for subjects taught today or the next school
    # day (TIMETABLE values are already canonical codes), tie-broken by
    # confidence rank (red > yellow > green).
    today_codes = set(today_subjects)
    tomorrow_codes = set(tomorrow_subjects)
    for it in items:
        code = _subject_code(it["subject"])
        if code in today_codes:
            it["urgency"] = 0
        elif code in tomorrow_codes:
            it["urgency"] = 1
        else:
            it["urgency"] = 2
    items.sort(key=lambda it: (it["urgency"], CONF_RANK.get(it["confidence"], 1)))

    n = len(items)
    window = e - s
    dst = duration_h * 60
    break_total = BREAK_MIN * (n - 1)
    total_base = sum(CONF_BLOCKS[it["confidence"]] for it in items)
    required_minutes = total_base + break_total
    MIN_BLOCK = 5

    scaled = False
    if n == 1:
        durs = [min(total_base, dst, window)]
        if durs[0] < total_base:
            scaled = True
    elif total_base + break_total <= window and total_base <= dst:
        durs = [CONF_BLOCKS[it["confidence"]] for it in items]
    else:
        scaled = True
        cap_study = min(dst, window - break_total)
        if cap_study < MIN_BLOCK * n:
            cap_study = min(dst, window)
            if cap_study < MIN_BLOCK * n:
                cap_study = MIN_BLOCK * n
        scale = (cap_study / total_base) if total_base else 1.0
        min_base = min(CONF_BLOCKS[it["confidence"]] for it in items)
        if min_base * scale < MIN_BLOCK:
            scale = min(1.0, MIN_BLOCK / min_base)
        durs = [max(MIN_BLOCK, int(round(b * scale))) for b in (CONF_BLOCKS[it["confidence"]] for it in items)]
        while sum(durs) > dst:
            j = max(range(n), key=lambda k: durs[k])
            if durs[j] <= MIN_BLOCK:
                break
            durs[j] -= 1

    blocks = []
    cursor = s
    used = 0
    for idx, it in enumerate(items):
        blk = durs[idx]
        if blk:
            if cursor + blk > e:
                blk = max(1, e - cursor)
            if blk:
                blocks.append({
                    "id": uuid.uuid4().hex[:12],
                    "start": cursor,
                    "end": cursor + blk,
                    "time": f"{_hhmm(cursor)}-{_hhmm(cursor + blk)}",
                    "duration": blk,
                    "origDuration": blk,
                    "type": "study",
                    "subject": it["subject"],
                    "topic": it["topic"],
                    "confidence": it["confidence"],
                    "status": "pending",
                    "active": False,
                    "elapsed": 0,
                    "note": f"{CONF_NOTE[it['confidence']]} · {MODE_SUFFIX[mode]} · {CRITERION_SHORT[criterion]}",
                })
                used += blk
                cursor += blk
        if idx < n - 1:
            nxt_blk = durs[idx + 1] if idx + 1 < n else 0
            if blk and cursor + BREAK_MIN + nxt_blk <= e:
                blocks.append({
                    "id": uuid.uuid4().hex[:12],
                    "start": cursor,
                    "end": cursor + BREAK_MIN,
                    "time": f"{_hhmm(cursor)}-{_hhmm(cursor + BREAK_MIN)}",
                    "duration": BREAK_MIN,
                    "origDuration": BREAK_MIN,
                    "type": "break",
                    "subject": "",
                    "topic": "Mola",
                    "status": "pending",
                    "note": "Kısa mola: su + zihni boşaltma",
                })
                cursor += BREAK_MIN

    scheduled = {b["topic"] for b in blocks if b["type"] == "study"}
    dropped = [f"{it['subject']}: {it['topic']}" for it in items if it["topic"] not in scheduled]
    note_parts = [f"{len(blocks) - sum(1 for b in blocks if b['type']=='break')} blok · {used} dk odak"]
    if scaled:
        note_parts.insert(0, f"⚠ {n} konu için en az {required_minutes / 60:.1f} saat gerekli — bloklar ölçeklendi")
    if dropped:
        note_parts.append("Sığmayan: " + ", ".join(dropped))
    if school_clamped:
        note_parts.append("🏫 Okul sonrasına alındı (15:30 sonrası)")
    if day_status == "school" and today_subjects:
        tsub = ", ".join(today_subjects)
        nsub = ", ".join(tomorrow_subjects) if tomorrow_subjects else "—"
        note_parts.append(f"📚 Bugün: {tsub} · Yarın: {nsub}")
    if day_status in ("holiday", "weekend"):
        note_parts.append(f"🌤 {day_label} — tüm gün serbest")

    plan = {
        "id": uuid.uuid4().hex[:8],
        "created": datetime.now().strftime("%H:%M"),
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
            "note": " · ".join(note_parts),
            "dropped": dropped,
            "mode": MODE_LABELS.get(mode, mode),
            "criterion": CRITERIA[criterion],
            "school": {
                "academic_year": ACADEMIC_YEAR,
                "date": today_key,
                "day": _weekday_short(today_key),
                "status": day_status,
                "status_label": day_label,
                "school_day": day_status == "school",
                "school_window": f"{_hhmm(SCHOOL_START)}-{_hhmm(SCHOOL_END)}",
                "lunch": f"{_hhmm(LUNCH[0])}-{_hhmm(LUNCH[1])}",
                "clamped_to_after_school": school_clamped,
                "subjects_today": today_subjects,
                "subjects_next_day": tomorrow_subjects,
                "periods": _period_table(datetime.strptime(today_key, "%Y-%m-%d").weekday()),
            },
            "fit": {
                "scaled": scaled,
                "topic_count": n,
                "required_minutes": required_minutes,
                "required_hours": round(required_minutes / 60, 1),
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
    return jsonify({"status": "success", "plan": _load_plan()})


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
                block["duration"] = orig
        elif action == "extend":
            orig = int(block.get("origDuration") or block.get("duration") or 45)
            block["duration"] = min(
                int(block.get("duration", orig)) + 5, orig + 60
            )
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
    return jsonify({"status": "success", **drawer(_load_doc())})


@app.get("/api/weak")
def get_weak():
    return jsonify({"status": "success", "weak": _load_doc().get("weak", [])})


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
    return jsonify({"status": "success", "tomorrow": _load_doc().get("tomorrow", [])})


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
            end = start + 15
            time_str = f"{_hhmm(start)}-{_hhmm(end)}"
            blocks.append({
                "id": blk_id,
                "start": start,
                "end": end,
                "time": time_str,
                "duration": 15,
                "origDuration": 15,
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
                    "day": day, "minutes": max(15, min(int(it.get("minutes") or 30), 60)),
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