"""Run only TRAIN and VALIDATION summaries for the frozen SPY/QQQ artifacts."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
SPLITS = json.loads((ROOT / "split_manifest.json").read_text(encoding="utf-8"))["splits"]


def _one(contract: pd.Series, marks: pd.DataFrame) -> dict:
    initial = float(contract.credit)
    x = marks.sort_values("mark_date").head(20).copy()
    x = x[x.quote_available].copy()
    if x.empty:
        return {"candidate_id": contract.candidate_id, "decision_date": contract.decision_date, "pnl": np.nan, "exit_reason": "INSUFFICIENT_QUOTES", "stop": False}
    x["conservative_cost"] = x.short_ask - x.long_bid
    stop = x[x.stop_triggered].head(1)
    target = x[x.conservative_cost <= initial * 0.5].head(1)
    if not target.empty and (stop.empty or target.iloc[0].mark_date <= stop.iloc[0].mark_date):
        exit_row, reason, stopped = target.iloc[0], "PROFIT50", False
    elif not stop.empty:
        exit_row, reason, stopped = stop.iloc[0], "STOP", True
    else:
        exit_row, reason, stopped = x.iloc[-1], "TIME_EXIT", False
    pnl = (initial - float(exit_row.conservative_cost)) * 100.0
    return {"candidate_id": contract.candidate_id, "decision_date": contract.decision_date, "pnl": pnl, "exit_reason": reason, "stop": stopped}


def _stats(out: pd.DataFrame) -> dict:
    out = out.dropna(subset=["pnl"]).sort_values("decision_date").copy()
    pnl = out.pnl.astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    curve = pnl.cumsum()
    dd = curve - curve.cummax()
    gross_loss = abs(float(losses.sum()))
    years = out.assign(year=pd.to_datetime(out.decision_date).dt.year).groupby("year").agg(trade_count=("pnl", "size"), total_pnl=("pnl", "sum"), expectancy=("pnl", "mean"), stop_rate=("stop", "mean")).reset_index()
    return {
        "trade_count": int(len(out)), "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
        "expectancy": float(pnl.mean()) if len(pnl) else None,
        "total_pnl": float(pnl.sum()) if len(pnl) else None,
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss else None,
        "average_winner": float(wins.mean()) if len(wins) else None,
        "average_loser": float(losses.mean()) if len(losses) else None,
        "max_drawdown": float(dd.min()) if len(dd) else None,
        "stop_rate": float(out.stop.mean()) if len(out) else None,
        "worst_trade": float(pnl.min()) if len(pnl) else None,
        "return_on_planned_risk": float(pnl.sum() / out.planned_risk.sum()) if len(out) and out.planned_risk.sum() else None,
        "yearly_breakdown": years.to_dict("records"),
    }


def main() -> None:
    report = {"module": "spy_qqq_train_validation", "version": "20260821.v1", "final_oos_run": False, "symbols": {}}
    for symbol in ("SPY", "QQQ"):
        c = pd.read_parquet(ROOT / f"{symbol}_entry_contract_v2.parquet")
        l = pd.read_parquet(ROOT / f"{symbol}_lifecycle_marks.parquet")
        rows = []
        for _, contract in c.iterrows():
            marks = l[l.candidate_id.eq(contract.candidate_id)]
            row = _one(contract, marks)
            row["planned_risk"] = float(contract.planned_loss)
            rows.append(row)
        outcomes = pd.DataFrame(rows)
        outcomes.to_parquet(ROOT / f"{symbol}_train_validation_outcomes.parquet", index=False)
        report["symbols"][symbol] = {}
        for split in SPLITS[symbol][:2]:
            a, b = pd.Timestamp(split["start"]), pd.Timestamp(split["end"])
            subset = outcomes[pd.to_datetime(outcomes.decision_date).between(a, b)]
            stats = _stats(subset)
            stats["classification"] = (
                "ROBUST_POSITIVE" if stats["trade_count"] >= 30 and stats["expectancy"] > 0 and (stats["profit_factor"] or 0) >= 1.25
                else "WEAK_POSITIVE" if stats["expectancy"] > 0 and (stats["profit_factor"] or 0) > 1
                else "NEGATIVE" if stats["expectancy"] is not None and stats["expectancy"] < 0
                else "INSUFFICIENT_SAMPLE"
            )
            report["symbols"][symbol][split["name"]] = stats
    vals = [report["symbols"][s]["VALIDATION"] for s in ("SPY", "QQQ")]
    report["system_index_baseline"] = "PASS" if all(x["classification"] == "ROBUST_POSITIVE" for x in vals) else "WEAK PASS" if all(x["classification"] in {"ROBUST_POSITIVE", "WEAK_POSITIVE"} for x in vals) else "FAIL" if all(x["trade_count"] >= 30 for x in vals) else "INSUFFICIENT EVIDENCE"
    report["final_oos_ready"] = "YES"
    (ROOT / "train_validation_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
