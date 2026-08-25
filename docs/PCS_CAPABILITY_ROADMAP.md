# PCS Capability Roadmap

This is the canonical registry of implemented, in-progress, planned, and
research-only PCS capabilities. It is an orientation and governance document;
it does not change production strategy rules.

Repository status summary: `PROJECT_STATUS.md`. Documentation map:
`docs/README.md`. Dated ticker reports and incident files are evidence
snapshots and must not override this registry, active routes/manifests, or
CURRENT artifact identities.

Status values are `COMPLETE`, `IN_PROGRESS`, `BLOCKED`, `PLANNED`, and
`RESEARCH_ONLY`.

## 1. Core PCS replay / data infrastructure

| Capability | Status | Current boundary or note |
|---|---|---|
| `PCSDataAccess` | COMPLETE | Canonical ticker-aware access boundary with manifest and bounded Parquet/DuckDB reads. |
| `options_v2` | IN_PROGRESS | Canonical dataset and reader exist; ticker cutovers and full validation remain incomplete. |
| Ticker source routing | COMPLETE | Routes are configuration-driven; failed cutovers remain on their prior validated route. |
| Ticker readiness gate | COMPLETE | Five strategy-neutral readiness flags plus manifest/provenance agreement; readiness is not profitability. |
| Historical purchased vendor sources | COMPLETE | Ingestion is bounded at the raw-data boundary; provenance and replay metadata are retained. |
| Batch 1 / Batch 2 source boundary | COMPLETE | Source batches remain distinct and are not silently merged into one unqualified population. |
| Exact strike identity | COMPLETE | Full option identity, including fractional strikes, is preserved for duplicate and replay checks. |
| Vendor conflict policy | COMPLETE | Exact duplicates are removed; unresolved same-key historical conflicts use first raw-file occurrence under the documented policy. |
| Phase 0 candidate universe | COMPLETE | Candidate universe normalization and persistence contracts exist; rejected candidates are preserved. |
| Phase 0 lifecycle marks | COMPLETE | Lifecycle validation and explicit missing-quote states are persisted; values are not imputed. |
| Event readiness | COMPLETE | Strict PIT and ex-post historical modes are explicit and fail closed where evidence is unavailable. |
| Stage 4A Entry Contract v2 | COMPLETE | All authoritative ticker populations have persisted v2 inputs, support states, PIT metadata, and exact identities. |
| Stage 4A full replay readiness | COMPLETE | Readiness audit passed; full replay remains not yet run. |
| Long-history PIT chunk/cache path | IN_PROGRESS | Bounded four-worker, 250-date checkpoint path exists; fresh/resume/parallel full equivalence and authoritative MSFT replay remain required. |
| Ticker Bear-State Rule | IMPLEMENTED — RESEARCH VALIDATION REQUIRED | PIT-safe daily single-stock state (`NORMAL`, `WEAK_BEAR`, `BEAR_CONFIRMED`); separate from `MarketRegimeEngine` and not a production block. |
| `breadth_positive` market input | COMPLETE — SEMANTICS VERSIONED | Legacy field name retained; current meaning is `SPY_QQQ_MARKET_CONFIRMATION` using SPY/QQQ close versus SMA50. It is not traditional constituent breadth. |

The `options_v2` TSLA cutover remains blocked by the duplicate-key gate and the
old canonical route's ambiguous quote keys. No strategy rule is changed by this
roadmap.

## 2. Profit target research

**PCS Fixed Profit Target Research** is `RESEARCH_ONLY` and `PLANNED`.

Targets: **40%, 50%, 60%, 70%, 75%, 85%**. Optional sensitivity targets are
**45%, 55%, 65%, and 80%**.

The study may compare total P&L, expectancy, profit factor, win rate, stop
rate, average holding days, maximum drawdown, capital efficiency, and
annualized return. Only the profit-close target changes in the experiment.
Entry rules, Safe Strike, DTE, stop rules, Planned Loss, candidate populations,
and lifecycle data remain fixed.

This research must not automatically modify production profit targets.

## 3. Annualized performance reporting

Annualized Performance Reporting is `COMPLETE`.

Account performance metrics:

- `total_realized_pnl`, `starting_equity`, `ending_equity`, `CAGR`, `max_drawdown`

PCS capital-efficiency metrics:

- `average_collateral_used`, `peak_collateral_used`, `annualized_return_on_average_collateral`

Planned-Loss efficiency metrics:

- `average_planned_loss_exposure`, `peak_planned_loss_exposure`, `annualized_return_on_average_planned_loss`

Collateral-based and Planned-Loss-based annualized metrics must not be called
CAGR. CAGR is reserved for a valid account-equity simulation.

