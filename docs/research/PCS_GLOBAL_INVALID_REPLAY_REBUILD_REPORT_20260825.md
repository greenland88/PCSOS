# PCS Global Invalid Replay Rebuild Report — 2026-08-25

## Final status

The inventory contains 28 `INVALID_REPLAY_ARTIFACT` rows across QQQ, NVDA, AMD, META, AMZN, COST, MSFT, and TSLA. The authoritative table is `research_outputs/system_integrity/global_rebuild_summary.csv`.

| Status | Count |
|---|---:|
| REBUILT_PASS | 12 |
| BLOCKED_BY_CANONICAL_DATA | 8 |
| BLOCKED_BY_POLICY | 6 |
| BLOCKED_BY_RESEARCH_SPEC | 2 |
| PENDING | 0 |

## Contract and cleanup

Every `REBUILT_PASS` replacement has a reproducibility manifest validated by `validate_reproducibility_manifest`. Corrected replays use canonical `PCSDataAccess`, one economic selected trade per episode/date, and the typed `SelectedTradeLedger` lifecycle boundary. Adaptive artifacts were not silently converted to fixed mode. Old artifacts were deleted where a distinct replacement exists, or recorded as `REPLACED_IN_PLACE` where the canonical runner regenerated the same path. Blocked artifacts were retained.

## Blockers

- AMD: canonical provenance incomplete.
- AMZN and TSLA: canonical options route/physical coverage unavailable; no legacy/raw fallback was used.
- NVDA authoritative baseline: canonical lifecycle/corporate-action admission is fail-closed after one-entry reconstruction; no completed lifecycle.
- COST frozen SMA50: one lifecycle quote series unavailable.
- MSFT: runbook preflight passed data/PIT stages but reached zero at `PRECURSOR_EPISODES`; signal execution is not defined, so it is `MSFT_ENGINEERING_BLOCKED`.
- Adaptive artifacts: FIXED-only scope, so they remain policy-blocked.
- META global quality: legacy candidates expose only two dates while the legacy report claims fourteen episodes; signal population is incomplete.

## Safety controls

FINAL OOS was not read. Production strategy definitions and thresholds were not modified. Canonical routing and invalid quote handling remained fail-closed. SPY remains audit-only because no auditable replay artifact was in scope.

## Verification

Focused integrity tests passed. Remaining blocked rows are explicit and have no replacement artifact, so their old artifacts remain retained for audit traceability.
