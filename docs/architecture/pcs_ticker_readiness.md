# PCS ticker-readiness gate

All PCS strategy research must call `pcs.research.ticker_readiness.preflight_ticker`
or `run_batch` before loading a research specification. The gate is
strategy-neutral and fail-closed. A `PCS_RESEARCH_READY=YES` result requires
all five component readiness flags to be `YES`; a `NO` result must expose every
blocker in the per-ticker JSON and report.

The persisted batch artifacts are under
`research_outputs/pcs_data_readiness/`. They are diagnostic readiness outputs,
not strategy results, and must never be used to promote rules or authorize
FINAL OOS reads.