## 4. Capital reuse metrics

Capital reuse analysis is `PLANNED` / `RESEARCH_ONLY`. It will record holding
calendar days, holding trading days, capital-days, capital turnover, and
average capital occupation. Its purpose is to test whether earlier exits (for
example, 40–50%) improve annualized capital efficiency even when profit per
trade is lower.

## 5. Exit policy research

### Current / base exit policy

The current production exit policy remains the baseline and is not changed by
this roadmap.

### Research exit variants

Future `RESEARCH_ONLY` work may examine fixed 40–85% profit targets, dynamic
profit protection, time-efficiency exits, stop behavior, and roll behavior.
Multiple exit-policy variables must not be combined in one experiment unless
explicitly approved.

## 6. Future ticker-specific PCS profiles

Later research will determine whether different tickers should use different
PCS methods. Initial examples are NVDA, AMD, TSLA, AMZN, MU, GOOGL, CRM, META,
AAPL, and VRT.

Possible differences include Safe Strike method, support method, profit target,
stop/Planned Loss, event risk, tail risk, premium compensation, and option
quality. These must not be optimized until Stage 4A replay/data readiness is
complete.

## 6A. Expanded Universe / Strict Entry Quality Study

Status: **PLANNED / RESEARCH_ONLY**.

After the current frozen OOS validation, expand the universe to approximately
25–30+ tickers, prioritizing additional non-technology tickers. The objective
is aggregate opportunity quality, not single-ticker trade frequency. The study
will test whether stricter versions of one existing entry factor at a time:

- remove more losing setups;
- reduce stop rate and tail loss;
- improve expectancy and profit factor; and
- improve worst trade and maximum drawdown.

Lower trade count is not a failure if the expanded universe retains sufficient
opportunities. The first pass must not combine multiple entry changes or add
new thresholds. Rules, candidate populations, and FINAL OOS remain frozen;
FINAL OOS is validation-only and cannot be used for tuning. No result may
change production strategy without the standard promotion gate.

## 6B. Research Visualization / Trade Replay Chart

Status: **PLANNED / P2 / RESEARCH_ONLY**.

This is an auxiliary read-only visualization tool, not part of the PCS replay
or decision path. Given a ticker and candidate/trade ID, it may render an
initial PNG (and later an HTML browser view) from authoritative artifacts,
including daily price, MA20/50/200, support, entry, short/long strike, Safe
Strike, exit/stop, event, regime, and P&L. It must support explicit
`ACCEPTED` and `FILTERED_WEAK_SUPPORT` markers.

The tool must not modify strategy rules, create alternate calculations or data
sources, or participate in `OPEN` / `NO_TRADE` decisions.

## 6C. Idempotent Existing-State Reuse

Status: **GOVERNANCE / REQUIRED FOR ALL DATA PIPELINES**.

Every ticker task must inspect and validate existing local daily data,
`options_v2` partitions, manifests, provenance, active routes, candidate and
Entry Contract artifacts, lifecycle outputs, OOS split/config, and eligibility
results before creating anything. Valid state is verified and reused; invalid
state is repaired only through the standard generic pipeline; missing state is
created only through that pipeline.

The pipeline must not overwrite valid partitions, rebuild valid manifests,
create duplicate routes or registrations, or create parallel ticker-specific
artifacts. Repeated onboarding is idempotent and must return
`ALREADY_COMPLETE` or resume only the missing/invalid portion.

## 6D. Early Recovery Confirmation After Breakdown

Status: **PLANNED / RESEARCH_ONLY**. This is a separate future research object:
`EARLY_RECOVERY_CONFIRMATION_AFTER_BREAKDOWN`. It does not authorize a new
production predicate, threshold, selector, or entry rule.

The objective is to determine whether information available **as of the entry
decision** can distinguish an early recovery after a breakdown from continued
downward pressure. It is explicitly not an attempt to predict bottoms. The
primary context is ticker-specific daily state; `MarketRegime` remains
secondary context only.

The work is motivated by the completed QQQ breakdown/pullback forensic audit:
profitable validation breakdown entries were associated with post-entry
rebounds, but the repository has no production reclaim/reconfirmation
predicate, and most such entries were continuing positions rather than
confirmed recoveries. These observations do not establish an entry signal.

Required phased design:

- Phase 1 is a descriptive, as-of-date feature audit of support usability and
  distance, breakdown duration, daily price/ATR/close-location/wick behaviour,
  higher-low and support-reclaim evidence, volume, trend slope, existing raw
  predicate states, and only available daily options facts.
