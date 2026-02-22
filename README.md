# Hedge Fund 13F Tracker

Lightweight daily checker for hedge fund 13F filings. It pulls each manager's SEC submissions JSON, detects new 13F-HR / 13F-HR/A filings, downloads the filing's `infotable.xml`, diffs positions vs. the last stored snapshot, and sends notifications to Telegram.

## Data Sources (SEC official)
- Submissions JSON: `https://data.sec.gov/submissions/CIK##########.json`
- Filing folder index: `https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/index.json` (used to locate `infotable.xml`)

The SEC expects a descriptive User-Agent and fair-access request rates.

## Quick Start
1. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Configure env vars:
   ```bash
   cp config/.env.example .env
   ```
   The app auto-loads `.env` from the repo root.
3. Edit managers list in `config/managers.json`.
4. Run the tracker:
   ```bash
   python -m tracker
   ```

## Configuration
Required:
- `SEC_USER_AGENT` (include contact email)

Optional:
- `MANAGERS_FILE` (path to `managers.json`)
- `DB_PATH` (SQLite path, default `data/tracker.sqlite3`)
- `NOTIFIERS` (comma-separated, e.g. `telegram`)
- `SEC_RATE_LIMIT_PER_SEC` (requests/sec, must be <= 10; default 5)
- `MAX_FILING_AGE_DAYS` (ignore filings older than N days; default 180)
- `TREND_BLEND_MODE` (`tactical` by default; also supports `portfolio`)
- `TREND_LIVE_PRICES_SYMBOLS_FILE` (symbol map for live price fetch from `stooq`, default `config/cusip_tickers.json`)
- Quarterly pipeline options:
  - `PIPELINE_TOP_K` (default `20`)
  - `PIPELINE_MIN_CONF` (default `0.45`)
  - `PIPELINE_HOLD_QUARTERS` (default `2`)
  - `PIPELINE_POSITION_CAP` (default `0.07`)
  - `PIPELINE_SECTOR_CAP` (default `0.30`)
  - `PIPELINE_ADV20_USD_MIN` (default `3000000`)
  - `PIPELINE_PRICE_MIN` (default `5`)
  - `PIPELINE_COST_BPS_PER_SIDE` (default `10`)
  - `PIPELINE_REPORT_DIR` (default `reports/quarterly`)
  - `SYMBOL_METADATA_FILE` (default `config/symbol_metadata.json`)
  - `PIPELINE_FAIL_ON_QUALITY` (optional CSV from `WATCH,FAIL`; when set, CLI exits non-zero for listed quality statuses)

Paths in `.env` may be relative to the repo root.
For local development, prefer a non-tracked path such as `data/local/tracker.local.sqlite3`.

Telegram:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### Managers file format
```json
[
  { "name": "Appaloosa Management LP", "cik": "0001656456" }
]
```

## Behavior Notes
- The tracker ignores filings older than `MAX_FILING_AGE_DAYS` (default 180 days).
- The first time a manager is seen, only the most recent eligible filing is used to seed baseline state.
- Use `--notify_on_first_start` to send a single baseline notification on that initial seed.
- After baseline exists, the tracker notifies only when it detects position changes (new, exited, increased, decreased).

## Scheduling (cron)
Example daily run at 7am:
```bash
0 7 * * * cd /Users/roman/Documents/Development/hedge_funds_tracker && . .venv/bin/activate && python -m tracker >> logs/cron.log 2>&1
```

## GitHub Actions
This repo includes `/Users/roman/Documents/Development/hedge_funds_tracker/.github/workflows/13f-tracker.yml` to run daily at 07:00 Europe/Kyiv and on manual trigger.

Setup steps:
1. Push this repository to GitHub.
2. In GitHub repo settings, ensure Actions are enabled.
3. Add repository secret `SEC_USER_AGENT` with a descriptive value like:
   `HedgeFundsTracker/1.0 (you@example.com)`
4. Required Telegram setup for notifications:
   - Secret `TELEGRAM_BOT_TOKEN`
   - Secret `TELEGRAM_CHAT_ID`
5. Optional repository variables/secrets:
   - Variable `SEC_RATE_LIMIT_PER_SEC` (default `5`)
   - Variable `MAX_FILING_AGE_DAYS` (default `180`)
   - Variable `NOTIFIERS` (`telegram`, default is `telegram`)
6. Run once from Actions tab using `workflow_dispatch` to create baseline state.

Note:
- Workflow commits `data/tracker.sqlite3` back to the repository to persist state between runs.
- Commit message includes `[skip ci]` to avoid loops.

## CLI commands
Main tracker:
```bash
python -m tracker
```

Common command examples:
```bash
python -m tracker --help
python -m tracker --notify_on_first_start
python -m tracker --notify_on_first_start clean_state
python -m tracker --dry-run
python -m tracker --force-trend-recompute
python -m tracker --show-trends-detailed
python -m tracker --test-notification
python -m tracker --run-quarterly-pipeline
python -m tracker --run-quarterly-pipeline --pipeline-quarter 2025Q4
python -m tracker --run-quarterly-pipeline --pipeline-dry-run-report
python -m tracker --backfill-trend-history
python -m tracker --backfill-trend-history --backfill-from-quarter 2023Q1 --backfill-to-quarter 2024Q4
python -m tracker --backfill-trend-history --backfill-force --backfill-include-latest
```

Flag reference:
- `--help`
  Prints CLI help and exits without running SEC checks.
- `--notify_on_first_start`
  On first run for a manager (no state yet), sends one baseline notification for the latest eligible filing.
  Without this flag, initial baseline is still stored but notification is suppressed.
