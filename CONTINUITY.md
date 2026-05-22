## Snapshot

- Goal: Improve merged Trend Output shortlist evidence with compact directional contributor names and a high-signal Appaloosa marker from recomputed stored payloads.
- Success criteria: Appaloosa config weight is `1.5`, future contributor payloads retain raw configured weights, CLI/script shortlist rows show up to three compact directional contributors, and high-signal markers appear only from stored raw weight evidence.
- Current state: The focused high-signal contributor patch is implemented on the contributor follow-up branch while local SQLite state stays uncommitted.
- Next action: Review and publish the contributor follow-up when requested.
- Open questions:
- Stale/superseded:

## Git context

- Repo root: /Users/roman/Documents/Development/hedge_funds_tracker
- Working directory: /Users/roman/.codex/worktrees/trend-output-v2/hedge_funds_tracker
- Branch: codex/trend-shortlist-contributors
- Base branch: merged Trend Output v2 feature tip
- Merge status: not-merged

## Worktree detail

- Worktree reason: feature isolation from dirty primary docs checkout
- Ownership: trend idea selection, trend output/rendering, evaluation script/use case, related tests, README.md, CONTINUITY.md
- Conflicts:
- Cleanup proof:

## Working set

- tracker/domain/trend_ideas.py
- tracker/interfaces/cli/main.py
- tracker/domain/trend_telegram_message.py
- tracker/domain/trend_presentation.py
- tracker/application/use_cases/evaluate_trend_ideas.py
- scripts/show_trends.py
- scripts/evaluate_trend_ideas.py
- config/cusip_tickers.json
- config/managers.json
- tests/test_default_tracking_config.py
- tests/test_trend_ideas.py
- tests/test_evaluate_trend_ideas.py
- README.md
- CONTINUITY.md

## Done (recent)

- Investigated current GitHub Actions runs, the last real tracker log, remote SQLite state, and SEC submissions evidence for Coatue.
- Corrected Coatue to the SEC 13F filer CIK and changed the workflow gate to use the triggered cron plus Kyiv offset instead of delayed wall-clock hour.
- Re-ran `git diff --check`, workflow YAML parsing, and manager JSON validation before publishing the manual-run branch.
- Published the fix branch, triggered manual Actions run `26247310908`, and fast-forwarded the workflow-pushed SQLite state update locally.
- Added shared promoted idea selection and switched CLI/script/Telegram shortlist paths to reduction-oriented long-term ranking while preserving raw diagnostics.
- Added trend explanation rendering and a historical evaluation use case/script for symbol, price, and forward-return coverage before future formula changes.
- Self-reviewed evaluation coverage windows and Telegram reduction wording, then added regression tests for both.
- Expanded the default CUSIP map with top SPY holdings identifiers, restored default CLI portfolio shares/breakdowns, switched freshness markers to ASCII, and added Situational Awareness LP.
- Added raw configured manager weight to future trend contributor payloads and compact high-signal Appaloosa evidence to CLI/script shortlist contributor labels.

## Receipts

