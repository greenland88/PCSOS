# PCS Capability Roadmap

## Mandatory startup rule

**Every new PCS Codex/Agent thread MUST read `AGENTS.md` and `docs/PCS_CAPABILITY_ROADMAP.md` before doing work.**

## Canonical system state

### COMPLETE

- PCSDataAccess / per-ticker routing
- Purchased vendor source boundary
- Phase 0 Artifact Contract
- Phase 0 Event Readiness
- Stage 4A Contract Boundary

### IN PROGRESS

- Entry Contract v2

### NOT YET RUN

- Full Stage 4A Replay

### RESEARCH_ONLY / PLANNED

- PCS Performance & Exit Research
- Fixed profit target research: 40/50/60/70/75/85%
- Optional 45/55/65/80 sensitivity
- CAGR
- Annualized return on average collateral
- Annualized return on average Planned Loss
- Capital-days / capital turnover
- Max drawdown
- Profit factor
- Expectancy
- Stop rate
- Ticker-specific exit target research

## Phase 0 frozen populations

| Ticker | Candidates |
|---|---:|
| NVDA | 826 |
| AMD | 474 |
| TSLA | 1119 |
| AMZN | 794 |

Phase 0 final status: **100% lifecycle coverage**, **100% quote availability**, **0 missing quote candidates**; all four are **FULLY_REPLAYABLE**.

## Legacy NVDA benchmark

1823 missing observations across 192/826 candidates.

Status: `LEGACY_NON_RECONSTRUCTABLE_BENCHMARK` (non-blocking).

## Entry Contract v2 definitions

### `expected_move_1d`

- Replaces `normal_daily_move` in v2.
- Do not claim historical equivalence.

### `nearby_strikes`

- Same date/expiration/option type.
- Exact distinct strikes; short strike excluded.
- Two nearest below plus two nearest above.
- Range `0..4`.
- No liquidity filtering.

### `later_expirations`

- Distinct expirations strictly later than candidate expiration.
- Same option type.
- Entry-time chain only.
- No liquidity filtering.
- No invented DTE window.

### `price_confirmation`

`price_confirmation = confirmation_score / 4 * 100`

Mapping: `0/1/2/3/4 -> 0/25/50/75/100`.

Existing 70/30 trend scoring is unchanged.

## Current Entry Contract readiness

- AMD: **READY**
- TSLA: **READY**
- NVDA: **BLOCKED** — `support_level` unavailable for 76 rows
- AMZN 309-row Stage4A artifact: **BLOCKED**
  - Support unavailable for 84
  - Short-leg quote fields unavailable for 158

The AMZN 309 Stage4A population and Phase 0 794 population are materially different; exact overlap is 18. Authority must be reconciled before the full Stage 4A replay.

## Event modes and readiness

Event modes: `EVENT_MODE_STRICT_PIT`, `EVENT_MODE_EX_POST_HISTORICAL`.

Current event readiness:

- AMD: 474/474 historically observable
- NVDA: 196/200 historically observable
- 4 NVDA rows: `FUTURE_EVENT_WINDOW_UNSUPPORTED`
- 0 historical event-data-quality missing

## Purchased vendor boundary

### Batch 1 authoritative

NVDA, QQQ, TSLA, AMZN. Canonical source: `data/parquet/options_v2`.

### Batch 2 authoritative for later ticker universe

`K:\\BaiduNetdiskDownload\\USDailyOptions`, including AMD, META, HOOD, AAPL, GOOGL, MU, CRM, NFLX, SPY, VRT, etc.

Batch 2 may physically overlap NVDA/QQQ/TSLA/AMZN, but is validation-only for those four unless explicitly promoted.

Vendor conflict policy: `VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW`.

## Deflate64 status

Adapter compatibility path implemented. Environment currently lacks 7z/7za/7zz; fail closed with `VENDOR_ARCHIVE_UNREADABLE_DEFLATE64`.

## Production promotion rule

`RESEARCH` -> `VALIDATION` -> `CONTRACT OWNER DECISION` -> `PRODUCTION CHANGE`

Research never auto-promotes to production. Check this roadmap before creating a capability, preserve frozen populations unless explicitly changed, and do not run the full Stage 4A replay until readiness passes.
