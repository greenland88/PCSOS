from pathlib import Path
import json
import pandas as pd
from pcs.research.variant_b_replay import summarize_replay

ROOT = Path("research_outputs/oos_walk_forward_20260821")

def yearly(frame):
    if frame.empty:
        return {}
    x = frame.copy(); x["year"] = pd.to_datetime(x["date"]).dt.year
    return {str(int(y)): summarize_replay(g).to_dict("records")[0] for y, g in x.groupby("year")}

def classification(t, tr, va, oo):
    if t == "NVDA":
        return "INSUFFICIENT_DATA"
    vals = [tr.get("expectancy_per_trade"), va.get("expectancy_per_trade"), oo.get("expectancy_per_trade")]
    if any(v is None for v in vals):
        return "INSUFFICIENT_DATA"
    if all(v > 0 for v in vals) and all((x.get("profit_factor") or 0) >= 1 for x in (tr, va, oo)):
        return "ROBUST_POSITIVE"
    if all(v < 0 for v in vals):
        return "NEGATIVE"
    if sum(v > 0 for v in vals) >= 2:
        return "CONDITIONAL_POSITIVE"
    return "UNSTABLE"

all_results = {}
for ticker in ("AMD", "AMZN", "TSLA", "NVDA"):
    replay = pd.read_parquet(ROOT / f"{ticker}_train_validation_replay.parquet")
    split = json.loads((Path("research_outputs/oos_splits_20260821") / f"{ticker}.json").read_text())
    result = json.loads((ROOT / f"{ticker}_validation.json").read_text())
    for part in split["splits"]:
        mask = pd.to_datetime(replay["date"]).between(part["start"], part["end"])
        g = replay.loc[mask].copy()
        s = summarize_replay(g, test_start_date=part["start"], test_end_date=part["end"])
        rec = s.to_dict("records")[0] if not s.empty else {}
        rec["yearly_breakdown"] = yearly(g)
        rec["split_candidate_count"] = int(len(g))
        rec["stop_rate"] = rec.get("stop_frequency")
        rec["return_on_planned_risk"] = rec.get("annualized_return_on_average_planned_loss")
        result["splits"][part["name"]] = rec
    result["classification"] = classification(ticker, result["splits"]["TRAIN"], result["splits"]["VALIDATION"], result["splits"]["FINAL_OOS"])
    result["final_oos_status"] = "RUN_ONCE_FROZEN_CONFIG"
    (ROOT / f"{ticker}_final_oos.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    all_results[ticker] = result

system = {
    "frozen_config_id": "PCS-OOS-FROZEN-20260821-V1",
    "config_hash": "CBAF956586AF43AFEAF0E3512E4B491D625613464392FD4440800BDBC026793B",
    "final_oos": "RUN_ONCE",
    "parameter_search": "NOT_RUN",
    "rules_modified": False,
    "tickers": {t: {"classification": r["classification"], "train": r["splits"]["TRAIN"], "validation": r["splits"]["VALIDATION"], "final_oos": r["splits"]["FINAL_OOS"]} for t, r in all_results.items()}
}
positive = [r["classification"] in {"ROBUST_POSITIVE", "CONDITIONAL_POSITIVE"} for r in all_results.values()]
system["system_classification"] = "PASS" if all(positive) else "WEAK PASS" if sum(positive) >= 3 else "FAIL" if sum(positive) == 0 else "INSUFFICIENT EVIDENCE"
(ROOT / "system_summary.json").write_text(json.dumps(system, indent=2, default=str), encoding="utf-8")
print(json.dumps(system, indent=2, default=str))