- Phase 2 may test one pre-specified signal at a time, using only TRAIN
  multi-year walk-forward evaluation with setup-group separation. Missing
  fields remain `UNKNOWN`; they never imply `PASS` or `FAIL`.
- Phase 3 may define research-only `RECLAIMING_RESEARCH` or
  `EARLY_RECOVERY_CONFIRMATION_RESEARCH` states only if the prior phases show
  stable, repeatable TRAIN evidence. A fixed elapsed-day proxy is prohibited.
- Phase 4 may evaluate the incremental value of 5/15/30-minute data only if
  daily data cannot support a repeatable signal. Daily close on T can support
  no earlier than a T+1 entry decision.

Guardrails: no production logic/config/rule change; no threshold change; no
FINAL OOS read; no use of future prices, future options data, later exits, or
P&L in a decision-time feature; no validation-driven tuning. The already seen
2026 validation slice is `PREVIOUSLY_OBSERVED_VALIDATION_DIAGNOSTIC` only and
cannot define a new feature or rule. Candidate outcomes remain restricted to
`PRE_REBOUND_SIGNAL_CONFIRMED_IN_TRAIN`,
`RESEARCH_PROMISING_BUT_INSUFFICIENT`,
`NO_REPEATABLE_PRE_REBOUND_SIGNAL_FOUND`, or
`NOT_COMPUTABLE_WITH_AVAILABLE_DATA`.

This item depends on the preserved forensic evidence in
`research_outputs/qqq_breakdown_pullback_forensic_audit_20260821/` and must
retain its existing frozen artifacts and research boundaries.

Recorded research observation — SPY diagnostic (2026-08-22): the same
`DAILY_EARLY_RECOVERY_CONFIRMATION` study was run as a separate ticker-specific
research output. TRAIN contained 210 preserved entries with P&L `+$4,381`;
the previously observed VALIDATION diagnostic contained 38 entries with
baseline P&L `-$101`. The strongest single-signal diagnostic results were
`close_location` (`+$325`, PF 1.60, 27 retained entries), `iv_change` (`+$296`,
PF 1.49, 28 retained entries), `close_location_vs_3d_mean` (`+$293`, PF 1.45,
28 retained entries), and `downside_change` (`+$196`, PF 1.24, 31 retained
entries). All 248 rows passed `feature_date < entry_date`; 243 exact prior-day
contract quotes were available; FINAL OOS was not read. Status is
`RESEARCH_PROMISING_BUT_INSUFFICIENT`, not a production candidate.

## 7. Production vs research rule

> **Research results NEVER automatically become production strategy.**

Every research result must go through:

`RESEARCH` → `VALIDATION` → `CONTRACT OWNER DECISION` → `PRODUCTION CHANGE`

Codex must not promote the numerically best backtest parameter automatically.

## 8. Current work status

| Work item | Status |
|---|---|
| Phase 0 Artifact Contract | COMPLETE |
| Phase 0 Event Readiness | COMPLETE |
| Stage 4A Contract Boundary | COMPLETE |
| Entry Contract v2 | COMPLETE |
| Full Stage 4A Replay | NOT YET RUN |
| PCS Profit Target Research | PLANNED / RESEARCH_ONLY |
| Annualized Performance Reporting | COMPLETE |

## 10. AMD research status (2026-08-20)

AMD remains research-only. No production eligibility, stop rules, Safe Strike,
candidate population, routes, option data, position sizing, or profit targets
were changed.

| AMD research item | Status |
|---|---|
| Standard PCS | `RESEARCH — NO ROBUST EDGE CURRENTLY ESTABLISHED` |
| Profit target | `40% ROBUST_TARGET, BUT NEGATIVE STRATEGY` |
| Stop research | `PARTIAL — MIXED TEMPORARY + STRUCTURAL FAILURES` |
| Entry profile | `NO ROBUST ENTRY PROFILE EDGE` |
| Dynamic profit protection | `NOT PRIMARY REPAIR PATH` |
| Regime research | `BLOCKED — missing authoritative VIX + market breadth` |
| AMD regime hypothesis | `UNRESOLVED_BY_DATA` |

The AMD regime branch is parked until authoritative PIT VIX and market breadth
history is available and passes the unchanged `MarketRegimeEngine` input
contract. No substitute regime model and no additional AMD parameter sweeps are
authorized by this status.

## 9. Codex usage rule

Every Codex thread working on PCS should read this file before introducing a
new capability or changing an existing one. If a requested capability already
exists or is already planned, continue the existing design rather than creating
a duplicate implementation.

This roadmap does not authorize autonomous live trading and does not permit an
agent or research result to bypass market-regime hard stops, portfolio risk
limits, liquidity rejection, or position-sizing limits.
