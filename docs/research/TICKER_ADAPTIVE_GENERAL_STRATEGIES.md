# Ticker-adaptive general PCS strategies

This research-only capability resolves a configuration for a general strategy
archetype and ticker from PIT-safe underlying behavior. The canonical entry
predicates, options selector, lifecycle engine, and production rules remain
separate and unchanged.

## Supported archetypes

The existing catalog admits Trend Continuation, Constructive Recovery,
Controlled Reset, Recovery Stabilization After Reset, and SMA50 Reclaim After
Weakness. Historical NVDA/QQQ strategy IDs remain available for frozen artifact
reproduction; the general IDs are the transfer interface.

## Resolution inputs

`pcs.strategies.adaptive_profiles.resolve_strategy_config` accepts a canonical
daily frame and an optional `as_of` date. It measures realized volatility,
normal pullback depth, trend persistence, recovery speed, volume behavior, and
optional option quote coverage. Rows after `as_of` are excluded before any
measurement. No trade ledger, exit, P&L, PF, win rate, or FINAL OOS data is
read.

The result is a typed, JSON-serializable configuration envelope with module,
version, calculation version, ticker, as-of date, characteristics, resolved
research parameters, and reason codes.

## Fixed execution boundaries

Resolution does not alter the frozen execution constants: DTE 30–45, Safe
Strike 2.3 ATR, minimum credit/width 0.10, liquidity rules, lifecycle
semantics, or market-regime hard stops. The resolver is research-only and does
not promote a configuration to production.

When multiple archetypes match one date, the family runner preserves every
`matched_strategy_ids` value but executes at most one canonical economic trade
for that date.
