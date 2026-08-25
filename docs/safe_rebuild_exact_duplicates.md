# Safe exact-duplicate-only options rebuild

`scripts/safe_rebuild_exact_duplicates.py` is limited to the explicitly approved TSLA and AMZN quarters. It normalizes the raw CSV schema, treats the complete 17-field option record as the duplicate identity, rejects any identity key with more than one quote variant, and writes only under `data/parquet/options_v2/safe_rebuild_20260820`.

The JSON summary and CSV provenance manifest record source/output hashes, raw rows, exact duplicates removed, conflicting keys, unique keys preserved, and normalized-output comparison status. It does not modify old canonical storage or strategy logic.

For purchased historical vendor TXT/ZIP data, the approved deterministic policy is: exact full-row duplicates are removed, then a same-key conflict without source timestamp/version is resolved by the first raw-file occurrence. The policy name is `VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW`. ClickHouse is not used to resolve historical vendor conflicts; it remains an incremental-data authority.
