# Unified PCS data architecture

`pcs.data.unified.UnifiedDataAccess` is the common ticker-aware boundary for
canonical historical datasets. It consults `data/manifests/storage_manifest.csv`,
verifies date coverage, and queries ticker-partitioned Parquet through bounded
DuckDB SQL. Research artifacts remain derived outputs and are never a source for
market data.

| Caller | Dataset | Current source | Unified source | Action |
|---|---|---|---|---|
| `credit_stop.py` / Phase 0 | options | canonical ticker Parquet | `UnifiedDataAccess` contract | ACTIVE via compatibility wrapper |
| `variant_b_replay.py` | options | existing replay API | `credit_stop` compatibility wrapper → unified layer | ACTIVE |
| `duckdb_store.py` | options/daily | DuckDB views over Parquet | query engine only | KEEP as analytical adapter |
| daily providers/importers | daily | canonical daily Parquet/CSV ingestion | resolver contract | WRAP |
| `data/raw/**` import scripts | raw | vendor/raw files | ingestion boundary only | KEEP, never normal research input |
| `data/parquet/research/**` | research artifacts | derived Parquet | excluded from canonical resolver | KEEP separate |

Canonical option coverage currently catalogued:

| Ticker | First date | Last date |
|---|---:|---:|
| TSLA | 2010-07-08 | 2026-07-31 |
| MU | 2020-01-02 | 2026-07-31 |
| QQQ | 2010-11-22 | 2026-07-31 |

Migration policy: existing loaders remain available for validated compatibility;
new research access must use the unified resolver and must not fall back to a
different ticker or partial DuckDB table.

Method-preservation status: Variant B, TSLA/MU replay methods, Safe Strike and
ATR/P90/P95/breach-probability research modules, stabilization/delayed-entry,
stop/defense, structure, event filters, ticker profiles, and production Entry
Engine behavior remain available. No method was removed or semantically
rewritten. Legacy CSV loaders remain COMPATIBILITY where callers still depend
on them; none is marked DEPRECATED.
