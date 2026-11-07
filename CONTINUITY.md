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
- Current state: draft PR #41 open (`reporting-source-transitions` -> `main`, branch from `origin/main` 8ccb1a7); local suite green on the branch (221 passed); awaiting review/CI.
- Next action: await PR review and CI; address feedback.
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

- 2026-11-06 [RESEARCH] Delivered US market-intelligence dashboard research report at `reports/2026-11-06-market-intelligence-research-uk.md`; three subagent web-research reports synthesized; values date-stamped 2026-11-06; no code, schema, or service changes; concurrent reporting-source work untouched.
- 2026-11-06 Self-QA of the uncommitted feature: read-only review, no edits; full suite 220 passed in 166.86s on the pre-rebase tree; only low-severity findings.
- 2026-11-06 Moved the uncommitted set onto origin/main 8ccb1a7: `.gitignore` merged as union, local DB conflict resolved from byte-exact backup, duplicate `now_fn` pinning in `tests/test_sync_quarter_snapshots.py` resolved to origin's identical version.
- 2026-07-26 [TOOL] Retired CodeGraph: removed the user-local installation, MCP registrations, local index, bootstrap scripts, and active routing instructions.
- 2026-05-24 Option trend tables landed after stash-restore and backfill/XLSX export gap fixes.

## Receipts

- 2026-11-07: PR #41 review round 2 (8bfe687 fix + a3a8b11 test): quarter-scoped review gates (latest trends + summary), resumption quarter-break against composite accession churn, no review creation on transient submissions fetch failure. Focused suites: 37 passed (26 seam + 11 CLI). Full suite: 239 passed in 1005.93s - merge gate passed.
- 2026-11-06: PR #41 review fixes (F1+F4): composite-source helpers centralized in `signals/domain/filing_sources.py` (34f229b); 13 seam tests added (891fd4d, 4 files); full suite 234 passed in 1005.92s on the branch.
- 2026-11-06: origin/main ran 50 DB-chore commits plus workflow/`.gitignore`/test changes ahead of local `main` (e8c81bd); branch was created from 8ccb1a7, not the stale local tip.
- 2026-11-06: Draft PR #41 opened: https://github.com/romanr111/hedge-funds-tracker/pull/41 (verified OPEN/draft, base main). Commits: 75354db chore codegraph, 7c47cb8 feat reporting-source, dfb8255 ledger.
- 2026-11-06: `pytest -q`: 220 passed in 166.86s on the pre-rebase tree; on the branch base: 221 passed in 1012.16s (slow run under concurrent subagent load).
- 2026-11-06: Local DB backup: /tmp/signals-preserv/signals-local-20261106.sqlite3 (7.8M, byte-matched after conflict resolution).
- 2026-07-25: CodeGraph index rebuild: 99 files, 1,608 nodes, 3,775 edges; global install attempts failed with EPERM (Homebrew Cellar, ~/.codegraph).
- 2026-06-15: Restored CLI heading fix for Actions run 27538230507; full local pipeline-equivalent pytest 194 passed in 15.30s.
