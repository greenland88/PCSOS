"""Run the focused NVDA moneyness and ATR-distance contract surface."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.covered_call_research import (prepare_entry_signal_chains,
    prepare_selected_entry_observations, replay_prepared_entry_observations)
from pcs.research.covered_call import CoveredCallResearchConfig, select_contract

ROOT = Path(__file__).resolve().parents[1]; SYMBOL = __import__("os").environ.get("CC_SYMBOL", "NVDA").upper()
FEATURE = ROOT / f"research_outputs/safe_strike_risk_map_v0_1/trend_histories/{SYMBOL}_trend.parquet"
MARKET = ROOT / "data/derived/canonical_pit_market_states.parquet"
OUT_ROOT = ROOT / f"research_outputs/covered_call_{SYMBOL.lower()}_strike_surface"

def main():
    access = PCSDataAccess.canonical(); source = access.resolve_source("options", SYMBOL)
    daily = pd.read_parquet(FEATURE); market = pd.read_parquet(MARKET)
    start=max(pd.to_datetime(daily.date).min(),pd.Timestamp(source.first_date)); end=min(pd.to_datetime(daily.date).max(),pd.Timestamp(source.last_date))
    shard_start = pd.Timestamp(__import__("os").environ.get("CC_ENTRY_START", start.date()))
    shard_end = pd.Timestamp(__import__("os").environ.get("CC_ENTRY_END", end.date()))
    OUT = OUT_ROOT / str(shard_start.year)
    daily=daily[(pd.to_datetime(daily.date)>=start)&(pd.to_datetime(daily.date)<=end)]
    market=market[(pd.to_datetime(market.date)>=start)&(pd.to_datetime(market.date)<=end)]
    # Fixed user-requested entry condition; no hypothesis discovery here.
    feature = daily.copy(); feature.date = pd.to_datetime(feature.date)
    feature["sma20"] = feature.close.rolling(20).mean()
    feature["extension20"] = (feature.close - feature.sma20) / feature.atr14
    feature["ret3"] = feature.close.pct_change(3)
    feature["momentum_deceleration"] = feature.ret3 < feature.ret3.shift(3)
    eligible_dates = set(feature.loc[(feature.extension20 >= 1.0) & feature.momentum_deceleration, "date"].dt.normalize())
    daily = daily[pd.to_datetime(daily.date).dt.normalize().isin(eligible_dates)].copy()
    daily = daily[(pd.to_datetime(daily.date) >= shard_start) & (pd.to_datetime(daily.date) <= shard_end)].copy()
    snapshot=prepare_entry_signal_chains(SYMBOL,daily,market,data_access=access,dte=37)
    snapshot["signals"] = [x for x in snapshot["signals"] if pd.Timestamp(x["date"]).normalize() in eligible_dates]
    snapshot["funnel"]["FIXED_ENTRY_DATES"] = len(snapshot["signals"])
    # Deliberately small, interpretable safe-region surface.  These are
    # contract variants on frozen entry dates, not a threshold optimizer.
    specs=[("MONEYNESS",x) for x in (1.10,1.15,1.20,1.25,1.30)] + [("ATR",x) for x in (2.0,3.0,4.0,5.0)]
    variants=[]
    cells=[]
    for method,target in specs:
        entries=[]
        for signal in snapshot["signals"]:
            chain=snapshot["chains"].get(pd.Timestamp(signal["date"]).normalize(), [])
            kwargs={"selection_method":method,"dte":37,"target_delta":.15,"underlying_price":signal.get("close"),"atr":signal.get("atr")}
            if method == "MONEYNESS":
                kwargs["target_moneyness"] = target
            else:
                kwargs["target_atr_distance"] = target
            chosen=select_contract(chain,config=CoveredCallResearchConfig(),**kwargs)
            if chosen is not None:
                entries.append({**signal,"expiration":chosen.expiration,"strike":chosen.strike,"bid":chosen.bid,"ask":chosen.ask,"delta":chosen.delta,"dte":chosen.dte})
        variants.append((method,target,entries))
    all_entries=[]; seen=set()
    for _,_,entries in variants:
        for e in entries:
            key=(str(pd.Timestamp(e["date"]).date()),e["expiration"],float(e["strike"]))
            if key not in seen: seen.add(key); all_entries.append(e)
    prepared=prepare_selected_entry_observations(SYMBOL,all_entries,data_access=access)
    prepared_by_key={(str(pd.Timestamp(x["entry"]["date"]).date()),x["entry"]["expiration"],float(x["entry"]["strike"])):x for x in prepared}
    for method,target,entries in variants:
        subset=[prepared_by_key[(str(pd.Timestamp(e["date"]).date()),e["expiration"],float(e["strike"]))] for e in entries if (str(pd.Timestamp(e["date"]).date()),e["expiration"],float(e["strike"])) in prepared_by_key]
        replay=replay_prepared_entry_observations(SYMBOL,subset)
        cells.append({"method":method,"target":target,"funnel":{**snapshot["funnel"],"CONTRACT_AVAILABLE_DATES":len(entries)},"metrics":replay.get("metrics"),"status":replay.get("status"),"reason_codes":["CONTRACT_VARIANT_FROZEN_ENTRY_SIGNAL","PIT_CLOSE_AT_ENTRY","PIT_ATR_AT_ENTRY","NO_AUTOMATIC_PROMOTION"]})
    result={"module":"pcs.research.covered_call_strike_surface","version":"1.0","research_id":f"covered_call_{SYMBOL.lower()}_strike_surface_{shard_start.year}","symbol":SYMBOL,"status":"COMPLETED","data_source":"PCS_CANONICAL_DATA","dte":37,"entry_shard_start":str(shard_start.date()),"entry_shard_end":str(shard_end.date()),"cells":cells,"effective_research_start_date":str(start.date()),"effective_research_end_date":str(end.date()),"final_oos_read":False,"production_changes_allowed":False,"created_at":datetime.now(timezone.utc).isoformat()}
    OUT.mkdir(parents=True,exist_ok=True); target=OUT/"strike_surface.json"; target.write_text(json.dumps(result,indent=2,default=str))
    manifest={"research_id":result["research_id"],"status":"CURRENT","current":True,"data_source":"PCS_CANONICAL_DATA","ticker":SYMBOL,"final_oos_read":False,"production_changes_allowed":False,"files":{target.name:hashlib.sha256(target.read_bytes()).hexdigest()},"reason_codes":["MONEYNESS_AND_ATR_SURFACE_EXECUTED","NO_AUTOMATIC_PROMOTION"]}
    (OUT/"artifact_manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps({"cells":len(cells),"summary":[{"method":c["method"],"target":c["target"],"trades":(c["metrics"] or {}).get("trades"),"combined_pnl":(c["metrics"] or {}).get("combined_pnl"),"conflict_rate":(c["metrics"] or {}).get("hard_constraint_conflict_rate")} for c in cells]},indent=2,default=str))
if __name__=="__main__": main()
