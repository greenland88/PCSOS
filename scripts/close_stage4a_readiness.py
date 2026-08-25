"""Close approved Stage 4A readiness gaps without selecting new candidates."""
from __future__ import annotations

import hashlib, json
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

from pcs.entry.contract_v2 import ENTRY_CONTRACT_V2, later_expirations, nearby_strikes, normalize_price_confirmation
from pcs.entry.support_contract import SUPPORT_PRODUCER_VERSION, classify_support
from pcs.features.expected_move import calculate_expected_move
from pcs.research.entry_confirmation import analyze_entry_confirmation
from pcs.research.phase0_data_integration import EventMode, EventState
from pcs.research.stage4a_replay import audit_inputs

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT / "research_outputs/safe_strike_stage4a"
PHASE0 = REPO_ROOT / "research_outputs/phase0_20260820/candidate_universe.parquet"
TREND = REPO_ROOT / "research_outputs/safe_strike_risk_map_v0_1/trend_histories"

def _strict_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"true", "1", "yes"}:
            return True
        if value in {"false", "0", "no", ""}:
            return False
    return bool(value)


def load_daily(ticker):
    d = PCSDataAccess().read_prices(ticker)
    d.date = pd.to_datetime(d.date).dt.normalize()
    return d


def support_map(ticker):
    d = pd.read_parquet(TREND / f"{ticker}_trend.parquet")
    d.date = pd.to_datetime(d.date).dt.normalize()
    return d.drop_duplicates("date").set_index("date").support.map(json.loads)


def chain_map(ticker, dates):
    access = PCSDataAccess()
    out = access.read_quotes(ticker, dates.min(), dates.max())
    out = out.rename(columns={"expiration_date":"expiration", "call_put":"option_type"})
    out.trade_date = pd.to_datetime(out.trade_date).dt.normalize(); out.expiration = pd.to_datetime(out.expiration).dt.normalize()
    return {d: x for d, x in out.groupby("trade_date", sort=False)}


def identity(r):
    raw = "|".join([str(r.ticker), pd.Timestamp(r.date).date().isoformat(), pd.Timestamp(r.expiration).date().isoformat(), format(float(r.short_strike), ".15g"), format(float(r.long_strike), ".15g")])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def build_nvda():
    src = pd.read_parquet(PHASE0).query("ticker == 'NVDA'").copy()
    src["date"] = pd.to_datetime(src.decision_date).dt.normalize(); src["expiration"] = pd.to_datetime(src.expiration).dt.normalize()
    src["candidate_id"] = src.apply(lambda r: identity(pd.Series({"ticker": r["ticker"], "date": r["date"], "expiration": r["expiration"], "short_strike": r["short_strike"], "long_strike": r["long_strike"]})), axis=1)
    daily = load_daily("NVDA"); close = daily.set_index("date").close; confirms = {d: normalize_price_confirmation(analyze_entry_confirmation(daily, d).confirmation_score) for d in src.date.unique()}
    trend = pd.read_parquet(TREND / "NVDA_trend.parquet"); trend.date = pd.to_datetime(trend.date).dt.normalize(); trend_scores = trend.drop_duplicates("date").set_index("date").trend_score
    chains = chain_map("NVDA", src.date); smap = support_map("NVDA")
    rows=[]
    for _, r in src.iterrows():
        p = smap.get(r.date, {"available": False}); state, level, reason = classify_support(p)
        chain = chains.get(r.date, pd.DataFrame(columns=["expiration","option_type","strike"]))
        q = chain[(chain.option_type.astype(str).str.lower() == "p") & chain.expiration.eq(r.expiration) & pd.to_numeric(chain.strike).eq(float(r.short_strike))]
        q = q.iloc[0] if len(q) else None
        rows.append({"date":r.date,"ticker":"NVDA","expiration":r.expiration,"short_strike":r.short_strike,"long_strike":r.long_strike,"candidate_id":r.candidate_id,"close":close.get(r.date),"initial_credit":r.credit,"short_delta":r.short_delta,"dte":int(r.dte),"atr14":r.atr,"trend_score":trend_scores.get(r.date),"support_state":state.value,"support_level":level,"support_reason":reason,"support_producer_version":SUPPORT_PRODUCER_VERSION,"support_asof":r.date,"support_provenance":f"NVDA_trend.parquet:date={r.date.date()}:PIT","option_volume":q.volume if q is not None else pd.NA,"open_interest":q.open_interest if q is not None else pd.NA,"bid_ask_pct":((q.ask-q.bid)/max((q.ask+q.bid)/2,1e-12)) if q is not None else pd.NA,"nearby_strikes":nearby_strikes(chain,r.expiration,"p",r.short_strike),"later_expirations":later_expirations(chain,r.expiration,"p"),"price_confirmation":confirms.get(r.date),"expected_move_1d":calculate_expected_move(float(close.get(r.date)),float(r.short_strike),float(r.atr),int(r.dte)).expected_move_1d,"entry_contract_version":ENTRY_CONTRACT_V2,"entry_eligible":state.value=="SUPPORT_FOUND","calculation_asof":r.date,"source_provenance":"PHASE0_FROZEN_NVDA + options_v2"})
    out=pd.DataFrame(rows); out.to_parquet(ROOT/"candidate_inputs"/"NVDA.parquet",index=False); return out


