# PCS Agent Startup Contract

## Mandatory startup rule

**Every new PCS Codex/Agent thread MUST read `AGENTS.md` and `docs/PCS_CAPABILITY_ROADMAP.md` before doing work.**

Before doing any PCS work, every agent must read, in this order:

1. `docs/PCS_CAPABILITY_ROADMAP.md`
2. `docs/architecture/ticker_onboarding.md`
3. `docs/phase0_data_integration.md`
4. Any relevant task-specific contract, strategy, or replay documentation.
5. `docs/research/UNIFIED_RESEARCH_RUNNER.md` before starting or modifying research.
6. `docs/research/PARAMETER_EXPERIMENT_STANDARD.md` and `config/research/parameter_experiment_policy.yaml` before performing any strategy parameter research.

All agents performing strategy parameter research MUST follow the parameter experiment standard and machine policy: vary one independent parameter family at a time, freeze unrelated parameters, use the minimum necessary candidate set, pass data/accounting validity gates, reuse valid artifacts, and stop after the scoped decision question. The unified runner must reject explicitly declared experiments that vary more than one family.

## Capability and change control

- Check the roadmap before creating a capability.
- Do not create duplicate implementations; extend the canonical implementation or document an explicit replacement.
- Research never auto-promotes to production.
- No threshold changes without owner approval.
- Preserve frozen candidate populations unless explicitly changed and documented.
- Promotion is strictly: `RESEARCH` -> `VALIDATION` -> `CONTRACT OWNER DECISION` -> `PRODUCTION CHANGE`.
- Do not change strategy code in documentation-only work.

## PCS Research Population Routing — Mandatory

All new research MUST use the unified Research Runner and a validated
ResearchSpec. Agents must not bypass ResearchSpec or invoke a research script
as an alternate entry point.

Before starting any PCS research, classify it into exactly one mode:

1. `EXISTING_TRADE`
   - Starts from a frozen candidate/trade ledger.
   - Only for stop, profit target, lifecycle, trade management, or filtering existing trades.
   - Must never be used to discover entry dates not present in baseline.
2. `NEW_ENTRY`
   - Starts from the ticker's full point-in-time daily calendar.
   - Required for recovery, reclaim, stabilization, support rejection, and every hypothesis capable of creating a new entry date.
   - Must discover signal dates first, then select contracts using the options chain available at those dates.
   - A frozen trade ledger is forbidden as the starting population.
3. `CONTRACT_VARIANT`
   - Freezes entry dates but reselects contracts.
   - Used for ATR, strike, delta, DTE, width, credit, and liquidity studies.

Mandatory rules:

- Never infer research mode from whichever artifact is easiest to load.
- Never silently substitute frozen candidates for a full-calendar replay.
- Every run requires a validated ResearchSpec.
- If the specification is incomplete, stop with `SPEC_INCOMPLETE`.
- Report the first funnel stage that becomes zero.
- Never use `NOT_COMPUTABLE_WITH_AVAILABLE_DATA` when a more specific cause can be identified.
- FINAL OOS is inaccessible unless the ResearchSpec explicitly authorizes it.
- Research code must not change production rules or frozen artifacts.
- Read `docs/research/RESEARCH_FRAMEWORK.md` before creating or modifying PCS research code.
- `NEW_ENTRY` always starts from the complete ticker point-in-time trading-day
  calendar. A frozen candidate/trade ledger is forbidden as its starting
  population.
- `FINAL OOS` is forbidden by default and requires explicit ResearchSpec
  authorization.
- Research runs default to isolated research output directories and may not
  write production rules or frozen artifacts.

## Research artifact lifecycle

- Each `research_id` has exactly one `CURRENT` authoritative artifact set.
- Proven-invalid artifacts are deleted from the active workspace; Git history
  is the historical record and no executable legacy reproduction path is kept.
- A successful replacement invalidates/replaces the previous artifact set.
- Synthetic fixtures belong only in tests or temporary directories and must
  never be written to `research_outputs/`.
- `research_outputs/` may contain only `PCS_CANONICAL_DATA` results.
- Runners generate outputs through a temporary directory, validate them, and
  replace the current set; failed runs must not leave partial artifacts.
- Artifact manifests are mandatory. Readers require `current=true`, valid
  hashes, matching spec/code/data versions, and canonical data source;
  otherwise the result is `STALE_ARTIFACT`.
- Timestamped conflicting directories for one `research_id` are forbidden.

## Deterministic PCS boundaries

