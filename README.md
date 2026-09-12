# Oztudy — Academic Strategy Planner

A daily academic planner for IB MYP Year 4 (Grade 9), packaged as a native
macOS desktop app with a lightweight Flask + vanilla JS/CSS frontend (`tr` UI).

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
- **Frontend:** single-file HTML/CSS/JS (no build step, Inter font, SVG icons)
- **Desktop shell:** pywebview (macOS WKWebView), with automatic browser fallback
- **Storage:** JSON data file (`Planner/userData/planner.json`)

## Getting started

Requires Python 3.

```bash
# 1. dependencies
pip install -r Planner/requirements.txt       # flask only
pip install pywebview                          # optional, for the native window

# 2. run in your browser
python3 Planner/app.py                         # http://127.0.0.1:5000

# 3. or run as a desktop app (macOS)
python3 Planner/run_desktop.py
#    (double-click Planner/Daily Planner.command also works)
```

The launcher frees port 5000 automatically before starting, writes logs to
`Planner/error.log`, and falls back to the default browser if pywebview is
unavailable.

## Data

User data (plan, timer state, XP, settings, statistics) is stored locally in
`Planner/userData/planner.json`. It is created on first run and is not part of
the repository.

## Project layout

```
Planner/
  app.py                        Flask backend (API + template rendering)
  templates/index.html          entire frontend (single page)
  static/                       icons and PWA assets
  run_desktop.py                pywebview launcher
  Daily Planner.command         double-click launcher entry point
  requirements.txt              python dependencies
  setup.py                      py2app packaging config
```

## License

MIT — see [LICENSE](LICENSE).