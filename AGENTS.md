# Signals — Agent Guide

## Project Overview

A Python CLI application that tracks hedge fund 13F filings from the U.S. Securities and Exchange Commission (SEC) EDGAR system. For each configured manager it:

1. Polls the SEC submissions JSON feed (`https://data.sec.gov/submissions/CIK##########.json`).
2. Downloads 13F information table XML and parses holdings.
3. Stores manager state and quarter snapshots in SQLite.
4. Detects position changes (new, exited, increased, decreased) and sends Telegram notifications.
5. Computes cross-fund trend signals for the latest report quarter that is complete across all configured managers.
6. Prints trend tables in the terminal and can send Telegram trend-summary notifications.

The project follows a layered architecture (ports-and-adapters / clean architecture) with explicit separation between domain logic, application use cases, and infrastructure concerns.

## Technology Stack

- **Language:** Python 3.11+ (CI targets 3.11; local development works on newer versions).
- **Package Management:** `requirements.txt` only. No `pyproject.toml`, `setup.py`, or `setup.cfg`.
- **Dependencies:**
  - `requests>=2.31.0` — HTTP client for SEC and Telegram APIs.
  - `python-dotenv>=1.0.1` — `.env` file loading (with a fallback parser when unavailable).
  - `pytest>=8.0.0` — test runner.
  - `types-requests>=2.31.0` — type stubs.
  - `openpyxl>=3.1.0` — Excel workbook export for trend summaries.
- **Persistence:** SQLite via the standard library `sqlite3` module.
- **External Data Sources:**
  - SEC EDGAR API for submissions and 13F information tables.
  - Stooq (free price feed) for live/latest price lookups used in trend freshness decay.
- **Notifications:** Telegram Bot API.

## Agent Tooling

This repo configures **CodeGraph** and **Headroom** to help agents navigate and summarize the codebase.

- **CodeGraph** (`codegraph`) — symbol/file/flow lookup and cross-reference queries.
  - Version: `0.9.9`.
  - Registered as an MCP server in [`.mcp.json`](.mcp.json): `codegraph serve --mcp`.
  - Repo-local recipes in the [`justfile`](justfile):
    - `just graph-bootstrap` — initialize CodeGraph for this worktree (enforces local DB ownership).
    - `just graph-status` — show index status.
    - `just graph-sync` — sync incremental changes.
    - `just graph-reindex` — rebuild the full index.
  - Isolation guard: [`scripts/codegraph-bootstrap.sh`](scripts/codegraph-bootstrap.sh) writes a `.codegraph/worktree-root` owner marker so a `.codegraph/` directory copied from another worktree is rejected. New worktrees must run `just graph-bootstrap` first.

- **Headroom** (`headroom`) — primary context-optimization layer for bulky/noisy outputs: build logs, test output, simulator logs, crash logs, JSON, verbose diagnostics, and large diffs.
  - Version: `0.24.0`.
  - No repo-local Headroom MCP entry exists in `.mcp.json`; this file is CodeGraph-only. If your agent already has a user-managed Headroom MCP config, keep using it.

## Project Structure