- Use `PCSDataAccess` and per-ticker source routing.
- Use exact strike and exact contract identity only.
- Respect Batch 1 versus Batch 2 vendor source authority.
- Fail closed rather than weakening validation.
- Never allow AI to override market-regime hard stops, portfolio risk limits, liquidity rejection, or position-sizing limits.
- Never add automatic live trading in V1.
- Distinguish planned risk from theoretical maximum loss.
- Decision actions must be one of: `OPEN`, `WAIT`, `HOLD`, `CLOSE`, `ROLL`.
- Agents must call deterministic PCS engines and must not read raw option CSVs or recompute core indicators and risk metrics.

## Data and context discipline

- Download and persist data programmatically.
- Use Python, SQL, and DuckDB to filter, aggregate, and calculate features first.
- Send only compact summaries, exceptional records, and selected evidence to AI.
- Historical database size must not determine AI context size.

## Parallel execution and validation

- Parallel work must use isolated outputs or worktrees. Workers must not share mutable dataframes or mutable state.
- For independent Stage 1 ATR targets (`1.5`, `2.0`, `2.5`, `3.0`), use separate outputs and checkpoints, the same Trend PASS universe, monthly Parquet source, and qualification logic; never overwrite the locked population.
- Rerun validation on the final merged HEAD.
- Do not run the full Stage 4A replay until readiness passes.

## Agent-ready interfaces

All new research and production modules, and materially changed storage interfaces, must follow `docs/architecture/agent_ready_interfaces.md`:

- Expose a Python callable API; CSVs, reports, and logs are presentation outputs, not the only interface.
- Use typed dataclasses, Pydantic models, or a documented stable dictionary schema that is JSON serializable.
- Use finite enums for status and action fields.
- Include machine-readable `reason_codes` on key results.
- Result envelopes must include `module`, `version`, `symbol`, `as_of`, `status` and/or `action`, `data_timestamp`, `calculation_version`, `run_id`, and `request_id`.
- Persist sufficient version and source-data metadata to audit and replay runs.

## Unified Market Data Control Plane

All ticker data readiness and import requests MUST use
`pcs.data.control_plane.get_market_data_status(symbol, requirements)` and
`ensure_market_data(symbol, requirements)`. The canonical source allowlist is
`config/market_data_source_registry.yaml`; remediation rules are in
`config/data_remediation_registry.yaml`.

Consumers MUST NOT call Massive, Yahoo, ClickHouse, raw CSV/TXT/ZIP readers,
or mutate canonical Parquet directly. Provider calls are restricted to
registered adapters and all writes must pass the staging/promotion boundary.
Before reporting a data blocker, the control plane must inspect canonical
coverage and all authorized sources; source-unavailable and pre-listing states
must retain machine-readable reason codes. Strategy, frozen artifacts, and
 FINAL OOS are outside the import system and must not be changed by it.

## Pool Scan production contract

The canonical Pool Scan implementation is
`pcs.pool.runner.run_pcs_pool`, exposed through the `pool-scan` CLI.
Agents must extend this path rather than creating an alternate scanner.

### Execution requirements

- Every scan must be bounded and observable.
- The CLI must emit `POOL_SCAN_STARTED` immediately after argument validation.
- Every execution stage must have a finite timeout and return one explicit
  outcome per requested symbol.
- A process that hangs, times out, or returns no ticker outcomes is not a
  successful scan.
- Preserve input symbol ordering in the final result.
- Do not perform provider imports, promotion, recovery, or canonical writes
  unless `auto_prepare_data` was explicitly enabled by the caller.
- Read-only scans must not mutate canonical storage.

### Canonical data requirements

- Daily and options data must be read only through `PCSDataAccess` and
  generation-pinned verified handles.
- Passing an explicit `PCSDataAccess` instance must not disable the canonical
  daily or options handle resolvers.
- Options routing must begin with the logical request
  `_resolve_route("options", symbol)`.
- The routed physical dataset may be `options`, `options_v2`, or `options_v3`;
  consumers must not guess the physical version.
- A manifest snapshot may be reused only when its resolved path matches the
  routed manifest path.
- A daily/default manifest snapshot must never be used to resolve an options
  generation routed to another manifest.
- Options coverage for a decision session requires
  `min_date <= decision_date <= max_date`.
- Missing route, generation, provenance, checksum, schema, or date coverage
  must fail closed with the most specific machine-readable reason code.
- Do not fall back to an unrelated manifest or ordinary/raw data reader after
  verified resolution fails.

### Runtime and result correctness

- Daily and options handle resolution must be single-flight and cached by the
  complete decision identity, including symbol and trading session.
- Worker-local result collections must be initialized before guarded execution
  so an error path cannot raise `UnboundLocalError` and mask the original
  failure.
- `spread_count` is nonzero only when options status is `PASS` and real spread
  candidates were produced.
- An options data failure after timing succeeds must preserve the ticker's
  eligibility/timing result, set options status to `DATA_BLOCKED`, return zero
  spreads, and preserve the original options reason code.
