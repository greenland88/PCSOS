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
