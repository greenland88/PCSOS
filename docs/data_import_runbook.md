# Unified Data Import Runbook

The canonical entry point for data readiness is `pcs.data.control_plane`.
Consumers must call `require_market_data(symbol, requirements)` and must not
read provider files or call providers directly.

`get_market_data_status()` inspects canonical `PCSDataAccess` routes and emits
a machine-readable `CoveragePlan`. `ensure_market_data()` accepts an injected
import orchestration callback; the callback is responsible for using the
registered adapters and existing atomic onboarding writers.

Daily repairs use bounded `fetch_daily_safety_window()` reads and validate the
returned window before canonical commit. Historical options use the existing
ZIP/ClickHouse overlap onboarding; current options use bounded ClickHouse
queries after calculating the canonical last trade date.

The canonical source registry is `config/market_data_source_registry.yaml`. It is the allowlist:
Massive option contracts are reference-only and are never treated as a
historical quote source. Strategy code is not invoked by this system.

CLI example:

```text
python -m pcs.cli market-data-status PLTR --start 2018-01-01 --end 2026-08-14
```

To execute the registered handlers for missing coverage:

```text
python -m pcs.cli import-market-data PLTR --start 2018-01-01 --end 2026-08-26
```

The same boundary is part of the repository agent contract in `AGENTS.md`.
