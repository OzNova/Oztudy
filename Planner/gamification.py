"""Gamification + habits: XP, levels, badges, streaks, habit tracking."""
from datetime import datetime, timedelta



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
