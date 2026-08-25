from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.credit_stop import run_backtest

WINDOWS = {
    "SPY": ("2020-01-02", "2026-07-31"),
    "QQQ": ("2020-01-01", "2026-07-31"),
    "NVDA": ("2024-06-10", "2026-07-31"),
    "AMZN": ("2022-06-06", "2026-07-31"),
}

def daily(symbol, end):
    x = PCSDataAccess().read_prices(symbol, None, end).sort_values("date").drop_duplicates("date")
    x["date"] = pd.to_datetime(x["date"]).dt.normalize()
    return x[x.date <= pd.Timestamp(end)].copy()

def main():
    all_trades = []
    summaries = []
    for symbol, (start, end) in WINDOWS.items():
        stock = daily(symbol, end)
        benchmark = daily("QQQ", end)
        result = run_backtest(stock, benchmark, option_root=f"data/raw/options/{symbol}", start=start, end=end, backend="duckdb")
        trades = pd.DataFrame([{**{k: v for k, v in row.items() if k != "events"}, "ticker": symbol, "entry_date": row["date"], "spot": row["close"], "ATR": row["atr14"], "Trend Gate": row.get("trend_gate"), "DTE": (pd.Timestamp(row["expiration"]) - pd.Timestamp(row["date"])).days, "width": row["short_strike"] - row["long_strike"], "credit": row["initial_credit"], "short_delta": None} for row in result["trades"]])
        all_trades.append(trades)
        trend = stock[(stock.date >= pd.Timestamp(start)) & (stock.date <= pd.Timestamp(end))]
        summaries.append({"ticker": symbol, "option_data_start_used": start, "option_data_end_used": end, "trend_pass_dates": int((trend["trend_gate"] == "PASS").sum()) if "trend_gate" in trend else None, "valid_30_45_dte_dates": None, "liquidity_reliability_survivors": len(trades), "final_option_qualified_pcs_trades": len(trades), "pipeline_exclusions": result["exclusions"]})
    out = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    out.to_parquet("research_outputs/safe_strike_unified_option_qualified_population.parquet", index=False)
    out.to_csv("research_outputs/safe_strike_unified_option_qualified_population.csv", index=False)
    pd.DataFrame(summaries).to_json("research_outputs/safe_strike_unified_option_qualified_summary.json", orient="records", indent=2, default_handler=str)
    print(pd.DataFrame(summaries).to_string(index=False))

if __name__ == "__main__":
    main()
