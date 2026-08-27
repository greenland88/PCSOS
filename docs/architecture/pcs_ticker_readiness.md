# PCS ticker-readiness gate

All PCS strategy research must call `pcs.research.ticker_readiness.preflight_ticker`
or `run_batch` before loading a research specification. The gate is
strategy-neutral and fail-closed. A `PCS_RESEARCH_READY=YES` result requires
all five component readiness flags to be `YES`; a `NO` result must expose every
blocker in the per-ticker JSON and report.

The five required flags are:

| Flag | Evidence |
|---|---|
| `DATA_READY` | canonical daily route, exchange-session coverage, OHLCV integrity, and price-basis consistency |
| `PIT_READY` | non-empty PIT indicator/state population with explicit warmup handling |
| `OPTIONS_READY` | route/manifest/provenance agreement, unique exact keys, valid quotes/expirations/strikes, and usable 30–45 DTE rows |
| `CONTRACT_SELECTION_READY` | at least one deterministic exact-contract smoke case |
| `LIFECYCLE_READY` | the smoke case completes with exit date and realized P&L |

`PCS_RESEARCH_READY=YES` additionally requires no manifest/provenance blocker.
One successful recent PIT smoke row does not claim full historical PIT coverage;
the Research Runner builds and identity-checks the requested full timeline.

The persisted batch artifacts are under
`research_outputs/pcs_data_readiness/`. They are diagnostic readiness outputs,
not strategy results, and must never be used to promote rules or authorize
FINAL OOS reads.

Readiness is not profitability. A failed gate is a data/infrastructure result,
not a rejected strategy hypothesis. A passed gate authorizes only the declared
research operation; it does not authorize production changes.
