# New ticker onboarding

All new data-readiness and import requests use `pcs.data.control_plane`:

1. `get_market_data_status(symbol, requirements)` inspects canonical coverage
   and authorized sources without importing data.
2. `ensure_market_data(symbol, requirements)` executes authorized remediation
   for missing or invalid state. Reuse valid partitions and generations.
3. Registered adapters obtain source data; consumers must not bind a provider,
   load historical option archives, or write canonical Parquet themselves.
4. Writes pass through isolated staging, schema/quality/ticker validation, and
   transactional promotion, including manifest, provenance, catalog, and ledger.
5. Replay the promoted data through `PCSDataAccess` and resolve verified handles
   for the actual decision session before reporting readiness.

The source allowlist is `config/market_data_source_registry.yaml`; remediation
is configured in `config/data_remediation_registry.yaml`. Batch source authority,
exact fractional strikes, and full contract identity must remain intact.

`pcs import-market-data` is the CLI import entrypoint. `pcs.cli.onboard()` is a
compatibility wrapper over the same control plane. Historical functions such as
`pcs.data.onboarding.onboard_ticker()` are not alternate consumer entrypoints.
`HistoricalTxtZipAdapter` remains an adapter named in the source registry, not
authorization to restore a CLI ZIP fallback.

## Quote conflicts

The current executable quality boundary is `audit_option_frame()` in
`pcs.data.storage_schema`. It quarantines every eligible row participating in
a duplicate or conflicting full contract identity with
`OPTION_DUPLICATE_IDENTITY` or `OPTION_CONFLICTING_IDENTITY`. It never repairs a
quote or chooses a conflicting row by file order. `apply_conflict_policy()` in
the historical onboarding module delegates to that boundary.

`canonicalize_option_frame()` is a distinct normalization helper: it may remove
identical duplicates but raises on conflicting payloads. Its exact-duplicate
behavior does not authorize selecting one conflicting quote for execution.

Older first-raw-row policy descriptions are historical records, not current
executable instructions. This documentation update does not change the conflict
implementation or rewrite existing data or frozen research artifacts.

## Preparation and acceptance

Requirements must identify the symbol, date range, datasets, decision session,
and required history. Daily warmup, options readiness, event readiness, and
portfolio readiness are separate. An import's success alone does not establish
`PCS_RESEARCH_READY`, a valid trading opportunity, or production approval.

Repeated preparation must reuse validated state or resume the missing/invalid
portion. Report source, partition, coverage, validation and promotion outcomes,
with the original machine-readable blocker. Never turn an unavailable source
or missing event calendar into evidence that no data/event exists.

Daily migration admission returns a `partition_results` record for every
validated, reused, promoted, or failed logical partition. Each record carries
the candidate path, physical and semantic hashes, source, active generation
before/after values, and the original reason code; promotion records also keep
the formal receipt and independent read-back evidence. `ADMISSION_INCOMPLETE`
must preserve committed, reused, failed, and unprocessed partitions so a retry
can resume only the remaining work. A top-level admission status never proves
the ticker is daily-ready: readiness is established independently for the
decision date and required warmup window.

Pool Scan defaults to `READ_ONLY`. Its optional daily preparation requires both
`data_mode="PREPARE_THEN_SCAN"` and `auto_prepare_data=True`. Prepare options
through the control plane with explicit requirements; the scan's daily
preparation mode does not promise full options/event/portfolio readiness.

See [system onboarding](system_onboarding.md) for the operating sequence and
the difference between read-only scan deadlines and write transaction safety.
