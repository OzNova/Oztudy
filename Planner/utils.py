"""Shared time helpers (minutes-from-midnight clock + day keys) and the
minimum review-block size shared by storage and scheduler."""
from datetime import datetime


# Spaced-review/catch-up blocks are fixed human blocks too — never below this.
REVIEW_BLOCK = 25


def _minutes(hhmm):
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _hhmm(total):
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def _day_key(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _now_min():
    now = datetime.now()
    return now.hour * 60 + now.minute


def _weekday_short(daykey):
    names = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
    return names[datetime.strptime(daykey, "%Y-%m-%d").weekday()]
