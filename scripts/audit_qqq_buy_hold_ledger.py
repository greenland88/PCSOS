"""Emit the static QQQ benchmark trace for the covered-call accounting audit."""
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

START, END = "2020-01-01", "2026-07-31"
OUT = Path("research_outputs/qqq_buy_hold_accounting_trace.csv")

def main() -> None:
    d = PCSDataAccess.canonical().read_daily("QQQ", START, END).sort_values("date")
    initial = float(d.close.iloc[0]) * 100.0
    out = pd.DataFrame({
        "date": d.date.dt.strftime("%Y-%m-%d"),
        "QQQ_price": d.close.astype(float),
        "shares": 100,
        "cash": 0.0,
        "stock_value": d.close.astype(float) * 100.0,
        "stock_realized_pnl": 0.0,
        "stock_unrealized_pnl": d.close.astype(float) * 100.0 - initial,
        "option_pnl": 0.0,
        "portfolio_equity": d.close.astype(float) * 100.0,
        "benchmark_equity": d.close.astype(float) * 100.0,
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print({"start_date": str(d.date.iloc[0].date()), "end_date": str(d.date.iloc[-1].date()),
           "start_price": float(d.close.iloc[0]), "end_price": float(d.close.iloc[-1]),
           "expected_buy_hold_pnl": float(d.close.iloc[-1] * 100.0 - initial),
           "first_divergence": None, "rows": len(out), "output": str(OUT)})

if __name__ == "__main__":
    main()
