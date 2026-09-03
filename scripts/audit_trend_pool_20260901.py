"""Audit daily trend/timing independently from the options gate."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.snapshot import build_trend_snapshot
from pcs.trend.interpretation import interpret_trend
from pcs.trend.scoring import score_trend
from pcs.entry.trend_gate import evaluate_trend_gate
from pcs.entry.pullback_gate import evaluate_pullback_gate

AS_OF = "2026-09-01"
ARTIFACT = Path("research_outputs/global_pcs_base_universe/pool_selection_manual_validation_20260901.csv")
OUT = Path("research_outputs/global_pcs_base_universe/trend_audit_20260901.json")
ROWS_OUT = OUT.with_suffix(".rows.jsonl")

def one(symbol, access):
    try:
        # PCSDataAccess keeps a process-local manifest snapshot; each worker
        # gets its own canonical reader so parallel audits cannot race that
        # snapshot and silently fall back to the 2026-only row set.
        access = PCSDataAccess.canonical()
        warmup_start = (pd.Timestamp(AS_OF) - pd.Timedelta(days=400)).date().isoformat()
        daily = access.read_prices(symbol, start_date=warmup_start, end_date=AS_OF).sort_values("date")
        bench = access.read_prices("QQQ", start_date=warmup_start, end_date=AS_OF).sort_values("date")
        snap = build_trend_snapshot(daily, bench, as_of_date=pd.Timestamp(AS_OF), symbol=symbol, benchmark="QQQ", config=TrendIndicatorConfig())
        interp = interpret_trend(snap); score = score_trend(snap, interp)
        trend = evaluate_trend_gate(score, interp, snap); pull = evaluate_pullback_gate(trend, snap, interp)
        ms = getattr(snap, "market_structure_engine", None)
        return {"symbol": symbol, "history_rows": len(daily), "feature_max_date": str(getattr(ms, "feature_max_date", "")),
                "trend_state": getattr(score, "trend_state", "UNKNOWN"), "trend_direction": getattr(interp, "trend_direction", "UNKNOWN"),
                "trend_health": getattr(interp, "trend_health", "UNKNOWN"), "structural_trend": getattr(ms, "structural_trend", "UNKNOWN"),
                "phase": getattr(ms, "short_term_phase", "UNKNOWN"), "trend_gate": trend.trend_gate_result,
                "pullback_gate": pull.pullback_gate_result, "trend_reasons": list(trend.reasons), "pullback_reasons": list(pull.reasons),
                "sma20_slope": getattr(ms, "sma20_slope", None), "sma50_slope": getattr(ms, "sma50_slope", None), "ema200_slope": getattr(ms, "ema200_slope", None)}
    except Exception as exc:
        return {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}", "trend_gate": "UNAVAILABLE"}

def main():
    symbols = sorted(pd.read_csv(ARTIFACT).query("status == 'CURRENT_TO_2026_09_01'").symbol.astype(str).str.upper())
    access = PCSDataAccess.canonical(); rows=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(one, s, access) for s in symbols]
        for f in as_completed(futures): rows.append(f.result())
    frame=pd.DataFrame(rows).sort_values("symbol")
    def counts(col): return frame[col].fillna("UNKNOWN").value_counts().to_dict() if col in frame else {}
    reasons={}
    for col in ("trend_reasons", "pullback_reasons"):
        if col in frame:
            for values in frame[col].dropna():
                for value in values if isinstance(values, list) else []: reasons[value]=reasons.get(value,0)+1
    result={"as_of":AS_OF,"universe":len(frame),"trend_state_distribution":counts("trend_state"),"phase_distribution":counts("phase"),
            "structural_trend_distribution":counts("structural_trend"),"trend_gate_distribution":counts("trend_gate"),
            "pullback_gate_distribution":counts("pullback_gate"),"reason_counts":dict(sorted(reasons.items(),key=lambda x:(-x[1],x[0]))),
            "insufficient_history":int((frame.get("history_rows",pd.Series(dtype=float))<260).sum()),
            "feature_max_date_distribution":counts("feature_max_date"),"sample":frame[frame.symbol.isin(["NVDA","QQQ"] + symbols[:18])].to_dict("records")}
    OUT.write_text(json.dumps(result,indent=2,default=str),encoding="utf-8")
    frame.to_json(ROWS_OUT, orient="records", lines=True, date_format="iso")
    print(json.dumps({k:result[k] for k in ("universe","trend_state_distribution","phase_distribution","structural_trend_distribution","trend_gate_distribution","pullback_gate_distribution","insufficient_history")},indent=2))
if __name__ == "__main__": main()
