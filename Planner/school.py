"""School domain: timetable, academic calendar, exams, day digest."""
from datetime import datetime, timedelta

from utils import _hhmm, _weekday_short



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


def _next_exam(weeks: list[dict] | None, today: datetime.date | None = None) -> dict | None:
    """Return the nearest upcoming (or ongoing) exam week.

    ``days_until`` is clamped to 0 while an exam is ongoing and an explicit
    ``ongoing`` flag is set. Malformed entries (bad/missing dates) are
    skipped instead of raising.
    """
    from datetime import date as _date

    today = today or datetime.now().date()
    if isinstance(today, datetime):
        today = today.date()
    today_key = today.isoformat() if isinstance(today, _date) else str(today)
    best: dict | None = None
    for w in weeks or []:
        if not isinstance(w, dict):
            continue
        start = str(w.get("start") or "")
        end = str(w.get("end") or "")
        if not start or not end:
            continue
        try:
            start_d = datetime.strptime(start, "%Y-%m-%d").date()
            end_d = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            continue
        if end < today_key:
            continue
        if best is None or start < best["start"]:
            best = {"label": w.get("label") or "Sınav", "start": start, "end": end}
    if best is None:
        return None
    try:
        start_d = datetime.strptime(best["start"], "%Y-%m-%d").date()
        end_d = datetime.strptime(best["end"], "%Y-%m-%d").date()
    except ValueError:
        return None
    days = (start_d - today).days if isinstance(today, _date) else 0
    ongoing = days <= 0 and end_d >= today if isinstance(today, _date) else False
    best["days_until"] = max(0, days)
    best["ongoing"] = bool(ongoing)
    return best


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
