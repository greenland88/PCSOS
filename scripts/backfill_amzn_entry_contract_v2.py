"""Read-only readiness backfill for the authoritative frozen AMZN 794 set."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import duckdb, pandas as pd
from pcs.entry.contract_v2 import ENTRY_CONTRACT_V2, later_expirations, nearby_strikes, normalize_price_confirmation
from pcs.features.expected_move import calculate_expected_move
from pcs.research.entry_confirmation import analyze_entry_confirmation
from pcs.research.stage4a_replay import audit_inputs

SOURCE=Path("data/parquet/research/variant_b_full/AMZN_full_post2020_2d.parquet")
PHASE0=Path("research_outputs/phase0_20260820/candidate_universe.parquet")
OUT=Path("research_outputs/safe_strike_stage4a/authoritative_amzn_794_entry_contract_v2.parquet")
META=Path("research_outputs/safe_strike_stage4a/authoritative_amzn_794_entry_contract_v2_readiness.json")

def identity(r):
    s="|".join([str(r.ticker),pd.Timestamp(r.date).date().isoformat(),pd.Timestamp(r.expiration).date().isoformat(),format(float(r.short_strike),'.15g'),format(float(r.long_strike),'.15g')]); return hashlib.sha256(s.encode()).hexdigest()[:24]

def main():
    src=pd.read_parquet(SOURCE).copy(); src["date"]=pd.to_datetime(src.date).dt.normalize(); src["expiration"]=pd.to_datetime(src.expiration).dt.normalize(); src["candidate_id"]=src.apply(identity,axis=1)
    p0=pd.read_parquet(PHASE0); p0=p0[p0.ticker.eq("AMZN")].copy(); p0["candidate_id"]=p0.apply(lambda r: hashlib.sha256("|".join([str(r.ticker),pd.Timestamp(r.decision_date).date().isoformat(),pd.Timestamp(r.expiration).date().isoformat(),format(float(r.short_strike),'.15g'),format(float(r.long_strike),'.15g')]).encode()).hexdigest()[:24],axis=1)
    exact=src.merge(p0[["candidate_id","decision_date"]],on="candidate_id",how="outer",indicator=True); identity_match=bool(len(src)==794 and len(p0)==794 and exact._merge.eq("both").all())
    daily=pd.read_csv("data/raw/daily_forward_adjusted/AMZN_daily_qfq.csv").rename(columns={"日期":"date","开盘价":"open","最高价":"high","最低价":"low","收盘价":"close","成交量":"volume"}); daily.date=pd.to_datetime(daily.date).dt.normalize(); confirmations={d:normalize_price_confirmation(analyze_entry_confirmation(daily,d).confirmation_score) for d in src.date.unique()}
    trend=pd.read_parquet("research_outputs/safe_strike_risk_map_v0_1/trend_histories/AMZN_trend.parquet"); trend.date=pd.to_datetime(trend.date).dt.normalize(); support=trend.drop_duplicates("date").set_index("date").support.map(lambda x: json.loads(x).get("nearest_support") if isinstance(x,str) else None)
    root=Path("data/parquet/options_v2/symbol=AMZN"); glob=str((root/"**"/"*.parquet").as_posix()); con=duckdb.connect(); chain=con.execute("SELECT trade_date, expiration_date AS expiration, strike, call_put AS option_type, bid, ask, volume, open_interest FROM read_parquet(?) WHERE trade_date BETWEEN ? AND ?",[glob,src.date.min().date(),src.date.max().date()]).fetchdf(); con.close(); chain.trade_date=pd.to_datetime(chain.trade_date).dt.normalize(); chain.expiration=pd.to_datetime(chain.expiration).dt.normalize(); chains={d:g for d,g in chain.groupby("trade_date",sort=False)}
    close=daily.set_index("date").close; out=src.copy(); out["close"]=out.date.map(close); out["dte"]=(out.expiration-out.date).dt.days; out["expected_move_1d"]=out.apply(lambda r:calculate_expected_move(float(r.close),float(r.short_strike),float(r.atr),int(r.dte)).expected_move_1d,axis=1); out["support_level"]=out.date.map(support); out["option_volume"]=out.short_volume; out["open_interest"]=out.short_oi; out["bid_ask_pct"]=(out.short_ask-out.short_bid)/((out.short_ask+out.short_bid)/2).clip(lower=1e-12); out["nearby_strikes"]=out.apply(lambda r:nearby_strikes(chains.get(r.date,chain.iloc[0:0]),r.expiration,"p",r.short_strike),axis=1); out["later_expirations"]=out.apply(lambda r:later_expirations(chains.get(r.date,chain.iloc[0:0]),r.expiration,"p"),axis=1); out["price_confirmation"]=out.date.map(confirmations); out["entry_contract_version"]=ENTRY_CONTRACT_V2; out["contract_version"]=ENTRY_CONTRACT_V2; out["producer_version"]="backfill_amzn_entry_contract_v2:v1"; out["pit_asof_date"]=out.date; out["source_provenance"]="PURCHASED_VENDOR_BATCH_1:data/parquet/options_v2/symbol=AMZN"; out["canonical_option_source"]= "data/parquet/options_v2"; out.to_parquet(OUT,index=False)
    a=audit_inputs(out); support_pop=int(out.support_level.notna().sum()); report={"ticker":"AMZN","candidate_count":len(out),"identity_match":identity_match,"expected_move_populated":int(out.expected_move_1d.notna().sum()),"support_populated":support_pop,"support_unavailable":len(out)-support_pop,"option_volume_populated":int(out.option_volume.notna().sum()),"open_interest_populated":int(out.open_interest.notna().sum()),"bid_ask_populated":int(out.bid_ask_pct.notna().sum()),"nearby_populated":int(out.nearby_strikes.notna().sum()),"later_expirations_populated":int(out.later_expirations.notna().sum()),"price_confirmation_populated":int(out.price_confirmation.notna().sum()),"pit":a.lookahead_safe,"audit_inputs":a.can_run_decision_engine,"audit_missing":list(a.missing),"legacy_309_status":"LEGACY_SAFE_STRIKE_STAGE2_RESEARCH_ONLY","canonical_source":"data/parquet/options_v2","batch2_direct_read":False,"status":"READY" if identity_match and a.can_run_decision_engine else "BLOCKED"}
    META.write_text(json.dumps(report,indent=2,default=str),encoding="utf-8"); print(json.dumps(report,indent=2))

if __name__=="__main__": main()
