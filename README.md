# PCS Decision Assistant V1

Deterministic-first Put Credit Spread decision support. V1 reads mock/provider data, evaluates hard rules, scores candidates and current positions, records decisions to SQLite, and displays results in Streamlit.

It is not an automated trading bot and contains no live trading/write methods.

## Quick Start

```powershell
pip install -e .[dev]
pytest
streamlit run dashboard/app.py
```

Import downloaded daily CSV files into the per-symbol historical store and
DuckDB-readable Parquet store:

```powershell
python -m pcs.data.import_daily_snapshot
```

The importer keeps `data/manifests/daily_snapshot_import_manifest.csv`, so
unchanged files that were already imported successfully are skipped automatically.

Download one YouTube transcript into the research folder:

```powershell
pcs-lite download-youtube-subtitles "https://www.youtube.com/watch?v=VIDEO_ID"
```

Run the daily PCS paper-trading simulator:

```powershell
pcs-lite simulate-daily --as-of 2026-08-18
```

The simulator uses the deterministic PCS rule engine and writes compact daily
outputs under `research_outputs/paper_trading/YYYY-MM-DD/`:

- `paper_trading_snapshot.json` for the agent-ready typed result envelope
- `paper_trading_summary.csv` for daily action and risk totals
- `paper_trading_snapshots.csv` for one row per candidate or position

It records simulated decisions only. It does not place orders, does not add live
trading methods, and keeps planned risk separate from theoretical maximum loss.

## Current Scope

- Config-driven PCS capital, liquidity, regime, sizing, and roll rules
- Pydantic models for candidates, positions, market state, options, and decisions
- Mock provider with QQQ, NVDA, MSFT, and AMZN sample data
- Market regime, liquidity, strike, opportunity, position sizing, and roll engines
- Daily paper-trading simulator for reviewing rule behavior over time
- Trend Engine Phase 1 base indicators through the `pcs.trend` public API
- SQLite journal schema
- Streamlit dashboard for V1 demonstration

## Trend Engine

Phase 1 provides only base technical indicators for standard OHLCV pandas DataFrames:

- `sma20`
- `sma50`
- `sma200`
- `atr14`
- `adx14`
- `rsi14`

External PCS modules should call `calculate_base_indicators` and `TrendIndicatorConfig` from `pcs.trend`. TA-Lib is isolated inside the Trend Engine indicator implementation so the rest of PCS does not depend on the indicator library directly.

This phase does not implement Trend Score, A/B/C/D/E trend grades, HH/HL, relative strength, pullback, support, thesis failure, PCS entry logic, roll logic, or backtesting.

## Safety

The rule engine is authoritative. AI helpers may classify news or thesis risk as structured JSON, but they cannot override hard stops such as RED market regime, poor liquidity, broken thesis, or portfolio risk limits.

## Agent-Ready Interfaces

PCS OS is designed to become callable by AI agents without making the AI the
calculation or risk authority. New core modules and storage interfaces must expose
typed, JSON-serializable Python APIs with stable enums, reason codes, version
metadata, and audit/replay identifiers. CSV files and reports remain presentation
adapters rather than the sole interface.

See [Agent-Ready Interface Contract](docs/architecture/agent_ready_interfaces.md)
for the required result envelope, storage boundary, compatibility rules, and
planned MCP/REST/local-agent tool surface.
