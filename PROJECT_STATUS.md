# Project Status

Updated: 2026-08-19

## Safe Strike ATR Research

**COMPLETE — FINAL DEFAULT ATR = 2.3**

- Conservative alternative: 2.5 ATR
- Tested values: 1.8, 2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
- Stage 1, Stage 2, and Stage 3 outputs preserved
- Stage 4A audit preserved; full production-engine replay was not claimed because required historical inputs were unavailable
- Live PCS configuration: unchanged
- Additional ATR testing: closed

Canonical record: `research_outputs/safe_strike_atr_research_final.md`

## Current Boundary

The research parameter is finalized at 2.3 ATR, but production/live PCS has not
been switched. Any future production change must follow the approved rules
configuration, documentation, and regression-test process.

## Support Response / Buying Pressure

**PLANNED — research only; no production Defense Engine changes**

- Add a unified historical 5-minute OHLCV data layer through PCS Data Access.
- Design an independent `SupportResponseEngine` covering support touch, reclaim,
  higher low, rejection, intraday recovery, volume response, and optional
  relative-strength response.
- Use deterministic states: `NONE`, `EARLY`, `CONFIRMED`, and `FAILED`.
- Validate with historical replay, including real AMD 5-minute cases for
  support holds versus support failures.
- Compare temporary excursion versus structural failure using recovery, breach,
  stop, MAE/MFE, P&L, expectancy, drawdown, and time-to-recovery metrics.
- Do not integrate with production HOLD/CLOSE/ROLL decisions until out-of-sample
  research demonstrates reliable separation and the production criteria are met.
- Support Response may supplement, but may not override, planned-loss limits,
  hard stops, liquidity rejection, position sizing, daily structure, or event
  risk rules.

Current feature status: `PLANNED`.

## Future Strict Entry Quality Study

**PLANNED — research only; start after broader universe onboarding and current OOS validation.**

Objective: expand the research universe to approximately 25–30+ tickers,
including additional non-technology names, and test whether stricter entry
quality filters improve the aggregate opportunity set without optimizing for
single-ticker trade frequency.

The study will measure loser removal, stop rate, tail loss, expectancy, profit
factor, worst trade, and maximum drawdown. A lower trade count is acceptable if
the expanded universe retains sufficient opportunities.

Experiment controls:

- Freeze the current baseline and FINAL OOS; FINAL OOS is never used for tuning.
- Change only one already-defined entry factor per experiment.
- Do not add new thresholds, invent new factors, or perform ticker-specific
  tuning in the first pass.
- Preserve candidate populations and report aggregate-universe impact before
  considering any ticker-specific profile.
- Keep all results in `RESEARCH_ONLY` until the normal
  `RESEARCH → VALIDATION → CONTRACT OWNER DECISION → PRODUCTION CHANGE` gate.

## Research Visualization / Trade Replay Chart

**PLANNED — P2 / auxiliary research tool; not on the current execution path.**

Purpose: provide a read-only chart for a selected `ticker + candidate/trade id`
using authoritative artifacts only. The initial PNG view may include daily
price, MA20/50/200, support, entry, short/long strikes, Safe Strike, exit/stop,
event, regime, and P&L, with explicit `ACCEPTED` and
`FILTERED_WEAK_SUPPORT` markers. An HTML browsing view may be added later.

Boundaries: visualization only; it must not modify strategy logic, participate
in `OPEN`/`NO_TRADE` decisions, or create alternate data or indicator sources.

## Idempotent Existing-State Reuse

**GOVERNANCE RULE — applies to every future ticker/data task.**

Before creating or importing any artifact, inspect and validate existing local
daily data, options_v2 partitions, manifests, provenance, active routes,
candidate populations, Entry Contract artifacts, lifecycle artifacts, OOS
split/config, and eligibility results.

- Existing and valid: verify and reuse.
- Existing but invalid/incomplete: report the exact gap and repair only through
  the standard generic pipeline.
- Missing: create only through the standard generic pipeline.

Never overwrite valid partitions or manifests, create duplicate routes or
ticker registrations, or create parallel ticker-specific artifacts. Repeated
onboarding must return `ALREADY_COMPLETE` or resume only missing/invalid
portions.
