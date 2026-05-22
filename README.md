# Hedge Fund 13F Tracker

Tracks configured hedge fund 13F filings from SEC data, persists SQLite state,
computes cross-fund trend signals for completed report quarters, and can notify
Telegram when filings or trend summaries are ready.

## What It Does

- Polls SEC submissions JSON for configured managers in `config/managers.json`.
- Downloads 13F information tables and stores manager state plus quarter snapshots.
- Computes trend signals for the latest report quarter that is complete for every configured manager.
- Prints trend tables in the terminal from saved signals.
- Sends Telegram filing and trend-summary notifications when notifiers are configured.
- Includes DB-only scripts for state inspection and portfolio-position trend analysis.

SEC inputs:
- Submissions JSON: `https://data.sec.gov/submissions/CIK##########.json`
- Filing folder index: `https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/index.json`

The SEC requires a descriptive `SEC_USER_AGENT` and fair request rates.

## Setup

Use the project virtualenv. Running system `python3` without dependencies will
fail with imports such as `requests`.

```bash
cd /Users/roman/Documents/Development/hedge_funds_tracker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config/.env.example .env
```

Set `SEC_USER_AGENT` in `.env` before running the tracker:

```dotenv
SEC_USER_AGENT="HedgeFundsTracker/1.0 (your_email@example.com)"
```

The app auto-loads repo-root `.env`.

## Databases

Database choice matters.

- `config/.env.example` uses `DB_PATH="data/local/tracker.local.sqlite3"` for local runs.
- GitHub Actions uses tracked state at `data/tracker.sqlite3`.
- CLI commands that load app config follow `DB_PATH` from `.env` unless you override it in the shell.
- DB-only scripts default to `data/tracker.sqlite3` unless `--db` is passed.

Print trend signals from the workflow DB even when local `.env` points to a
local DB:

```bash
DB_PATH=data/tracker.sqlite3 \
python -m tracker --show-trends-only --trends-quarter 2026Q1
```

Print from the local DB configured in `.env`:

```bash
python -m tracker --show-trends-only
```

## Configuration

Required:
- `SEC_USER_AGENT`: descriptive SEC user agent with contact details.

Common optional values:
- `DB_PATH`: SQLite path. Local example: `data/local/tracker.local.sqlite3`.
- `MANAGERS_FILE`: managers JSON path. Default: `config/managers.json`.
- `MANAGERS_JSON`: managers JSON array passed directly through environment.
- `NOTIFIERS`: comma-separated notifier names. Empty means no notifications.
- `SEC_RATE_LIMIT_PER_SEC`: SEC requests per second, `> 0` and `<= 10`. Default: `5`.
- `MAX_FILING_AGE_DAYS`: ignore filings older than this many days. Default: `180`.
- `TREND_BLEND_MODE`: `tactical` by default; `portfolio` is also supported.
- `TREND_LIVE_PRICES_SYMBOLS_FILE`: CUSIP/instrument symbol map for live-price freshness. Default: `config/cusip_tickers.json`.

Telegram variables are needed only when `NOTIFIERS` includes `telegram`:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Managers are configured in `config/managers.json`:

```json
[
  {
    "name": "Appaloosa Management LP",
    "cik": "0001656456",
    "weight": 1.0
  }
]
```

## Terminal Commands

Run commands from the repo root with the virtualenv active:

```bash
source .venv/bin/activate
```

### Show Saved Trend Tables

Read saved trend signals only. This does not poll SEC, recompute trends, write
state, or send notifications.

```bash
python -m tracker --show-trends-only
python -m tracker --show-trends-only --trends-quarter 2026Q1
DB_PATH=data/tracker.sqlite3 python -m tracker --show-trends-only --trends-quarter 2026Q1
python -m tracker --show-trends-only --trends-show-reversals
```

Direct DB-only equivalent:

```bash
python scripts/show_trends.py --db data/tracker.sqlite3 --quarter 2026Q1
python scripts/show_trends.py --db data/tracker.sqlite3 --quarter 2026Q1 --show-reversals
```

### Run Tracker Without Notifications

Fetch SEC data, update the configured DB, recompute eligible latest-quarter
trends, and avoid Telegram by clearing notifier selection for this command:

```bash
NOTIFIERS= python -m tracker
NOTIFIERS= python -m tracker --show-trends-detailed
NOTIFIERS= python -m tracker --force-trend-recompute --show-trends-detailed
```

Use `--dry-run` for polling and diff validation with no notifications and no DB
writes:

```bash
python -m tracker --dry-run
```

### Run Tracker With Configured Notifications

Uses `NOTIFIERS` and notifier credentials from `.env`:

```bash
python -m tracker
python -m tracker --show-trends-detailed
python -m tracker --notify_on_first_start
python -m tracker --test-notification
```