- `clean_state`
  Positional command to clear all rows from `manager_state` before running SEC checks.
  Example: `python -m tracker --notify_on_first_start clean_state`
- `--dry-run`
  Executes SEC polling and diff logic, NO notifications, no DB writing.
  Useful for safe validation and troubleshooting.
  Cannot be combined with `clean_state`.
- `--force-trend-recompute`
  Forces trend engine recalculation even when fingerprints are unchanged (bypasses `skipped_no_new_completed_quarter`
  and `skipped_no_top_change` short-circuit paths).
- `--show-trends-detailed`
  Always prints the detailed trends table after the run (same style as `scripts/show_trends.py`), including non-interactive output.
  Related options:
  `--trends-quarter`, `--trends-min-conf`, `--trends-limit`, `--trends-show-reversals`, `--trends-symbols-file`.
- `--trend-live-prices-symbols-file`
  JSON map used for live prices from `stooq` (key: CUSIP/instrument_key, value: ticker),
  default `config/cusip_tickers.json`.
- `--test-notification`
  Sends a test notification immediately through configured notifiers and exits.
  Does not poll SEC and cannot be combined with `--dry-run` or `clean_state`.
- `--run-quarterly-pipeline`
  Runs an additive research pipeline: risk-filter -> portfolio -> walk-forward backtest -> KPI report.
  Entry timing uses actual filings availability: `max(filing_date across managers for quarter) + 1 day`, then snapped to next trading day.
  If trend history before selected `--pipeline-quarter` is short (less than 8 quarters), tracker auto-runs
  historical backfill first to seed missing trend quarters, then runs pipeline.
- `--pipeline-quarter`
  Optional `YYYYQn` target quarter for quarterly pipeline. Default uses latest trend quarter available in DB.
- `--pipeline-dry-run-report`
  Runs quarterly pipeline and writes report files without persisting quarterly pipeline tables in SQLite.
- `--backfill-trend-history`
  Runs a separate historical trend backfill mode. It does not execute the daily notify flow.
- `--backfill-from-quarter`, `--backfill-to-quarter`  
  Optional backfill range in `YYYYQn`. If omitted, backfill targets the latest 9 historical quarters.
- `--backfill-force`
  Recomputes quarters even if trend signals already exist.
- `--backfill-include-latest`
  Includes latest completed quarter in backfill mode (excluded by default).

Interactive UX note:
- In interactive terminal runs (`python -m tracker` in TTY), when trend data is ready the CLI prints the detailed trend table by default.
- To force the same detailed output in non-interactive runs, use:
  `python -m tracker --show-trends-detailed`
- You can also print the same format directly with:
  `python scripts/show_trends.py --db <DB_PATH> --quarter <YYYYQn>`

Backfill note:
- Main trend engine remains latest-quarter only in the default tracker run.
- Backfilled rows are explicitly marked in DB with `is_backfill=1` and `backfill_batch_id`.

State viewer utility:
```bash
python scripts/show_state.py
python scripts/show_state.py --db data/tracker.sqlite3
```

## Trend Engine Logic (Latest-Flow Core)
`python -m tracker` computes trend signals for only one target quarter: the latest completed quarter common to all configured managers.

### 1) Input gating
- Build `common_quarters` as intersection across managers.
- Pick latest quarter from that intersection as `target_quarter`.
- Use a rolling window of up to 4 quarters ending at `target_quarter`.
- Require at least 2 quarters and a full snapshot matrix (each manager present in each window quarter).
- If requirements are not met, status is `pending_*` (expected waiting state, not a crash).

### 2) Per-manager contribution
For each instrument inside manager snapshots:
- Convert positions to portfolio weights.
- Compute trade flow (`trade_dw`) from shares/weights delta with corporate-action guard.
- Robust-normalize flow with MAD-based sigma and clipped z-score.
- Convert into manager signal contribution:
  - `signal_value = manager_weight_effective * position_weight * flow_participation * tanh(z / 2)`

Where:
- `manager_weight_effective` = manager weight adjusted by manager quality multiplier, then normalized.
- `position_weight` emphasizes meaningful positions.
- `flow_participation` = how strongly manager actually traded this name.

### 3) Instrument-level aggregation
- `np_raw = sum(signal_value)`
- `np_impulse_raw = sum(signal_value * entry_impulse_multiplier)` (boosts new large entries/exits)
- Compute breadth and crowding:
  - directional manager counts and directional participation weights
  - `crowding_hhi` from normalized absolute contributions

### 4) Memory (EWMA) and blend
- `impulse_score` uses short half-life (1 quarter).
- `accumulation_score` uses longer half-life (3 quarters).
- Blended score:
  - `tactical`: `0.60 * impulse + 0.40 * accumulation`
  - `portfolio`: `0.35 * impulse + 0.65 * accumulation`

### 5) Confidence layer
Confidence combines:
- breadth (count + weight),
- persistence,
- anti-crowding penalty,
- magnitude vs quarter scale,
- high-conviction bonus,
- disagreement penalty,
- live-price freshness decay.

Final signal:
- `trend_ewma = blended_score * confidence`
- `trend_delta = trend_ewma - prev_trend_ewma`
- regime is classified from sign, persistence, gates, and delta (`STRONG_BUY`, `REVERSAL_SELL`, etc.).

### 6) Table actions (research, not auto-trading)
- `ACTION_BUY`, `WATCH_BUY`, `MONITOR`
- `WATCH_SELL`, `ACTION_SELL`, `MONITOR`
- thresholds are confidence + regime based (see `tracker/interfaces/cli/main.py`).
