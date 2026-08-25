# AMD controlled v2 cutover

AMD now resolves through the isolated validated onboarding dataset
`options_v2_onboarding_amd_20260820` via `config/data_source_routes.yaml`.

The dataset contains historical vendor TXT normalized by
`VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW` plus the validated ClickHouse
increment through 2026-08-18. The cutover is per-ticker only; NVDA and QQQ
routes are unchanged. Rollback is the default AMD route to the old `options`
dataset in `data/manifests/storage_manifest.csv`.

This is a controlled route cutover, not autonomous live trading.
