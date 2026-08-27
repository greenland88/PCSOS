# Phase 0 replay infrastructure

> Historical readiness snapshot. The API contract below remains useful, but the
> final paragraph records an older repository inventory. Current readiness must
> come from `pcs.research.ticker_readiness`, active routes/manifests, and a
> CURRENT artifact manifest.

`pcs.research.phase0_replay` is a research-only contract layer. It preserves
accepted and rejected candidate rows, validates point-in-time timestamps,
maps historical targets only to listed strikes, and calculates coverage.

It does not run Phase 1, change `config/pcs_rules.yaml`, or alter production
entry, Safe Strike, Variant B, Planned Loss, stop, scanner, or live profiles.

Phase 1 readiness requires 100% coverage for the required lifecycle inputs for
the selected method universe. Missing inputs are recorded as reason codes;
they are never imputed or inferred.

The storage boundary exposes `normalize_candidate_universe`, `validate_lifecycle`,
`persist_phase0_artifacts`, and `write_coverage_report`. These research-only
functions preserve rejected candidates and explicit missing-quote states.

At the time of this snapshot, repository coverage was insufficient for Phase 1:
DuckDB contained
17,756,008 option rows for QQQ (2010-11-22 through 2026-07-31), but no TSLA/MU
rows and no persisted ticker-specific Phase 0 universe/lifecycle coverage
report. Those counts are historical evidence only and must not override current
ticker readiness.
