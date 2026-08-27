# PCS System Integrity / Reproducibility Audit

Status: `IN_PROGRESS — implementation complete; environment/regression cleanup remains`

This audit records the system-level repair prompted by the NVDA, QQQ, and
adaptive configuration incidents. It does not promote research, change
strategy thresholds, modify production rules, or open FINAL OOS.

## Implemented boundaries

- `pcs.research.integrity_contract` provides deterministic hashes, typed
  candidate/selected-trade/lifecycle ledger kinds, and cardinality checks.
- Lifecycle admission exposes `from_selected_trade_ledger`; candidate ledgers
  are rejected at that boundary.
- Adaptive evaluations record consumed configuration fields and fail with
  `CONFIG_NOT_CONSUMED` if an adaptive evaluation reaches execution without
  consuming configuration.
- Route activation performs a bounded file-open and Parquet schema smoke test;
  unreadable files fail with `CANONICAL_FILE_NOT_READABLE`.
- Artifact manifests record the actual Git SHA. Manifests without the required
  reproducibility identity are not authoritative and are classified
  `LEGACY_REFERENCE_INCOMPLETE`.
- The reader now rejects even legacy manifests that incorrectly claim
  `current=true` unless `reproducibility_complete=true` and every required
  identity is present.

## Focused verification

The focused integrity, lifecycle, adaptive, route-promotion, and artifact
tests pass (`12 passed` in the latest run). `compileall` and `git diff --check`
also pass.

## Current readiness evidence

The latest readiness JSON is under `research_outputs/pcs_data_readiness/`.
For the required audit universe: NVDA, QQQ, META, SPY, COST, and MSFT pass;
AMD is blocked by `PROVENANCE_INCOMPLETE`; TSLA and AMZN are blocked by
`OPTIONS_ROUTE_OR_SOURCE_UNAVAILABLE`. Readiness is an infrastructure gate,
not a profitability result.

No canonical dataset was demoted, rebuilt, or deleted by this repair. QQQ's
invalid quote rows remain represented by the existing quarantine evidence;
route promotion continues to require a clean, readable, provenance-backed
dataset. No route was activated as a workaround for a failed gate.

## Regression evidence

NVDA frozen regression has been executed with the fixed
`PCS_NVDA_TREND_CONTINUATION_V1` and `PCS_CONSTRUCTIVE_RECOVERY_V1` rules.
Current full-calendar discovery is 35/20 episodes versus legacy 26/17. The replay
contained 167/75 contract candidates but only 20/11 selected economic trades,
respectively; candidates were not treated as lifecycles. The difference is
classified as a signal-population/reference-coverage difference pending
authoritative legacy ledger reconstruction.

QQQ canonical replay completed for all three fixed strategies after readiness
passed: Controlled Reset (908 lifecycles, +$4,324), Recovery Stabilization (42,
+$43), and SMA50 Reclaim (31, +$391). The detailed report is
`research_outputs/frozen_strategy_regression/QQQ/QQQ_FROZEN_REGRESSION_REPORT.md`.
The prior QQQ report that says `BLOCKED_BEFORE_REPLAY` is superseded by the
new readiness evidence and must not be used as the final result.

## Explicit non-changes

- No strategy threshold changed.
- No production strategy logic changed.
- No frozen candidate population was overwritten.
- FINAL OOS remains sealed.

## Remaining verification

The full repository suite currently has 523 passing tests and 10 failures.
Those failures are existing data/artifact-state assumptions (missing or
permission-denied canonical partitions, incomplete AMD artifact files, and a
fixture lifecycle artifact), not failures in the focused integrity tests. A
manifest inventory found seven existing manifests that claim CURRENT but lack
the required identity fields; they are now rejected by the reader as
`LEGACY_REFERENCE_INCOMPLETE` and have not been silently promoted or rewritten.
