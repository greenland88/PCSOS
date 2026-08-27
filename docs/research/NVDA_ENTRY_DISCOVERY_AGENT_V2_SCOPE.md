# NVDA PCS Entry Discovery Agent V2 scope

This agent is an NVDA-only research workstream for discovering a PCS mode
library. It starts from the existing broad authoritative NVDA TRAIN outcome
universe (623 executable dates) and does not rebuild or replace that population.

The reusable families `PCS_TREND_CONTINUATION` and
`PCS_CONSTRUCTIVE_RECOVERY` are frozen reference families. Their definitions,
thresholds, and consumed validation evidence are not changed or retuned.

New work must test materially different structures, such as pullback,
support/reclaim, post-selloff, range/consolidation, volatility opportunity, or
market-confirmed modes. A naming change or small threshold variation of either
preserved family is not a new mode.

Bad-case research is PIT-safe and covers `NORMAL LOSS`, `STOP LOSS`, and
`TAIL LOSS`. It may conclude `CAUTION`, setup-specific `NO_TRADE`, global
`NO_TRADE`, or `NO RELIABLE FILTER`; it must not force a filter.

Every new-entry hypothesis uses the unified Research Runner with a validated
`ResearchSpec`. FINAL OOS is sealed. Research outputs are isolated and cannot
write production rules or frozen artifacts. A candidate becomes a reusable
family only after authoritative replay, independent episode analysis,
multi-year stability, leave-one-episode-out analysis, PnL concentration,
tail-risk review, and structural-sensitivity review.

Out of scope: AMZN data-quality repair, SPY broad-map performance engineering,
cross-ticker transfer, and cross-ticker infrastructure maintenance.