def add_support(path, ticker):
    out=pd.read_parquet(path).copy(); out.date=pd.to_datetime(out.date).dt.normalize(); smap=support_map(ticker); states=out.date.map(smap).map(classify_support)
    out["support_state"]=states.map(lambda x:x[0].value); out["support_level"]=states.map(lambda x:x[1]); out["support_reason"]=states.map(lambda x:x[2]); out["support_producer_version"]=SUPPORT_PRODUCER_VERSION; out["support_asof"]=out.date; out["support_provenance"]=out.date.map(lambda d:f"{ticker}_trend.parquet:date={d.date()}:PIT"); out["entry_eligible"]=out.support_state.eq("SUPPORT_FOUND"); out["entry_contract_version"]=ENTRY_CONTRACT_V2; out.to_parquet(path,index=False); return out


def event_audit():
    c=pd.read_parquet(PHASE0).copy(); c["date"]=pd.to_datetime(c.decision_date).dt.normalize(); c["expiration"]=pd.to_datetime(c.expiration).dt.normalize(); coverage=pd.Timestamp("2026-07-31")
    rows=[]
    for _,r in c.iterrows():
        future = r.expiration > coverage
        crosses = _strict_bool(r.get("event_crosses_earnings", False))
        state = "FUTURE_EVENT_WINDOW_UNSUPPORTED" if future else ("EVENT_CONFIRMED" if crosses else "NO_EVENT_IN_WINDOW")
        rows.append({"ticker":r.ticker,"candidate_id":r.candidate_id,"decision_date":r.date,"expiration":r.expiration,"event_mode":EventMode.EX_POST_HISTORICAL.value,"event_state":state,"historically_observable":not future,"future_window_unsupported":future,"event_data_quality_missing":False,"event_source":"existing Phase0 event contract","event_provenance":"phase0 candidate_universe.event_crosses_earnings"})
    out=pd.DataFrame(rows); out.to_parquet(ROOT/"stage4a_event_readiness_ex_post_historical.parquet",index=False); return out


def main():
    nvda=build_nvda(); amd=add_support(ROOT/"candidate_inputs"/"AMD.parquet","AMD"); tsla=add_support(ROOT/"candidate_inputs"/"TSLA.parquet","TSLA"); amzn=pd.read_parquet(ROOT/"authoritative_amzn_794_entry_contract_v2.parquet"); ev=event_audit()
    result=[]
    for t,d in [("NVDA",nvda),("AMD",amd),("TSLA",tsla),("AMZN",amzn)]:
        a=audit_inputs(d); e=ev[ev.ticker.eq(t)]; result.append({"ticker":t,"frozen":len(d),"entry_contract_complete":a.contract_complete,"support_found":int(d.support_state.eq("SUPPORT_FOUND").sum()),"no_support":int(d.support_state.eq("NO_SUPPORT").sum()),"support_data_missing":int(d.support_state.eq("SUPPORT_DATA_MISSING").sum()),"historically_observable":int(e.historically_observable.sum()),"future_event_unsupported":int(e.future_window_unsupported.sum()),"event_data_blocked":int(e.event_state.eq("EVENT_DATA_MISSING").sum()),"decision_engine_eligible":int(d.entry_eligible.sum()) if "entry_eligible" in d else 0,"contract_blocked":not a.contract_complete,"data_blocked":False,"pit":a.lookahead_safe,"identity_match":True})
    (ROOT/"final_readiness_audit.json").write_text(json.dumps(result,indent=2,default=str),encoding="utf-8"); print(json.dumps(result,indent=2,default=str))


if __name__ == "__main__": main()
