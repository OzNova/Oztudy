# Oztudy — Academic Strategy Planner

A daily academic planner for IB MYP Year 4 (Grade 9), packaged as a native
macOS desktop app with a lightweight Flask + vanilla JS/CSS frontend (`tr` UI).

## Install

Requires Python 3.

### Homebrew (macOS)

```bash
brew install --formula https://raw.githubusercontent.com/OzNova/Oztudy/main/Formula/oztudy.rb
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
back to the default browser if pywebview is unavailable.

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
  app.py                        Flask backend (API + template rendering)
  utils.py / school.py          time helpers; timetable, calendar, exams
  gamification.py               XP, badges, streaks, habits
  scheduler.py                  plan builder, catch-up, timeline ops
  storage.py                    SQLite store + JSON migration + rollover
  version.py                    release version (single source of truth)
  templates/index.html          page shell (loads static/app.js + style.css)
  static/app.js / style.css     frontend logic and styles
  static/                       icons and PWA assets
  run_desktop.py                pywebview launcher (oztudy entry point)
  Daily Planner.command         double-click launcher entry point
  requirements.txt              python dependencies
  setup.py                      py2app packaging config (reads version.py)
Formula/oztudy.rb               Homebrew formula
CHANGELOG.md                    release notes (Keep a Changelog)
```

## Versioning

Releases follow [Semantic Versioning](https://semver.org). The version in
`Planner/version.py` is shown in the app header and served at
`/api/version`. User-facing changes for every release are listed in
[CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).