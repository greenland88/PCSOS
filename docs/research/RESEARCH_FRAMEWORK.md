# PCS Research Framework

This is the mandatory design contract for PCS research. Read it before adding a
research runner. Research is descriptive and cannot promote itself to production.

## Population routing

| Mode | Starting population | Permitted purpose | Forbidden |
|---|---|---|---|
| `EXISTING_TRADE` | frozen candidate/trade ledger | lifecycle, stops, targets, management, filtering | discovering dates absent from the ledger |
| `NEW_ENTRY` | complete ticker point-in-time daily calendar | recovery, reclaim, stabilization, support rejection, any new-entry hypothesis | starting from a frozen ledger |
| `CONTRACT_VARIANT` | frozen entry dates | strike, delta, ATR, DTE, width, credit, liquidity variants | changing entry dates |

`CURRENT_STRATEGY_REPLAY` is a guarded plumbing/reproduction mode for an
explicit current rule set over a ticker calendar. It is not a fourth
hypothesis-discovery mode and must not be used to evade the three population
routes above. It requires `population_source.type=ticker_daily_calendar`,
explicit rules, canonical options/lifecycle dependencies, and the same FINAL
OOS and production-write guards.

Correct: `BREAKDOWN t0 -> RECOVERY t1 -> NEW PCS ENTRY t1` is `NEW_ENTRY`.
Incorrect: filtering a frozen PCS candidate ledger to test whether recovery at
t1 created an entry that baseline never generated. A frozen candidate ledger
cannot test new opportunities absent from baseline.

`entry_date` does not require the market to remain in `BREAKDOWN`; the setup may
break down at t0 and satisfy recovery/confirmation at t1.

Every run uses a validated ResearchSpec. Mode is never inferred from the easiest
artifact. Missing or contradictory specifications stop as `SPEC_INCOMPLETE`.

## Command phases

- `--dry-run` validates intent, population routing, and effective rules. It
  does not establish data readiness.
- `--real-preflight` reads declared canonical dependencies and may build or
  resume PIT state, but does not execute an unfrozen signal or lifecycle.
- `--execute` is allowed only after the spec and ticker readiness pass. It
  still cannot read unauthorized FINAL OOS or write production/frozen state.

## PIT and split boundaries

All features and contract quotes are as-of the decision date. No future price,
quote, lifecycle result, or P&L may affect signal or contract selection. TRAIN
may define a research method. VALIDATION diagnostic runs use the frozen method
without retuning. FINAL OOS is inaccessible by default and requires explicit
ResearchSpec authorization; this framework's dry-run never reads it.

## NEW_ENTRY funnel

`ALL_TRADING_DAYS -> FEATURE_READY_DAYS -> PRECURSOR_EPISODES -> SIGNAL_DATES -> EVENT_ELIGIBLE_DATES -> CONTRACT_AVAILABLE_DATES -> LIQUIDITY_ELIGIBLE_DATES -> SIMULATED_ENTRIES`.

Every stage records `first_zero_stage`, `input_count`, `output_count`,
`exact_reason`, and `remediation`. `UNKNOWN` is not converted to FAIL or PASS.
The standard statuses are the enum in `research_framework.py`: specific data,
gate, sample, `COMPUTABLE`, and `COMPLETED` statuses only. Use the most specific
cause instead of generic not-computable wording.

## New ticker admission

`DATA_DISCOVERY -> DAILY_VALIDATION -> OPTIONS_VALIDATION -> PIT_FEATURE_BUILD -> STATE_TIMELINE_BUILD -> CONTRACT_SELECTION_SMOKE_TEST -> LIFECYCLE_SMOKE_TEST -> RESEARCH_READY`.

Each stage is reported independently. A ticker that is not ready is a data or
readiness result, not evidence that its hypothesis failed.

## Examples and safeguards

`EXISTING_TRADE` may calculate a stop outcome for a trade ID already in a sealed
ledger; it may not create a new signal date. `CONTRACT_VARIANT` may reselect a
$5 or $10 spread for the same frozen entry dates; it may not add a date. A
`NEW_ENTRY` recovery study must enumerate the full daily calendar, construct PIT
state, discover t1, and only then query the t1 options chain.

Research outputs belong under `research_outputs/`; production and frozen
artifacts are protected by the runner. No research result changes production
rules or frozen artifacts.