```
signals/
  __init__.py                 # Package root
  __main__.py                 # Entry point: delegates to main()
  main.py                     # Thin wrapper around CLI main; also re-exports legacy helpers
  config.py                   # Environment / .env configuration loading
  composition.py              # Dependency injection: builds Runtime (client, store, notifiers, gateway)
  parse_13f.py                # LEGACY re-export -> signals.domain.parsing
  sec_client.py               # LEGACY re-export -> signals.infrastructure.sec.sec_http_gateway
  diff.py                     # LEGACY re-export -> signals.domain.diffing
  notifiers.py                # LEGACY re-export -> signals.infrastructure.notify.notifiers
  storage.py                  # LEGACY re-export -> signals.infrastructure.storage.sqlite_state_repository
  application/
    ports/                    # Protocol definitions (StateRepository, SecGateway, NotifierPort, HistoricalPriceGateway)
    use_cases/                # Business logic orchestration
      track_manager.py        # Per-manager polling, diffing, notifying
      sync_quarter_snapshots.py
      run_trend_engine.py
      backfill_trend_history.py
      notify_quarterly_reports_completion.py
      notify_trend_analysis_summary.py
      analyze_portfolio_positions_trends.py
  domain/
    models.py                 # Dataclasses: Manager, Filing, ManagerState, DiffResult, ManagerQuarterSnapshot, TrendRun, TrendStockSignal
    exceptions.py             # Hierarchy rooted at SignalsError
    filings.py                # SEC submission extraction and filtering
    parsing.py                # 13F information table XML parsing
    diffing.py                # Position diff logic and message formatting
    quarters.py               # Quarter parsing and ordering
    trends.py                 # Core trend signal computation (~1000 lines of quantitative logic)
    trend_presentation.py     # Signal -> human-readable action/setup/conviction mapping
    trend_telegram_message.py # Telegram message builder for trend summaries
    timing.py                 # Kyiv timezone helpers
    formatting.py             # Message formatting utilities
    portfolio.py              # Portfolio value/share direction helpers
  infrastructure/
    logging/json_logger.py    # Structured JSON logging with trace_id contextvars
    market/                   # Stooq price/history gateways
    notify/notifiers.py       # TelegramNotifier and notifier builder
    sec/sec_http_gateway.py   # SecClient: SEC EDGAR HTTP client with rate limiting
    storage/sqlite_state_repository.py  # StateStore: SQLite implementation of state and trend schema
    export/xlsx_exporter.py   # Excel workbook writer for trend summary tables
  interfaces/cli/main.py      # argparse CLI and command dispatch

scripts/
  show_state.py               # Standalone DB state inspector
  show_trends.py              # Standalone trend table printer from DB
  analyze_portfolio_positions_trends.py  # Portfolio tickers vs DB snapshot analyzer

config/
  .env.example                # Template for required/optional environment variables
  managers.json               # JSON array of {name, cik, weight} objects
  cusip_tickers.json          # CUSIP/instrument_key -> ticker symbol map
  symbol_metadata.json        # Extra symbol metadata

data/
  signals.sqlite3             # CI/production database (committed by GitHub Actions)
  local/signals.local.sqlite3 # Default local development database (gitignored)

tests/
  test_*.py                   # pytest function-based tests (no Test classes)
```

## Build and Run Commands

All commands assume the repo root as working directory and the virtualenv active.

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config/.env.example .env
# Edit .env and set SEC_USER_AGENT before running.

# Run tests
python -m pytest -q

# Run signals (full sync + trend compute)
python -m signals

# Run without notifications
NOTIFIERS= python -m signals

# Dry run (no DB writes, no notifications)
python -m signals --dry-run

# Show saved trend table only (no sync)
python -m signals --show-trends-only
python -m signals --show-trends-only --trends-quarter 2026Q1

# Force trend recompute
python -m signals --force-trend-recompute

# Send trend summary from existing DB signals
python -m signals --send-trend-summary-from-db

# Backfill historical trends
python -m signals --backfill-trend-history --backfill-from-quarter 2023Q1 --backfill-to-quarter 2024Q4

# Export trend summary to Excel (idempotent — skips if unchanged)
python -m signals --export-xlsx
python -m signals --export-xlsx --export-xlsx-path data/exports

# Dry run with export preview (no file written)
python -m signals --dry-run --export-xlsx

# Test Telegram notification
python -m signals --test-notification

# Clean manager state
python -m signals clean_state

# Standalone scripts
python scripts/show_state.py --db data/signals.sqlite3
python scripts/show_trends.py --db data/signals.sqlite3 --quarter 2026Q1
python scripts/analyze_portfolio_positions_trends.py --positions-file data/positions.json --db data/signals.sqlite3
```

## Configuration

Configuration is environment-driven. The app auto-loads `.env` from the repo root.

**Required:**
- `SEC_USER_AGENT` — descriptive user agent with contact email for SEC API access.

**Common optional:**
- `DB_PATH` — SQLite file path. Default: `data/signals.sqlite3`.
- `MANAGERS_FILE` — path to managers JSON. Default: `config/managers.json`.
- `MANAGERS_JSON` — inline JSON array of managers (overrides file).
- `NOTIFIERS` — comma-separated notifier names (e.g., `telegram`). Empty means no notifications.
- `SEC_RATE_LIMIT_PER_SEC` — SEC requests per second, `> 0` and `<= 10`. Default: `5`.
- `MAX_FILING_AGE_DAYS` — ignore filings older than this. Default: `180`.
- `TREND_BLEND_MODE` — `tactical` (default) or `portfolio`.
- `TREND_LIVE_PRICES_SYMBOLS_FILE` — symbol map for live-price freshness. Default: `config/cusip_tickers.json`.

**Telegram (only when `NOTIFIERS` includes `telegram`):**
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Code Style Guidelines

- Every module starts with `from __future__ import annotations`.
- Use type hints everywhere. Prefer `list[str]`, `str | None`, etc.
- Use `dataclass(frozen=True)` for value objects / models.
- Naming: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE_CASE` for module-level constants.
- Domain logic in `signals/domain/` must remain pure (no I/O, no external dependencies).
- Application use cases in `signals/application/use_cases/` orchestrate domain logic and call ports.
- Infrastructure in `signals/infrastructure/` implements port protocols.
- The CLI in `signals/interfaces/cli/main.py` is the only place that should contain argparse, `print()`, and user-facing formatting.
- Legacy root-level modules (`parse_13f.py`, `sec_client.py`, `diff.py`, `notifiers.py`, `storage.py`) are re-export-only shims. Do not add new logic there; put it in the layered packages.
- Logging is structured JSON. Use `extra={...}` on log calls. A `trace_id` contextvar is propagated automatically when `log_context()` is used.
- Defensive input validation at config boundaries; raise domain-specific exceptions (`SignalsError` subclasses) rather than leaking raw library exceptions upward.

