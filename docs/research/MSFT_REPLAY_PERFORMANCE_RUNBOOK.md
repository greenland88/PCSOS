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

## Future improvement

The next performance step is an incremental/vectorized PIT snapshot builder
that preserves the existing snapshot and gate semantics while eliminating
per-date full-history slicing. It must pass the same bounded exact-equivalence
tests before being used for the full TRAIN replay.
