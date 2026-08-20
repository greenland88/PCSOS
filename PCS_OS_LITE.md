# PCS OS Lite

Lite keeps ongoing cost low by making repeated work local and deterministic.

Included:
- read-only provider interfaces
- pluggable `HoodTraderProvider` for any local read-only Hood client
- local JSON Hood snapshot adapter
- SQLite journal and Parquet snapshots
- core PCS decision engine
- simple Roll engine
- simple synthetic Stress Lab
- `pcs-lite` CLI

Not included in Lite:
- dashboard
- live trading
- complex AI news analysis
- machine learning
- expensive historical-options backtesting

Commands:

```powershell
pcs-lite analyze-mock
pcs-lite collect-options --hood-json .\hood_snapshot.json QQQ NVDA MSFT
pcs-lite stress .\portfolio.json
```

Data rule:

Raw Hood, market, and options data stays local in SQLite/Parquet. Python, SQL, and DuckDB calculate features first. Only compact summaries should be given to AI.

Current Hood limitation:

Codex can call the Hood Trader connector during a conversation, but the local Python program cannot import that connector directly. For now, Lite supports a local JSON read-only client shape so repeated analysis can run without Codex once snapshots exist. If a real local Hood SDK/HTTP/MCP client becomes available, plug it into `HoodTraderProvider` without changing the rule engine.
