# TSLA specialized quote lifecycle — open issues

Status: `RESEARCH_INFRASTRUCTURE_ONLY`  
Production changes: none.

> Progress snapshot from the original incremental run. The counters below are
> not a resume authority by themselves. Resume only from the current validated
> progress artifact and per-batch manifests; if their identities do not match
> current code/data, report `STALE_ARTIFACT` and rebuild through the canonical
> path.

## Current progress

- Total deterministic micro-batches: 5,547
- Completed: 60
- Remaining: 5,487
- Quote-days adjudicated: 1,502
- Quote-days remaining: 152,541
- Both-leg coverage among processed quote-days: 100%
- Confirmed source gaps, source ambiguities, invalid quotes in processed rows: 0

The current lifecycle status is `TSLA QUOTE LIFECYCLE PARTIAL — EXECUTION INCOMPLETE`.
No timeout or incomplete unit may be classified as a source gap.

## Open issues

### 1. Lifecycle execution incomplete

The one-variant micro-batch implementation commits valid units atomically and
resumes correctly, but 5,487 units remain. Continue the incremental runner
until every unit has a final adjudicated status and `NOT_RUN_REMAINS = 0`.

Required outcome: aggregate final exact-identity quote and spread-mark outputs
only after all units are complete or otherwise adjudicated.

### 2. Final research artifacts blocked on full coverage

Do not finalize these files until lifecycle processing is complete:

- `research_outputs/tsla_specialized_pcs_20260820/tsla_specialized_daily_quotes.parquet`
- `research_outputs/tsla_specialized_pcs_20260820/tsla_specialized_spread_marks.parquet`

### 3. Baseline 2.3 ATR parity blocked

Baseline parity must run after the exact lifecycle is finalized. It must report
matches, mismatches, and missing values for short strike, long strike, initial
credit, exit date, exit reason, and realized P&L. No 2.6/3.0/3.3/3.6 ATR
performance research may proceed before this evidence is understood.

### 4. Structure-aware stop data dependency

The frozen TSLA replay artifact does not persist a numeric PIT entry support
level. Although the research contract states that support was found for all
1,119 candidates, the authoritative PIT support level and provenance must be
located and persisted before `support_intact`, confirmed support breaks, and
structure-aware-stop replay can be completed. Do not recompute support using
future data.

### 5. Volatility-aware stop definition blocked

No already-approved ATR adverse-excursion boundary has been identified. The
status remains `DEFINITION_BLOCKED`; no ATR multiple may be invented for
research or production use without an approved deterministic definition.

## Existing implementation

- `scripts/build_tsla_quote_lifecycle_batched.py`: one-candidate micro-batch
  lifecycle builder, exact identity lookup, atomic Parquet commits, manifests,
  resume validation, and timing telemetry.
- `research_outputs/tsla_specialized_pcs_20260820/quote_batches/`: committed
  batch outputs and per-batch manifests.
- `research_outputs/tsla_specialized_pcs_20260820/tsla_quote_batch_progress.json`:
  current measurable progress.
