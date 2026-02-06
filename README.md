# Hedge Fund 13F Tracker

Lightweight daily checker for hedge fund 13F filings. It pulls each manager's SEC submissions JSON, detects new 13F-HR / 13F-HR/A filings, downloads the filing's `infotable.xml`, diffs positions vs. the last stored snapshot, and sends notifications to Telegram and/or email.

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
- `NOTIFIERS` (comma-separated, e.g. `telegram,email`)
- `SEC_RATE_LIMIT_PER_SEC` (requests/sec, must be <= 10; default 5)

Paths in `.env` may be relative to the repo root.

Telegram:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Email:
- `SMTP_HOST`
- `SMTP_PORT` (default 587)
- `SMTP_USER`
- `SMTP_PASS`
- `EMAIL_FROM`
- `EMAIL_TO`

### Managers file format
```json
[
  { "name": "Appaloosa Management LP", "cik": "0001656456" }
]
```

## Behavior Notes
- The first time a manager is seen, a baseline snapshot is stored. Use `--notify-initial` to send a baseline notification.
- The tracker only notifies when it detects position changes (new, exited, increased, decreased).

## Scheduling (cron)
Example daily run at 7am:
```bash
0 7 * * * cd /Users/roman/Documents/Development/hedge_funds_tracker && . .venv/bin/activate && python -m tracker >> logs/cron.log 2>&1
```

## GitHub Actions
This repo includes `/Users/roman/Documents/Development/hedge_funds_tracker/.github/workflows/13f-tracker.yml` to run every 15 minutes and on manual trigger.

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
   - Variable `NOTIFIERS` (`telegram`, `email`, or `telegram,email`, default is `telegram`)
   - Email secrets: `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_FROM`, `EMAIL_TO`
   - Optional variable `SMTP_PORT` (default `587`)
6. Run once from Actions tab using `workflow_dispatch` to create baseline state.

Note:
- Workflow commits `data/tracker.sqlite3` back to the repository to persist state between runs.
- Commit message includes `[skip ci]` to avoid loops.

## CLI flags
```bash
python -m tracker --help
```
