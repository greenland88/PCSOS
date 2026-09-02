# PCS Universe Funnel Architecture Audit (U0)

## Scope

This audit freezes the initial implementation boundary for the ticker-neutral
pool scanner described by the universe-funnel contract. It is based on the
canonical production/data modules on `main` at commit
`673cbcf6da7877c2b4923a468b382c6ebc5ca441`.

## Reusable canonical components

| Concern | Existing implementation | Pool-funnel use |
|---|---|---|
| Universe loading | `pcs.data.universe.load_market_universe`, `merge_symbols` | Normalize and snapshot requested symbols; preserve duplicates as reasoned outcomes where needed. |
| Canonical data | `pcs.data.access.PCSDataAccess` | All daily/option reads; no raw CSV/Parquet/provider reads in pool code. |
| Data readiness | `pcs.research.ticker_readiness.preflight_ticker`, `pcs.data.strategy_readiness.ensure_strategy_ready` | Eligibility/readiness evidence and verified handles. |
| Daily structure | `pcs.trend.market_structure_engine.build_market_structure_engine` | Daily timing adapter; do not reproduce trend/support/pullback logic. |
| Entry gates | `pcs.entry.gates.EventGate`, `LiquidityGate`, `DTEGate`, `SafeStrikeGate`, `CreditEfficiencyGate` | Existing event/liquidity/contract validation only. |
| Portfolio risk | `pcs.risk.portfolio_risk.PortfolioRiskSnapshot` | Aggregate risk gate; no new risk limits. |
| Existing orchestration | `pcs.research.runner`, `pcs.research.general_pcs_runner` | Reference only; neither is the ticker-pool facade. |

## Existing direct-read/provider/promotion/recovery findings

The repository contains direct `read_prices`/`read_quotes` calls in legacy
research, onboarding, live-state, and CLI paths. The new pool path must call
only through pinned `PCSDataAccess` handles. It must not import or invoke
provider, promotion, or recovery services during an ordinary decision run.

The CLI has legacy data-update/onboarding commands and a one-ticker
`pcs-status` command. The pool command must be a separate explicit command and
must not implicitly call those commands or their implementation helpers.

## Configuration findings

The existing market-universe YAML contains a small configured PCS universe and
benchmarks; it is not the requested approximately-1700-symbol source. The
implementation therefore needs an explicit `UniverseSpec`/registry import
boundary and must not invent or silently expand the ticker population.

Existing PCS configuration owns DTE, safe-strike, credit, liquidity, and risk
thresholds. The funnel must resolve and reuse those values; this task does not
authorize threshold changes.

## Frozen ownership

| Owner | Files/boundary | Constraint |
|---|---|---|
| Coordinator | `src/pcs/pool/` shared models, snapshot, facade, stage wiring, CLI, manifests | Sole formal entry point; immutable run inputs; no duplicate engines. |
| Registry/eligibility | `src/pcs/pool/registry.py` | Static facts and registry persistence only; no full options-chain reads. |
| Daily timing | `src/pcs/pool/timing.py` | Adapter around `MarketStructureEngine`; no copied algorithm. |
| Options shortlist | `src/pcs/pool/options.py` | Only timing-ready symbols; existing selector/gates/config only. |
| Event/portfolio | `src/pcs/pool/final_gates.py` | Existing event/risk contracts; explicit event policy. |
| Invariants/tests | `tests/pool/` | Tests only unless a contract defect is demonstrated. |

Until isolated worker changes exist, the coordinator owns shared public models
and integration tests. No existing user research files may be staged.

## Initial integration order

1. Shared enums/result envelopes and immutable `PoolRunSnapshot`.
2. Registry and static eligibility.
3. Daily timing adapter.
4. Options/event/portfolio adapters.
5. Facade, artifact manifest, CLI, and invariant tests.

The first executable acceptance target is U1 (`RAW -> ELIGIBILITY -> DAILY`)
using fixtures and authorized canonical tickers. This audit does not claim
1700-ticker production readiness.
