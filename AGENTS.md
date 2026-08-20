# PCS System Agent Rules

This repository implements a conservative but return-oriented PCS trading system.

Capital preservation is important.

Never silently change trading rules.

Any rule change must:
1. update `config/pcs_rules.yaml`
2. update documentation
3. update or add tests

Never allow AI to override:
- market regime hard stops
- portfolio risk limits
- liquidity rejection
- position sizing limits

Never add automatic live trading in V1.

Always distinguish:
- planned risk
- theoretical maximum loss

Always produce one of:
- OPEN
- WAIT
- HOLD
- CLOSE
- ROLL

Never send bulk raw market or options datasets to the LLM.

Download and persist data programmatically.

Use Python, SQL and DuckDB to filter, aggregate and calculate features first.

Only send compact summaries, exceptional records, and selected evidence to AI.

Historical database size must not determine AI context size.

## Parallel Execution Requirements

Use parallel execution wherever it is safe and correctness is unaffected.
Prefer maximum stable parallelism over serial execution for independent ticker,
ATR-target, and partition work.

### Stage 1 Safe Strike Generation

- ATR targets are `1.5`, `2.0`, `2.5`, and `3.0`.
- Run independent ATR targets in parallel; prefer four ATR workers when system
  resources remain stable.
- Reduce to three or two workers only when RAM pressure becomes high, swap
  starts, disk I/O becomes the bottleneck, or per-worker runtime materially
  degrades.
- Each ATR target must have a separate output and checkpoint.
- Workers must not share mutable dataframes or mutable state.
- Never overwrite the locked population.
- All ATR workers must use the same Trend PASS universe, the same monthly
  Parquet source, and the same qualification logic.

## Agent-Ready Interface Requirements

PCS OS is expected to be called by AI agents in the future. All new research and
production modules, and all new or materially changed storage interfaces, must
follow `docs/architecture/agent_ready_interfaces.md`.

- Every core module must expose a Python callable API. CSV files, reports, and
  logs are presentation outputs, not the only interface.
- Core results must use typed dataclasses, Pydantic models, or a documented stable
  dictionary schema, and must be JSON serializable without natural-language parsing.
- Status and action fields must use finite enums. Decision actions remain limited
  to `OPEN`, `WAIT`, `HOLD`, `CLOSE`, and `ROLL`.
- Machine-readable `reason_codes` are required on key results; an empty list is
  allowed when no reason applies. `explanation` may supplement them but must
  never be required for interpretation.
- Result envelopes must include `module`, `version`, `symbol`, `as_of`, `status`
  and/or `action`, `data_timestamp`, `calculation_version`, `run_id`, and
  `request_id`.
- Tool inputs and outputs must be persistable with enough version and source-data
  metadata to audit and replay a run.
- AI agents must call deterministic PCS OS engines. They must not read raw option
  CSV files or recompute core indicators and risk metrics.
- Storage APIs must return typed records or stable schemas suitable for Python,
  MCP, REST, and local-agent adapters.

Do not implement autonomous live trading or allow an agent-facing adapter to
bypass deterministic hard stops, portfolio limits, liquidity rejection, or
position sizing limits.
