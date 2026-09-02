"""Normal-user EOD PCS workflow."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from uuid import uuid4
import pandas as pd
from pydantic import BaseModel, Field
from pcs.data.access import PCSDataAccess
from pcs.engine.decision_engine import DecisionEngine, load_rules
from pcs.models.decision import Action
from pcs.models.trade import TradeCandidate
from pcs.research.stage4a_production_universe import generate_structural_put_opportunities
from pcs.research.ticker_readiness import preflight_ticker
from pcs.recovery import SystemHealthController
from pcs.market_context import build_market_context
from pcs.entry.contract_v2 import nearby_strikes, later_expirations
from pcs.features.expected_move import calculate_expected_move
from pcs.data.live_market_state import require_live_market_state
from pcs.data.covered_call_readiness import EARNINGS_NOT_APPLICABLE, EVENT_RISK_SYMBOL

class PCSStatusResult(BaseModel):
    module: str = "pcs_status"; version: str = "1.1"
    symbol: str; as_of: str; status: str; action: str | None = None
    system_status: str; strategy_status: str
    strategy_evaluated: bool = False
    contract_selection_evaluated: bool = False
    decision_engine_executed: bool = False
    auto_recovered: bool = False
    reason_codes: list[str] = Field(default_factory=list); data_timestamp: str|None = None
    calculation_version: str = "decision-engine-v1"; run_id: str; request_id: str
    effective_market_date: str|None = None; decision: dict[str,Any]|None = None
    readiness: dict[str,Any]|None = None; funnel: dict[str,int] = Field(default_factory=dict)
    readiness_underlying_generation_id: str|None = None
    readiness_options_generation_id: str|None = None
    runner_underlying_generation_id: str|None = None
    runner_options_generation_id: str|None = None

def _blocked(symbol, as_of, code, detail=None, readiness=None, run_id=None, request_id=None, effective=None):
    return PCSStatusResult(symbol=symbol, as_of=as_of, status="BLOCKED",
        system_status="BLOCKED", strategy_status="NOT_RUN", reason_codes=[code],
        run_id=run_id or uuid4().hex, request_id=request_id or uuid4().hex,
        readiness=readiness, decision=detail, effective_market_date=effective)

def _event_calendar(path: str | Path | None = None) -> pd.DataFrame:
    """Load the existing source-backed PIT calendar; never fabricate events."""
    target = Path(path or "research_outputs/scheduled_events_v1/scheduled_event_calendar_v1.csv")
    if not target.exists():
        raise FileNotFoundError("EVENT_CALENDAR_UNAVAILABLE")
    frame = pd.read_csv(target)
    required = {"symbol", "event_date", "event_type", "event_date_known_at_entry"}
    if not required <= set(frame):
        raise ValueError("EVENT_CALENDAR_PIT_METADATA_MISSING")
    frame["event_date"] = pd.to_datetime(frame.event_date, errors="raise").dt.normalize()
    return frame

def _event_source_blocker(symbol: str, as_of: str, calendar: pd.DataFrame) -> str | None:
    if symbol in EARNINGS_NOT_APPLICABLE:
        return None
    event_symbol = EVENT_RISK_SYMBOL.get(symbol, symbol)
    earnings = calendar[
        calendar.event_type.astype(str).str.upper().eq("EARNINGS") &
        calendar.symbol.astype(str).str.upper().eq(event_symbol)
    ]
    future = earnings[pd.to_datetime(earnings.event_date).dt.normalize().ge(pd.Timestamp(as_of).normalize())]
    # An authorized, readable PIT calendar with no future event for this
    # instrument is positive evidence of NO_KNOWN_EVENT.  It is not a source
    # outage.  Only missing/invalid calendar metadata or an explicit provider
    # failure may block the strategy.
    if future.empty:
        return None
    known = future.event_date_known_at_entry.astype(str).str.upper()
    if not known.isin({"YES", "TRUE", "1"}).all():
        return "EVENT_CALENDAR_PIT_METADATA_UNVERIFIED"
    return None

def _hard_eligible_rows(rows, context, rules):
    """Apply only DecisionEngine hard rules as a lossless batch accelerator."""
    if not rows:
        return []
    frame = pd.DataFrame(rows).copy()
    day = pd.to_datetime(frame.date).dt.normalize()
    dte = (pd.to_datetime(frame.expiration).dt.normalize() - day).dt.days
    width = pd.to_numeric(frame.short_strike) - pd.to_numeric(frame.long_strike)
    credit = pd.to_numeric(frame.short_bid) - pd.to_numeric(frame.long_ask)
    spread = ((pd.to_numeric(frame.short_ask) - pd.to_numeric(frame.short_bid)) /
              pd.to_numeric(frame.short_bid).clip(lower=1e-9))
    entry, liquidity = rules["entry"], rules["liquidity"]
    mask = (
        dte.between(int(entry["hard_dte_min"]), int(entry["hard_dte_max"]))
        & pd.to_numeric(frame.short_bid).gt(0)
        & pd.to_numeric(frame.short_ask).ge(pd.to_numeric(frame.short_bid))
        & pd.to_numeric(frame.short_volume).ge(int(liquidity["min_option_volume"]))
        & pd.to_numeric(frame.short_oi).ge(int(liquidity["min_open_interest"]))
        & spread.le(float(liquidity["max_bid_ask_pct"]))
        & (credit / width).ge(float(entry["min_credit_width_ratio"]))
    )
    if context.atr14 is None or context.atr14 <= 0:
        mask &= False
    else:
        mask &= ((float(context.underlying_price) - pd.to_numeric(frame.short_strike)) /
                 float(context.atr14)).ge(float(entry["safe_strike_atr"]))
    return frame.loc[mask].to_dict("records")

def _decision_reason_codes(decision) -> list[str]:
    if decision.reason_codes:
        return list(decision.reason_codes)
    mapping = {
        "no strike provides enough 3-5 day buffer": "STRIKE_BUFFER_SCORE_ZERO",
        "opportunity score below open threshold": "OPPORTUNITY_SCORE_BELOW_OPEN_THRESHOLD",
        "liquidity/rollability below hard threshold": "LIQUIDITY_SCORE_BELOW_THRESHOLD",
        "portfolio capacity exceeded": "PORTFOLIO_CAPACITY_EXCEEDED",
        "RED market blocks new PCS": "REGIME_RED",
        "sizing rules allow no new contracts": "POSITION_SIZING_CAPACITY_ZERO",
    }
    if decision.action == Action.OPEN:
        return ["ENTRY_GATES_PASSED"]
    return [mapping.get(decision.reason, "STRATEGY_WAIT")]

def _candidate(row, context, canonical_chain):
    day=pd.Timestamp(row["date"]).normalize()
    expiry=pd.Timestamp(row["expiration"]).normalize(); dte=int((expiry-day).days)
    expected=calculate_expected_move(float(context.underlying_price),float(row["short_strike"]),atr=context.atr14,dte=dte)
    mid=max((float(row["short_ask"])+float(row["short_bid"]))/2,1e-9)
    return TradeCandidate(ticker=str(row["ticker"]).upper(), expiration=expiry.date().isoformat(), short_strike=float(row["short_strike"]), long_strike=float(row["long_strike"]), underlying_price=context.underlying_price, credit=float(row["short_bid"]-row["long_ask"]), dte=dte, short_delta=float(row["short_delta"]), expected_move=float(expected.expected_move_1d), expected_move_1d=float(expected.expected_move_1d), support_level=float(context.support or 0), option_volume=int(row["short_volume"]), open_interest=int(row["short_oi"]), bid_ask_pct=float((row["short_ask"]-row["short_bid"])/mid), nearby_strikes=nearby_strikes(canonical_chain,expiry,"p",float(row["short_strike"])), later_expirations=later_expirations(canonical_chain,expiry,"p"), business_quality=80, trend_score=float(context.trend_score or 0), support_score=0, sector_alignment=80, price_confirmation=0, event_risk=context.event_risk, atr=float(context.atr14 or 0), long_option_volume=int(row["long_volume"]), long_open_interest=int(row["long_oi"]), bid=float(row["short_bid"]), ask=float(row["short_ask"]), long_bid=float(row["long_bid"]), long_ask=float(row["long_ask"]), entry_date=day.date().isoformat(), trend_snapshot=context.snapshot, trend_interpretation=context.interpretation, trend_score_result=context.score_result)

def _candidate_report(row, context, *, qualified=False):
    short=float(row["short_strike"]); long=float(row["long_strike"])
    credit=float(row["short_bid"])-float(row["long_ask"]); width=short-long
    max_loss=max(width-credit, 0.0)
    reasons=[]
    if short >= float(context.underlying_price): reasons.append("SHORT_STRIKE_ABOVE_SPOT")
    if context.atr14 and (float(context.underlying_price)-short)/float(context.atr14) < 2.3: reasons.append("ATR_DISTANCE_BELOW_MINIMUM")
    if abs(float(row["short_delta"])) >= .45: reasons.append("HIGH_DELTA")
    if short > float(context.underlying_price): reasons.append("INTRINSIC_VALUE_PRESENT")
    if not qualified and not reasons: reasons.append("HARD_GATE_FAILED")
    return {"qualification":"QUALIFIED" if qualified else "REJECTED_NEAR_MISS", "failure_reasons":reasons,
            "expiration":str(pd.Timestamp(row["expiration"]).date()), "short_strike":short, "long_strike":long,
            "short_bid":float(row["short_bid"]), "short_ask":float(row["short_ask"]),
            "long_bid":float(row["long_bid"]), "long_ask":float(row["long_ask"]),
            "net_credit":credit, "max_loss":max_loss, "breakeven":short-credit,
            "return_on_risk":credit/max_loss if max_loss else 0.0, "delta":float(row["short_delta"]),
            "open_interest":int(row["short_oi"]), "volume":int(row["short_volume"]),
            "bid_ask_spread":float(row["short_ask"])-float(row["short_bid"]),
            "support":context.support, "distance_to_support":(short-float(context.support)) if context.support is not None else None,
            "distance_to_support_atr":((short-float(context.support))/float(context.atr14)) if context.support is not None and context.atr14 else None,
            "risk_level": ("LOW" if qualified and short < float(context.underlying_price) and abs(float(row["short_delta"])) < .35 and (not context.atr14 or ((float(context.underlying_price)-short)/float(context.atr14) >= 2.3)) and credit/max_loss >= .25 else "MEDIUM" if qualified else "REJECTED")}

def _read_verified_dataset(access, handle):
    """Read every partition through the shared validated pinned reader."""
    return access.read_verified_dataset(handle)

def evaluate_pcs_status(symbol: str, as_of: str, *, mode="eod", portfolio_context=None,
                        data_access=None, rules=None, event_calendar=None,
                        event_calendar_path=None, full_research_readiness=False,
                        auto_recover=False):
    symbol=str(symbol).strip().upper(); run_id,request_id=uuid4().hex,uuid4().hex; access=data_access or PCSDataAccess()
    if mode not in {"eod", "live"}:
        return _blocked(symbol,as_of,"MODE_NOT_SUPPORTED",{"mode":mode},run_id=run_id,request_id=request_id)
    if not as_of:
        return _blocked(symbol, as_of or "", "DECISION_AS_OF_REQUIRED", run_id=run_id, request_id=request_id)
    from pcs.data.strategy_readiness import StrategyDataRequirements, ensure_strategy_ready
    readiness_mode = "LIVE" if mode == "live" else "HISTORICAL"
    gate = ensure_strategy_ready(symbol, "PUT_CREDIT_SPREAD", as_of, readiness_mode, StrategyDataRequirements(option_right="PUT", target_dte_min=30, target_dte_max=45), data_access=access)
    if gate.data_status != "READY":
        return _blocked(symbol, as_of, "DATA_BLOCKED", {"data_reason":gate.data_reason,"coverage":gate.to_dict()}, run_id=run_id, request_id=request_id)
    handle = gate.verified_data_handle
    if handle is None:
        return _blocked(symbol, as_of, "DATA_BLOCKED", {"data_reason":"VERIFIED_DATA_HANDLE_MISSING"}, run_id=run_id, request_id=request_id)
    try:
        daily = _read_verified_dataset(access, handle.underlying_handle)
        quotes = _read_verified_dataset(access, handle.options_handle)
    except Exception as exc:
        return _blocked(symbol, as_of, "DATA_BLOCKED", {"data_reason":"PINNED_READ_FAILED", "detail":str(exc)}, run_id=run_id, request_id=request_id)
    readiness_ids = {"underlying": handle.underlying_generation_id, "options": handle.options_generation_id}
    # Readiness already validated the exact target window and returned the
    # pinned rows above.  Do not perform a second ticker-based live read here;
    # that could select a different snapshot than the one admitted by the
    # readiness gate.
    if daily.empty or quotes.empty:
        return _blocked(symbol, as_of, "DATA_BLOCKED",
                        {"data_reason": "PINNED_DATA_EMPTY"},
                        run_id=run_id, request_id=request_id)
    effective=None
    try:
        initial=daily.copy()
        if "date" not in initial and "trade_date" in initial: initial=initial.rename(columns={"trade_date":"date"})
        initial=initial[pd.to_datetime(initial["date"],errors="coerce") <= pd.Timestamp(as_of).normalize()]
        if not initial.empty: effective=pd.to_datetime(initial.date).dt.normalize().max().date().isoformat()
        recovered=False
        if initial.empty:
            health=SystemHealthController(access).ensure_capability("EOD_PCS_DECISION",symbol,as_of)
            if health.status != "READY": return _blocked(symbol,as_of,(list(health.reason_codes) or ["CAPABILITY_NOT_READY"])[0],{"system_health":health.to_dict()},run_id=run_id,request_id=request_id,effective=effective)
            recovered=bool(health.repairs_succeeded)
            initial=daily.copy()
            if initial.empty: return _blocked(symbol,as_of,"DAILY_CANONICAL_UNAVAILABLE",run_id=run_id,request_id=request_id)
            effective=pd.to_datetime(initial.date).dt.normalize().max().date().isoformat()
        # A decision is a current-session request.  Historical coverage alone
        # is insufficient: force the control plane to assess freshness and,
        # when needed, synchronize the current options snapshot before the
        # selector is allowed to inspect the chain.
        if effective:
            quotes=quotes[pd.to_datetime(quotes.get("trade_date"),errors="coerce").dt.normalize() == pd.Timestamp(effective)]
        if quotes.empty:
            if not auto_recover:
                return _blocked(symbol,as_of,"OPTION_CHAIN_REFRESH_REQUIRED",run_id=run_id,request_id=request_id,effective=effective)
            # Daily discovery needs the exact completed-session chain, not a
            # full historical research import. The same control plane and
            # canonical promotion boundary remain authoritative.
            return _blocked(symbol,as_of,"DATA_BLOCKED",{"data_reason":"PINNED_OPTION_DATA_EMPTY"},run_id=run_id,request_id=request_id,effective=effective)
        if full_research_readiness:
            readiness=preflight_ticker(symbol,access=access,end_date=effective,run_id=run_id,request_id=request_id); rd=readiness.to_dict()
            # Historical research admission is informational for a current
            # decision.  LIVE readiness was already established above; a
            # research blocker must not suppress a locally valid chain.
        else:
            rd={"decision_readiness":"READY","daily_rows":int(len(initial)),"option_rows":int(len(quotes)),"effective_market_date":effective}
        daily=initial.copy()
        # Use the same deterministic duplicate-date repair as readiness so a
        # small historical duplicate set cannot poison the state adapter.
        daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.normalize()
        if daily["date"].duplicated().any():
            daily["_complete"] = daily[["open","high","low","close","volume"]].notna().sum(axis=1)
            daily["_priority"] = pd.to_numeric(daily.get("source_priority", pd.Series(index=daily.index)), errors="coerce").fillna(-1)
            daily["_updated"] = pd.to_datetime(daily.get("updated_at", pd.Series(index=daily.index)), errors="coerce")
            daily = (daily.sort_values(["date","_priority","_updated","_complete"], ascending=[True,False,False,False])
                     .drop_duplicates("date", keep="first")
                     .drop(columns=["_complete","_priority","_updated"], errors="ignore"))
        calendar = _event_calendar(event_calendar_path) if event_calendar is None else event_calendar.copy()
        event_blocker = _event_source_blocker(symbol,effective,calendar)
        if event_blocker:
            if not auto_recover:
                return _blocked(symbol,as_of,"EVENT_CALENDAR_REFRESH_REQUIRED",readiness=rd,run_id=run_id,request_id=request_id,effective=effective)
            prepared=access.ensure_ready("events",symbol,effective,effective)
            if prepared.status != "DATASET_READY":
                specific=next((code for code in prepared.reason_codes if str(code).startswith("EVENT_")),None)
                return _blocked(symbol,as_of,specific or event_blocker,{"dataset_readiness":prepared.__dict__},readiness=rd,run_id=run_id,request_id=request_id,effective=effective)
            canonical_events=access.read("events",symbol,effective,None).copy()
            canonical_events["event_date_known_at_entry"]="YES"
            calendar=canonical_events
        # Target-window gate precedes structural construction.  A complete
        # historical chain may contain near-expiry or far-away strikes, but
        # those rows must never seed a PCS decision.
        spot = float(pd.to_numeric(initial.close, errors="coerce").dropna().iloc[-1])
        q0=quotes.copy(); q0["expiration_date"]=pd.to_datetime(q0["expiration_date"], errors="coerce").dt.normalize(); q0["strike"]=pd.to_numeric(q0["strike"], errors="coerce")
        q0=q0[q0.call_put.astype(str).str.lower().isin({"p","put"})]
        q0["dte"]=(q0["expiration_date"]-pd.Timestamp(effective)).dt.days
        target=q0[q0.dte.between(30,45) & q0.strike.between(max(0.0,spot-32.73),spot+2.27)]
        if target.empty:
            return _blocked(symbol,as_of,"DATA_BLOCKED",{"reason":"TARGET_CHAIN_MISSING","spot":spot,"required_dte":[30,45],"required_strike_range":[max(0.0,spot-32.73),spot+2.27]},readiness=rd,run_id=run_id,request_id=request_id,effective=effective)
        quotes=quotes[quotes.index.isin(target.index)]
        chain=quotes.rename(columns={"trade_date":"Trade Date","expiration_date":"Expiry Date","call_put":"Call/Put","strike":"Strike","bid":"Bid Price","ask":"Ask Price","open_interest":"Open Interest","volume":"Volume","delta":"Delta"})
        rows=generate_structural_put_opportunities(chain,symbol,effective); funnel={"chains_loaded":int(bool(len(chain))),"candidates_generated":len(rows),"hard_eligible_candidates":0,"engine_evaluated":0,"engine_open":0}
        if not rows: return PCSStatusResult(symbol=symbol,as_of=as_of,status="PASS",action=Action.WAIT.value,system_status="READY",strategy_status="EXECUTED",strategy_evaluated=True,contract_selection_evaluated=True,auto_recovered=recovered,reason_codes=["NO_EXACT_PUT_SPREAD_CANDIDATE"],data_timestamp=effective,run_id=run_id,request_id=request_id,readiness=rd,funnel=funnel,effective_market_date=effective,readiness_underlying_generation_id=readiness_ids["underlying"],readiness_options_generation_id=readiness_ids["options"],runner_underlying_generation_id=readiness_ids["underlying"],runner_options_generation_id=readiness_ids["options"])
        active_rules=rules or load_rules(); context=build_market_context(symbol,effective,data_access=access,rules=active_rules,daily_frame=daily,mode="FORMAL"); engine=DecisionEngine(active_rules); portfolio=portfolio_context or {"planned_risk":0,"theoretical_max_loss":0,"bucket_risk":{}}
        # Trend/timing is authoritative and precedes option ranking.  A valid
        # chain must not turn a structural downtrend or an unconfirmed
        # reclaim into an OPEN result.
        from pcs.entry.trend_gate import evaluate_trend_gate
        from pcs.entry.pullback_gate import evaluate_pullback_gate
        trend_gate = evaluate_trend_gate(context.score_result, context.interpretation, context.snapshot)
        pullback_gate = evaluate_pullback_gate(trend_gate, context.snapshot, context.interpretation)
        if trend_gate.trend_gate_result != "PASS" or pullback_gate.pullback_gate_result != "PASS":
            timing_action = "WAIT" if trend_gate.trend_gate_result in {"WATCH", "REJECT"} else "DATA_BLOCKED"
            return PCSStatusResult(symbol=symbol, as_of=as_of, status="PASS", action=timing_action,
                system_status="READY", strategy_status="EXECUTED", strategy_evaluated=True,
                contract_selection_evaluated=False, decision_engine_executed=False,
                auto_recovered=recovered, reason_codes=list(dict.fromkeys([*trend_gate.reasons, *pullback_gate.reasons])),
                data_timestamp=context.data_timestamp, run_id=run_id, request_id=request_id, readiness=rd,
                funnel={**funnel, "trend_gate": 0, "timing_gate": 0, "options_ranked": 0}, effective_market_date=effective,
                readiness_underlying_generation_id=readiness_ids["underlying"], readiness_options_generation_id=readiness_ids["options"],
                runner_underlying_generation_id=readiness_ids["underlying"], runner_options_generation_id=readiness_ids["options"],
                decision={"market_context": context.model_dump(mode="json", exclude={"market_state","snapshot","interpretation","score_result"}),
                          "trend_gate": trend_gate.__dict__, "pullback_gate": pullback_gate.__dict__})
        selected=_hard_eligible_rows(rows,context,active_rules)
        # If every spread fails a vectorized hard rule, execute the engine on
        # one deterministic structural row to preserve auditable semantics.
        evaluation_rows=selected or [sorted(rows,key=lambda x:(pd.Timestamp(x["expiration"]),float(x["short_strike"]),float(x["long_strike"])))[0]]
        funnel["hard_eligible_candidates"]=len(selected)
        decisions=[engine.evaluate_candidate(_candidate(row,context,quotes),context.market_state,portfolio,event_calendar=calendar) for row in evaluation_rows]
        selected_keys={(str(r["expiration"]),float(r["short_strike"]),float(r["long_strike"])) for r in selected}
        candidate_reports=[_candidate_report(row, context, qualified=(str(row["expiration"]),float(row["short_strike"]),float(row["long_strike"])) in selected_keys) for row in rows]
        funnel["engine_evaluated"]=len(decisions)
        opens=[d for d in decisions if d.action==Action.OPEN]; funnel["engine_open"]=len(opens); chosen=max(opens or decisions,key=lambda d:d.total_score)
        qualified_reports=[x for x in candidate_reports if x["qualification"]=="QUALIFIED"]
        ranked=sorted(qualified_reports,key=lambda x:(x["risk_level"]!="LOW",-x["return_on_risk"]))
        final_reasons = _decision_reason_codes(chosen) if selected else ["NO_QUALIFIED_SAFE_CANDIDATE"]
        final_action = chosen.action.value if selected and chosen.action != Action.NO_TRADE else Action.WAIT.value
        return PCSStatusResult(symbol=symbol,as_of=as_of,status="PASS",action=final_action,system_status="READY",strategy_status="EXECUTED",strategy_evaluated=True,contract_selection_evaluated=True,decision_engine_executed=True,auto_recovered=recovered,reason_codes=final_reasons,data_timestamp=context.data_timestamp,run_id=run_id,request_id=request_id,readiness=rd,funnel=funnel,effective_market_date=effective,readiness_underlying_generation_id=readiness_ids["underlying"],readiness_options_generation_id=readiness_ids["options"],runner_underlying_generation_id=readiness_ids["underlying"],runner_options_generation_id=readiness_ids["options"],decision={"market_context":context.model_dump(mode="json",exclude={"market_state","snapshot","interpretation","score_result"}),"engine":chosen.model_dump(mode="json"),"candidate_count":len(candidate_reports),"qualified_candidate_count":len(qualified_reports),"candidates":candidate_reports,"safest_candidate":ranked[0] if ranked else None,"best_return_on_risk":max(qualified_reports,key=lambda x:x["return_on_risk"]) if qualified_reports else None,"best_overall":ranked[0] if ranked else None})
    except Exception as exc: return _blocked(symbol,as_of,"PCS_STATUS_EXECUTION_FAILED",{"detail":str(exc)},run_id=run_id,request_id=request_id,effective=effective)
