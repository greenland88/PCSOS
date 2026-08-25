# Agent-Ready Interface Contract

Status: required for all new research and production modules, and for all new or
materially changed storage interfaces.

PCS OS is the deterministic system of record. Future AI agents orchestrate its
engines and interpret compact results; they do not read raw option files,
recalculate core metrics, or override hard risk controls.

This contract is prospective. Existing interfaces may be migrated when they are
next changed; this document does not require an immediate rewrite of legacy code.

## Core Module Contract

Each core module must expose a public Python callable. A CLI, CSV export, report,
dashboard, or log may wrap that callable but cannot be its only interface.

Public inputs and outputs must use one of:

1. Pydantic models
2. typed dataclasses
3. a documented stable dictionary schema at a compatibility boundary

Pydantic models are preferred for external tool boundaries because they provide
validation and JSON Schema generation. Do not expose pandas DataFrames as the
only public result type.

Every key result must serialize to JSON using stable, documented field names.
Dates and timestamps use ISO 8601 strings at the serialized boundary. Timestamps
must include a timezone; UTC is preferred.

## Result Envelope

Every key result must contain these fields:

| Field | Contract |
| --- | --- |
| `module` | Stable engine/module identifier, not a display label |
| `version` | Version of the result schema or public module interface |
| `symbol` | Canonical uppercase underlying symbol |
| `as_of` | Requested market/business effective time |
| `status` and/or `action` | Value from a finite enum |
| `data_timestamp` | Latest source observation used by the calculation |
| `calculation_version` | Deterministic calculation/rule implementation version |
| `run_id` | Identifier shared by one research or decision run |
| `request_id` | Identifier for one callable/tool invocation |
| `reason_codes` | Stable machine-readable enum values; an empty list is allowed |

Decision results use the existing finite action set: `OPEN`, `WAIT`, `HOLD`,
`CLOSE`, or `ROLL`. Non-decision modules may define a module-specific finite
`status` enum. Never infer status, action, or rejection reasons from prose.

Additional structured fields belong under stable typed fields or a documented
`data` model. An optional `explanation` is for humans and must not contain facts
that are absent from the machine-readable fields.

Example serialized shape:

```json
{
  "module": "trend_snapshot",
  "version": "1.0",
  "symbol": "QQQ",
  "as_of": "2026-08-18",
  "status": "READY",
  "data_timestamp": "2026-08-18T20:00:00Z",
  "calculation_version": "trend-v1",
  "run_id": "run_...",
  "request_id": "req_...",
  "reason_codes": [],
  "data": {},
  "explanation": "Optional human-readable summary."
}
```

## Reason Codes And Compatibility

`reason_codes` must be a list of finite enum values such as
`MARKET_REGIME_BLOCKED`, `LIQUIDITY_REJECTED`, or `INSUFFICIENT_HISTORY`.
Free-form `reason` or `explanation` fields may coexist for display, but are not a
substitute for codes.

Field meanings cannot change silently. Additive optional fields are allowed in a
compatible schema version. Renaming/removing fields, changing types, changing
enum meaning, or changing requiredness requires a public schema version change
and migration notes. Calculation behavior changes require a new
`calculation_version` and the normal trading-rule change process when applicable.

## Audit And Replay

Every research run and decision run must have a `run_id`. Every callable or tool
invocation must have a `request_id`; callers may supply one for idempotency, or
PCS OS may generate one and return it.

Persist enough information to reconstruct and compare a call:

- normalized typed input, including the requested `as_of`
- complete serialized output
- module and result schema versions
- calculation/config version or immutable config hash
- source dataset identifiers, schema versions, and data timestamps
- run ID, request ID, invocation time, and completion status
- deterministic ordering for list results

Replay must call the same deterministic engine through the same Python API. Logs
alone are not an audit record, and an LLM response is not a deterministic result.

## Storage Boundary

Raw option CSV and bulk raw market data remain internal ingestion inputs. Agent
adapters must not expose them directly. Use Python, SQL, and DuckDB to validate,
filter, aggregate, and calculate features before returning compact typed results.

New storage interfaces must provide callable read/write/query methods with typed
records or stable schemas. Storage implementations may use CSV, Parquet, SQLite,
or DuckDB internally, but callers must not need to parse display reports or know
physical filenames to use a core engine.

CSV and reports are export adapters over structured results. They are never the
sole source of a key decision or research result.

## Planned Tool Surface

Keep domain APIs transport-neutral so thin adapters can later expose them through
MCP, REST, or local agent tools. Reserve these intended capabilities and naming:

```python
get_trend_snapshot(symbol, as_of)
evaluate_pcs_entry(symbol, as_of, expiration, short_strike, long_strike)
scan_pcs_candidates(as_of)
evaluate_position(position_id, as_of)
get_defense_state(position_id, as_of)
get_roll_candidates(position_id, as_of)
get_historical_similar_cases(...)
get_probability_estimate(...)
```

These are future domain APIs, not authorization to build a complete AI agent or
automatic live trading now.

## Review Checklist

Before merging a new core module or storage interface, verify:

- a Python callable exists independently of CLI/report code
- inputs and outputs are typed and JSON serializable
- the required result envelope and stable `reason_codes` are present
- all status/action values are finite enums
- run/request IDs and version/source metadata support audit and replay
- CSV/log/report output is generated from the structured result
- the agent boundary exposes compact derived data, never raw option datasets
- deterministic engines remain authoritative over AI interpretation
- schema and enum contracts have focused tests
