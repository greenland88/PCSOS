"""Build a future-outcome-free NVDA PCS manual review from the fixed TRAIN artifact."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "research_outputs/nvda_entry_discovery_agent_v2/pit_feature_outcome_table.parquet"
OUT = ROOT / "research_outputs/nvda_pcs_manual_blind_review_2020-12-10_2023-12-29"

def main():
    d = pd.read_parquet(SRC).copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"]).dt.date
    d = d[d["trade_date"].between(pd.Timestamp("2020-12-10").date(), pd.Timestamp("2023-12-29").date())].copy()
    d = d.sort_values("trade_date")
    # All derived fields below use only observations through the signal date.
    close = d["close"]
    d["volume_ma50"] = pd.NA  # source artifact has no daily volume column
    d["rsi14"] = pd.NA
    d["macd_hist"] = pd.NA
    d["volume_vs_ma50"] = "UNKNOWN_SOURCE_FIELD"
    d["rsi_state"] = "UNKNOWN_SOURCE_FIELD"
    d["macd_state"] = "UNKNOWN_SOURCE_FIELD"
    d["trend_state"] = d.apply(lambda r: "UPTREND_CONFIRMED" if pd.notna(r.nvda_close_vs_sma20) and pd.notna(r.nvda_close_vs_sma50) and r.nvda_close_vs_sma20 > 0 and r.nvda_close_vs_sma50 > 0 else "RECLAIM_OR_PULLBACK_NOT_CONFIRMED", axis=1)
    action = d["executable_pcs"].map({True: "OPEN", False: "WAIT"}).fillna("DATA_BLOCKED")
    out = pd.DataFrame({
        "signal_date": d.trade_date, "quote_timestamp": "UNKNOWN_CANONICAL_DAILY_QUOTE_TIME",
        "NVDA_price": d.close, "market_regime": "UNKNOWN_NOT_IN_ARTIFACT",
        "Trend": d.nvda_close_vs_sma200.map(lambda x: "UP" if pd.notna(x) and x > 0 else "UNKNOWN_OR_DOWN"),
        "Trend_score": "UNKNOWN_NOT_IN_ARTIFACT", "ADX": "UNKNOWN_NOT_IN_ARTIFACT", "RSI": "UNKNOWN_NOT_IN_ARTIFACT",
        "trend_state": d.trend_state, "volume_ma50": d.volume_ma50, "volume_vs_ma50": d.volume_vs_ma50,
        "rsi14": d.rsi14, "rsi_state": d.rsi_state, "macd_hist": d.macd_hist, "macd_state": d.macd_state,
        "ATR14": d.nvda_atr14, "MA20": d.nvda_sma20, "MA50": d.nvda_sma50, "MA200": d.nvda_sma200,
        "underlying_volume": "UNKNOWN_NOT_IN_ARTIFACT", "dollar_volume": "UNKNOWN_NOT_IN_ARTIFACT",
        "RVOL": d.nvda_volume_rel20, "volume_trend": "UNKNOWN_NOT_IN_ARTIFACT",
        "support": "UNKNOWN_NOT_IN_ARTIFACT", "support_strength": "UNKNOWN_NOT_IN_ARTIFACT",
        "pullback_reclaim_overextension": "UNKNOWN_NOT_IN_ARTIFACT", "event_risk": d.event_state,
        "expiration": d.expiration, "DTE": d.dte, "short_strike": d.short_strike, "long_strike": d.long_strike,
        "executable_credit": d.credit, "credit_efficiency": "UNKNOWN_NOT_IN_ARTIFACT",
        "breakeven": "UNKNOWN_NOT_IN_ARTIFACT", "max_loss": "UNKNOWN_NOT_IN_ARTIFACT", "return_on_risk": "UNKNOWN_NOT_IN_ARTIFACT",
        "delta": "UNKNOWN_NOT_IN_ARTIFACT", "OI": "UNKNOWN_NOT_IN_ARTIFACT", "option_volume": "UNKNOWN_NOT_IN_ARTIFACT",
        "bid_ask_spread": "UNKNOWN_NOT_IN_ARTIFACT", "capacity": "UNKNOWN_NOT_IN_ARTIFACT",
        "action": action, "reason_code": d.reason_code,
    })
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "nvda_pcs_manual_blind_review_2020-12-10_2023-12-29.csv", index=False)
    best = out[d.executable_pcs if "executable_pcs" in out else action.eq("OPEN")].head(10)
    near = out[action.ne("OPEN")].head(10)
    neg = out.sort_values(["Trend", "NVDA_price"]).head(10)
    def table(x):
        cols = list(x.columns)
        lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for row in x.astype(object).where(pd.notna(x), "").itertuples(index=False, name=None):
            lines.append("| " + " | ".join(str(v).replace("|", "\\|") for v in row) + " |")
        return "\n".join(lines)
    md = ["# NVDA PCS Manual Blind Review", "", "Population: fixed canonical TRAIN artifact `nvda_entry_discovery_agent_v2`; period 2020-12-10 through 2023-12-29. FINAL OOS and all post-signal outcomes are excluded.", "", f"Rows: {len(out)}; OPEN: {int(action.eq('OPEN').sum())}; WAIT/DATA_BLOCKED: {int((~action.eq('OPEN')).sum())}", "", "## Daily blind-review rows", "", table(out), "", "## Sample A — first 10 executable PCS dates", "", table(out[action.eq("OPEN")].head(10)), "", "## Sample B — first 10 nearest non-executable dates", "", table(near), "", "## Sample C — 10 negative-control dates", "", table(neg), "", "## Human review form", "", "| 日期 | 系统判断 | 系统Strike | Trend正确 | Volume正确 | 支撑正确 | 时机正确 | Strike正确 | 人工结论 | 备注 |\n|---|---|---:|---|---|---|---|---|---|---|", ""]
    (OUT / "nvda_pcs_manual_blind_review_2020-12-10_2023-12-29.md").write_text("\n".join(md), encoding="utf-8")
    print({"rows": len(out), "open": int(action.eq("OPEN").sum()), "out": str(OUT)})

if __name__ == "__main__": main()
