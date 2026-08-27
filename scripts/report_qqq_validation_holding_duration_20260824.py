"""Report actual holding duration from persisted QQQ validation lifecycles."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "qqq_frozen_validation_20260824"
END = pd.Timestamp("2026-05-31")

FILES = {
    "QQQ_CONTROLLED_RESET": OUT / "QQQ_CONTROLLED_RESET_lifecycle.parquet",
    "H006_RECOVERY_STABILIZATION": OUT / "H006_RECOVERY_STABILIZATION_lifecycle.parquet",
    "QQQ_SMA50_RECLAIM_AFTER_WEAKNESS": OUT / "QQQ_SMA50_RECLAIM_AFTER_WEAKNESS_lifecycle.parquet",
}

def main() -> None:
    access = PCSDataAccess()
    cal = access.read_prices("QQQ", "2010-01-01", "2026-05-31")
    trading_dates = pd.DatetimeIndex(pd.to_datetime(cal.date).dt.normalize().drop_duplicates().sort_values())
    rows = []
    for strategy, path in FILES.items():
        d = pd.read_parquet(path).copy()
        d["date"] = pd.to_datetime(d["date"]).dt.normalize()
        d["exit_date"] = pd.to_datetime(d["exit_date"]).dt.normalize()
        d = d[d.pnl.notna() & d.exit_date.notna()].copy()
        for r in d.itertuples():
            entry = pd.Timestamp(r.date); exit_date = pd.Timestamp(r.exit_date)
            expiry = pd.Timestamp(r.expiration)
            rows.append({
                "strategy_name": strategy,
                "entry_date": str(entry.date()),
                "exit_date": str(exit_date.date()),
                "entry_DTE": int(r.dte),
                "exit_DTE": int((expiry - exit_date).days),
                "trading_days_held": int(((trading_dates >= entry) & (trading_dates <= exit_date)).sum()),
                "calendar_days_held": int((exit_date - entry).days),
                "exit_reason": str(r.exit_reason),
                "P&L": float(r.pnl),
            })
    detail = pd.DataFrame(rows).sort_values(["strategy_name", "entry_date"]).reset_index(drop=True)
    detail.to_csv(OUT / "qqq_validation_actual_holding_duration.csv", index=False)
    summary = {}
    for strategy, g in detail.groupby("strategy_name", sort=False):
        summary[strategy] = {
            "completed_trades": int(len(g)),
            "average_trading_days_held": float(g.trading_days_held.mean()),
            "median_trading_days_held": float(g.trading_days_held.median()),
            "minimum_trading_days_held": int(g.trading_days_held.min()),
            "maximum_trading_days_held": int(g.trading_days_held.max()),
            "average_days_held_by_exit_reason": {
                reason: ({"trading_days": float(x.trading_days_held.mean()), "calendar_days": float(x.calendar_days_held.mean()), "count": int(len(x))} if len(x) else None)
                for reason in ["PROFIT50", "STOP", "TIME_EXIT", "EXPIRATION"]
                for x in [g[g.exit_reason.eq(reason)]]
            },
        }
    report = {"module": "pcs.reporting.qqq_validation_actual_holding_duration", "source_artifacts": {k: str(v) for k, v in FILES.items()}, "final_oos_touched": False, "strategy_logic_rerun": False, "detail": rows, "summary": summary}
    (OUT / "qqq_validation_actual_holding_duration.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(detail.to_string(index=False))
    print(json.dumps(summary, indent=2, default=str))

if __name__ == "__main__":
    main()