`--notify_on_first_start` sends a baseline notification when a manager is first
seeded. Without it, first-start baseline state is stored without a baseline
message.

### Trend Summary Notification Commands

Send the Telegram trend summary from already saved DB signals without SEC sync:

```bash
python -m tracker --send-trend-summary-from-db
python -m tracker --send-trend-summary-from-db --trends-quarter 2026Q1
python -m tracker --send-trend-summary-from-db --send-trend-summary-force
```

`--send-trend-summary-force` requires `--send-trend-summary-from-db` and ignores
the per-quarter dedup marker.

### State Reset And Backfill

Clear `manager_state` before a normal tracker run:

```bash
NOTIFIERS= python -m tracker clean_state
python -m tracker --notify_on_first_start clean_state
```

Run historical trend backfill:

```bash
python -m tracker --backfill-trend-history
python -m tracker --backfill-trend-history --backfill-from-quarter 2023Q1 --backfill-to-quarter 2024Q4
python -m tracker --backfill-trend-history --backfill-force --backfill-include-latest
```

Backfill mode is separate from the daily notification flow.

### Main CLI Options

```bash
python -m tracker --help
```

Current flags:
- `--notify_on_first_start`
- `clean_state`
- `--test-notification`
- `--dry-run`
- `--force-trend-recompute`
- `--show-trends-detailed`
- `--show-trends-only`
- `--send-trend-summary-from-db`
- `--send-trend-summary-force`
- `--trends-quarter YYYYQn`
- `--trends-min-conf FLOAT`
- `--trends-limit INT`
- `--trends-show-reversals`
- `--trends-symbols-file PATH`
- `--trend-live-prices-symbols-file PATH`
- `--backfill-trend-history`
- `--backfill-from-quarter YYYYQn`
- `--backfill-to-quarter YYYYQn`
- `--backfill-force`
- `--backfill-include-latest`

## Utility Scripts

Show stored manager state:

```bash
python scripts/show_state.py
python scripts/show_state.py --db data/local/tracker.local.sqlite3
```

Show saved trend signals directly from a DB:

```bash
python scripts/show_trends.py --help
python scripts/show_trends.py --db data/tracker.sqlite3 --quarter 2026Q1
```

Analyze a JSON list of portfolio tickers against existing DB snapshots:

```json
["AAPL", "MSFT", "GOOGL"]
```

```bash
python scripts/analyze_portfolio_positions_trends.py \
  --positions-file data/positions.json

python scripts/analyze_portfolio_positions_trends.py \
  --positions-file data/positions.json \
  --db data/tracker.sqlite3 \
  --quarter 2026Q1 \
  --output-json data/portfolio_trends_2026Q1.json
```

The portfolio analyzer accepts either:
- a JSON array of ticker strings, or
- a nested JSON object with ticker values under keys containing `Stocks`.

It can use live Stooq prices for data freshness. Disable that lookup with
`--skip-live-prices`.

## Trend Completion Rules

The default tracker run computes only the latest report quarter that is common
to all configured managers.

For that quarter it requires:
- at least two common quarters of history,
- a complete snapshot matrix for the managers in the trend window,
- saved positions from SEC filings selected by the sync flow.

Until every configured manager has the needed report-quarter snapshot, trend
status can remain `pending_*` and no new trend summary is available for that
quarter.

## GitHub Actions

Workflow: `.github/workflows/13f-tracker.yml`

The scheduled workflow targets `07:00` and `19:00` Europe/Kyiv. It declares UTC
cron entries for DST and standard time, then gates duplicate offset runs inside
the workflow. Manual `workflow_dispatch` runs bypass that schedule gate.

The workflow:
1. installs dependencies,
2. validates required secrets and notifier configuration,
3. runs pytest,
4. runs the tracker or selected manual force mode,
5. commits changed `data/tracker.sqlite3` state back to the run branch,
6. uploads CI diagnostics artifacts.

Required GitHub secret:
- `SEC_USER_AGENT`

Required when workflow variable `NOTIFIERS` includes `telegram`:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional GitHub variables:
- `SEC_RATE_LIMIT_PER_SEC`
- `MAX_FILING_AGE_DAYS`
- `NOTIFIERS` (workflow default is `telegram`)

Manual workflow inputs:
- `force_full_rebuild_and_resend`: runs forced trend recompute, then force-sends trend summary from DB.
- `force_resend_only`: force-sends trend summary from existing DB signals.

Trigger from terminal with GitHub CLI:

```bash
gh workflow run 13f-tracker.yml \
  --repo romanr111/hedge-funds-tracker \
  --ref main
```

Watch a run:

```bash
gh run list --repo romanr111/hedge-funds-tracker --workflow 13f-tracker.yml --limit 5
gh run watch <RUN_ID> --repo romanr111/hedge-funds-tracker --exit-status
```

## Tests

```bash
python -m pytest -q
```
