# Long-History Replay Performance Runbook

## MSFT incident record — 2026-08-24

The MSFT TRAIN replay was not strategically invalid. The initial
`real-preflight` became slow and was interrupted during PIT trend-structure
construction. MSFT contains 10,188 daily rows, so evaluating a full-history
DataFrame independently for every date creates a large repeated-computation
and allocation cost.

Observed causes:

- repeated base-indicator calculation;
- repeated confirmed-swing discovery;
- repeated self-relative-strength joins and close-frame construction;
- repeated per-date DataFrame slicing inside the canonical snapshot path;
- unbounded concurrent snapshots caused an approximately 36 GB memory spike
  with 8 threads.

## Required safeguards

- Use the canonical MSFT spec:
  `config/research/templates/msft_current_strategy_replay.yaml`.
- Do not change strategy gates, thresholds, production rules, frozen pools,
  or FINAL OOS access.
- Reuse only PIT-safe daily caches whose values are independent of future rows:
  base indicators, confirmed swings filtered by `confirmed_at`, and the
  self-benchmark relative-strength result.
- Run the bounded numerical-equivalence test before a full replay. The
  validated checks were 20 rows and 260 rows, both exact equalities.
- Use bounded chunks of 250 dates and four workers maximum. Eight concurrent
  snapshot evaluations are unsafe because they multiply large intermediate
  DataFrame allocations.
- Check process memory and responsiveness during execution. A failed or
  interrupted run must not be treated as a strategy result.
- Require an authoritative artifact before classifying the strategy. Until
  then the only valid classification is `MSFT_ENGINEERING_BLOCKED`.

## Canonical commands

Run from the repository root with the branch's source tree on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = "src"

# Validate the ResearchSpec and effective rule set without reading market data.
python -m pcs.research run `
  --spec config/research/templates/msft_current_strategy_replay.yaml `
  --dry-run

# Build or resume the canonical PIT timeline and report data readiness only.
python -m pcs.research run `
  --spec config/research/templates/msft_current_strategy_replay.yaml `
  --real-preflight

# Execute only after readiness and bounded-equivalence checks pass.
python -m pcs.research run `
  --spec config/research/templates/msft_current_strategy_replay.yaml `
  --execute
```

The canonical spec is TRAIN-only through `2025-12-31`, uses
`outcome_horizon_policy=SPLIT_CUTOFF`, keeps `final_oos_access=false`, and does
not allow production changes. Its research replay intentionally sets
`regime_gate=false`; this is a declared research rule-set choice, not a change
to the production `DecisionEngine`.

## Resume and cache contract

- The runner evaluates 250-date chunks with at most four shared-cache threads.
- Chunk checkpoints live beneath
  `research_outputs/msft_current_strategy_replay_active_options_v2_20260824/pit_chunks/<identity>/`.
- The identity binds ticker, exact daily source identity, date range, trend
  feature config, research rule set, and corporate-action input.
- Valid chunks are reused. Missing chunks are recomputed. Corrupt, short, stale,
  or identity-mismatched chunks are not accepted.
- The assembled `pit_state_timeline.parquet` is written through a validated
  temporary file and atomically replaced.
- Completion of preflight is not a strategy result. Classification requires the
  complete replay outputs and a CURRENT, hash-valid artifact manifest.

## Acceptance checks

Before calling the engineering blocker resolved, record all of the following:

1. bounded old-path versus cached-path exact equality;
2. fresh versus resumed timeline equality;
3. one-worker versus four-worker deterministic equality;
4. peak memory and elapsed time;
5. canonical daily/options route identities;
6. complete funnel and lifecycle counts;
7. CURRENT artifact-manifest validation;
8. confirmation that FINAL OOS and production config were untouched.

## Future improvement

The next performance step is an incremental/vectorized PIT snapshot builder
that preserves the existing snapshot and gate semantics while eliminating
per-date full-history slicing. It must pass the same bounded exact-equivalence
tests before being used for the full TRAIN replay.
