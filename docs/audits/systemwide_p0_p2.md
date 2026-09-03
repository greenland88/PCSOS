# PCSOS system-wide audit — P0/P1/P2 inventory

Scope: canonical data, readiness/recovery, public execution, PCS, CSP/Put,
Covered Call, PIT/as-of semantics, options identity, and broad discovery.

## P0

| Finding | Evidence | State |
|---|---|---|
| Non-ready data must not enter a strategy | `execution_contract.py` now checks dependency `data_status` before invoking the executor | FIXED; regression coverage passes |
| PIT scan could terminate the process on Windows | concurrent pandas/NumPy access violation in full-suite run, stack in `research/runner.py` | FIXED; chunk evaluation is serial |
| Live freshness failure was exposed as a strategy WAIT | `data/live_market_state.py` returned `LiveMarketState(status=\"WAIT\")` when live gates failed | FIXED; failed live gates return `BLOCKED` |

## P1

| Finding | Evidence | State |
|---|---|---|
| Strategy phase could read a different options snapshot than readiness | PCS/CSP/CC had post-readiness ticker reads | PARTIALLY FIXED; PCS/CSP/CC target-chain reads are pinned |
| Covered Call executor performed a second data refresh | `covered_call_executor.py` called `ensure_market_data` after readiness | FIXED; readiness is the sole refresh boundary |
| Broad discovery silently truncated OPEN results | `pcs_broad_discovery.py` used `.head(20)` | FIXED; complete OPEN set is retained |
| Covered Call production entry was NVDA-specific | `covered_call_production.py` used literal `NVDA` throughout | FIXED; generic symbol entry plus compatibility wrapper |
| Readiness failure envelope was incomplete | CC executor failure branch omitted `system_status` and `run_id` | FIXED |
| Research CC result lacked a stable action field | NVDL research result had `candidate_status` but no `action` | FIXED |
| Historical spec builders omitted `strategy_type` | unified parser rejected otherwise explicit CC/GENERAL PCS specs | FIXED |
| EOD request used LIVE readiness semantics | PCS/CC public runners passed a fixed `LIVE` mode for EOD requests | FIXED; mode is now propagated |
| LIVE accepted date-only/stale option evidence | readiness did not require timestamp or session equality | FIXED; LIVE timestamp/session gate added |
| Public CC command duplicated orchestration | CLI independently preflighted/refreshed/evaluated and swallowed refresh errors | FIXED; delegates to the canonical executor |
| CSP missing `decision_as_of` returned WAIT before strategy execution | `csp_production.py` returned a normal decision for an incomplete request | FIXED; DATA_BLOCKED/NOT_RUN envelope and regression test |
| CSP selector was hard-coded to SOXL identity/risk names | `cash_secured_put.py` enforced a literal ticker and SOXL-specific risk keywords | FIXED; expected-symbol validation and generic risk fields |
| Covered Call profile/gate prerequisites returned WAIT before evaluation | `covered_call_production.py` treated missing profile/gates as ordinary opportunity waits | FIXED; DATA_BLOCKED/NOT_EVALUATED |
| Recoverable validation gaps skipped the import coordinator | control-plane only coordinated `PARTIAL`, not `BLOCKED` validation gaps | FIXED; authorized repairable validation codes enter recovery |
| Manifest scalar partition metadata was converted to invalid identifiers | readiness fallback iterated strings / accepted `nan` partition IDs | FIXED; normalized scalar metadata and year/quarter reconstruction |
| Broad queue timeout counted queued work as execution timeout | one timeout covered the whole worker queue | FIXED; worker-sized batches |
| LIVE Covered Call provider could omit freshness validation | `decide_call_today()` treated missing `freshness` as acceptable | FIXED; missing capability is `DATA_BLOCKED/LIVE_FRESHNESS_UNAVAILABLE` |
| CLI retained unreachable legacy Covered Call orchestration | code after canonical executor return included duplicate refresh/evaluation and `except Exception: pass` | FIXED; dead path removed |
| Empty broad survivor set was reported as complete strategy coverage | coverage predicate was true for `0 == 0` | FIXED; empty survivor set is explicitly incomplete |
| Readable PIT event calendar with no future event was treated as source outage | event blocker returned ticker-unavailable for an empty future-event selection | FIXED; empty selection is `NO_KNOWN_EVENT` |

## P2 / remaining migration

| Finding | Evidence | State |
|---|---|---|
| Some historical Covered Call tests expect pre-readiness semantics | full pytest failures expect `WAIT`/`NO_SELL` where current fail-closed contract returns `NOT_RUN`/`SELL` | REMAINING; tests/fixtures require governed migration |
| Historical NVDA fixture coverage is narrower than requested replay range | `PCSDataAccess.resolve_source` rejects the old 2020–2025 request | REMAINING; no fallback added |
| Some context paths still use ordinary access for non-target/benchmark data | proxy pinning covers requested ticker, benchmark remains canonical access | REMAINING; requires explicit multi-dataset handle contract |
| Global broad survivor data coverage is incomplete | latest scan has 23 survivors, with daily-session and event-provider blockers | REMAINING; provider/canonical coverage work required |

No production strategy thresholds were changed by this audit.