- 2026-05-21: `CONTINUITY.md` was missing in the primary checkout at session start.
- 2026-05-21: Actions run `25996767780` reached the tracker and logged `pending_no_completed_quarter` with missing manager `Coatue Management LLC` for Q1 2026.
- 2026-05-21: SEC submissions confirmed Coatue 13F filings are under CIK `0001135730`; configured CIK `0001608624` is `BENEFITTER INSURANCE SOLUTIONS, INC.` with no recent 13F rows.
- 2026-05-21: `python3 -m pytest -q tests/test_config_manager_weights.py` failed because host Python has no `pytest`.
- 2026-05-21: Container fallback test attempt failed because the Docker daemon socket was unavailable.
- 2026-05-21: Workflow run `26247310908` passed GitHub-hosted pytest with `139 passed in 12.61s`.
- 2026-05-21: Workflow run `26247310908` synced Coatue CIK `0001135730`, computed `2026Q1` trends with 219 signals, and logged `Trend analysis summary notification sent`.
- 2026-05-21: Feature worktree `/Users/roman/.codex/worktrees/trend-output-v2/hedge_funds_tracker` created on `codex/trend-output-v2` from the committed hotfix branch tip because the primary checkout had pending README/continuity edits.
- 2026-05-21: Focused Trend Output v2 suite passed with `21 passed in 0.85s` after fixing shortlist candidate counts to remain pre-limit.
- 2026-05-21: Full Trend Output v2 verification passed with `/Users/roman/Documents/Development/hedge_funds_tracker/.venv/bin/python -m pytest -q` reporting `150 passed in 7.14s`.
- 2026-05-21: Live Q1 shortlist smoke showed `Stored signals: 219`, `Directional candidates: Buy 10 | Reduction 11`, and `Promoted shortlist: Buy 4 | Reduction 8 | Monitored 7`; `--trends-explain SPGI` resolved stored contributors.
- 2026-05-21: Self-review added bounded price lookahead for availability/forward-return coverage and rendered Telegram `SELL` shortlist labels as `REDUCE` for reduction framing.
- 2026-05-21: Post-self-review full verification passed with `/Users/roman/Documents/Development/hedge_funds_tracker/.venv/bin/python -m pytest -q` reporting `153 passed in 6.83s`; `git diff --check` passed.
- 2026-05-22: SEC EDGAR evidence identified Situational Awareness LP 13F filer CIK `0002045724` and Q1 2026 filing accession `0002045724-26-000008`.
- 2026-05-22: State Street SPY daily holdings workbook dated 2026-05-21 supplied the top 400 CUSIP/ticker identifiers merged into `config/cusip_tickers.json`; the map grew from 149 to 470 entries while existing mappings won conflicts.
- 2026-05-22: Local Stooq gateway smoke returned quotes for `AVGO` and `AMAT`; `--show-trends-only --trends-quarter 2026Q1` rendered those tickers plus the default portfolio shares and direction breakdowns from stored Q1 signals.
- 2026-05-22: Temporary DB recompute loaded 468/470 configured market-price keys, stored Situational Awareness LP baseline accession `0002045724-26-000008`, recomputed Q1 with 243 signals, and turned mapped top-row freshness into market-derived states such as `AMAT` `Stale`.
- 2026-05-22: Follow-up verification passed with `/Users/roman/Documents/Development/hedge_funds_tracker/.venv/bin/python -m pytest -q` reporting `155 passed in 14.88s`; `git diff --check` passed.
- 2026-05-22: Final freshness marker tweak changed stale raw output from `!` to `-`; live remote check found no PR for `codex/fix-trend-summary-blockers` or `codex/trend-output-v2` before publication.
- 2026-05-22: Post-marker verification passed with `/Users/roman/Documents/Development/hedge_funds_tracker/.venv/bin/python -m pytest -q` reporting `155 passed in 14.77s`; `git diff --check` passed.
- 2026-05-22: PR #35 merged Trend Output v2 and its Q1 summary recovery base into remote `main` at merge commit `17baf16`; branch `codex/trend-shortlist-contributors` was created for the next shortlist contributor-label follow-up.
- 2026-05-22: Raw freshness recheck observed `+`, `-`, and `?` in both `--trends-view raw` and `scripts/show_trends.py --view raw`; remote `origin/main` config recheck confirmed Situational Awareness LP CIK `0002045724` is merged.
- 2026-05-22: Focused high-signal contributor verification passed for config, trend payload, formatter, CLI shortlist, script shortlist, and the direct `_compute_quarter_metrics` branch test; `git diff --check` passed.
- 2026-05-22: Full high-signal contributor verification passed with `/Users/roman/Documents/Development/hedge_funds_tracker/.venv/bin/python -m pytest -q` reporting `159 passed in 14.43s`; a temporary Q1 backfill DB recompute rendered MSFT contributors as `[TCI, ✅ Appaloosa, Coatue]`.
