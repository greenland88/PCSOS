"""Export sealed SPY/QQQ FINAL OOS trade detail; no market-data access."""
from pathlib import Path
import pandas as pd

ROOT = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
OUT = Path("research_outputs/spy_qqq_final_oos_trade_detail_20260821")
OUT.mkdir(parents=True, exist_ok=True)
START, END = "2026-06-01", "2026-08-18"


def export(ticker):
    contracts = pd.read_parquet(ROOT / f"{ticker}_entry_contract_v2.parquet")
    lifecycle = pd.read_parquet(ROOT / f"{ticker}_lifecycle_marks.parquet")
    outcomes = pd.read_parquet(ROOT / f"{ticker}_train_validation_outcomes.parquet")
    contracts["entry_date"] = pd.to_datetime(contracts["decision_date"])
    oos = contracts[contracts.entry_date.between(START, END)].copy()
    outcomes["entry_date"] = pd.to_datetime(outcomes["decision_date"])
    oos = oos.merge(outcomes.drop(columns=["decision_date"], errors="ignore"), on=["candidate_id", "entry_date"], how="left", suffixes=("", "_outcome"))
    lifecycle["mark_date"] = pd.to_datetime(lifecycle["mark_date"])
    exits = lifecycle[lifecycle.exit.fillna(False)].sort_values("mark_date").drop_duplicates("candidate_id", keep="first")
    exit_cols = [c for c in ["candidate_id", "mark_date", "spread_mark", "pnl"] if c in exits.columns]
    exits = exits[exit_cols].rename(columns={"mark_date": "exit_date", "spread_mark": "exit_value", "pnl": "lifecycle_exit_pnl"})
    oos = oos.merge(exits, on="candidate_id", how="left")
    oos["ticker"] = ticker
    oos["trade_id"] = oos["candidate_id"]
    oos["expiration"] = pd.to_datetime(oos["expiration"])
    oos["DTE"] = oos["dte"] if "dte" in oos else pd.NA
    oos["width"] = oos["exact_width"] if "exact_width" in oos else pd.NA
    oos["entry_credit"] = oos["credit"] if "credit" in oos else pd.NA
    oos["pnl_pct_of_credit"] = oos["pnl"] / (oos["entry_credit"] * 100.0) if "pnl" in oos and "entry_credit" in oos else pd.NA
    oos["stopped"] = oos["stop"].map({True: True, False: False})
    oos["exit_reason"] = oos["exit_reason"] if "exit_reason" in oos else pd.NA
    oos["holding_days"] = (oos["exit_date"] - oos["entry_date"]).dt.days
    oos["underlying_entry"] = pd.NA; oos["underlying_exit"] = pd.NA
    for c in ["regime", "support_state", "market_confirmation", "mae", "mfe", "short_strike_breach", "long_strike_breach"]:
        if c not in oos.columns: oos[c] = pd.NA
    preferred = ["ticker", "trade_id", "candidate_id", "entry_date", "exit_date", "expiration", "DTE", "underlying_entry", "underlying_exit", "short_strike", "long_strike", "width", "entry_credit", "exit_value", "pnl", "pnl_pct_of_credit", "stopped", "exit_reason", "holding_days", "safe_strike", "safe_strike_atr", "atr", "regime", "support_state", "market_confirmation", "mae", "mfe", "short_strike_breach", "long_strike_breach"]
    remaining = [c for c in oos.columns if c not in preferred]
    detail = oos[preferred + remaining].sort_values("entry_date").copy()
    detail.to_csv(OUT / f"{ticker.lower()}_final_oos_trades.csv", index=False)
    detail.nsmallest(10, "pnl").to_csv(OUT / f"{ticker.lower()}_worst_10.csv", index=False)
    detail["year"] = detail.entry_date.dt.year; detail["quarter"] = "Q" + detail.entry_date.dt.quarter.astype(str)
    for group, name in [(["year"], "year"), (["year", "quarter"], "year_quarter"), (["stopped"], "stopped"), (["exit_reason"], "exit_reason")]:
        detail.groupby(group, dropna=False).agg(trades=("pnl", "size"), total_pnl=("pnl", "sum"), expectancy=("pnl", "mean"), worst_trade=("pnl", "min")).reset_index().to_csv(OUT / f"{ticker.lower()}_summary_by_{name}.csv", index=False)
    detail[["trade_id", "entry_date", "pnl", "stopped", "exit_reason"]].assign(cumulative_pnl=detail.pnl.cumsum()).to_csv(OUT / f"{ticker.lower()}_cumulative_pnl.csv", index=False)
    return detail


def main():
    all_detail = {t: export(t) for t in ("SPY", "QQQ")}
    missing = ["underlying_entry", "underlying_exit", "regime", "support_state", "market_confirmation", "mae", "mfe", "short_strike_breach", "long_strike_breach"]
    summary = {"source": "sealed Entry Contract + lifecycle + outcomes only", "final_oos_window": [START, END], "trade_counts": {t: len(x) for t, x in all_detail.items()}, "missing_diagnostic_fields": missing, "current_market_data_queried": False}
    (OUT / "export_manifest.json").write_text(__import__("json").dumps(summary, indent=2, default=str))
    print(__import__("json").dumps(summary, indent=2, default=str))


if __name__ == "__main__": main()
