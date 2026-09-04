# Historical acceptance record — 2026-08-26

This record preserves the implementation and acceptance facts formerly embedded
in the root agent contract. It is historical evidence only: it describes the
then-current environment, account, data, and accepted HEAD, and does not prove
that the current checkout, current data, credentials, or provider are available.
Recheck active routes, manifests, artifact identity, and `git status` before reuse.

## Implementation record

The unified market-data import path was implemented and pushed on branch
`codex/msft-replay-clean` with these commits:

`73bcb36` automatic ClickHouse adapter wiring; `64da41f` canonical NVDL options
route; `759f739` automatic options promotion tests and hardening; `3ece8be`
detailed unified import result envelope; `7345f13` removal of legacy ZIP fallback;
`fcd4e93` detailed `ensure_market_data()` execution fields; `03830e1` route
onboarding compatibility through the control plane; `b3bdadb` execution-time
source-registry authorization; `29dac04` transactional rollback for
multi-partition promotion.

The recorded canonical path was `import-market-data` → control plane → authorized
source adapter → isolated staging → schema/quality/ticker validation →
transactional promotion → manifest/provenance/catalog/ledger → `PCSDataAccess`.
`onboard()` delegated to `ensure_market_data()`. Missing provider state was
fail-closed with reason codes including `CLICKHOUSE_CREDENTIALS_MISSING`,
`CLICKHOUSE_CONNECTION_FAILED`, `CLICKHOUSE_SOURCE_TABLE_UNAVAILABLE`,
`AUTHORIZED_SOURCE_NO_ROWS`, and `SOURCE_NOT_AUTHORIZED`.

## NVDL acceptance facts

For the requested 2018-01-01 through 2026-08-26 window, provider start was
clamped to the canonical listing boundary. The approved table was
`firstrate.options_kline_1d`. Recorded coverage was 1,054,510 physical rows,
1,039,298 unique contract keys, 527,212 calls, and 527,298 puts, covering
2023-09-26 through 2026-08-25. Canonical replay contained 1,016,021 NVDL rows;
duplicate executable keys and conflicting executable keys were zero. Readiness
passed `DATA_READY`, `PIT_READY`, `OPTIONS_READY`, `CONTRACT_SELECTION_READY`,
`LIFECYCLE_READY`, and `PCS_RESEARCH_READY`, with no blockers.

The focused command recorded 39 passing tests:

```text
python -m pytest tests/data/test_control_plane.py tests/data/test_control_plane_boundary.py tests/data/test_clickhouse.py tests/data/test_pcs_data_access.py tests/data/test_logical_options_routing.py tests/data/test_readiness.py -q
```

It had only a Windows pytest-cache permission warning. This is not a claim about
the unrelated full repository suite or the current HEAD.

## Other historical notes

The covered-call audit then recorded bid entry, ask close, new-bid minus old-ask
roll credit, required roll liquidity fields, fail-closed missing context, and a
position-roll unit correction. NVDL did not inherit NVDA parameters and had no
validated Covered Call profile. Stage 1 ATR targets `1.5`, `2.0`, `2.5`, and
`3.0` were historical experiment details; they do not authorize reruns or change
the frozen population.

The following paths were recorded as existing uncommitted user research and
were intentionally not staged: `config/covered_call/pltr_covered_call_research.yaml`,
`scripts/run_pltr_cc_*_train.py`, `src/pcs/covered_call_research/`, and
`tests/data/test_covered_call_*.py`. Current ownership must be established from
actual status and diffs, not this historical list.
