# Pool Scan contract

The canonical implementation is `pcs.pool.runner.run_pcs_pool`, exposed by the
`pool-scan` CLI. Extend that path; do not create an alternate scanner.

## Execution and data boundaries

- Every scan is bounded and observable. After argument validation the CLI emits
  `POOL_SCAN_STARTED` immediately. Every stage has a finite timeout and returns
  one ordered outcome per requested symbol. A hang, timeout, process failure, or
  empty ticker result is not success.
- Read-only scans do not import, promote, recover, or write canonical data.
  Preparation is opt-in through `data_mode="PREPARE_THEN_SCAN"` plus
  `auto_prepare_data=True` and still uses the control plane.
- Daily and options data resolve only through `PCSDataAccess` and
  generation-pinned verified handles. Explicitly supplied access objects do not
  disable handle resolvers. Options resolution starts with logical
  `_resolve_route("options", symbol)`; consumers never guess `options`,
  `options_v2`, or `options_v3`.
- Reuse a manifest snapshot only when its path equals the routed manifest path.
  A daily/default snapshot cannot resolve an options generation on another route.
  Missing route, generation, provenance, checksum, schema, or coverage fails
  closed with the specific reason code. Never fall back to raw or unrelated data.
- Required options coverage is `min_date <= decision_date <= max_date`.

Stage-B discovery does not approve a contract. A caller that has the existing
PCS selector/DecisionEngine and its complete market, event, and portfolio
context may provide the explicit `contract_selector` adapter to
`run_pcs_pool`; only an adapter result with `status=PASS` sets
`options_status=PASS`. Selected contract identity, selector result, reason
codes, and data identity are retained. Without that adapter, candidates remain
`DISCOVERED` with `CONTRACT_SELECTION_NOT_CONNECTED`.

Historical comparison validates the run manifest, completion state, payload
hashes, run id, and non-empty/non-`unknown` identities. An explicit
`baseline_run_id` is authoritative; timestamp-selected runs are observation
only. Effective session, universe, mode, code, engine, profiles, and refresh
policy determine observed comparability; a manifest/generation change is
recorded separately and may be attributed to recovery only when a linked
receipt and read identity support it. Missing receipts are incomplete history.
Recovery evidence may be attached per symbol, including nested
`admission_result` values, but READY counts still come from the independent
scan result.

`PoolScanResult.preparation_results` exposes sanitized per-symbol preparation
records, including nested partition admission evidence and promotion
read-backs; live provider result objects are excluded. Its
`recovery_summary` counts only partition statuses and promotion receipts
present in that evidence. These fields are audit output and do not change
readiness or promotion semantics.

When an output directory is requested, the same fields are persisted in
`preparation_recovery.json` and the run manifest records its recovery summary;
the artifact writer also creates `reconciliation.json` against the newest
prior current run in that directory. Identity mismatches are explicitly marked
non-comparable rather than attributed to recovery, and no live provider object
is reopened.

## Runtime and result invariants

Handle resolution is single-flight and cached by the complete decision identity,
including symbol and trading session. Initialize worker-local result collections
before guarded execution so error paths preserve the original exception.
`spread_count` is nonzero only when real spread candidates were produced under
the current approved options status contract; fabricated inputs and relaxed
validation are forbidden.
If options fail after timing succeeds, retain eligibility/timing, return
`options_status=DATA_BLOCKED`, zero spreads, and the original options reason code.
Eligibility, timing, options, event, portfolio, and final trade readiness remain
separate; missing required adapters cannot be reported as `PCS_TRADE_READY`.

## Focused acceptance checklist

Material Pool Scan, routing, or runtime changes require focused regression
evidence for these nine risks (select applicable items for a local change;
shared routing, scheduling, or handle changes cover cross-module items):

1. bounded execution and immediate startup observability;
2. one ordered result per requested symbol;
3. daily/options handle single-flight behavior;
4. logical-to-physical options routing;
5. rejection of a manifest snapshot from another route;
6. exact options date-window validation;
7. preservation of the original reason code on options failure;
8. a timing-ready verified-options fixture producing at least one real spread;
9. a negative fixture returning `DATA_BLOCKED`, zero spreads, and no masked exception.

A valid-JSON CLI smoke test with `spread_count=0` proves CLI completion only; it
does not prove the verified options path or trade readiness.

## Built-in EOD decision context adapter

`pool-scan --decision-context-json /path/to/context.json` now wires the built-in
`PoolContextAdapters` in both the read-only child and preparation entrypoint.
The Python equivalent is `load_pool_context_adapters(path, rules_path=...)`
expanded into `run_pcs_pool`. Omission preserves discovery-only behavior.
This adapter is EOD-only; it does not claim intraday snapshot freshness.

