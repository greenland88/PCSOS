# Global PCS Trend-Gate Hardening Plan

Status: PLAN_RECORDED — implementation intentionally deferred.

## Objective

Prevent PCS candidates in a confirmed broad/medium-term downtrend from being
presented as good or OPEN candidates. The rule must apply globally through the
canonical decision path, not through NVDA-specific logic or a presentation-only
patch.

## Planned changes

1. Canonical trend classification
   - Persist `trend_direction`, `trend_health`, `trend_state`, and
     `setup_context` for every daily prescreen result.
   - Add an explicit `COUNTER_TREND_REBOUND` state when price is recovering
     while the higher-timeframe/medium-term structure remains bearish or
     unconfirmed.
   - Preserve existing configured thresholds; do not introduce ticker-specific
     thresholds.

2. Global prescreen ordering
   - Enforce market/regime and long-term/medium-term trend gates before support,
     timing, options, credit, DTE, or ATR candidate ranking.
   - A bearish/broken trend must fail closed with machine-readable reason codes.
   - A counter-trend rebound may be reported for diagnostics but cannot be
     labelled `OPEN`, `BEST`, `SAFEST`, or `RECOMMENDED`.

3. Daily evidence fields
   - Expose close versus SMA20/50/200, MA slopes/alignment, ATR14, ADX14,
     RSI14, MACD line/signal/histogram and state, volume, volume MA50,
     dollar-volume metrics, RVOL, and explicit comparison states.
   - Preserve PIT semantics: every field must use data available through the
     signal date only.

4. Gate and decision reasons
   - Add/standardize reason codes for `DOWNTREND`, `TREND_UNCONFIRMED`,
     `COUNTER_TREND_REBOUND`, `VOLUME_CONFIRMATION_MISSING`, and any existing
     support/event/options rejection.
   - Ensure downstream option economics cannot override an earlier trend hard
     stop.

5. Outputs and reports
   - Add the above fields and gate results to global pool CSV, Markdown, JSON,
     and callable result envelopes.
   - Include primary rejection reason plus secondary diagnostics for every
     rejected and near-miss ticker.
   - Keep NVDA audit output as a consumer of the same global interface; no
     separate NVDA rule path.

6. Tests and validation
   - Add unit tests for bearish structure, counter-trend rebound, weak-volume
     recovery, confirmed uptrend, missing data, and PIT date boundaries.
   - Add integration coverage proving options credit cannot turn a trend reject
     into `OPEN`.
   - Run focused trend/gate/discovery tests, then rerun global discovery on the
     final merged code and compare funnel counts/reason codes.
   - Do not alter strategy parameters, FINAL OOS access, frozen populations, or
     the running global-pool update artifact.

## Deferred execution boundary

Implementation starts only after the current global pool update has a safe
checkpoint/atomic boundary. The running update must not be cancelled,
restarted, or made to write into the new diagnostic/report outputs.
