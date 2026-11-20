# market_intel - Agent Guide

`market_intel` is a second CLI in this repo, separate from the `signals` 13F
pipeline. It collects free market series, computes five module gauges
(valuation, sentiment, technique, macro/liquidity, credit), and publishes the
dashboard to a native Google Sheet through the `gog` CLI. Entry point:
`python -m market_intel`. `CLAUDE.md` here is a symlink to this file.

## Purpose and boundary

`market_intel/` never participates in the `signals` run and shares no
database, models, or CLI flags with it; imports from `signals` are limited to
`signals.domain.timing` and `signals.infrastructure.logging.*`. The only
place in this package with argparse, `print()`, and user-facing formatting is
`market_intel/interfaces/cli/main.py`.

## Quick start

```bash
python -m pip install -r requirements.txt    # then copy config/.env.example to .env if needed
python -m market_intel --dry-run             # temp SQLite store, no publish
python -m market_intel --no-publish          # real store, no sheet publish
python -m market_intel                       # collect + compute + publish
python -m pytest tests/test_market_intel_*.py -q   # offline focused suite
```

Publishing also needs `gog` on `PATH` and non-interactive auth (see
Configuration); without it the run succeeds with `skipped_no_gog`.

## Architecture and data flow

```
CLI (interfaces/cli/main.py) -> config.load_config -> composition.build_runtime
  -> use_cases.run_market_intel
       -> collect_vintages    one fetch per series, isolated per source
       -> compute_dashboard   point-in-time reads -> snapshots -> gauges
       -> gauge_history       one row per (run_date, module)
       -> GogSheetsPublisher  fingerprint skip, gog writes, publish_run log
```

Layout: `domain/` (pure models, calculations, percentiles, gauges, bands,
thresholds, verdicts, forward returns), `application/ports/` and
`application/use_cases/`, `infrastructure/` (gateways, VintageStore,
sheet_layout, gog client and publisher), `interfaces/cli/`, `config.py`,
`composition.py`, `registries.py` (every series and computed value).

Dependencies point inward: `domain` imports nothing from this package's
application, infrastructure, or CLI layers. `domain/` stays pure - no I/O, no
clock access, no randomness; callers inject dates and values.

## Registry contract

`registries.SERIES_SPECS` is the single source of truth for every series:
`key` (consumer key and stored `series_id`), `title_uk`, `source`, `freq`,
`staleness_days`, `band_ref` (band table in `domain/bands.py`; `None` for
diagnostics not displayed alone), `weight` and `module_key` (`weight=None` is
display-only; `module_key=None` is an upstream input for computed series),
`invert` (risk flip, applied once in the gauges layer),
`percentile_window_days` (trailing window, default 7300), and `params`
(gateway knobs: FRED series id, Yahoo symbol, URLs, parse hints,
`source_label`).

A series with `source="computed"` is derived by `compute_dashboard` from
stored upstream series; it is never fetched and appears as a `computed` skip
during collection. Registry weights and the registry-to-store-to-compute
contract are pinned by `test_market_intel_registries.py` and
`test_market_intel_pipeline.py`.

## Domain invariants

- Point-in-time: every compute read caps history at `as_of` (the Kyiv run
  date); never read a later observation. `read_point_in_time` takes the latest
  `obs_date <= as_of` and, for the same date, the earliest `fetched_at` (the
  value first published).
- Percentiles are trailing: computed over the series history within
  `percentile_window_days` ending at `as_of`; a window with fewer than 30
  values excludes the member with a warning instead of scoring it.
- Snapshots store raw percentiles. The `invert` risk flip is applied once, in
  `gauges.module_score`; do not pre-invert series or invert again downstream.
- Staleness: a series older than `staleness_days` is marked stale; it drops
  out of the composite and the module renormalizes over remaining weights.
