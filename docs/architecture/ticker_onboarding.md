# New ticker onboarding

New historical option tickers use `pcs.data.onboarding.onboard_ticker`.

1. `HistoricalTxtZipAdapter` reads the approved `K:\BaiduNetdiskDownload\USDailyOptions` TXT/ZIP source and records archive/member hashes.
2. Vendor conflicts use the frozen `VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW` policy: exact full-row duplicates are removed and conflicting keys retain the first row in the immutable vendor file. `validate_txt_clickhouse_overlap` then requires exact-contract-key overlap with ClickHouse; quote differences are counted as an audit field, not treated as missing overlap.
3. Only a `READY` overlap result may call `PCSDataAccess.write_partition`; the onboarding module has no direct Parquet writer.
4. Each written partition updates the PCSDataAccess manifest and records provenance with source/member, hashes, period, row count, dataset, and status.

The pipeline does not rebuild or cut over existing tickers. It is an onboarding boundary for future tickers and preserves the existing strategy/data route semantics.
