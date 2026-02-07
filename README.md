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
python -m tracker --test-notification
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
- `--test-notification`
  Sends a test notification immediately through configured notifiers and exits.
  Does not poll SEC and cannot be combined with `--dry-run` or `clean_state`.

State viewer utility:
```bash
python scripts/show_state.py
python scripts/show_state.py --db data/tracker.sqlite3
```
