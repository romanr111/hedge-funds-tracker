## Snapshot

- Goal: Keep the tracker docs aligned with the recovered Q1 trend workflow and current CLI/operator commands.
- Success criteria: README explains setup, DB selection, terminal-only trend viewing, tracker notification modes, workflow behavior, and the available tracker/script commands from current code.
- Current state: README was rewritten from current CLI help, scripts, config example, workflow, and the verified Q1 trend-table path, and the full virtualenv test suite passed.
- Next action: Review the final docs diff and decide whether to publish the README update.
- Open questions:
- Stale/superseded:

## Git context

- Repo root: /Users/roman/Documents/Development/hedge_funds_tracker
- Working directory: /Users/roman/Documents/Development/hedge_funds_tracker
- Branch: codex/fix-trend-summary-blockers
- Base branch: origin/main
- Merge status: not-merged

## Worktree detail

- Worktree reason: hotfix
- Ownership: config/managers.json, .github/workflows/13f-tracker.yml, README.md, CONTINUITY.md
- Conflicts:
- Cleanup proof:

## Working set

- config/managers.json
- .github/workflows/13f-tracker.yml
- README.md
- CONTINUITY.md

## Done (recent)

- Investigated current GitHub Actions runs, the last real tracker log, remote SQLite state, and SEC submissions evidence for Coatue.
- Corrected Coatue to the SEC 13F filer CIK and changed the workflow gate to use the triggered cron plus Kyiv offset instead of delayed wall-clock hour.
- Re-ran `git diff --check`, workflow YAML parsing, and manager JSON validation before publishing the manual-run branch.
- Published the fix branch, triggered manual Actions run `26247310908`, and fast-forwarded the workflow-pushed SQLite state update locally.
- Rewrote README around current setup, workflow/local DB split, terminal-only trend-table commands, tracker modes, utility scripts, and GitHub Actions commands.

## Receipts

- 2026-05-21: `CONTINUITY.md` was missing in the primary checkout at session start.
- 2026-05-21: Actions run `25996767780` reached the tracker and logged `pending_no_completed_quarter` with missing manager `Coatue Management LLC` for Q1 2026.
- 2026-05-21: SEC submissions confirmed Coatue 13F filings are under CIK `0001135730`; configured CIK `0001608624` is `BENEFITTER INSURANCE SOLUTIONS, INC.` with no recent 13F rows.
- 2026-05-21: `python3 -m pytest -q tests/test_config_manager_weights.py` failed because host Python has no `pytest`.
- 2026-05-21: Container fallback test attempt failed because the Docker daemon socket was unavailable.
- 2026-05-21: Workflow run `26247310908` passed GitHub-hosted pytest with `139 passed in 12.61s`.
- 2026-05-21: Workflow run `26247310908` synced Coatue CIK `0001135730`, computed `2026Q1` trends with 219 signals, and logged `Trend analysis summary notification sent`.
- 2026-05-21: README CLI flag inventory was checked against current `python -m tracker --help` and utility-script help output.
- 2026-05-21: `NOTIFIERS= DB_PATH=data/tracker.sqlite3 .venv/bin/python -m tracker --show-trends-only --trends-quarter 2026Q1` printed the saved Q1 trend table.
- 2026-05-21: `.venv/bin/python -m pytest -q` passed with `139 passed in 6.99s`.
