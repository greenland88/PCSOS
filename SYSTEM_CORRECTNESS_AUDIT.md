# PCS OS System Correctness Audit

> Audit snapshot, not live status. Use `PROJECT_STATUS.md` and
> `docs/PCS_CAPABILITY_ROADMAP.md` for the current repository summary.

Audit baseline: `origin/codex/msft-replay-clean` at `fd168a2`, plus the
system-correctness fixes present in that audit workspace. Later commits may
change the implementation or close findings; re-run the listed tests before
updating a status. This audit did not run a full MSFT replay and did not read
FINAL OOS.

| SUBSYSTEM | INVARIANT | STATUS | EVIDENCE | TEST | REMAINING_RISK |
|---|---|---|---|---|---|
| DATA | Canonical reads use ticker-aware PCSDataAccess and content identity | PASS | `PCSDataAccess.source_data_identity()` hashes active physical inputs | `tests/data/test_pcs_data_access.py` | Repository-wide raw-path classification still pending |
| PIT FEATURES | Warmup history is independent from option coverage | PARTIAL | Current replay keeps daily warmup before requested scope | Targeted replay tests | All research runners still need an explicit warmup/eligibility audit |
| STATE / TREND | Unexpected evaluation errors propagate | PASS | `evaluate_as_of()` catches only known data/domain exceptions | `test_underlying_state_adapter.py` | Full cross-engine parity pending |
| ENTRY CONTEXT | Caller context is authoritative when supplied | PASS | `DecisionEngine` no longer silently rebuilds supplied context | `tests/test_hard_rules.py` and entry tests | Mismatch-specific integration test pending |
| CANDIDATE GENERATION | Candidate identity is unique | PASS | Duplicate ticker/date/expiry/short/long fails closed | Replay correctness tests | Other legacy generators need inventory |
| CONTRACT SELECTION | Exact put contract identity is preserved | PASS | Identity includes symbol/expiry/call_put/strike | Replay correctness tests | Cross-engine parity pending |
| EVENT GATE | Historical event dates require PIT proof | PASS | Historical replay calendar requires `event_date_known_at_entry` metadata | Event gate tests | Existing calendars without metadata are intentionally blocked |
| REGIME / LIQUIDITY / SAFE STRIKE | Existing gates remain hard gates | PASS | No threshold or production rule changes made | Existing gate suite | Full data-backed regression pending |
| POSITION SIZING | Proposed risk fits remaining total/bucket/ticker capacity | PASS | Sizer caps contracts by post-trade remaining capacity | `tests/test_hard_rules.py` | Multi-contract boundary matrix should be expanded |
| PORTFOLIO POST-TRADE CAP | current + proposed never exceeds hard cap | PASS | 9,500 + proposal is resized to <= 10,000 | `tests/test_hard_rules.py` | None identified in current engine path |
| SEQUENTIAL CAPACITY RESERVATION | Each OPEN reserves risk before next candidate | PASS | Paper loop sorts candidates and mutates planned/bucket/ticker reservations | `tests/simulation/test_paper_trading.py` | Dedicated multi-OPEN stress fixture should be added |
| LIFECYCLE RIGHT-CENSORING | Incomplete horizon is not normal TIME_EXIT | PASS | Returns `RIGHT_CENSORED` with no realized P&L | Replay correctness tests | Full adapter parity pending |
| EXCHANGE CALENDAR | Production/research event and holding distances use canonical sessions | PARTIAL | HardGatePipeline accepts session source; paper provider interface exposes it | Event/lifecycle tests | Legacy callers without sessions still use compatibility bdate fallback |
| CACHE / CHECKPOINT | Data/code identity and interrupted PIT work invalidate/resume safely | PARTIAL | Physical data hash, atomic timeline write, per-chunk checkpoint, corrupt rebuild | PIT cache tests | Full fresh/resume equivalence not yet executed with canonical data |
| ARTIFACTS | Reruns do not inherit replay-owned stale outputs | PASS | Replay-owned artifacts are invalidated before rewrite; writes are atomic for Parquet | Static review | Full temp-directory replacement still preferable |
| RESEARCH SPEC | Dry-run validates same effective rules as execution | PASS | `run_spec()` calls `validate_rule_set()` and shared effective-rule function | Research framework/rule tests | Full spec matrix pending |
| PAPER TRADING | Snapshot is deterministic and capacity-aware | PASS | Candidate ordering is stable and reservations are cumulative | Paper tests | Provider session/calendar integration pending |
| FINAL OOS GUARD | No FINAL OOS read without explicit authorization | PASS | Existing guard remains enforced; MSFT horizon is `SPLIT_CUTOFF` | Research framework tests | Full call-graph scan pending |
| CROSS-ENGINE PARITY | Identical lifecycle inputs yield identical outputs | PARTIAL | Batch/current/Stage4A share the batch helper and identity contract | Lifecycle tests | Fresh parity matrix not yet complete |
| REPOSITORY BYPASS AUDIT | No forbidden live raw/legacy bypass | NOT_COMPLETE | Static inventory is still in progress | Not yet complete | Direct readers remain outside the audited replay path |

## Acceptance state

```text
DATA_BOUNDARY = PASS
PIT_SAFETY = PASS
EVENT_PIT = PASS (metadata-required path; legacy metadata-free calendars blocked)
ENTRY_CONTEXT = PASS
CANDIDATE_GENERATION = PASS
CONTRACT_SELECTION = PASS
PORTFOLIO_POST_TRADE_CAP = PASS
SEQUENTIAL_CAPACITY_RESERVATION = PASS
LIFECYCLE_RIGHT_CENSORING = PASS
EXCHANGE_CALENDAR = PARTIAL
CACHE_INVALIDATION = PARTIAL
RERUN_EQUIVALENCE = NOT_RUN
PARALLEL_EQUIVALENCE = NOT_RUN
FINAL_OOS_GUARD = PASS
RAW_LIVE_BYPASS_COUNT = NOT_YET_CLASSIFIED
LEGACY_LIVE_BYPASS_COUNT = NOT_YET_CLASSIFIED
FULL_TEST_SUITE = NOT_RUN_TO_COMPLETION (clean branch intentionally has no data artifacts)
```

The PCS OS system is **not DONE**. P0 capacity invariants are repaired and
covered. Remaining P1 work is the repository-wide bypass inventory, canonical
session propagation for every caller, full cache/resume and parallel numeric
equivalence, and the data-backed full test suite.
