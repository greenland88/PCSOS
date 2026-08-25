"""Isolated, resumable QQQ H006 NEW_ENTRY replay.

This module is research-only.  It starts from QQQ's complete canonical daily
calendar, derives the frozen H006 signal, selects contracts through
PCSDataAccess, and replays lifecycles through the approved adapter.  Every
date receives a row, including failures.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json, os
import hashlib
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions, PriceBasis
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.indicators import calculate_base_indicators
from pcs.research.current_strategy_replay import build_lifecycle_quote_rows, validate_lifecycle_corporate_action, _identity
from pcs.research.stage4a_lifecycle import Stage4ALifecycleReplayAdapter, LifecycleAdapterError
from pcs.research.variant_b_replay import ReplayPolicy

YEARS = (2020, 2021, 2022, 2023)
MODULE = "pcs.research.qqq_h006_new_entry_sharded"

def _shard_identity(year: int) -> dict:
    access = PCSDataAccess()
    daily = access.resolve_source("daily", "QQQ", "2010-01-01", f"{year}-12-31")
    options = access.resolve_source("options", "QQQ", f"{year}-01-01", f"{year}-12-31")
    repo = Path(__file__).resolve().parents[3]
    code = Path(__file__).resolve()
    dependencies = {
        "shard_runner": code,
        "current_strategy_replay": repo / "src/pcs/research/current_strategy_replay.py",
        "lifecycle_adapter": repo / "src/pcs/research/stage4a_lifecycle.py",
        "price_basis": repo / "src/pcs/data/price_basis.py",
    }
    payload = {"module": MODULE, "version": "v1", "year": int(year),
               "daily_source_version": daily.source_version,
               "options_source_version": options.source_version,
               "implementation_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                         for name, path in dependencies.items()},
               "corporate_actions_sha256": hashlib.sha256(
                   (repo / "config/data/corporate_actions.csv").read_bytes()).hexdigest()
               if (repo / "config/data/corporate_actions.csv").is_file() else "MISSING"}
    payload["identity_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return payload

def _atr14(d):
    # Match the canonical production trend ATR (Wilder smoothing).  A plain
    # rolling TR mean changes the same 2.3-ATR safe-strike rule.
    return calculate_base_indicators(d, TrendIndicatorConfig())["atr14"]

def _features(d):
    d=d.sort_values("date").copy(); d["atr14"]=_atr14(d)
    d["ret5"]=d.close.pct_change(5); d["ret10"]=d.close.pct_change(10)
    d["drawdown60"]=d.close/d.close.rolling(60,min_periods=60).max()-1
    return d

def _one_year(year: int, root: Path) -> dict:
    target=root/f"shard_{year}.parquet"; summary=target.with_suffix(".json")
    if target.exists() and summary.exists():
        try:
            prior = json.loads(summary.read_text())
            if prior.get("shard_identity") == _shard_identity(year) and prior.get("status") == "COMPLETED":
                return prior
        except Exception:
            pass
    access=PCSDataAccess(); daily=access.read_prices("QQQ",f"{year}-01-01",f"{year}-12-31").copy()
    daily.date=pd.to_datetime(daily.date).dt.normalize(); all_daily=access.read_prices("QQQ","2010-01-01",f"{year}-12-31").copy(); all_daily.date=pd.to_datetime(all_daily.date).dt.normalize()
    f=_features(all_daily); f=f[f.date.dt.year.eq(year)].copy(); rows=[]; lifecycle_rows=[]; registry=load_corporate_actions()
    # First confirmation per contiguous controlled-reset episode.
    candidate_dates=[]; in_episode=False; confirmed=False
    for r in f.itertuples():
        ready=pd.notna(r.drawdown60) and pd.notna(r.ret10) and pd.notna(r.ret5)
        controlled=bool(ready and r.drawdown60<=-.02 and r.ret10>0)
        signal=bool(controlled and r.ret5>0)
        if not controlled: in_episode=False; confirmed=False
        elif not in_episode: in_episode=True; confirmed=False
        if signal and not confirmed:
            candidate_dates.append(pd.Timestamp(r.date)); confirmed=True
    signal_set=set(candidate_dates)
    for r in f.itertuples():
        day=pd.Timestamp(r.date); rec={"trade_date":day,"ticker":"QQQ","pit_feature_ready":bool(pd.notna(r.atr14)),"signal_date":day in signal_set,"option_chain_available":False,"contract_selected":False,"lifecycle_completed":False,"reason_code":"NOT_SIGNAL"}
        if day not in signal_set: rows.append(rec); continue
        if not rec["pit_feature_ready"]: rec["reason_code"]="NO_PIT_FEATURE"; rows.append(rec); continue
        try: chain=access.read_option_chain("QQQ",day)
        except Exception as exc: rec["reason_code"]="OPTION_READ_FAIL"; rec["error"]=type(exc).__name__; rows.append(rec); continue
        rec["option_chain_available"]=bool(len(chain))
        if chain.empty: rec["reason_code"]="NO_OPTION_DATA"; rows.append(rec); continue
        chain.expiration_date=pd.to_datetime(chain.expiration_date); puts=chain[chain.call_put.astype(str).str.lower().eq("p")].copy(); puts["dte"]=(puts.expiration_date-day).dt.days; puts=puts[puts.dte.between(30,45)]
        if puts.empty: rec["reason_code"]="NO_DTE"; rows.append(rec); continue
        candidates=[]; raw_width=5.0*registry.adjustment_factor("QQQ",day,PriceBasis.MARKET_RAW,PriceBasis.ANALYTIC_ADJUSTED)
        for exp,g in puts.groupby("expiration_date",sort=True):
            for _,short in g.sort_values("strike",ascending=False).iterrows():
                long=g[g.strike.eq(float(short.strike)-raw_width)]
                if long.empty or (float(r.close)-registry.to_comparison_strike("QQQ",day,float(short.strike)))/float(r.atr14)<2.3: continue
                long=long.iloc[0]; credit=float(short.bid-long.ask)
                if credit>0: candidates.append((pd.Timestamp(exp),float(short.strike),float(long.strike),credit,short,long))
        if not candidates: rec["reason_code"]="NO_SAFE_STRIKE"; rows.append(rec); continue
        exp,ss,ls,credit,short,long=candidates[0]; rec.update({"expiration":exp,"dte":int((exp-day).days),"short_strike":ss,"long_strike":ls,"width":5.0,"credit":credit,"contract_selected":True,"reason_code":"CONTRACT_SELECTED"})
        if float(short.bid)<=0 or float(long.ask)<0: rec["reason_code"]="LIQUIDITY_FAIL"; rows.append(rec); continue
        cand={"candidate_id":_identity("QQQ",day,exp,ss,ls),"ticker":"QQQ","date":day,"expiration":exp,"short_strike":ss,"long_strike":ls,"initial_credit":credit,"contract_mapping_available":True}
        try:
            q=access.read_quotes("QQQ",day,exp,expirations=[exp],strikes=[ss,ls]); validate_lifecycle_corporate_action(cand,registry); lifecycle_rows.extend(build_lifecycle_quote_rows(q,cand)); rec["reason_code"]="READY_FOR_LIFECYCLE"
        except Exception as exc: rec["reason_code"]="LIFECYCLE_FAIL"; rec["lifecycle_error"]=type(exc).__name__
        rows.append(rec)
    out=pd.DataFrame(rows)
    if lifecycle_rows:
        adapter=Stage4ALifecycleReplayAdapter(pd.DataFrame(lifecycle_rows),ReplayPolicy()); results=[]
        for rec in out[out.reason_code.eq("READY_FOR_LIFECYCLE")].to_dict("records"):
            try:
                life=adapter({"candidate_id":_identity("QQQ",rec["trade_date"],rec["expiration"],rec["short_strike"],rec["long_strike"]),"ticker":"QQQ","date":rec["trade_date"],"expiration":rec["expiration"],"short_strike":rec["short_strike"],"long_strike":rec["long_strike"],"initial_credit":rec["credit"]})
                results.append({**rec,**life,"lifecycle_completed":str(life.get("status"))=="COMPLETE","reason_code":"LIFECYCLE_COMPLETED" if str(life.get("status"))=="COMPLETE" else "LIFECYCLE_FAIL"})
            except Exception as exc: rec["reason_code"]="LIFECYCLE_FAIL"; rec["lifecycle_error"]=type(exc).__name__; results.append(rec)
        if results:
            rr=pd.DataFrame(results); out=out[~out.trade_date.isin(rr.trade_date)].copy(); out=pd.concat([out,rr],ignore_index=True)
    out=out.sort_values("trade_date"); tmp=target.with_suffix(".tmp.parquet"); out.to_parquet(tmp,index=False); os.replace(tmp,target)
    option_failures = int(out.reason_code.eq("OPTION_READ_FAIL").sum())
    lifecycle_failures = int(out.reason_code.eq("LIFECYCLE_FAIL").sum())
    shard_status = "COMPLETED" if option_failures == 0 and lifecycle_failures == 0 else "BLOCKED_INCOMPLETE_CANONICAL_REPLAY"
    counts={"year":year,"rows":len(out),"signal_dates":int(out.signal_date.sum()),"option_data_available":int(out.option_chain_available.sum()),"contract_selected":int(out.contract_selected.sum()),"lifecycle_completed":int(out.lifecycle_completed.sum()),"option_read_failures":option_failures,"lifecycle_failures":lifecycle_failures,"status":shard_status,"shard_identity":_shard_identity(year)}
    for code in ["NO_OPTION_DATA","NO_DTE","NO_SAFE_STRIKE","LIQUIDITY_FAIL","CREDIT_FAIL","CONTRACT_FAIL","LIFECYCLE_FAIL"]: counts[code]=int(out.reason_code.eq(code).sum())
    summary.write_text(json.dumps(counts,indent=2,default=str),encoding="utf-8"); return counts

def run(output_dir="research_outputs/qqq_entry_discovery_agent_v1_h006_timing_train/shards", workers=4):
    root=Path(output_dir); root.mkdir(parents=True,exist_ok=True); workers=min(8,max(1,int(workers)))
    with ThreadPoolExecutor(max_workers=workers) as pool: parts=list(pool.map(lambda y:_one_year(y,root),YEARS))
    if any(part.get("status") != "COMPLETED" for part in parts):
        return {"module":MODULE,"version":"v1","status":"BLOCKED_INCOMPLETE_CANONICAL_REPLAY","data_source":"PCS_CANONICAL_DATA","workers":workers,"shards":parts,"rows":0,"final_oos_read":False,"validation_read":False,"production_changes":False}
    frames=[pd.read_parquet(root/f"shard_{y}.parquet") for y in YEARS]; merged=pd.concat(frames,ignore_index=True).sort_values(["trade_date","ticker"]); merged.to_parquet(root/"merged.parquet",index=False)
    return {"module":MODULE,"version":"v1","status":"COMPLETED","data_source":"PCS_CANONICAL_DATA","workers":workers,"shards":parts,"rows":len(merged),"final_oos_read":False,"validation_read":False,"production_changes":False}