## Testing Instructions

- Test framework: **pytest**, function-based (no `unittest.TestCase` classes).
- Run: `python -m pytest -q`
- Tests use `tmp_path` and `monkeypatch` fixtures extensively.
- Storage tests create temporary SQLite databases via `tmp_path`.
- When testing config, clear environment variables first using a helper like `_clear_config_env(monkeypatch)` to avoid leakage from the host or `.env` file.
- The CI test count as of the latest verified run: 185 tests.

## Architecture Decisions

### Ports and Adapters

The codebase explicitly separates interfaces:

- `StateRepository` (protocol) — implemented by `StateStore` (SQLite).
- `SecGateway` (protocol) — implemented by `SecClient` (HTTP).
- `NotifierPort` / `Notifier` (protocol/class) — implemented by `TelegramNotifier`.
- `HistoricalPriceGateway` (protocol) — implemented by `StooqHistoryGateway` and `StooqPriceGateway`.

### Dependency Injection

`signals/composition.py` builds a `Runtime` dataclass containing concrete implementations. The CLI creates one `Runtime` per invocation and passes components into use-case functions.

### Fingerprint-Based Idempotency

The trend engine computes an `input_fingerprint` (SHA-256 of normalized snapshot metadata + blend mode + live prices) and a `top_fingerprint` (top buy/sell/reversal instrument keys). If inputs are unchanged, the engine skips recomputation to avoid unnecessary DB writes. Use `--force-trend-recompute` to override.

### DB Schema Management

`StateStore` auto-creates tables on first open and runs lightweight schema migrations in `_migrate_schema()`. There is no external migration framework.

## Deployment

- **GitHub Actions:** `.github/workflows/signals.yml`
  - Scheduled runs at 07:00 and 19:00 Europe/Kyiv (uses UTC cron with a DST-aware schedule gate).
  - Manual `workflow_dispatch` with optional force flags.
  - Steps: install deps → validate secrets → run pytest → run signals → commit `data/signals.sqlite3` back to the branch.
  - CI uploads diagnostic artifacts (logs) with 14-day retention.
- **Required GitHub secret:** `SEC_USER_AGENT`
- **Required when Telegram is enabled:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- **Optional GitHub variables:** `SEC_RATE_LIMIT_PER_SEC`, `MAX_FILING_AGE_DAYS`, `NOTIFIERS`

## Security Considerations

- `SEC_USER_AGENT` is mandatory and must include a descriptive contact email per SEC fair-access policy.
- SEC rate limiting is enforced both by configuration validation (`<= 10` req/sec) and by `SecClient` sleep intervals.
- Telegram credentials and the SEC user agent must be provided via environment variables or GitHub secrets, never committed to source control.
- `.env` and `data/` are gitignored; only `data/signals.sqlite3` is force-added by CI.
- No user input reaches raw SQL; the SQLite layer uses parameterized queries.

## Common Patterns for Agents

- When adding a new use case, place it in `signals/application/use_cases/`, keep it free of CLI concerns, and inject ports.
- When adding a new model, use `dataclass(frozen=True)` in `signals/domain/models.py`.
- When adding a new exception, subclass `SignalsError` in `signals/domain/exceptions.py`.
- When changing the CLI, update `signals/interfaces/cli/main.py` and add corresponding tests in `tests/test_cli_*.py`.
- When changing the database schema, update `sqlite_state_repository.py` schema creation and migration methods, and verify with existing tests.
- When adding a new notifier, implement the `Notifier` interface in `signals/infrastructure/notify/notifiers.py` and register a builder in `DEFAULT_NOTIFIER_BUILDERS`.
