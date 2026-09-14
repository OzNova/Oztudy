# Oztudy — Academic Strategy Planner

A daily academic planner for IB MYP Year 4 (Grade 9), packaged as a native
macOS desktop app with a lightweight Flask + vanilla JS/CSS frontend (`tr` UI).

## Install

Requires Python 3.

### Homebrew (macOS)

```bash
brew trust oznova/oztudy   # one-time: third-party taps need explicit trust
brew tap OzNova/oztudy
brew install oztudy
oztudy
```

This installs a self-contained virtualenv plus an `oztudy` launcher. Your data
lives in `~/.oztudy` (override with the `OZTUDY_DATA_DIR` environment variable).
See [CHANGELOG](CHANGELOG.md) for what's in each release.

### Manual

```bash
# 1. dependencies
pip install -r Planner/requirements.txt       # flask + psutil
pip install pywebview                          # optional, for the native window

# 2. run in your browser
python3 Planner/app.py                         # http://127.0.0.1:5000

# 3. or run as a desktop app (macOS)
python3 Planner/run_desktop.py
#    (double-click Planner/Daily Planner.command also works)
```

## Features

- **IB MYP curriculum topics** — subject and topic picker organized around the
  standard MYP Year 4 units (Math, Sciences, English, etc.)
- **Plan builder** — pick topics, set your study window and session length, and
  generate a structured plan with alternating focus blocks and breaks
- **Focus timer** — per-block timer with start / pause / resume, +5 / -5 minute
  adjustments and "Erken Bitir" (finish early). Pausing preserves the session —
  stopping never resets progress and never auto-starts the next block
- **Zen mode** — fullscreen focus overlay with ambient audio (Lo-Fi Beats,
  Deep Space), an abandon-confirmation prompt, and building-damage consequences
  for quitting early
- **Unforgetting / Weak topics** — marks "Zorlanıyorum" (struggling) topics and
  blocks pushed to tomorrow; spaced-review labels on regenerated study plans
- **Catch-Up mode** — detects overdue topics and redistributes them across the
  next days
- **Statistics** — daily and weekly focus totals, completed blocks, weekly chart
- **Settings** — light / dark theme, focus and break durations, daily study
  window and total hours; settings are persisted and applied when a plan is
  rebuilt
- **School strip** — shows DERS GÜNÜ (school day), weekend programme, or holiday
  status, plus a weekly streak counter

## Tech stack

- **Backend:** Python 3 + Flask
- **Frontend:** HTML shell + separate CSS/JS (`templates/index.html`,
  `static/app.js`, `static/style.css`; no build step, Inter font, SVG icons)
- **Desktop shell:** pywebview (macOS WKWebView), with automatic browser fallback
- **Storage:** SQLite (`planner.db`, WAL mode) with one-time JSON migration

## Getting started

```bash
# check your installed version any time
python3 Planner/run_desktop.py --version
# or open http://127.0.0.1:5000/api/version while the app runs
```

The launcher frees port 5000 automatically before starting, writes logs to
`error.log` (next to the app, or inside `OZTUDY_DATA_DIR` when set), and falls
back to the default browser if pywebview is unavailable. Launcher logs use a
rotating file handler (512 KB × 3 backups); the Flask child process appends to
the same path.

## Development

```bash
# run the backend with live reload off (binds 127.0.0.1:5000, no auth)
python3 Planner/app.py

# run the unit tests (stdlib only, isolated temp store for API tests)
python3 -m unittest discover -s Planner/tests -v
# or: cd Planner && python3 -m unittest discover -s tests -v
```

Python 3.10+ is required (uses `X | Y` type syntax and builtin generics).

## API reference

Base URL `http://127.0.0.1:5000`. All responses are JSON with
`{"status": "success" | "error", ...}`. No authentication; local-only bind.

