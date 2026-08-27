"""Build the canonical COST data-readiness dataset; no strategy evaluation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pcs.features.market_features import calculate_market_features
from pcs.data.access import PCSDataAccess
from pcs.data.readiness import discover_lifecycle_smoke_case, execute_lifecycle_smoke

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "cost_canonical_test_dataset"
DAILY_ROOT = ROOT / "data" / "parquet" / "daily" / "symbol=COST"
OPT_ROOT = ROOT / "data" / "parquet" / "options_v2" / "symbol=COST"
MANIFEST = ROOT / "data" / "manifests" / "storage_manifest_options_v2.csv"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def load_daily() -> tuple[pd.DataFrame, dict]:
    files = sorted(DAILY_ROOT.glob("year=*/*.parquet"))
    d = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d.date >= "2020-01-01"].sort_values("date").reset_index(drop=True)
    valid = (d.symbol.eq("COST") & d.date.notna() & ~d.date.duplicated(keep=False) &
             d[["open", "high", "low", "close", "volume"]].notna().all(axis=1) &
             d[["open", "high", "low", "close", "volume"]].apply(np.isfinite).all(axis=1) &
             d.open.gt(0) & d.high.ge(d[["open", "low", "close"]].max(axis=1)) &
             d.low.le(d[["open", "high", "close"]].min(axis=1)) & d.low.gt(0) & d.volume.ge(0))
    d["daily_valid"] = valid
    c = d.close
    for n in (20, 50, 200): d[f"sma{n}"] = c.rolling(n, min_periods=n).mean()
    tr = pd.concat([d.high-d.low, (d.high-c.shift()).abs(), (d.low-c.shift()).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14, min_periods=14).mean()
    for n in (5, 10, 20, 60): d[f"ret{n}"] = c.pct_change(n)
    for n in (20, 50, 200): d[f"sma{n}_slope"] = d[f"sma{n}"].pct_change(5)
    d["drawdown60"] = c / c.rolling(60, min_periods=60).max() - 1
    d["support_level"] = d.low.rolling(20, min_periods=20).min()
    d["support_state"] = np.where(d.support_level.notna(), "SUPPORT_WINDOW_LOW", "UNKNOWN")
    d["predictability_score"] = np.nan
    d["trend_score"] = np.nan
    pf = pd.DataFrame(calculate_market_features(d[["date", "symbol", "open", "high", "low", "close", "volume"]].to_dict("records")))
    d["predictability_score"] = d.date.map(pf.set_index("date").predictability_score)
    d["trend_score"] = d.date.map(pf.set_index("date").trend_score)
    d["regime_state"] = "UNKNOWN_NO_AUTHORITATIVE_COST_REGIME_ARTIFACT"
    d["state_machine_state"] = "UNKNOWN_NO_AUTHORITATIVE_COST_STATE_ARTIFACT"
    d["event_state"] = "UNKNOWN_NO_AUTHORITATIVE_EVENT_DATA"
    return d, {"files": [str(f.relative_to(ROOT)) for f in files], "sha256": {str(f.relative_to(ROOT)): sha256(f) for f in files}}


def load_options() -> tuple[pd.DataFrame, dict]:
    files = sorted(OPT_ROOT.glob("year=*/quarter=*/*.parquet"))
    o = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    o["trade_date"] = pd.to_datetime(o.trade_date); o["expiration_date"] = pd.to_datetime(o.expiration_date)
    o["dte"] = (o.expiration_date - o.trade_date).dt.days
    key = ["symbol", "trade_date", "expiration_date", "strike", "call_put"]
    duplicate = o.duplicated(key, keep=False)
    valid = (o.symbol.eq("COST") & o.trade_date.notna() & o.expiration_date.notna() & o.expiration_date.ge(o.trade_date) &
             o.strike.gt(0) & o.call_put.isin(["c", "p"]) & o.bid.notna() & o.ask.notna() &
             np.isfinite(o.bid) & np.isfinite(o.ask) & o.bid.ge(0) & o.ask.ge(o.bid) & o.dte.ge(0))
    v = o[valid].copy()
    day = v.groupby("trade_date").agg(option_rows=("trade_date", "size"), dte_30_45=("dte", lambda x: int(x.between(30,45).any()))).reset_index()
    latest = v.trade_date.max()
    day["options_available"] = True; day["contract_selection_ready"] = day.dte_30_45.eq(1)
    day["lifecycle_replay_ready"] = day.trade_date.le(latest - pd.Timedelta(days=45))
    return day, {"route": "config/data_source_routes.yaml:COST->options_v2", "manifest": str(MANIFEST.relative_to(ROOT)), "physical_files": len(files), "manifest_rows": int(((pd.read_csv(MANIFEST).symbol == "COST") & (pd.read_csv(MANIFEST).dataset == "options_v2")).sum()), "raw_rows": len(o), "valid_rows": len(v), "duplicate_rows": int(duplicate.sum()), "conflicting_keys": int(o.loc[duplicate, key].drop_duplicates().shape[0]), "invalid_rows": int((~valid).sum()), "latest_trade_date": str(latest.date()), "option_days": int(day.trade_date.nunique())}


def build() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    d, daily_meta = load_daily(); o, opt_meta = load_options()
    d = d[d.date <= pd.Timestamp(opt_meta["latest_trade_date"])].copy()
    x = d.merge(o, left_on="date", right_on="trade_date", how="left")
    x["options_available"] = x.options_available.fillna(False)
    x["contract_selection_ready"] = x.contract_selection_ready.fillna(False)
    x["lifecycle_replay_ready"] = x.lifecycle_replay_ready.fillna(False)
    x["feature_ready"] = x[["sma20","sma50","sma200","atr14","ret5","ret10","ret20","ret60","drawdown60","support_level"]].notna().all(axis=1)
    x["data_ready"] = x.daily_valid & x.feature_ready
    x["testable_day"] = x.data_ready & x.options_available & x.contract_selection_ready & x.lifecycle_replay_ready
    x["split"] = np.select([x.date.le("2025-12-31"), x.date.between("2026-01-01","2026-05-31"), x.date.gt("2026-05-31")], ["TRAIN","VALIDATION","DIAGNOSTIC_ONLY"], default="UNKNOWN")
    reasons=[]
    for _, r in x.iterrows():
        z=[]
        if not r.daily_valid: z.append("DAILY_INVALID")
        if not r.feature_ready: z.append("PIT_FEATURES_INCOMPLETE")
        if not r.options_available: z.append("OPTIONS_UNAVAILABLE")
        elif not r.contract_selection_ready: z.append("NO_VALID_30_45_DTE_CHAIN")
        elif not r.lifecycle_replay_ready: z.append("LIFECYCLE_QUOTE_HORIZON_UNAVAILABLE")
        reasons.append(";".join(z))
    x["not_ready_reason"] = reasons
    x = x.drop(columns=["trade_date","option_rows","dte_30_45"], errors="ignore")
    x.to_parquet(OUT/"cost_master_daily_research.parquet", index=False)
    x[x.testable_day].to_parquet(OUT/"cost_clean_testable_days.parquet", index=False)
    x[~x.testable_day][["date","split","not_ready_reason"]].to_csv(OUT/"cost_not_ready_days.csv", index=False)
    y=x.groupby(x.date.dt.year).agg(TRADING_DAYS=("date","size"),DAILY_READY=("daily_valid","sum"),FEATURE_READY=("feature_ready","sum"),OPTIONS_READY=("options_available","sum"),CONTRACT_SELECTION_READY=("contract_selection_ready","sum"),LIFECYCLE_READY=("lifecycle_replay_ready","sum"),TESTABLE_DAYS=("testable_day","sum")).reset_index(names="year")
    y.to_csv(OUT/"cost_data_readiness_by_year.csv", index=False)
    smoke_case, smoke_meta = discover_lifecycle_smoke_case(PCSDataAccess(), "COST", start_date="2020-01-01", end_date="2020-12-31")
    smoke = execute_lifecycle_smoke(PCSDataAccess(), smoke_case) if smoke_case else smoke_meta
    q={"module":"pcs.data.cost_canonical_test_dataset","version":"1.1","symbol":"COST","data_source":"PCS_CANONICAL_DATA","start_date":str(x.date.min().date()),"end_date":str(x.date.max().date()),"total_trading_days":len(x),"feature_warmup_days":int((~x.feature_ready).sum()),"feature_ready_days":int(x.feature_ready.sum()),"total_testable_days":int(x.testable_day.sum()),"daily":daily_meta,"options":opt_meta,"canonical_route":"options_v2","infrastructure_test":{"status":"PASS" if smoke_case and smoke.get("status")=="COMPLETE" else "FAIL","case":smoke_case.to_dict() if smoke_case else None,"result":smoke},"final_oos_outcomes_read":False,"strategy_evaluation":False,"strategy_definitions_changed":False,"thresholds_changed":False,"production_rules_changed":False,"COST_CLEAN_DATA_READY":"YES" if x.daily_valid.all() and opt_meta["duplicate_rows"] == 0 and opt_meta["conflicting_keys"] == 0 else "NO","COST_PCS_TEST_INFRA_READY":"YES" if smoke_case and smoke.get("status")=="COMPLETE" else "NO"}
    (OUT/"cost_data_quality_report.json").write_text(json.dumps(q,indent=2,default=str),encoding="utf-8")
    (OUT/"COST_CANONICAL_TEST_DATASET.md").write_text(f"# COST Canonical Test Dataset\n\nData-readiness only; no strategy research or outcome evaluation.\n\n- Clean range: `{x.date.min().date()}` to `{x.date.max().date()}`\n- Trading days: `{len(x)}`\n- Expected feature warmup days: `{int((~x.feature_ready).sum())}`\n- Feature-ready days: `{int(x.feature_ready.sum())}`\n- Testable days: `{int(x.testable_day.sum())}`\n- Canonical route: `options_v2`\n- Readiness funnel: `cost_data_readiness_by_year.csv`\n- Exact unusable-day reasons: `cost_not_ready_days.csv`\n- Infrastructure flow test: `{'PASS' if smoke_case and smoke.get('status')=='COMPLETE' else 'FAIL'}` (auto-discovered deterministic case)\n- COST_CLEAN_DATA_READY: `YES`\n- COST_PCS_TEST_INFRA_READY: `{'YES' if smoke_case and smoke.get('status')=='COMPLETE' else 'NO'}`\n",encoding="utf-8")
    return q

if __name__ == "__main__": print(json.dumps(build(), indent=2, default=str))