The input is a user-supplied, source-backed snapshot, not a live account or
calendar connector. JSON schema version 1 has `schema_version: 1` and
`symbols: {TICKER: {market: RECORD, events: RECORD, portfolio: RECORD}}`.
Each RECORD requires a nonempty `source_id`, `as_of` matching the completed
option decision session, and `data`. The caller must supply genuine upstream
source evidence; a source label alone is not external source verification.

- `market.data`: all fields of `pcs.models.market.MarketState`, explicitly
  supplied, including VIX/drawdown/selloff. No omitted bullish defaults.
- `events.data`: calendar records with `symbol`, `event_type`, `event_date`,
  `event_date_known_at_entry`; `events.coverage_end` must cover expiration.
  An explicit empty list means the source confirms no events through that
  coverage boundary. Absent, stale, unknown-PIT or wrong-symbol data blocks.
- `portfolio.data`: `planned_loss`, `theoretical_max_loss`, `bucket_risk`,
  `ticker_risk`, `account_capital`. Risk values must be finite and nonnegative;
  capital must be positive. Empty input never becomes a zero-risk account.

Selection builds formal canonical market context using generation-pinned
underlying/QQQ/SPY/SOXX frames through the run runtime, reuses the existing
candidate constructor, and calls the real DecisionEngine. Only OPEN with a
positive recommended size passes. Quotes remain from the scan's chain. Context
hash, selected contract and full decision are retained for audit. No raw option
file reads, provider calls, imports or strategy parameter changes are added.
EventGate remains authoritative. Final portfolio approval checks the selected
position's incremental planned loss against existing total, bucket and ticker
limits. READY candidates are individual alternatives against one snapshot;
their sizes cannot be summed into a jointly approved basket.

Focused verification: `PYTHONPATH=src python -m pytest
 tests/pool/test_context_adapters.py tests/pool/test_final_correctness.py
 tests/pool/test_preparation_orchestration.py tests/pool/test_artifacts.py
 tests/pool/test_process.py -q`. Adapter fixtures prove code wiring and rejection
invariants, not live account readiness or a successful canonical business scan.

Implementation validation (2026-09-05, base `c62db50`): the focused command
above returned **67 passed, 1 failed**. All 17 new adapter tests passed,
including real DecisionEngine OPEN/sizing -> final PCS_TRADE_READY on fixtures,
RED rejection and read-only child injection. The existing
`test_valid_legacy_daily_is_formally_admitted_and_idempotent` failed with
`PROMOTION_EXPECTED_ACTIVE_MISMATCH`; the same failure was reproduced in a
separate untouched checkout of `c62db50`. No canonical business scan, provider
probe, import or research run was performed by this adapter change.


## Current EOD decisions and scan-only use

Scanning does not require an account snapshot. Without a decision context the
runner performs daily timing and options discovery, preserves missing event
coverage, and does not claim portfolio approval or trade readiness. Account
capacity and sizing are deferred until a trade decision is requested.

The same `--decision-context-json` adapter also accepts schema version 2 with
`decision_mode: CURRENT_EOD`, a timezone-aware `decision_as_of`, and date-only
`market_data_as_of`. The CLI `--as-of` must equal `decision_as_of` and mode must
be EOD. The market date must be the exchange's last completed session,
including weekends and holidays. Candidate dates/DTE use the decision date;
quote reads and derived market context remain pinned to the completed session.
No alternate strategy engine is introduced.

Current portfolio records require timezone-aware `portfolio_observed_at`;
current event records require timezone-aware `event_known_at`. Both must be on
the UTC decision date and no later than the decision timestamp. This adapter
supports same-day snapshots, not a claim of live broker connectivity. Events
still require source-backed PIT metadata and coverage through expiration.
Selection evidence records all four timestamps and the explicit decision mode.
Schema version 1 remains historical PIT and rejects next-day account snapshots;
when bound to a run, timezone-aware observations after its timestamp also fail.

Options `required_start` and `required_end` are quote/trade dates, not expiration
dates. September 4 quotes for October expiration require September 4 coverage
and a Q3 trade-date partition, not Q4 quotes. The registered ClickHouse adapter
filters TradeDate and the importer partitions by trade_date year/quarter.


Run snapshots fingerprint the executing PCS Python source tree and effective
option rules. These content identities replace unknown code/engine labels and
remain independent of the canonical data manifest identity. A source digest is
not a git commit; delivery evidence records the actual commit separately.
