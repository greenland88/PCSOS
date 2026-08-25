"""Canonical regression replay for the three frozen QQQ strategies."""
from __future__ import annotations
import json, hashlib, os
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.current_strategy_replay import run_current_strategy_replay
from pcs.research.research_framework import from_mapping, validate_rule_set

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "frozen_strategy_regression" / "QQQ"
START, END = pd.Timestamp("2020-01-01"), pd.Timestamp("2026-05-31")
STRATEGIES = {
    "controlled_reset": ("Controlled Reset", lambda d: (d.drawdown60 <= -.02) & (d.ret10 > 0)),
    "recovery_stabilization": ("Recovery Stabilization After Reset", lambda d: (d.drawdown60 <= -.02) & (d.ret10 > 0) & (d.ret5 > 0)),
    "sma50_reclaim": ("SMA50 Reclaim After Weakness", lambda d: (d.drawdown60 <= -.02) & (d.ret10 > 0) & (d.prior_close_sma50_atr <= 0) & (d.close_sma50_atr > 0)),
}

def features(access):
    d = access.read_prices("QQQ", "2018-01-01", "2026-05-31").copy()
    d.date = pd.to_datetime(d.date).dt.normalize(); d = d.sort_values("date").reset_index(drop=True)
    prev = d.close.shift(1)
    tr = pd.concat([d.high-d.low, (d.high-prev).abs(), (d.low-prev).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14, min_periods=14).mean(); d["ret5"] = d.close.pct_change(5); d["ret10"] = d.close.pct_change(10)
    d["drawdown60"] = d.close / d.close.rolling(60, min_periods=60).max() - 1
    d["sma50"] = d.close.rolling(50, min_periods=50).mean(); d["close_sma50_atr"] = (d.close-d.sma50)/d.atr14; d["prior_close_sma50_atr"] = d.close_sma50_atr.shift(1)
    return d

def first_per_episode(d, mask, base=None):
    base = mask if base is None else base
    b = d.loc[base].sort_values("date").copy(); b["episode"] = (b.date.diff().dt.days.fillna(999) > 4).cumsum()
    x = d.loc[mask].sort_values("date").copy().merge(b[["date","episode"]], on="date", how="inner")
    return [pd.Timestamp(v).normalize() for v in x.groupby("episode", as_index=False).first().date]

def make_spec(research_id, dates):
    raw = {"research_id": research_id, "ticker": "QQQ", "research_mode": "CURRENT_STRATEGY_REPLAY",
      "hypothesis": "Regression replay of unchanged frozen QQQ strategy.",
      "population_source": {"type":"ticker_daily_calendar", "frozen":False},
      "signal_definition": {"creates_new_entry_dates":True, "purpose":"current_strategy_replay", "benchmark_symbol":"QQQ", "execution_dates":[str(x.date()) for x in dates], "track_a_execution_only":True},
      "entry_date_rule": {"rule":"frozen_signal_date_population"},
      "date_range": {"start":str(START.date()), "end":str(END.date()), "split":"TRAIN_AND_HISTORICAL_CLEAN_WITH_2026_VALIDATION"},
      "split_policy": {"name":"TRAIN_HISTORICAL_CLEAN_VALIDATION", "train_end":"2026-05-31", "frozen_train_end":"2023-12-31", "final_oos_start":"2026-06-01"},
      "contract_selection_policy": {"mode":"RULE_SET"}, "lifecycle_policy": {"source":"canonical_lifecycle_adapter"},
      "frozen_parameters": {"strategy_definition":"FIXED_FROZEN_QQQ"}, "allowed_parameters": {"rules":False},
      "final_oos_access":False, "production_changes_allowed":False,
      "rules":{"dte_min":30,"dte_max":45,"safe_strike_atr":2.3,"allowed_widths":[5,10,2],"width_mode":"ALL","min_credit_width_ratio":.10,"trend_gate":True,"pullback_gate":True,"support_gate":True,"regime_gate":False,"event_gate":True,"liquidity_gate":True,"predictability_gate":True}}
    return validate_rule_set(from_mapping(raw))

def main():
    OUT.mkdir(parents=True, exist_ok=True); access = PCSDataAccess.canonical(); d = features(access)
    reports = {}; date_rows = []
    requested = os.environ.get("QQQ_FROZEN_ONLY")
    selected = {requested: STRATEGIES[requested]} if requested else STRATEGIES
    for key, (label, predicate) in selected.items():
        print(f"START {key}", flush=True)
        base = predicate(d)
        dates = [x for x in first_per_episode(d, predicate(d), base=base) if START <= x <= END]
        for x in dates: date_rows.append({"strategy":label,"signal_date":str(x.date()),"period":"2026 validation" if x.year==2026 else "2024" if x.year==2024 else "2025" if x.year==2025 else "TRAIN"})
        spec = make_spec(f"qqq_frozen_{key}_canonical_20260825", dates)
        report = run_current_strategy_replay(spec, output_dir=OUT, data_access=access)
        print(f"DONE {key} lifecycles={report.get('funnel',{}).get('LIFECYCLES_COMPLETED')}", flush=True)
        reports[key] = {"label":label,"signal_dates":[str(x.date()) for x in dates],"replay":report,"research_id":spec.research_id}
    (OUT/"canonical_signal_dates.json").write_text(json.dumps(reports,indent=2,default=str),encoding="utf-8")
    pd.DataFrame(date_rows).to_csv(OUT/"canonical_signal_dates.csv",index=False)
    print(json.dumps(reports,indent=2,default=str))

if __name__ == "__main__": main()
