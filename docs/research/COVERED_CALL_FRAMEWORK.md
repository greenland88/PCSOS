# PCSOS Covered-Call Research Framework

The research-only covered-call path is ticker-agnostic. It models
`LONG_100_SHARES + SHORT_1_CALL` and uses the same code for NVDA, QQQ, META,
or any ticker admitted by the canonical data readiness gate.

## Execution flow

```text
PCSDataAccess daily calendar
  -> PIT stock + market features
  -> sell-call timing signal
  -> canonical call chain on the signal date
  -> DTE / delta / liquidity selection
  -> buy-back / expiry / assignment lifecycle
  -> stock + option economic metrics
```

The public Python entry point is:

```python
from pcs.research.covered_call_research import run_covered_call_research
result = run_covered_call_research("NVDA", trades=completed_trade_rows)
```

For a governed research run, use a `NEW_ENTRY` spec and the unified runner:

```powershell
$env:PYTHONPATH = "src"
python -m pcs.research run --spec config/research/nvda_covered_call_baseline.yaml --dry-run
python -m pcs.research run --spec config/research/nvda_covered_call_baseline.yaml --execute
```

The execute path requires ticker readiness and declared PIT feature/market
datasets. It reads options only through `PCSDataAccess`, never from raw option
files, and writes only to the isolated research output directory.

## Signal inputs

The signal evaluator uses stock trend/extension, volatility context, and the
canonical SPY/QQQ market confirmation. Missing or unknown fields produce
`WAIT` / `DATA_INSUFFICIENT`; they are never interpreted as permission to sell.

## Economic outputs

Reports include call premium, realized option P&L, stock P&L, combined P&L,
assignment impact, buy-and-hold P&L, excess return, upside sacrificed, win
rate, profit factor, expectancy, assignment rate, yearly results, leave-one-
year-out stability, and episode concentration.

This framework is research-only. It does not place live orders, alter PCS
production rules, or promote a parameter automatically.
