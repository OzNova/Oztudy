# Changelog

All notable changes to Oztudy are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The current version lives in `Planner/version.py` (`__version__`) and is
surfaced in the app header, `GET /api/version`, and `run_desktop.py --version`.

## [Unreleased]

### Changed

- Homebrew distribution moved to the
  [homebrew-oztudy tap](https://github.com/OzNova/homebrew-oztudy)
  (`brew tap OzNova/oztudy && brew install oztudy`); the in-repo
  `Formula/oztudy.rb` was removed to avoid drift. Note: Homebrew 6+ requires
  formulae to live in a tap, and third-party taps need a one-time
  `brew trust oznova/oztudy`.

## [1.0.1] - 2026-09-13

### Fixed

- Launcher no longer crashes when its directory is not writable (e.g. the
  Homebrew Cellar or its sandbox): the log file is opened lazily — after
  `--version` is handled — and logging degrades to stderr when the file
  cannot be opened.
- Launcher logs follow `OZTUDY_DATA_DIR` when it is set, so Homebrew installs
  keep `error.log` next to the data in `~/.oztudy` instead of the Cellar.

## [1.0.0] - 2026-09-13

First packaged release: Homebrew install, versioning, and changelog.

### Added

- SQLite persistence (`Planner/userData/planner.db`, WAL mode) with atomic
  transactions and a one-time automatic migration from `planner.json`
  (original archived as `planner.json.migrated.*`).
- Modular backend: `utils.py`, `school.py`, `gamification.py`, `scheduler.py`,
  `storage.py` — `app.py` is routes only with a compatible import surface.
- Split frontend: `templates/index.html` + `static/style.css` + `static/app.js`.
- Versioning: `Planner/version.py` single source of truth, `/api/version`
  endpoint, version badge in the app header, `--version` flag on the launcher,
  versioned `setup.py` bundle metadata.
- Homebrew formula (`Formula/oztudy.rb`): installs an `oztudy` launcher into a
  self-contained virtualenv (data kept in `~/.oztudy`, overridable via
  `OZTUDY_DATA_DIR`).
- Timer state persistence: the focus timer survives browser crashes/restarts
  via `localStorage` (saved on tick, `beforeunload`, and tab hide).
- UI/UX polish: responsive breakpoints (1180/920/560px), touch-sized targets,
  `:focus-visible` rings, skip link, `aria-live` status region, sticky timeline
  header, `prefers-reduced-motion` support, plan-reset confirmation, and a busy
  state on plan creation to prevent double submits.
- Cross-platform desktop launcher: `psutil`-based port cleanup with `lsof`
  fallback, and `webbrowser`-based fallback instead of macOS-only `open`.
- Free-gap-aware school clamping: a study window fully inside lunch
  (12:35–13:20) or a free period is kept as-is instead of forced past 15:30.

### Fixed

- Midnight rollover no longer wipes an active late-night session: plans carry
  `day`/`created_at`, and rollover is deferred until after 04:00.
- Infinite XP farming via Done-toggle: XP/base is granted once per block
  (`xp_awarded` flag; pre-fix done blocks backfilled).
- Silent data loss on corrupt writes: torn JSON is backed up as
  `planner.json.corrupt.*` instead of resetting to an empty store.
- Timeline overlap on "+5 min" extend/reset: duration changes now recalculate
  `end` and cascade-shift all later blocks.
- Stale streaks: game and habit streaks reset to 0 when the last active day is
  older than yesterday.
- Negative "days until" on ongoing exams: clamped to 0, `ongoing` flag drives
  the display.
- Break blocks now honor the user's `break_min` setting (previously hardcoded
  10 min while the cursor advanced by the setting), and confidence notes render
  the configured focus length. Removed dead `CONF_BLOCKS`/`STUDY_*` constants.
- All read endpoints now share the store lock (previously writes locked but
  reads did not); store lock is reentrant to allow load-inside-save paths.

### Changed

- `OZTUDY_DATA_DIR` environment variable overrides the data directory
  (default remains `Planner/userData/`).
- `requirements.txt` gains `psutil` for cross-platform port management.
- README documents Homebrew install, data layout, and project structure.

[Unreleased]: https://github.com/OzNova/Oztudy/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/OzNova/Oztudy/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/OzNova/Oztudy/releases/tag/v1.0.0
