# Unified PCS data architecture

`pcs.data.access.PCSDataAccess` is the canonical ticker-aware boundary for
historical daily and options datasets. `UnifiedDataAccess` remains a
compatibility layer for callers that have not yet migrated. Active routes come
from `config/data_source_routes.yaml`; manifests, physical input identity,
date coverage, provenance, and bounded Parquet/DuckDB reads are part of the
boundary. Research artifacts remain derived outputs and are never a source for
market data.

| Caller | Dataset | Current source | Unified source | Action |
|---|---|---|---|---|
| live research ticker readers | daily/options | configured active route | `PCSDataAccess` | REQUIRED |
| `credit_stop.py` / Phase 0 | options | canonical ticker Parquet | compatibility path into canonical access | COMPATIBILITY |
| `variant_b_replay.py` | options | existing replay API | shared lifecycle/access contracts | COMPATIBILITY |
| `duckdb_store.py` | options/daily | DuckDB views over Parquet | query engine only | KEEP as analytical adapter |
| daily providers/importers | daily | canonical daily Parquet/CSV ingestion | `PCSDataAccess` route/manifest contract | ACTIVE |
| `data/raw/**` import scripts | raw | vendor/raw files | ingestion boundary only | KEEP, never normal research input |
| `data/parquet/research/**` | research artifacts | derived Parquet | excluded from canonical resolver | KEEP separate |

The following coverage table is a dated inventory snapshot and must not be used
as current readiness evidence:

| Ticker | First date | Last date |
|---|---:|---:|
| TSLA | 2010-07-08 | 2026-07-31 |
| MU | 2020-01-02 | 2026-07-31 |
| QQQ | 2010-11-22 | 2026-07-31 |

Migration policy: existing loaders remain available only for validated
compatibility or bounded bulk materialization. New live research access must use
`PCSDataAccess` and must not fall back to a different dataset, legacy route,
different ticker, or partial DuckDB table.

Method-preservation status: Variant B, TSLA/MU replay methods, Safe Strike and
ATR/P90/P95/breach-probability research modules, stabilization/delayed-entry,
stop/defense, structure, event filters, ticker profiles, and production Entry
Engine behavior remain available. No method was removed or semantically
rewritten. A direct raw/Parquet reader is acceptable only when explicitly
classified as ingestion, test fixture, or bounded bulk materialization; it is
not an alternate live ticker reader.