| Method | Path | Body / Query | Success |
|---|---|---|---|
| GET | `/` | — | HTML shell |
| GET | `/api/version` | — | `{name, version}` |
| GET | `/api/plan` | — | `{plan \| null}` |
| POST | `/api/plan` | `{topics:[{subject,topic,confidence}], start, end, duration_h, mode, criterion}` | `{plan, weak, tomorrow}` |
| GET | `/api/school` | — | `{school: digest}` |
| GET | `/api/settings` | — | `{settings}` |
| POST | `/api/settings` | `{study_min, break_min, win_start, win_end, duration_h, theme}` (partial ok) | `{settings}` (clamped) |
| GET | `/api/exams` | — | `{exams, next:{label,start,end,days_until,ongoing}}` |
| POST | `/api/exams` | `{exams:[{id,label,start,end}]}` dates `YYYY-MM-DD`, `start <= end` | `{exams, next}` or 400 |
| GET | `/api/habits` | — | `{habits, track, days, today, streaks}` |
| POST | `/api/habits/toggle` | `{id, date: YYYY-MM-DD}` | `{track, today, streaks}` |
| POST | `/api/habits/save` | `{habits:[{label, time: HH:MM}]}` | `{habits, track, days, today, streaks}` or 400 |
| POST | `/api/blocks` | `{id, status: done\|pending}` or `{clear:true}` | `{plan}` |
| POST | `/api/blocks/adjust` | `{action: start\|stop\|reset\|extend\|done\|push\|restore\|shift, id?, delta?, offset?}` | `{plan, ...}` |
| POST | `/api/blocks/metrics` | `{id, questions?, pages?}` | `{plan}` |
| GET | `/api/stats` | — | `{today, week, subjects, days}` |
| POST | `/api/stats/reset` | — | `{status}` |
| GET | `/api/report?range=week\|month\|all` | query `range` | `{range, totals, days, subjects, topics, highlights}` |
| GET | `/api/game` | — | `{xp, level, xp_next, base_modules, base_health, badges, streak}` |
| GET | `/api/drawer` | — | `{weak, tomorrow}` |
| GET/POST | `/api/weak` | `{subject, topic, remove?}` | `{weak}` |
| GET/POST | `/api/tomorrow` | `{subject, topic, remove?}` | `{tomorrow}` |
| GET | `/api/reviews` | — | `{reviews: pending-today}` |
| POST | `/api/reviews/schedule` | — | `{plan, message}` |
| GET | `/api/overdue` | — | `{overdue, count}` |
| POST | `/api/catchup` | — | `{plan, summary}` |
| GET | `/api/export` | — | `{version, exported_at, data}` |

Notes:

- Study blocks use the configured `study_min` (default 45) and breaks use
  `break_min` (default 10); `POST /api/plan` extends the timeline past the
  window rather than dropping topics.
- On school days an overlapping window is clamped to start at 15:30, except a
  window fully inside a free gap (lunch 12:35–13:20, `Ara`/`Boş`), which is
  kept as-is.
- `days_until` is clamped to 0 while an exam is ongoing (`ongoing: true`).
- Streaks (game + habits) reset to 0 when the last active day is older than
  yesterday.

## Data integrity

- SQLite WAL store with one atomic `BEGIN IMMEDIATE` transaction per save;
  crash mid-save rolls back instead of corrupting data.
- All Flask reads and writes share a reentrant `LOCK`; per-connection busy
  timeout (5 s) avoids torn reads.
- `_repair_plan()` backfills `day`/`created_at` and resets invalid
  `type`/`status`/`confidence`/`duration` values to defaults.
- Corrupt legacy JSON is backed up as `planner.json.corrupt.*`, never silently
  dropped.

## Data

User data (plan, timer state, XP, settings, statistics) is stored locally in
SQLite (`Planner/userData/planner.db`, or `$OZTUDY_DATA_DIR` when set). On
first run after upgrading from an older version, the legacy
`planner.json` — if present — is migrated automatically and archived as
`planner.json.migrated.*`. The data directory is created on first run and is
not part of the repository.

## Project layout

```
Planner/
  app.py                        Flask routes only (API + template rendering)
  utils.py                      time helpers (_minutes/_hhmm/_day_key/...) + constants
  school.py                     timetable, academic calendar, exams (_next_exam, ...)
  gamification.py               XP, badges, streaks, habits
  scheduler.py                  plan builder (build_plan), catch-up, timeline ops
  storage.py                    SQLite store + JSON migration + rollover + repair
  version.py                    release version (single source of truth)
  tests/test_core.py            stdlib unittest suite (28 tests, no extra deps)
  templates/index.html          page shell (loads static/app.js + style.css)
  static/app.js / style.css     frontend logic and styles
  static/                       icons and PWA assets
  run_desktop.py                pywebview launcher (oztudy entry point)
  Daily Planner.command         double-click launcher entry point
  requirements.txt              python dependencies
  setup.py                      py2app packaging config (reads version.py)
  CHANGELOG.md                    release notes (Keep a Changelog)
```
The Homebrew formula lives in the [homebrew-oztudy tap](https://github.com/OzNova/homebrew-oztudy)
and is updated on each release (see its README for the maintainer checklist).

## Versioning

Releases follow [Semantic Versioning](https://semver.org). The version in
`Planner/version.py` is shown in the app header and served at
`/api/version`. User-facing changes for every release are listed in
[CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).