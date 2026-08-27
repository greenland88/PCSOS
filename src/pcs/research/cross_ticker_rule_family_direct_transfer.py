"""Direct-transfer battery for frozen reusable families (research only)."""
from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"
QQQ = ROOT / "research_outputs/qqq_entry_discovery_agent_v1/artifacts/qqq_pit_feature_outcome_table_train_2020_2023.parquet"

def metrics(g):
    p = g.realized_pnl.astype(float); wins = p[p > 0]; losses = p[p < 0]
    loo = [float(p.sum() - x) for x in p]
    top3 = float(p.nlargest(min(3, len(p))).sum() / p.sum()) if len(p) and p.sum() else None
    return {"qualifying_dates": int(len(g)), "independent_episodes": int(len(g)), "executable_episodes": int((g.lifecycle_completed == True).sum()),
            "total_pnl": float(p.sum()), "expectancy": float(p.mean()) if len(p) else None,
            "pf": float(wins.sum() / abs(losses.sum())) if len(losses) else None,
            "win_rate": float((p > 0).mean()) if len(p) else None, "stop_rate": float(g.stopped.mean()) if len(g) else None,
            "avg_win": float(wins.mean()) if len(wins) else None, "avg_loss": float(losses.mean()) if len(losses) else None,
            "worst_trade": float(p.min()) if len(p) else None, "years": sorted(g.trade_date.dt.year.unique().tolist()),
            "leave_one_episode_out": {"min_pnl": min(loo) if loo else None, "negative_count": int(sum(x < 0 for x in loo))},
            "top3_pnl_share": top3}

def episodes(d):
    d = d[d.lifecycle_completed == True].sort_values("trade_date").copy()
    d["gap"] = d.trade_date.diff().dt.days.fillna(999)
    d["episode_id"] = (d.gap > 10).cumsum()
    return d.groupby("episode_id", as_index=False).head(1)

def run(ticker="QQQ", outcome_path=QQQ):
    d = pd.read_parquet(outcome_path); d.trade_date = pd.to_datetime(d.trade_date)
    if "realized_pnl" not in d:
        raise ValueError("DIRECT_TRANSFER_REQUIRES_AUTHORITATIVE_LIFECYCLE_PNL")
    if "sma200" not in d or "ret5" not in d or "ret20" not in d or "volume_ratio20" not in d:
        dates = pd.to_datetime(d.trade_date)
        daily = PCSDataAccess().read_prices(ticker, dates.min() - pd.Timedelta(days=400), dates.max()).copy()
        daily.date = pd.to_datetime(daily.date); daily = daily.sort_values("date")
        close = daily.close.astype(float)
        features = pd.DataFrame({"trade_date":daily.date, "close":close, "sma200":close.rolling(200, min_periods=200).mean(), "ret5":close.pct_change(5), "ret20":close.pct_change(20), "volume_ratio20":daily.volume / daily.volume.rolling(20, min_periods=20).mean()})
        d = d.merge(features, on="trade_date", how="left")
    specs = {
        "PCS_TREND_CONTINUATION": (d.close > d.sma200) & (d.volume_ratio20 > 1) & (d.ret5 > 0),
        "PCS_CONSTRUCTIVE_RECOVERY": (d.close > d.sma200) & (d.ret20 < 0) & (d.ret5 > 0),
    }
    results = {}
    for family, mask in specs.items():
        qualifying = d[mask]
        q = episodes(qualifying)
        m = metrics(q)
        m["qualifying_dates"] = int(len(qualifying))
        m["family"] = family; m["ticker"] = ticker
        m["classification"] = ("DIRECT_TRANSFER_PASS" if m["independent_episodes"] >= 10 and m["total_pnl"] > 0 and (m["pf"] or 0) > 1 and m["leave_one_episode_out"]["negative_count"] == 0 else "INSUFFICIENT_DATA" if m["independent_episodes"] < 10 else "NO_TRANSFER")
        results[family] = m
    report = {"ticker":ticker, "source":str(outcome_path), "mode":"BROAD_NEW_ENTRY_CANONICAL_LIFECYCLE_DIRECT_TRANSFER", "results":results, "final_oos_read":False, "production_changes":False, "thresholds_modified":False}
    target = OUT / "cross_ticker_transfer"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{str(ticker).lower()}_direct_transfer.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report

if __name__ == "__main__": print(json.dumps(run(), indent=2, default=str))
