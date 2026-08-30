# Universal Multi-Ticker Covered Call Research

The covered-call research boundary is ticker-independent.  Ticker-specific
parameters belong in `CoveredCallProfile`; unvalidated profiles fail closed.

## Public research APIs

- `build_sell_timing_features(daily)` builds backward-looking PIT features.
- `run_sell_timing_research(symbol, daily, market, iv_by_date=...)` emits a
  descriptive timing funnel and the `ALWAYS_SELL_BASELINE` control.
- `run_contract_selection_research(symbol, entry_dates, data_access=...)`
  evaluates DTE/delta selection on frozen entry dates and retains rejected
  candidates.
- `run_covered_call_portfolio(symbols, ...)` keeps ticker data, profiles,
  capacity, and reports isolated.
- `reconcile_option_only_ledger(trades)` and
  `summarize_option_only_by_year(trades)` exclude stock P&L from option P&L.
- `persist_covered_call_artifacts(...)` and
  `validate_covered_call_artifacts(...)` provide isolated CURRENT artifact
  output with hash validation.

All research outputs are descriptive/research-only.  No result promotes a
production rule or opens FINAL OOS.

## Required execution order

SELL TIMING → STRIKE/CONTRACT → DTE → PREMIUM/LIQUIDITY → BTC → ROLL.

Roll is checked only after a canonical trigger.  A triggered day with no
eligible candidate holds the current call and retries on the next trading day.

## Current readiness evidence

The latest canonical preflight found:

| Ticker | Readiness | First blocker |
|---|---|---|
| QQQ | ready | none (`NOT_REQUIRED` earnings) |
| SPY | blocked | `OPTIONS_CANONICAL_FILE_ACCESS_DENIED` |
| NVDA | ready | none |
| AMD | blocked | `OPTIONS_CANONICAL_FILE_ACCESS_DENIED` |

These are data/readiness outcomes, not evidence of strategy failure.

For `OPTIONS_CANONICAL_FILE_ACCESS_DENIED`, the permitted remediation is an
owner-approved ACL repair or canonical re-import/promotion of the affected
partition. Readers must not use legacy files, alternate parquet paths, or
nearest-date quotes as a workaround.
