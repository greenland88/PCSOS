# PCS Agent Startup Contract

## Mandatory startup rule

**Every new PCS Codex/Agent thread MUST read `AGENTS.md` and `docs/PCS_CAPABILITY_ROADMAP.md` before doing work.**

Before doing any PCS work, every agent must read, in this order:

1. `docs/PCS_CAPABILITY_ROADMAP.md`
2. `docs/architecture/ticker_onboarding.md`
3. `docs/phase0_data_integration.md`
4. Any relevant task-specific contract, strategy, or replay documentation.
5. `docs/research/UNIFIED_RESEARCH_RUNNER.md` before starting or modifying research.

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
