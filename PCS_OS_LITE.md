# PCS OS Lite

Lite keeps ongoing cost low by making repeated work local and deterministic.
It is the local decision/paper-trading surface, not the historical research
stack under `pcs.research`.

Included:
- read-only provider interfaces
- pluggable `HoodTraderProvider` for any local read-only Hood client
- local JSON Hood snapshot adapter
- SQLite journal and Parquet snapshots
- core PCS decision engine
- simple Roll engine
- simple synthetic Stress Lab
- deterministic daily paper-trading snapshots
- incremental local daily/options_v2 updates
- `pcs-lite` CLI

Not included in Lite:
- the removed Streamlit demonstration dashboard
- live trading
- complex AI news analysis
- machine learning
- the guarded historical research/replay stack, which remains available
  separately through `python -m pcs.research`

Commands:

```powershell
pcs-lite analyze-mock
pcs-lite collect-options --hood-json .\hood_snapshot.json QQQ NVDA MSFT
pcs-lite simulate-daily --as-of 2026-08-18
pcs-lite update-data MSFT
pcs-lite stress .\portfolio.json
pcs-lite download-youtube-subtitles "https://www.youtube.com/watch?v=VIDEO_ID"
```

Data rule:

Raw Hood, market, and options data stays local in SQLite/Parquet. Python, SQL, and DuckDB calculate features first. Only compact summaries should be given to AI.

Historical research must use `PCSDataAccess`, ticker readiness, and the unified
Research Runner. Lite snapshots are not a substitute for canonical research
artifacts.

Current Hood limitation:

Codex can call the Hood Trader connector during a conversation, but the local Python program cannot import that connector directly. For now, Lite supports a local JSON read-only client shape so repeated analysis can run without Codex once snapshots exist. If a real local Hood SDK/HTTP/MCP client becomes available, plug it into `HoodTraderProvider` without changing the rule engine.
