"""Supplemental, isolated validation replay for the frozen QQQ H006 candidate."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pcs.data.access import PCSDataAccess
from run_qqq_frozen_validation_20260824 import (
    OUT as PRIOR_OUT,
    SCENARIO,
    SPLIT,
    TRAIN_ART,
    features,
    first_per_episode,
    replay,
    selector_daily,
    sha256,
    stats,
)
import yaml
from pcs.research.rules.core import resolve_scenario

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "qqq_frozen_validation_20260824"
H006 = "H006_RECOVERY_STABILIZATION_AFTER_RESET"


def main() -> None:
    access = PCSDataAccess()
    scenario = resolve_scenario(yaml.safe_load(SCENARIO.read_text(encoding="utf-8")))
    spec = {
        "module": "pcs.validation.qqq_h006_supplemental_validation_20260824",
        "version": "1.0", "symbol": "QQQ", "candidate": H006,
        "split": {"start": str(SPLIT[0].date()), "end": str(SPLIT[1].date())},
        "definition": "drawdown60 <= -0.02 AND ret10 > 0 AND ret5 > 0",
        "entry_timing": "first date inside each independent reset/weakness episode where full H006 condition is true; maximum one entry per episode",
        "episode_logic": ">4 calendar days starts a new independent episode, matching pre-existing H006 TRAIN replay",
        "feature_source": "pre-existing H006 QQQ TRAIN feature/replay semantics; no discovery or optimization",
        "contract_selection": {"source": str(SCENARIO), "sha256": sha256(SCENARIO), "dte": [30, 45], "safe_strike_atr": 2.3, "width_priority": [5, 10, 2]},
        "lifecycle_stop_exit": "same canonical machinery as prior QQQ frozen validation",
        "final_oos_read": False, "H006_DEFINITION_CHANGED": False, "H006_THRESHOLDS_CHANGED": False,
        "VALIDATION_USED_TO_TUNE_H006": False, "FINAL_OOS_TOUCHED": False, "PRODUCTION_RULES_CHANGED": False,
        "OTHER_QQQ_VALIDATION_RESULTS_ALREADY_EXPOSED": True,
        "source_hashes": {"scenario": sha256(SCENARIO), "h006_train_audit": sha256(TRAIN_ART / "QQQ_FULL_TRAIN_EVIDENCE_REVIEW.json"), "h016_artifact": sha256(TRAIN_ART / "h016_sma50_reclaim.json"), "split": sha256(ROOT / "research_outputs/spy_qqq_pcs_baseline_20260821/split_manifest.json")},
    }
    (OUT / "h006_supplemental_frozen_spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")

    # H006 is constructed alone. No other validation result is loaded here.
    d = features(access)
    mask = (d.drawdown60 <= -0.02) & (d.ret10 > 0) & (d.ret5 > 0)
    signal_dates = first_per_episode(d, mask)
    validation_dates = [x for x in signal_dates if SPLIT[0] <= x <= SPLIT[1]]
    audit, rows = replay(H006, validation_dates, scenario, SPLIT[1], selector_daily(access))
    frame = pd.DataFrame(rows)
    frame.to_parquet(OUT / "H006_RECOVERY_STABILIZATION_lifecycle.parquet", index=False)
    pd.DataFrame(audit).to_parquet(OUT / "H006_RECOVERY_STABILIZATION_signal_audit.parquet", index=False)

    prior = json.loads((OUT / "validation_report.json").read_text(encoding="utf-8"))
    tail_cut = float(prior["baseline"]["tail_loss_cut"])
    result = {
        "candidate": H006, "validation_trading_dates": 102,
        "qualifying_dates": len(validation_dates), "qualifying_date_list": [str(x.date()) for x in validation_dates],
        "independent_episodes": len(validation_dates), "executable_trades": len(frame),
        "completed_trades": int(frame.pnl.notna().sum()), "tail_loss_cut": tail_cut,
        **stats(frame),
        "tail_loss_count": int((frame.pnl.dropna() <= tail_cut).sum()),
        "tail_loss_rate": float((frame.pnl.dropna() <= tail_cut).mean()) if frame.pnl.notna().any() else 0.0,
    }
    completed = frame.dropna(subset=["pnl"]).copy()
    result["yearly_metrics"] = {str(y): {**stats(g, tail_cut), "tail_loss_count": int((g.pnl <= tail_cut).sum()), "tail_loss_rate": float((g.pnl <= tail_cut).mean())} for y, g in completed.assign(year=pd.to_datetime(completed.date).dt.year).groupby("year")}
    result["episode_level_pnl"] = [{"date": str(r.date.date()), "pnl": float(r.pnl), "stop": bool(r.stop), "exit_reason": r.exit_reason} for r in completed.sort_values("date").itertuples()]
    result["profitable_episodes"] = int((completed.pnl > 0).sum())
    result["losing_episodes"] = int((completed.pnl < 0).sum())
    total = float(completed.pnl.sum()) if len(completed) else 0.0
    result["episode_robustness"] = {
        "median_episode_pnl": float(completed.pnl.median()) if len(completed) else None,
        "worst_episode_pnl": float(completed.pnl.min()) if len(completed) else None,
        "top_episode_pnl_share": float(completed.nlargest(1, "pnl").pnl.sum() / total) if total else None,
        "top_2_episode_pnl_share": float(completed.nlargest(2, "pnl").pnl.sum() / total) if total else None,
        "top_3_episode_pnl_share": float(completed.nlargest(3, "pnl").pnl.sum() / total) if total else None,
        "loo_status": "INSUFFICIENT_SAMPLE" if len(completed) < 10 else "COMPUTED",
        "minimum_loo_pnl": None if len(completed) < 10 else float(total - completed.pnl.max()),
        "negative_loo_count": None if len(completed) < 10 else int(((total - completed.pnl) < 0).sum()),
    }
    result["classification"] = "VALIDATION_FAILED" if result["expectancy"] is not None and result["expectancy"] < 0 else "POSITIVE_BUT_INSUFFICIENT" if len(completed) < 10 else "VALIDATION_SUPPORTED"
    (OUT / "H006_supplemental_validation_report.json").write_text(json.dumps({"spec": spec, "result": result}, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
