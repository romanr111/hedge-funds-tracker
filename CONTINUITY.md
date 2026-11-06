# Continuity ledger

State lives in repository-root `CONTINUITY.md`. At session start, read it before the first tool call when present. Never duplicate it across worktrees. When needed but missing, create it from `/Users/roman/.claude/templates/CONTINUITY.md`.

## Snapshot triggers

Rewrite the Snapshot after a file modification, decision, new/resolved blocker, or task completion/abandonment. Reading and searching do not trigger a rewrite.

## Prune on every rewrite

- Move completed items to Done (recent), or delete them.
- Mark stale facts `SUPERSEDED [date]`; delete them on the next rewrite.
- Remove answered questions and inactive working-set files.
- Keep receipts only for decisions, commits, PRs, failures, and unusual tool outcomes.
- If the file exceeds 120 lines, compress Done (recent) into milestone bullets.

Before Git mutations, reconcile the ledger with actual worktree, branch, and status output as defined in `~/.claude/references/git-and-github.md`.

## Snapshot

- Goal: Land the 13F reporting-source transitions feature on `main` via draft PR.
- Success criteria: branch `reporting-source-transitions` pushed, draft PR open, full local suite green on the new base, CI green, review approval.
- Current state: branch created from `origin/main` (8ccb1a7); feature work plus CodeGraph-removal edits restored onto the base; commits and draft PR pending.
- Next action: push branch, open draft PR, await review/CI.
- Open questions: None.
- Stale/superseded: CodeGraph-repair details below are historical receipts.

## Git context

- Repo root: /Users/roman/Documents/Development/Signals
- Working directory: /Users/roman/Documents/Development/Signals
- Branch: reporting-source-transitions (created from origin/main 8ccb1a7)
- Base branch: main
- Merge status: not assessed

## Worktree detail

- Worktree reason: primary checkout; sibling detached worktree exists at `/Users/roman/Documents/Development/hedge_funds_tracker-gdrive-export`.
- Ownership: reporting-source feature and CodeGraph retirement of this checkout.
- Conflicts: `data/signals.sqlite3` intentionally left uncommitted (local-run data preserved in the worktree); untracked `reports/` retained; stash `reporting-source feature WIP` retained (content fully applied).
- Cleanup proof:

## Working set

- signals/application/use_cases/notify_reporting_source_reviews.py (new)
- signals/application/use_cases/{sync_quarter_snapshots,track_manager,run_trend_engine,notify_*.py}
- signals/infrastructure/storage/sqlite_state_repository.py
- signals/config.py, signals/domain/{filings,models}.py
- tests/test_reporting_source_*.py (new), config/managers.json, README.md

## Done (recent)

- 2026-11-06 Self-QA of the uncommitted feature: read-only review, no edits; full suite 220 passed in 166.86s on the pre-rebase tree; only low-severity findings.
- 2026-11-06 Moved the uncommitted set onto origin/main 8ccb1a7: `.gitignore` merged as union, local DB conflict resolved from byte-exact backup, duplicate `now_fn` pinning in `tests/test_sync_quarter_snapshots.py` resolved to origin's identical version.
- 2026-07-26 [TOOL] Retired CodeGraph: removed the user-local installation, MCP registrations, local index, bootstrap scripts, and active routing instructions.
- 2026-05-24 Option trend tables landed after stash-restore and backfill/XLSX export gap fixes.

## Receipts

- 2026-11-06: origin/main ran 50 DB-chore commits plus workflow/`.gitignore`/test changes ahead of local `main` (e8c81bd); branch was created from 8ccb1a7, not the stale local tip.
- 2026-11-06: `pytest -q`: 220 passed in 166.86s on the pre-rebase tree; rerun on the new base recorded in the PR evidence.
- 2026-11-06: Local DB backup: /tmp/signals-preserv/signals-local-20261106.sqlite3 (7.8M, byte-matched after conflict resolution).
- 2026-07-25: CodeGraph index rebuild: 99 files, 1,608 nodes, 3,775 edges; global install attempts failed with EPERM (Homebrew Cellar, ~/.codegraph).
- 2026-06-15: Restored CLI heading fix for Actions run 27538230507; full local pipeline-equivalent pytest 194 passed in 15.30s.
