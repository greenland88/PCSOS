"""Run pre-existing QQQ recovery definitions across the full clean COST population."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from dataclasses import replace

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.research.runner import ResearchRunner
from pcs.research.research_framework import ResearchMode, validate_population_routing, validate_rule_set

ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "research_outputs" / "cost_canonical_test_dataset" / "cost_clean_testable_days.parquet"
OUT = ROOT / "research_outputs" / "cost_frozen_recovery_transfer"

STRATEGIES = {
    "controlled_reset": {"name": "Controlled Reset", "id": "QQQ_CONTROLLED_RESET", "definition": "drawdown60 <= -0.02 AND ret10 > 0; first qualification per >4-calendar-day episode", "origin": "QQQ frozen historical-test family", "artifact": "research_outputs/qqq_frozen_validation_20260824/frozen_validation_spec.json"},
    "recovery_stabilization": {"name": "Recovery Stabilization After Reset", "id": "H006_RECOVERY_STABILIZATION", "definition": "drawdown60 <= -0.02 AND ret10 > 0 AND ret5 > 0; first confirmation within each reset episode", "origin": "QQQ H006 frozen historical-test family", "artifact": "research_outputs/qqq_frozen_validation_20260824/h006_supplemental_frozen_spec.json"},
    "sma50_reclaim": {"name": "SMA50 Reclaim After Weakness", "id": "H016_SMA50_RECLAIM", "definition": "drawdown60 <= -0.02 AND prior close_sma50_atr <= 0 AND current close_sma50_atr > 0; first reclaim per >4-calendar-day episode", "origin": "QQQ H016 frozen historical-test family", "artifact": "research_outputs/qqq_frozen_validation_20260824/frozen_validation_spec.json"},
}


def first_per_episode(frame: pd.DataFrame) -> list[str]:
    if frame.empty: return []
    q = frame.sort_values("date").copy(); q["episode"] = (q.date.diff().dt.days.fillna(999) > 4).cumsum()
    return [x.date.date().isoformat() for _, x in q.groupby("episode", sort=True).first().iterrows()]


def signals(kind: str, x: pd.DataFrame) -> list[str]:
    reset = x[x.drawdown60.le(-.02) & x.ret10.gt(0)].copy()
    if kind == "controlled_reset": return first_per_episode(reset)
    if kind == "sma50_reclaim": return first_per_episode(x[x.drawdown60.le(-.02) & x.prior_close_sma50_atr.le(0) & x.close_sma50_atr.gt(0)])
    if reset.empty: return []
    reset["episode"] = (reset.date.diff().dt.days.fillna(999) > 4).cumsum()
    return [g.iloc[0].date.date().isoformat() for _, g in reset.groupby("episode", sort=True) if (g.ret5 > 0).any() for g in [g[g.ret5 > 0]]]


def prepare() -> tuple[pd.DataFrame, dict]:
    x = pd.read_parquet(IN).sort_values("date").reset_index(drop=True)
    x.date = pd.to_datetime(x.date).dt.normalize()
    x["close_sma50_atr"] = (x.close - x.sma50) / x.atr14
    x["prior_close_sma50_atr"] = x.close_sma50_atr.shift(1)
    return x, {"clean_dataset": str(IN.relative_to(ROOT)), "clean_testable_days": len(x), "start": str(x.date.min().date()), "end": str(x.date.max().date()), "all_clean_testable_days_evaluated": True}


def run(kind: str) -> dict:
    x, population = prepare(); meta = STRATEGIES[kind]; dates = signals(kind, x)
    q = x[x.date.isin(pd.to_datetime(dates))].copy()
    q["episode"] = (q.date.diff().dt.days.fillna(999) > 4).cumsum()
    cfg = ROOT / "config/research/spy_frozen_controlled_reset_transfer.yaml"
    base = ResearchRunner.from_path(cfg).spec
    spec = replace(base, research_id=f"cost_frozen_{kind}", ticker="COST", research_mode=ResearchMode.CURRENT_STRATEGY_REPLAY,
                   population_source={"type":"ticker_daily_calendar","authoritative_clean_dataset":str(IN.relative_to(ROOT)),"frozen":False,"point_in_time":True},
                   signal_definition={"track_a_execution_only":True,"creates_new_entry_dates":True,"frozen_predicate":kind,"execution_dates":dates},
                   date_range={"start":population["start"],"end":population["end"],"split":"FULL_CLEAN_AVAILABLE_HISTORY"},
                   split_policy={"name":"FULL_CLEAN_AVAILABLE_HISTORY","train_end":population["end"],"2026_included":True},
                   rules={"dte_min":30,"dte_max":45,"safe_strike_atr":2.3,"allowed_widths":[5,10,2],"width_mode":"ALL","min_credit_width_ratio":.10,"trend_gate":False,"pullback_gate":False,"support_gate":False,"regime_gate":False,"event_gate":True,"liquidity_gate":True,"predictability_gate":False})
    runner = ResearchRunner(validate_rule_set(validate_population_routing(spec)))
    result = runner.execute_research_replay(data_access=PCSDataAccess())
    base_out = OUT / kind; base_out.mkdir(parents=True, exist_ok=True)
    funnel = result.get("funnel", {})
    report = {"module":"pcs.research.cost_frozen_recovery_transfer","version":"1.0","ticker":"COST","strategy":meta,"population":population,"frozen_before_cost_test":True,"cost_used_for_strategy_discovery":False,"cost_used_for_threshold_tuning":False,"qualifying_days":int(len(q)),"independent_episodes":int(q.episode.nunique()),"signal_dates":dates,"signal_dates_by_year":q.date.dt.year.value_counts().sort_index().astype(int).to_dict(),"canonical_replay":result,"controls":{"COST_CANONICAL_CLEAN_DATASET_USED":True,"ALL_CLEAN_TESTABLE_DAYS_EVALUATED":True,"COST_SPECIFIC_PREFILTER_ADDED":False,"STRATEGY_DEFINITIONS_CHANGED":False,"THRESHOLDS_CHANGED":False,"LIFECYCLE_RULES_CHANGED":False,"PRODUCTION_RULES_CHANGED":False,"FINAL_OOS_READ":False}}
    (base_out/"transfer_report.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1]), indent=2, default=str))
