# Phase 0 data integration contract

`pcs.research.phase0_data_integration` is the read-only boundary for Stage 4A
external data checks. Earnings rows require `event_type`, `symbol`,
`event_date`, `source`, `source_id`, and `event_asof`. A symbol with no trusted
rows is `EVENT_DATA_MISSING`; a trusted symbol with no row in the requested
window is `NO_EVENT_IN_WINDOW`. Missing data is never converted to no event.

Candidate and lifecycle artifacts may carry a `provenance_ref` foreign key.
The referenced registry row contains dataset, ticker, partition/path, source,
source table, version, checksum, query range, import time, run id, and request
id. This keeps artifact rows compact while allowing deterministic replay.

Daily OHLCV remains served by `DailyDataProvider` and the existing partitioned
data access layer; no second OHLCV system is introduced.

## Market confirmation contract

The legacy `MarketState.breadth_positive` field name is retained for interface
compatibility. Its current meaning is **`SPY_QQQ_MARKET_CONFIRMATION`**, not
traditional market breadth and not a constituent-membership breadth measure.
For trading date `t`:

```
breadth_positive = (SPY close(t) > SPY SMA50(t))
                   AND (QQQ close(t) > QQQ SMA50(t))
```

The input is built from the canonical SPY/QQQ daily OHLCV partitions. The
date-only historical replay convention is a post-session-close decision, so
the same date's close may be used only with `pit_status=PIT_SAFE` and
`available_as_of` after the New York session ends. A pre-close caller must use
the latest prior completed session. SMA50 warmup rows remain unavailable and
are never filled with a guessed boolean. No traditional breadth, historical
constituent membership, or full-universe symbol scan is part of this contract.

The deterministic artifact is
`data/derived/market_confirmation_daily.parquet`; its sidecar provenance and
validation records identify the canonical SPY/QQQ source versions and the
calculation version.

Stage 4A event evaluation supports two explicit modes. `EVENT_MODE_STRICT_PIT`
requires candidate-time evidence and remains fail-closed for retrospective-only
rows. `EVENT_MODE_EX_POST_HISTORICAL` may use authoritative actual event dates
for historical crossing/approach analysis, records that mode on every output
row, and never marks those rows PIT safe.

When an ex-post candidate window ends after the persisted source coverage end,
the row remains in the artifact with `event_coverage_complete=false` and
`event_readiness=FUTURE_EVENT_WINDOW_UNSUPPORTED`. It is excluded from
historical replay eligibility, but is not counted as historical event-data
quality failure and is not converted to `NO_EVENT_IN_WINDOW`.
