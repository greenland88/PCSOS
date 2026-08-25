# Unified Research Runner

Use one guarded entry point for all new PCS research:

```powershell
$env:PYTHONPATH = "src"
python -m pcs.research run --spec config/research/templates/new_entry.yaml --dry-run
# canonical-data preflight (no signal/lifecycle execution)
python -m pcs.research run --spec config/research/templates/new_entry.yaml --real-preflight
```

`ResearchSpec` is machine-readable and must declare the mode, population,
signal and entry-date semantics, split, contract selector, lifecycle and
parameter boundaries. Missing or empty `research_mode`, `signal_definition`,
or `entry_date_rule` returns `SPEC_INCOMPLETE` before data access.

Population routing is strict:

- `EXISTING_TRADE` starts with a frozen trade/candidate ledger and cannot
  create dates.
- `NEW_ENTRY` starts with the complete PIT ticker daily calendar. It cannot
  use any frozen ledger. Recovery means `BREAKDOWN(t0) -> RECOVERY(t1) ->
  entry(t1)`; t1 need not remain breakdown.
- `CONTRACT_VARIANT` starts with frozen entry dates and may only reselect
  contracts.

The runner emits the complete NEW_ENTRY funnel and identifies the first zero,
affected count, exact reason, and remediation. Data adapters should pass
counts obtained through `PCSDataAccess` and deterministic contract/lifecycle
engines; the runner never guesses missing counts or substitutes a population.
Outputs are dry-run research results under `research_outputs/<research_id>`. 
FINAL OOS access and production/frozen writes are blocked by default.

The count sequence used in unit tests is explicitly synthetic fixture data and
is never a ticker result. Real runs include `data_source=PCS_CANONICAL_DATA`,
read daily prices through `PCSDataAccess`, and persist `preflight.json`.
When the signal predicate is not frozen, the result is descriptive-only with
`signal_execution=NOT_RUN` and cannot be interpreted as a failed hypothesis.
The AMD early-recovery entry point performs full-calendar PIT state-timeline
construction, counts descriptive `BREAKDOWN` episodes, and stops before
`RECOVERY` signal dates until that predicate is frozen. The timeline is
persisted as `pit_state_timeline.parquet` beside `preflight.json`.

## Artifact lifecycle

Each `research_id` has one authoritative `CURRENT` artifact set under
`research_outputs/<research_id>/`, described by `artifact_manifest.json`.
The manifest records the spec, code, and data hashes and every file hash.
Readers reject missing, stale, mismatched, non-current, or non-canonical
artifacts with `STALE_ARTIFACT`. New output is built in a temporary directory,
validated, and atomically replaces the current set. Failed runs leave no
partial set. Synthetic fixtures are test-only and are never persisted under
`research_outputs/`; Git history is the only historical record for invalidated
research artifacts.
