"""Broad, from-scratch QQQ PCS discovery map (research-only)."""
from __future__ import annotations
from pathlib import Path
import csv, json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.price_basis import load_corporate_actions
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.indicators import calculate_base_indicators
from pcs.research.underlying_state import evaluate_as_of, UnderlyingState
from pcs.research.current_strategy_replay import build_lifecycle_quote_rows, validate_lifecycle_corporate_action, _identity
from pcs.research.stage4a_lifecycle import Stage4ALifecycleReplayAdapter, LifecycleAdapterError
from pcs.research.variant_b_replay import ReplayPolicy

VERSION = "qqq-entry-discovery-v1-broad-outcome-map-v1"

def _parquet_safe(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in out.columns:
        if out[col].map(lambda x: isinstance(x, (list, dict, tuple))).any():
            out[col] = out[col].map(lambda x: json.dumps(x, default=str) if isinstance(x, (list, dict, tuple)) else x)
    return out

def run(output_dir: str | Path = "research_outputs/qqq_entry_discovery_agent_v1", start="2010-01-01", end="2023-12-31", ticker: str = "QQQ") -> dict:
    out = Path(output_dir)
    if not out.is_absolute():
        out = Path(__file__).resolve().parents[3] / out
    out.mkdir(parents=True, exist_ok=True)
    for name in ("rounds", "cache", "artifacts"): (out / name).mkdir(exist_ok=True)
    access = PCSDataAccess(); ticker = str(ticker).upper()
    # Load prior canonical history so the PIT warmup is global rather than
    # incorrectly restarting at each chronological shard boundary.
    daily = access.read_prices(ticker, "2010-01-01", end).copy(); daily.date = pd.to_datetime(daily.date).dt.normalize()
    # Use the same Wilder ATR as the canonical trend/production path.
    daily["atr_14"] = calculate_base_indicators(daily, TrendIndicatorConfig())["atr14"]
    # One validated canonical read per chronological shard; all subsequent
    # selections are exact filters on this immutable frame.
    options_load_error = None
    try:
        options_all = access.read("options", ticker, start, end).copy()
        options_all.trade_date = pd.to_datetime(options_all.trade_date).dt.normalize()
        options_all.expiration_date = pd.to_datetime(options_all.expiration_date).dt.normalize()
        options_by_trade_date = {day: group for day, group in options_all.groupby("trade_date", sort=False)}
        options_by_expiration = {exp: group for exp, group in options_all.groupby("expiration_date", sort=False)}
    except Exception as exc:
        # Preserve a canonical data-quality failure at date level. Never
        # bypass PCSDataAccess validation or silently substitute raw files.
        options_all = pd.DataFrame(); options_by_trade_date = {}; options_by_expiration = {}
        options_load_error = str(exc)[:500]
    # Broad discovery population: only PIT warmup and valid underlying data
    # may admit a date. Existing trend/pullback/support/state gates are
    # features for later comparison, never population filters.
    # Map admission is intentionally cheaper than the later feature audit:
    # canonical OHLCV plus global warmup is sufficient here. Do not invoke
    # entry/state gates while constructing the broad opportunity population.
    states = daily[['date','close','high','low','volume']].copy()
    states['ticker'] = ticker
    states['available_data'] = True
    states['final_underlying_state'] = 'UNKNOWN_NOT_USED_FOR_ADMISSION'
    target_dates = daily.date.between(pd.Timestamp(start), pd.Timestamp(end))
    ready = states.loc[target_dates & (daily.index >= 200)].copy()
    rows=[]; lifecycle=[]; registry=load_corporate_actions()
    for s in ready.to_dict("records"):
        day=pd.Timestamp(s["date"]).normalize(); chain=options_by_trade_date.get(day, pd.DataFrame()).copy()
        rec={"trade_date":day,"ticker":ticker,"pit_feature_ready":True,"option_chain_available":(not options_load_error and bool(len(chain))),"valid_dte_available":False,"safe_strike_candidates":False,"liquidity_pass":False,"credit_pass":False,"contract_selected":False,"lifecycle_quotes_adapted":False,"lifecycle_completed":False,"reason_code":""}
        if options_load_error:
            rec["reason_code"]="CONTRACT_FAIL"; rec["data_quality_error"]=options_load_error; rows.append(rec); continue
        if chain.empty: rec["reason_code"]="NO_OPTION_DATA"; rows.append(rec); continue
        chain.trade_date=pd.to_datetime(chain.trade_date).dt.normalize(); chain.expiration_date=pd.to_datetime(chain.expiration_date).dt.normalize()
        puts=chain[chain.call_put.astype(str).str.lower().eq("p")].copy(); puts["dte"]=(puts.expiration_date-day).dt.days; puts=puts[puts.dte.between(30,45)]
        rec["valid_dte_available"]=bool(len(puts))
        if puts.empty: rec["reason_code"]="NO_DTE"; rows.append(rec); continue
        atr=float(daily.loc[daily.date.eq(day),"atr_14"].iloc[0]) if pd.notna(daily.loc[daily.date.eq(day),"atr_14"].iloc[0]) else 0
        if atr<=0: rec["reason_code"]="NO_PIT_ATR"; rows.append(rec); continue
        candidates=[]
        for exp,g in puts.groupby("expiration_date",sort=True):
            for _,short in g.sort_values("strike",ascending=False).iterrows():
                long=g[g.strike.eq(float(short.strike)-5)]
                if len(long) and (float(s["close"])-float(short.strike))/atr>=2.3: candidates.append((exp,short,long.iloc[0]))
        rec["safe_strike_candidates"]=bool(candidates)
        if not candidates: rec["reason_code"]="NO_SAFE_STRIKE"; rows.append(rec); continue
        exp,short,long=candidates[0]; credit=float(short.bid-long.ask); rec.update({"expiration":exp,"dte":int((pd.Timestamp(exp)-day).days),"short_strike":float(short.strike),"long_strike":float(long.strike),"width":5.0,"credit":credit,"liquidity_pass":float(short.bid)>0 and float(long.ask)>=0,"credit_pass":credit>0})
        if not rec["liquidity_pass"]: rec["reason_code"]="LIQUIDITY_FAIL"; rows.append(rec); continue
        if not rec["credit_pass"]: rec["reason_code"]="CREDIT_FAIL"; rows.append(rec); continue
        rec["contract_selected"]=True; cand={"candidate_id":_identity(ticker,day,exp,float(short.strike),float(long.strike)),"ticker":ticker,"date":day,"expiration":exp,"short_strike":float(short.strike),"long_strike":float(long.strike),"initial_credit":credit,"contract_mapping_available":True}
        try:
            q=options_by_expiration.get(pd.Timestamp(exp), pd.DataFrame()).copy(); q=q[q.call_put.astype(str).str.lower().eq("p") & (q.trade_date >= day) & (q.trade_date <= pd.Timestamp(exp)) & q.strike.isin([float(short.strike),float(long.strike)])]; validate_lifecycle_corporate_action(cand,registry); lifecycle.extend(build_lifecycle_quote_rows(q,cand)); rec["lifecycle_quotes_adapted"]=True; rec["reason_code"]="QUOTES_ADAPTED_LIFECYCLE_NOT_REPLAYED"
        except Exception as exc:
            # Preserve every authoritative replay failure in the date-level
            # funnel; a bad quote partition must not abort the shard.
            rec["reason_code"] = "LIFECYCLE_FAIL"
            rec["lifecycle_error"] = str(exc)[:500]
        rows.append(rec)
    outcome=pd.DataFrame(rows); _parquet_safe(outcome).to_parquet(out/"broad_pcs_outcome_map.parquet",index=False); _parquet_safe(states).to_parquet(out/"pit_feature_ready_calendar.parquet",index=False)
    options_ok = options_load_error is None
    summary={"module":"pcs.research.qqq_entry_discovery_v1","version":VERSION,"symbol":ticker,"as_of":end,"status":"COMPLETED_QUOTE_ADAPTATION_ONLY" if options_ok else "BLOCKED_CANONICAL_OPTIONS","data_source":"PCS_CANONICAL_DATA","TRAIN_TRADING_DAYS":int(target_dates.sum()),"PIT_FEATURE_READY_DAYS":len(ready),"OPTION_DATA_AVAILABLE_DAYS":int(outcome.option_chain_available.sum()),"CONTRACT_SELECTED_DAYS":int(outcome.contract_selected.sum()),"LIFECYCLE_QUOTES_ADAPTED_DAYS":int(outcome.lifecycle_quotes_adapted.sum()),"LIFECYCLE_COMPLETED_DAYS":0,"population_corrected":True,"global_warmup":True,"options_source_valid":options_ok,"options_load_error":options_load_error,"final_oos_read":False,"validation_read":False,"production_changes":False,"reason_codes":["FROM_SCRATCH_V1","NO_ENTRY_GATES","LIFECYCLE_QUOTES_ADAPTED_ONLY","NO_EXIT_OR_PNL_REPLAY"] if options_ok else ["CANONICAL_OPTIONS_READ_FAILED","NO_REUSE_ALLOWED"]}
    (out/"broad_outcome_map_summary.json").write_text(json.dumps(summary,indent=2,default=str)); (out/"agent_state.json").write_text(json.dumps({"AGENT_STATUS":"RUNNING","CURRENT_STAGE":"OUTCOME_MAP","LAST_COMPLETED_ROUND":1,"FINAL_OOS_TOUCHED":"NO","VALIDATION_TOUCHED":"NO","NEXT_PLANNED_ACTION":"Compare PIT feature distributions across outcome classes"},indent=2)); (out/"research_log.csv").write_text("round,status,artifact,next_action\n1,COMPLETED,broad_outcome_map.parquet,feature-outcome comparison\n")
    return summary