- Keep eligibility, timing readiness, options readiness, event readiness,
  portfolio readiness, and final trade readiness as separate states.
- Never report `PCS_TRADE_READY` when required event or portfolio adapters are
  absent. Do not fabricate adapter results to make a smoke test pass.

### Mandatory Pool Scan validation

Any material change to Pool Scan, data routing used by Pool Scan, or its runtime
must include focused regression tests for:

1. bounded execution and immediate startup observability;
2. one ordered result per requested symbol;
3. daily-handle and options-handle single-flight behavior;
4. logical-to-physical options routing;
5. rejection of a manifest snapshot belonging to a different route;
6. exact options date-window validation;
7. preservation of the original reason code on options failure;
8. a timing-ready positive fixture that traverses the verified options-handle
   path and produces at least one real spread;
9. a negative fixture that returns `DATA_BLOCKED`, zero spreads, and no masked
   exception.

A CLI smoke test returning valid JSON with `spread_count=0` proves only that the
CLI completed. It does not prove that the options path works. Options-path
acceptance requires the positive timing-ready regression test to produce a
nonzero spread count.

Agents must report exact test commands and results. Do not claim that the full
repository suite is green unless it was actually run successfully.

## Completed implementation and acceptance record (2026-08-26)

The unified market-data import path was implemented and pushed on branch
`codex/msft-replay-clean`. Relevant commits:

- `73bcb36` automatic ClickHouse adapter wiring
- `64da41f` canonical NVDL options route
- `759f739` automatic options promotion tests and hardening
- `3ecebe8` detailed unified import result envelope
- `7345f13` removal of legacy ZIP fallback
- `fcd4e93` detailed `ensure_market_data()` execution fields
- `03830e1` route onboarding compatibility through the control plane
- `b3bdadb` execution-time source-registry authorization
- `29dac04` transactional rollback for multi-partition promotion

The canonical implementation is `pcs.data.control_plane` plus
`pcs.data.clickhouse.PCSClickHouseClient`. The supported write path is:

`import-market-data` -> control plane -> authorized source adapter -> isolated
staging -> schema/quality/ticker validation -> transactional promotion ->
manifest/provenance/catalog/ledger -> `PCSDataAccess` replay/readiness.

Do not reintroduce a loader in CLI or onboarding code. `onboard()` is only a
compatibility wrapper and delegates to `ensure_market_data()`. Missing or
invalid provider state is fail-closed with reason codes including
`CLICKHOUSE_CREDENTIALS_MISSING`, `CLICKHOUSE_CONNECTION_FAILED`,
`CLICKHOUSE_SOURCE_TABLE_UNAVAILABLE`, `AUTHORIZED_SOURCE_NO_ROWS`, and
`SOURCE_NOT_AUTHORIZED`. Legacy fallback and `CLICKHOUSE_LOADER_NOT_PASSED`
are not valid wiring solutions.

NVDL acceptance was executed with the requested 2018-01-01 to 2026-08-26
window. The provider start was clamped to the NVDL canonical listing boundary.
The approved ClickHouse table was `firstrate.options_kline_1d`. Coverage
returned 1,054,510 physical rows, 1,039,298 unique contract keys, 527,212
calls, and 527,298 puts, covering 2023-09-26 through 2026-08-25. Canonical
replay contains 1,016,021 NVDL rows; duplicate executable keys and conflicting
executable keys are zero. NVDL readiness passed `DATA_READY`, `PIT_READY`,
`OPTIONS_READY`, `CONTRACT_SELECTION_READY`, `LIFECYCLE_READY`, and
`PCS_RESEARCH_READY`, with no blockers.

Focused acceptance command:

`python -m pytest tests/data/test_control_plane.py tests/data/test_control_plane_boundary.py tests/data/test_clickhouse.py tests/data/test_pcs_data_access.py tests/data/test_logical_options_routing.py tests/data/test_readiness.py -q`

It passed 39 tests on the accepted HEAD (with only a Windows pytest-cache
permission warning). Do not claim the unrelated full repository suite is green
without rerunning it.

Covered Call audit findings fixed in the pushed history: entry uses bid, close
uses ask, roll credit uses new bid minus old ask, liquidity fields are required
for roll selection, missing lifecycle/context fails closed, and the
position-roll dollar/unit error is corrected. Do not use midpoint economics or
treat missing event/market/OI/volume context as a SELL. NVDL must not inherit
NVDA parameters and has no validated Covered Call profile.

The following files were intentionally left uncommitted because they were
existing user research work and must not be staged without explicit owner
approval: `config/covered_call/pltr_covered_call_research.yaml`,
`scripts/run_pltr_cc_*_train.py`, `src/pcs/covered_call_research/`, and
`tests/data/test_covered_call_*.py`.
