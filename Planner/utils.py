"""Shared time helpers (minutes-from-midnight clock + day keys) and the
minimum review-block size shared by storage and scheduler."""
from __future__ import annotations

from datetime import datetime


# Spaced-review/catch-up blocks are fixed human blocks too — never below this.
REVIEW_BLOCK: int = 25

# Minimum plannable window (minutes). Plans shorter than this are rejected.
MIN_PLAN_WINDOW_MIN: int = 30

# Seconds in a day; used to offset day keys for catch-up scheduling.
SECONDS_PER_DAY: int = 86400

# Clock bounds.
MINUTES_PER_DAY: int = 24 * 60


def _minutes(hhmm: str) -> int | None:
    """Parse ``"HH:MM"`` into minutes since midnight, else ``None``."""
    try:
        h, m = str(hhmm).strip().split(":")
        hh, mm = int(h), int(m)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return hh * 60 + mm
    except (ValueError, AttributeError):
        return None


def _hhmm(total: int) -> str:
    """Format minutes since midnight as ``"HH:MM"`` (wraps past midnight)."""
    try:
        total = int(total) % MINUTES_PER_DAY
    except (TypeError, ValueError):
        total = 0
    return f"{total // 60:02d}:{total % 60:02d}"


def _day_key(ts: int | float) -> str:
    """Return the local ``YYYY-MM-DD`` key for a unix timestamp."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _now_min() -> int:
    """Return current local time as minutes since midnight."""
    now = datetime.now()
    return now.hour * 60 + now.minute


def _weekday_short(daykey: str) -> str:
    """Return the Turkish 3-letter weekday for a ``YYYY-MM-DD`` key.

    Returns ``"?"`` for malformed input instead of raising, so analytics
    endpoints never 500 on corrupt history entries.
    """
    names = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
    try:
        return names[datetime.strptime(str(daykey), "%Y-%m-%d").weekday()]
    except (ValueError, TypeError):
        return "?"
