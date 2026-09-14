"""Study-plan scheduler: build_plan, catch-up, timeline ops, history.

Study blocks use the user-configured ``study_min`` setting (default 45 min)
and breaks use ``break_min`` (default 10 min); there are no fixed-duration
constants beyond :data:`utils.REVIEW_BLOCK` for spaced reviews.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from gamification import _check_badges, _game, _game_end, _game_record_day
from school import PERIOD_SLOTS, SCHOOL_END, SCHOOL_START, _day_status, _school_digest
from storage import _load_doc, _settings_for
from utils import MIN_PLAN_WINDOW_MIN, REVIEW_BLOCK, SECONDS_PER_DAY, _day_key, _hhmm, _minutes, _now_min



CONFIDENCES = ("red", "yellow", "green")


CONF_RANK = {"red": 0, "yellow": 1, "green": 2}


# === FEATURE 1: Dynamic Topic Confidence ===
# One-step confidence transitions driven by post-session difficulty feedback.
FEEDBACK_LEVELS = ("easy", "medium", "hard")

_FEEDBACK_STEP = {
    "easy": {"red": "yellow", "yellow": "green", "green": "green"},
    "medium": {"red": "red", "yellow": "yellow", "green": "green"},
    "hard": {"red": "red", "yellow": "red", "green": "yellow"},
}


def _topic_key(subject: str, topic: str) -> str:
    """Stable map key for per-topic confidence (``"subject|topic"``)."""
    return f"{str(subject or '').strip()}|{str(topic or '').strip()}"


def _apply_feedback_step(confidence: str, difficulty: str) -> str:
    """Return the confidence after one feedback step (never raises)."""
    table = _FEEDBACK_STEP.get(difficulty)
    if table is None:
        return confidence if confidence in CONFIDENCES else "yellow"
    return table.get(confidence, "yellow")


def apply_block_feedback(doc: dict, block: dict, difficulty: str) -> str:
    """Apply difficulty feedback to a study block and persist it.

    Updates ``block["confidence"]`` one step, stamps
    ``block["last_feedback"]``, and records the result in
    ``doc["topic_confidence"]`` so future plans pick it up. Returns the
    new confidence. Raises ``ValueError`` on bad difficulty and
    ``LookupError`` when the block is not a study block.
    """
    if difficulty not in FEEDBACK_LEVELS:
        raise ValueError(f"Geçersiz zorluk: {difficulty!r}.")
    if not isinstance(block, dict) or block.get("type") != "study":
        raise LookupError("Geri bildirim yalnızca çalışma blokları için.")
    new_conf = _apply_feedback_step(block.get("confidence", "yellow"), difficulty)
    block["confidence"] = new_conf
    block["last_feedback"] = difficulty
    subj, topic = block.get("subject") or "", block.get("topic") or ""
    if subj and topic:
        tmap = doc.setdefault("topic_confidence", {})
        if isinstance(tmap, dict):
            tmap[_topic_key(subj, topic)] = new_conf
    return new_conf


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


def _block_duration(block: dict) -> int:
    """Return a validated non-negative study duration for a block.

    Non-numeric, missing, or negative values are treated as 0 so a single
    corrupt block can never crash stats or analytics.
    """
    try:
        return max(0, int(block.get("duration") or 0))
    except (TypeError, ValueError):
        return 0


def _stats(blocks: list[dict] | None) -> dict[str, int]:
    """Aggregate study time over ``blocks``, ignoring breaks/pushed items.

    Defensive: blocks may come from older or hand-edited stores, so every
    field is accessed via ``.get()`` and durations are validated. Only
    ``type == "study"`` blocks count toward totals.
    """
    if not isinstance(blocks, list):
        return {"total_min": 0, "done_min": 0, "done_count": 0}
    total = 0
    done = 0
    done_count = 0
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") != "study":
            continue
        if b.get("status") == "pushed":
            continue
        dur = _block_duration(b)
        total += dur
        if b.get("status") == "done":
            done += dur
            done_count += 1
    return {"total_min": total, "done_min": done, "done_count": done_count}


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


def _create_reviews(doc, block):
    reviews = doc.setdefault("reviews", [])
    now_ts = int(time.time())
    today = _day_key(now_ts)
    existing = {(r["subject"], r["topic"], r["targetDay"]) for r in reviews}
    offsets = [(3, "Hızlı Tekrar"), (7, "Kendini Sınama")]
    for days, label in offsets:
        target = _day_key(now_ts + days * SECONDS_PER_DAY)
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


def _block_start_hour(block: dict) -> int:
    """Return the block's start hour (0-23), or -1 when unknown.

    Never raises; corrupt ``start`` values yield -1 so analytics can skip
    the entry instead of crashing.
    """
    try:
        if block.get("start") is None:
            return -1
        hour = int(block.get("start") or 0) // 60
        return hour if 0 <= hour <= 23 else -1
    except (TypeError, ValueError):
        return -1


def _log_abandoned(doc: dict, block: dict) -> dict | None:
    """Record a stopped-without-completion study block for peak mapping.

    Returns the entry, or ``None`` when the block has no trackable progress
    (not a study block, already done, or ``elapsed`` <= 0).
    """
    if not isinstance(block, dict) or block.get("type") != "study":
        return None
    if block.get("status") == "done":
        return None
    try:
        elapsed = int(block.get("elapsed") or 0)
    except (TypeError, ValueError):
        elapsed = 0
    if elapsed <= 0:
        return None
    now_ts = int(time.time())
    entry = {
        "day": _day_key(now_ts),
        "subject": block.get("subject") or "Genel",
        "topic": block.get("topic") or "",
        "start_hour": _block_start_hour(block),
        "ts": now_ts,
    }
    abandoned = doc.setdefault("abandoned", [])
    if isinstance(abandoned, list):
        abandoned.append(entry)
        if len(abandoned) > 500:
            del abandoned[: len(abandoned) - 500]
        return entry
    return None


def _log_history(doc, block):
    now_ts = int(time.time())
    block["done_ts"] = now_ts
    day = _day_key(now_ts)
    subj = block.get("subject") or "Genel"
    topic = block.get("topic") or ""
    minutes = max(1, int(block.get("duration") or (block.get("end", 0) - block.get("start", 0)) or 1))
    q = max(0, int(block.get("questions") or 0))
    p = max(0, int(block.get("pages") or 0))
    start_hour = _block_start_hour(block)
    already_awarded = bool(block.get("xp_awarded"))
    if not already_awarded:
        block["xp_awarded"] = True
    hist = doc.setdefault("history", [])
    for h in hist:
        if h.get("day") == day and h.get("subject") == subj and h.get("topic") == topic:
            h["minutes"] = minutes
            h["start_hour"] = start_hour
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
        "start_hour": start_hour,
    })
    if already_awarded:
        return
    _game_record_day(doc, day)
    _game_end(doc, block)


def _gather_overdue(doc: dict) -> list[dict]:
    """Collect overdue study items from missed/scheduled queues and the live plan.

    Defensive: legacy docs may miss keys, so every block field is read via
    ``.get()`` and skipped when subject/topic are absent.
    """
    today = _day_key(int(time.time()))
    items: list[dict] = []
    seen: set[tuple] = set()

    def push(subject, topic, minutes, day, source, confidence):
        subject = str(subject or "").strip()
        topic = str(topic or "").strip()
        if not subject or not topic:
            return
        key = (subject, topic)
        if key in seen:
            return
        seen.add(key)
        try:
            minutes = max(1, int(minutes or 30))
        except (TypeError, ValueError):
            minutes = 30
        if confidence not in CONFIDENCES:
            confidence = "yellow"
        items.append({
            "subject": subject, "topic": topic,
            "minutes": minutes, "day": day,
            "source": source, "confidence": confidence,
        })

    for m in doc.get("missed", []) or []:
        if not isinstance(m, dict):
            continue
        push(m.get("subject"), m.get("topic"), m.get("minutes", 30), m.get("day"), m.get("source", "plan"), m.get("confidence"))
    for it in doc.get("tomorrow", []) or []:
        if not isinstance(it, dict):
            continue
        push(it.get("subject"), it.get("topic"), 30, today, "push", "yellow")
    for e in doc.get("scheduled", []) or []:
        if not isinstance(e, dict):
            continue
        if e.get("day") and e.get("day") < today:
            push(e.get("subject"), e.get("topic"), e.get("minutes", 30), e.get("day"), "scheduled", e.get("confidence", "yellow"))
    plan = doc.get("plan")
    if isinstance(plan, dict):
        now_min = _now_min()
        for b in plan.get("blocks", []) or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") != "study" or b.get("status") == "done":
                continue
            try:
                end = b.get("end")
                if end is None or int(end) > now_min:
                    continue
            except (TypeError, ValueError):
                continue
            push(b.get("subject"), b.get("topic"), b.get("duration", 30), today, "plan", b.get("confidence", "yellow"))
    # FEATURE 1: hardest topics first so reds get scheduled before greens.
    items.sort(key=lambda it: CONF_RANK.get(it.get("confidence", "yellow"), 1))
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


def build_plan(
    topics: list[dict] | None,
    start: str,
    end: str,
    duration_h: int,
    mode: str,
    criterion: str,
) -> tuple[dict | None, str | None]:
    """Build a study plan of ``study_min`` focus blocks + ``break_min`` breaks.

    Returns ``(plan, None)`` on success or ``(None, error_message)`` on bad
    input. Block lengths come from user settings (see
    :data:`storage.DEFAULT_SETTINGS`); the school-day clamp only avoids the
    full 08:00–15:30 window except for free gaps (lunch/``Ara``/``Boş``) —
    scattered teaching periods are intentionally not packed (documented
    simplification).
    """
    if not topics:
        return None, "En az bir konu seçmelisin."
    s, e = _minutes(start), _minutes(end)
    if s is None or e is None:
        return None, "Geçerli bir saat formatı kullan (HH:MM)."
    if e <= s:
        return None, "Bitiş saati başlangıçtan sonra olmalı."
    if e - s < MIN_PLAN_WINDOW_MIN:
        return None, f"Zaman penceresi en az {MIN_PLAN_WINDOW_MIN} dakika olmalı."

    stored_doc = _load_doc()
    st = _settings_for(stored_doc)
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
        in_free_gap = any(s >= a and e <= b and e - s >= MIN_PLAN_WINDOW_MIN for (a, b) in free_gaps)
        if not in_free_gap and s < SCHOOL_END and e > SCHOOL_START:
            from utils import MINUTES_PER_DAY

            length = e - s
            s = max(s, SCHOOL_END)
            e = min(s + length, MINUTES_PER_DAY - 1)
            if e - s < MIN_PLAN_WINDOW_MIN:
                e = min(s + MIN_PLAN_WINDOW_MIN, MINUTES_PER_DAY - 1)
            school_clamped = True
        if e <= s:
            return None, "Okul günü çalışma penceresi 15:30 sonrasına kaydırılamıyor — pencereyi güncelle."

    mode = mode if mode in MODE_LABELS else "practice"
    criterion = criterion if criterion in CRITERIA else "A"
    duration_h = max(1, int(duration_h or 0))

    items = []
    # FEATURE 1: persisted feedback wins over the user-picked confidence so
    # a topic marked hard last time is scheduled as hard again.
    saved_conf = stored_doc.get("topic_confidence")
    saved_conf = saved_conf if isinstance(saved_conf, dict) else {}
    for t in topics:
        conf = str(t.get("confidence") or "yellow")
        if conf not in CONFIDENCES:
            conf = "yellow"
        remembered = saved_conf.get(_topic_key(t.get("subject"), t.get("topic")))
        if remembered in CONFIDENCES:
            conf = remembered
        items.append({"subject": str(t.get("subject")), "topic": str(t.get("topic")), "confidence": conf})
    # Schedule hardest topics first (red → yellow → green).
    items.sort(key=lambda it: CONF_RANK.get(it.get("confidence", "yellow"), 1))

    # Topics are scheduled hardest-first (red → yellow → green, stable for
    # ties) as study_min blocks separated by break_min breaks (both from
    # settings). Nothing is capped, dropped, hidden or limited by the focus
    # window: if the total duration exceeds the window, the master timeline
    # extends past it.
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
