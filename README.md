# PCS Decision Assistant V1

PCS OS is a deterministic-first Put Credit Spread decision and research
system. It provides rule-based entry and position evaluation, canonical
historical data access, point-in-time trend context, guarded research replay,
paper-trading snapshots, audit metadata, and local persistence.

It is not an automated trading bot. The repository contains no live order
placement or broker write path.

## Start here

Read these files before changing the system:

1. [`AGENTS.md`](AGENTS.md) — mandatory safety and research boundaries.
2. [`docs/PCS_CAPABILITY_ROADMAP.md`](docs/PCS_CAPABILITY_ROADMAP.md) — canonical capability registry.
3. [`PROJECT_STATUS.md`](PROJECT_STATUS.md) — current engineering state and blockers.
4. [`docs/README.md`](docs/README.md) — documentation map and source-of-truth rules.

## Install and verify

```powershell
pip install -e .[dev]
pytest
```

The current branch does not ship the former Streamlit dashboard. Use the CLI,
typed Python APIs, and persisted JSON/CSV/Parquet artifacts described below.

## Local decision and data commands

```powershell
# Evaluate mock candidates and positions through the deterministic rule engine.
pcs-lite analyze-mock

# Collect read-only option-chain snapshots from a local Hood JSON export.
pcs-lite collect-options --hood-json .\hood_snapshot.json QQQ NVDA MSFT

# Run synthetic portfolio stress scenarios.
pcs-lite stress .\portfolio.json

# Persist a deterministic daily paper-trading snapshot; no orders are placed.
pcs-lite simulate-daily --as-of 2026-08-18

# Incrementally update canonical daily/options_v2 inputs from local inbound files.
pcs-lite update-data MSFT
```

`simulate-daily` writes under `research_outputs/paper_trading/YYYY-MM-DD/`:

- `paper_trading_snapshot.json` — typed, agent-ready result envelope;
- `paper_trading_summary.csv` — daily action and risk totals;
- `paper_trading_snapshots.csv` — one row per candidate or position.

Planned risk is kept separate from theoretical maximum loss.

## Canonical data boundary

Research and replay code must read ticker-aware data through `PCSDataAccess`.
Routes are defined in `config/data_source_routes.yaml`; manifests and source
identity are part of artifact validation. Live research readers must not bypass
this boundary with direct raw CSV or Parquet reads.

New ticker admission is fail-closed and follows:

`DATA_DISCOVERY -> DAILY_VALIDATION -> OPTIONS_VALIDATION -> PIT_FEATURE_BUILD -> STATE_TIMELINE_BUILD -> CONTRACT_SELECTION_SMOKE_TEST -> LIFECYCLE_SMOKE_TEST -> RESEARCH_READY`

See [`docs/architecture/ticker_onboarding.md`](docs/architecture/ticker_onboarding.md)
and [`docs/architecture/pcs_ticker_readiness.md`](docs/architecture/pcs_ticker_readiness.md).

### Unified market-data import control plane

All consumers declare requirements through:

```python
from pcs.data.control_plane import get_market_data_status, ensure_market_data
status = get_market_data_status("PLTR", {"start": "2018-01-01", "end": "2026-08-26"})
result = ensure_market_data("PLTR", {"datasets": {"daily": {"required": True}}})
```

Use `pcs-lite market-data-status SYMBOL` for a read-only plan or
`pcs-lite import-market-data SYMBOL` to execute registered import handlers.
The source allowlist is [`config/market_data_source_registry.yaml`](config/market_data_source_registry.yaml),
the remediation registry is [`config/data_remediation_registry.yaml`](config/data_remediation_registry.yaml),
and the derived catalog is `data/manifests/canonical_data_catalog.parquet`.
Provider calls and canonical writes are restricted to the control-plane/data
adapter boundary; consumers must not call providers or edit raw/canonical files.

## Guarded research runner

All new research uses a validated `ResearchSpec` through one entry point:

```powershell
$env:PYTHONPATH = "src"
python -m pcs.research run --spec config/research/templates/new_entry.yaml --dry-run
python -m pcs.research run --spec config/research/templates/new_entry.yaml --real-preflight
python -m pcs.research run --spec config/research/templates/new_entry.yaml --execute
```

Research modes have strict population semantics:

- `EXISTING_TRADE` — frozen trades/candidates; management or filtering only.
- `NEW_ENTRY` — complete point-in-time ticker calendar; may discover new dates.
- `CONTRACT_VARIANT` — frozen dates; may reselect contracts only.
- `CURRENT_STRATEGY_REPLAY` — full-calendar plumbing replay using an explicit rule set.

FINAL OOS and production writes are blocked unless explicitly authorized. A
research result never promotes itself to production. See
[`docs/research/UNIFIED_RESEARCH_RUNNER.md`](docs/research/UNIFIED_RESEARCH_RUNNER.md).

## Trend engine

The `pcs.trend` public API now includes the complete deterministic trend-context
pipeline, not only base indicators:

- base indicators: SMA20/50/200, ATR14, ADX14, RSI14;
- moving-average structure and slopes;
- confirmed swing / market structure analysis;
- relative strength;
- trend cleanliness and predictability;
- pullback and support analysis;
- point-in-time trend snapshots;
- interpretation and trend scoring.

External modules should import the public functions from `pcs.trend`, including
`calculate_base_indicators`, `build_trend_snapshot`, `interpret_trend`, and
`score_trend`. TA-Lib remains isolated behind the indicator implementation.

## Current capability surface

- Config-driven capital, regime, event, DTE, liquidity, Safe Strike, credit,
  portfolio, sizing, profit, hold, close, and roll rules.
- Pydantic and typed result models with stable actions and reason codes.
- Canonical daily/options access, routing, manifests, provenance, corporate
  action price basis, readiness checks, and incremental updates.
- Entry Contract v2 and point-in-time trend/support context.
- Unified research runner, artifact identity/lifecycle checks, strategy-transfer
  templates, lifecycle replay, and annualized reporting.
- SQLite journal, Parquet/CSV/JSON artifacts, paper-trading simulation, and
  synthetic stress tests.

For exact completion and blocker status, use the capability roadmap and project
status rather than inferring readiness from module existence.

## Safety

The deterministic rule engine is authoritative. AI helpers may classify news or
thesis risk into structured outputs, but cannot override hard gates, portfolio
risk limits, position sizing, artifact identity, data readiness, or OOS guards.

Decision actions are limited to `OPEN`, `WAIT`, `HOLD`, `CLOSE`, and `ROLL`.

## Agent-ready interfaces

Core modules expose typed, JSON-serializable Python APIs. CSVs, reports, and
logs are presentation adapters, not the only interface. See
[`docs/architecture/agent_ready_interfaces.md`](docs/architecture/agent_ready_interfaces.md).
