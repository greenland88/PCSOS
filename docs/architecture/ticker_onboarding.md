# New ticker onboarding

New historical option tickers use `pcs.data.onboarding.onboard_ticker`.

Onboarding is data infrastructure only. It does not establish a PCS edge,
change a route automatically, authorize research execution, or promote a
strategy.

1. `HistoricalTxtZipAdapter` reads the approved `K:\BaiduNetdiskDownload\USDailyOptions` TXT/ZIP source and records archive/member hashes.
2. Vendor conflicts use the frozen `VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW` policy: exact full-row duplicates are removed and conflicting keys retain the first row in the immutable vendor file. `validate_txt_clickhouse_overlap` then requires exact-contract-key overlap with ClickHouse; quote differences are counted as an audit field, not treated as missing overlap.
3. Only a `READY` overlap result may call `PCSDataAccess.write_partition`; the onboarding module has no direct Parquet writer.
4. Each written partition updates the PCSDataAccess manifest and records provenance with source/member, hashes, period, row count, dataset, and status.
5. Every written partition is replayed through `PCSDataAccess` and checked for
   row-count and exact-key uniqueness before onboarding can complete.
6. Onboarding completion is followed by the independent ticker-readiness gate;
   `READY` ingestion is not the same as `PCS_RESEARCH_READY=YES`.

The pipeline does not rebuild or cut over existing tickers. It is an onboarding
boundary for future tickers and preserves existing strategy/data route
semantics. Repeated work must validate and reuse existing state, returning
`ALREADY_COMPLETE` or resuming only missing/invalid periods rather than
creating parallel stores.

Required evidence includes source/member identity, conflict-policy counts,
overlap result, manifest/provenance rows, canonical replay validation, and
machine-readable reason codes for every blocked period.