- Units are mixed by design: WALCL and WTREGEN arrive in $M, RRP in $B;
  `netliq_tbn = WALCL/1000 - RRP - TGA/1000`; unit sanity checks drop a member
  (for example Buffett's ratio) off by orders of magnitude.
- Sentiment composite is a risk score: low = panic, high = greed; labels and
  verdicts read it in that direction. `FORWARD_ROWS` strings are verbatim
  research-report cells mapping current conditions to historical
  forward-return distributions - historical associations, not predictions.
  `test_market_intel_forward_returns.py` pins exact text; change only with a
  cited source.

## Sources and degradation

Gateways live in `market_intel/infrastructure/*_gateway.py`: `fred` (CSV,
retries, `Retry-After`), `yahoo` (chart JSON, drops an incomplete last bar,
breaker after three 429s), `multpl` (CAPE current + history), `finra` (margin
table), `cnn_fg` (Fear & Greed JSON), `factset` (Earnings Insight PDF via
landing-page link or direct response, parsed with `pdftotext`), `damodaran`
(annual `histimpl.html` implied-ERP table), and `computed` (derived in
`compute_dashboard`).

Collection is isolated per series. Skip codes are `manual`, `computed`,
`no_adapter`, `source_error`, and `internal`; a source failure never aborts
the run. Storage errors are recorded for that series and collection
continues. If no external source succeeds, the use case stops before gauge
persistence and publishing (`RunResult.all_external_sources_failed`), and the
CLI exits 2: stale or empty data is never recorded as today's result.

## Storage and publishing

`VintageStore` auto-creates `series_vintage` (append-only,
`UNIQUE(series_id, obs_date, value)`; `INSERT OR IGNORE` keeps re-fetches
idempotent and restated values as new vintages), `gauge_history` (upsert by
`run_date, module_key`), `publish_run` (status, fingerprint, target id), and
`settings` (persisted `sheets_spreadsheet_id`).

Publish invariants (`export/gog_sheets_publisher.py`):

- Content fingerprint skip returns `skipped_unchanged` only when the last
  accepted run has the same fingerprint, the target is not reset, and the
  last run's spreadsheet id equals the currently persisted id; a
  `--spreadsheet-id` switch or `--reset-sheet` forces a publish. Accepted
  terminal statuses are `created`, `updated`, `skipped_unchanged`.
- `create` is called with `retries=0`: a retried create can succeed twice and
  mint a duplicate spreadsheet.
- A spreadsheet the store points at and gog reports as missing triggers
  exactly one recreate; the not-found classifier ignores auth, permission,
  network, and non-404 failures, which raise `MarketIntelPublishError`
  instead (CLI exit 3).
- `--reset-sheet` deletes and recreates all eight tabs and is destructive;
  never use it against a sheet you do not own. Tests must use a fake gog
  client; never publish from a test.

## CLI contract

| Flag | Effect |
|---|---|
| `--dry-run` | Throwaway temp SQLite store, publisher disabled |
| `--no-publish` | Collect and compute; skip the sheet |
| `--show` | Detailed per-module tables; implies no publish |
| `--reset-sheet` | Delete and recreate tabs, bypassing the fingerprint skip |
| `--db PATH` | Override `MARKET_INTEL_DB_PATH` |
| `--spreadsheet-id ID` | Persist an override target sheet |
| `--skip-source NAME` | Repeatable; skips one source for the run |

`--verbose` enables DEBUG logging.

Valid `--skip-source` names are the registry source values: `fred`, `yahoo`,
`multpl`, `finra`, `cnn_fg`, `factset`, `damodaran`, `computed`. Series keys
such as `hy_oas` are rejected with exit 2.

| Exit | Meaning |
|---|---|
| 0 | Run completed, including `skipped_no_gog`, `skipped_unchanged`, and partial source failures when at least one source succeeded |
| 1 | `MarketIntelError` or `ValueError` (config error, missing integration) |
| 2 | `--dry-run` with `--no-publish`; unknown `--skip-source`; no external source succeeded |
| 3 | Google Sheets publish failure |

## Configuration

Loaded from the environment after `.env` at the repo root:

- `MARKET_INTEL_DB_PATH` - default `data/local/market_intel.sqlite3`; `--db`
  wins. `SHEETS_SPREADSHEET_ID` seeds the store only when no id is persisted
  (use `--spreadsheet-id` to override); `SHEETS_FOLDER_ID` is the optional
  Drive parent and falls back to `EXPORT_JSON_GDRIVE_FOLDER_ID` when empty.
- `MARKET_INTEL_FRED_INTERVAL` default 1.0 (0.2-10.0);
  `MARKET_INTEL_YAHOO_INTERVAL` default 1.5 (0.2-60.0);
  `MARKET_INTEL_HTTP_INTERVAL` default 1.0 (0.2-60.0) for multpl, finra,
  cnn_fg, factset, damodaran. Out-of-range values raise `ValueError` (exit 1).
- Publishing: `gog` on PATH; optional `GOG_ACCOUNT` selects the account;
  `GOG_KEYRING_PASSWORD`, `GOG_HOME`, and `GOG_ACCESS_TOKEN` are passed
  through to the child process.

## Testing

```bash
python -m pytest tests/test_market_intel_*.py -q   # 17 files, 183 tests, offline
python -m pytest -q                                # full repository suite
```

Tests use recorded fixtures and fakes; no live network, no `sleep`, no `gog`
binary. Time is injected (`now_utc`, `now_kyiv_datetime`, `fetched_on`); never
call the wall clock in tests. Key gates: `test_market_intel_pipeline.py`
(registry-to-store-to-compute), `test_market_intel_e2e_offline.py` (full
pipeline, all-sources-failed stop, publish idempotency),
`test_market_intel_composition.py` (persisted-spreadsheet-id rule),
`test_market_intel_gog_publisher.py` (skip identity, create safety, not-found
classification, metadata failure), and `test_market_intel_config.py` (defaults
and bounds; clear env vars before asserting defaults).

## Known operations and limitations

- Probed 2026-11-07 from the maintainer's egress: Yahoo returns 429 and FINRA
  returns 403 behind Cloudflare; both gateways fail soft and the technique
  module shows "Недоступно". Re-probe and date-stamp updates; do not rewrite
  parsers against live pages while offline.
- Known residual (deferred to phase 2): `damodaran` (annual ERP, a bare year
  only) and the `multpl` current CAPE print are stamped with the run date, so an
  unchanged value is inserted as a new vintage each run day. This adds tie mass
  to the ERP percentile and hides a dead source from staleness. Fixing it needs
  a design call on the ERP staleness and percentile window (`erp` relies on
  daily rows to reach the 30-value minimum). FactSet is not affected: it
  carries the report's publication date, bounded to a plausible range.
- A single dead source does not fail the run; if no external source succeeds
  the run exits 2 and writes nothing. The system does not predict direction:
  it maps conditions to historical forward-return distributions with explicit
  uncertainty, and provides no financial advice.

## Change recipes

- Add or change a series: edit `registries.SERIES_SPECS`, wire its consumer in
  `compute_dashboard`, then update `test_market_intel_registries.py` and the
  pipeline test.
- Add a source: implement a gateway over `HttpGatewayBase` that raises
  `CollectorError`, register it in `composition.build_runtime`, add a config
  interval only when it needs its own politeness, and test parsers with
  recorded fixtures.
- Change modules or tabs: update `MODULE_KEYS`/`MODULE_TITLES_UK` in
  `domain/models.py`, `TAB_NAMES_UK` in `sheet_layout.py`, and the gauge,
  layout, and pipeline tests together.
- Change the CLI: edit `interfaces/cli/main.py` only, keep `print` there, and
  add tests in `tests/test_market_intel_cli.py`. Change publishing: keep
  `sheet_layout` pure; route every gog call through `GogClient`; preserve the
  fingerprint, skip-identity, and create-safety invariants; extend
  `tests/test_market_intel_gog_publisher.py`.

## Pointers

Root `AGENTS.md` covers the signals pipeline and repo-wide rules; its
`signals`-specific CLI, exception, and configuration rules do not apply here.
`README.md` documents user-facing usage; `config/.env.example` lists every
environment key; PR #42 carries module history.
