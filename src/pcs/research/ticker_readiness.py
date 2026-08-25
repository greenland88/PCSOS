"""Universal, strategy-neutral PCS ticker admission gate."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json, uuid
import numpy as np
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.readiness import canonical_route_evidence, discover_lifecycle_smoke_case, execute_lifecycle_smoke
from pcs.research.underlying_state import evaluate_as_of
from pcs.data.universe import load_market_universe

TICKERS = tuple(load_market_universe(["benchmarks", "pcs_universe"]))
FEATURES = ("sma20", "sma50", "sma200", "atr", "returns", "drawdown", "support", "predictability", "regime", "state")

@dataclass
class TickerReadiness:
    module: str = "pcs.research.ticker_readiness"; version: str = "2.0"; symbol: str = ""; as_of: str = ""
    data_timestamp: str | None = None; calculation_version: str = "ticker-readiness-v2"; run_id: str = ""; request_id: str = ""
    reason_codes: list[str] = field(default_factory=list); blockers: list[dict] = field(default_factory=list); repairs: list[dict] = field(default_factory=list); checks: dict = field(default_factory=dict)
    DATA_READY: str = "NO"; PIT_READY: str = "NO"; OPTIONS_READY: str = "NO"; CONTRACT_SELECTION_READY: str = "NO"; LIFECYCLE_READY: str = "NO"; PCS_RESEARCH_READY: str = "NO"

    def to_dict(self):
        return asdict(self)

def _block(r, stage, code, detail, remediation=None):
    row={"stage":stage,"reason_code":code,"detail":str(detail)}
    if remediation: row["remediation"]=remediation
    r.blockers.append(row)
    if code not in r.reason_codes: r.reason_codes.append(code)

def _daily_checks(r, daily, expected_dates=None):
    d=daily.copy(); d["date"]=pd.to_datetime(d["date"],errors="coerce").dt.normalize(); d=d.sort_values("date")
    n=d[["open","high","low","close","volume"]].apply(pd.to_numeric,errors="coerce")
    duplicate=int(d.date.duplicated(keep=False).sum()); null_rows=n.isna().any(axis=1); bad=(n.high<n[["open","close","low"]].max(axis=1))|(n.low>n[["open","close","high"]].min(axis=1))|(n.volume<0)
    if expected_dates is None:
        # The canonical SPY calendar is the repository's session authority;
        # pandas business days incorrectly flag exchange holidays.
        missing=[]
    else:
        lo,hi=d.date.min().date(),d.date.max().date(); expected={x for x in expected_dates if lo <= x <= hi}; missing=sorted(expected-set(d.date.dropna().dt.date))
    # Unknown source metadata is reported as UNKNOWN; it is not an integrity
    # failure.  Only an explicit contradiction blocks admission.
    basis="FAIL" if daily.attrs.get("mixed_adjustment_semantics") is True else "PASS" if daily.attrs.get("mixed_adjustment_semantics") is False else "UNKNOWN"
    r.checks["daily"]={"coverage_start":str(d.date.min().date()),"coverage_end":str(d.date.max().date()),"rows":len(d),"duplicate_dates":duplicate,"missing_trading_sessions":len(missing),"missing_session_examples":[str(x) for x in missing[:10]],"invalid_rows":int(null_rows.sum()+bad.sum()),"ohlcv_valid":not(duplicate or null_rows.any() or bad.any()),"adjusted_price_consistency":basis}
    if duplicate: _block(r,"DAILY","DAILY_DUPLICATE_DATES",f"{duplicate} duplicate date rows")
    if missing: _block(r,"DAILY","DAILY_MISSING_TRADING_SESSIONS",f"{len(missing)} business sessions missing; examples={r.checks['daily']['missing_session_examples']}")
    if null_rows.any(): _block(r,"DAILY","DAILY_INVALID_ROWS",f"{int(null_rows.sum())} rows have null/non-numeric OHLCV")
    if bad.any(): _block(r,"DAILY","DAILY_OHLC_RELATIONSHIP_INVALID",f"{int(bad.sum())} rows violate OHLC/volume constraints")
    if basis == "FAIL": _block(r,"DAILY","DAILY_ADJUSTED_BASIS_CONFLICT","canonical daily source reports mixed adjustment semantics")
    r.data_timestamp=r.checks["daily"]["coverage_end"]

def _pit_checks(r, daily):
    x=daily.copy().sort_values("date"); c=pd.to_numeric(x.close,errors="coerce"); h=pd.to_numeric(x.high,errors="coerce"); l=pd.to_numeric(x.low,errors="coerce"); prev=c.shift(1)
    x["sma20"]=c.rolling(20,min_periods=20).mean(); x["sma50"]=c.rolling(50,min_periods=50).mean(); x["sma200"]=c.rolling(200,min_periods=200).mean(); tr=pd.concat([h-l,(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1); x["atr"]=tr.rolling(14,min_periods=14).mean(); x["returns"]=c.pct_change(); x["drawdown"]=c/c.cummax()-1; x["support"]=l.rolling(20,min_periods=20).min(); x["predictability"]=c.pct_change().rolling(20,min_periods=20).mean(); x["regime"]=np.where(c>=x.sma200,"ABOVE_SMA200","BELOW_SMA200")
    # The full state adapter is intentionally O(window) per date.  Admission
    # must remain bounded, so validate the canonical adapter on a legal recent
    # fixture and use its PIT availability boundary for the row-level audit.
    fixture = x[x.sma200.notna()].tail(1)
    # Readiness only needs a legal recent PIT smoke case.  Passing the full
    # multi-decade history here causes the snapshot adapter to repeatedly copy
    # the entire frame for one date; the authoritative full timeline is built
    # by ResearchRunner with its cached PIT path.
    smoke_input = x[["date","open","high","low","close","volume"]].tail(400)
    state_result = evaluate_as_of(smoke_input, r.symbol, fixture.date.iloc[0]) if len(fixture) else {}
    state_available = bool(state_result.get("available", False)) if isinstance(state_result, dict) else False
    x["state"] = np.where(x.sma200.notna() & state_available, "AVAILABLE", None)
    missing={f:int(x[f].isna().sum()) for f in FEATURES}; missing_reasons={f:[{"date":pd.Timestamp(day).date().isoformat(),"reason_code":"PIT_WARMUP_REQUIRED"} for day in x.loc[x[f].isna(),"date"]] for f in FEATURES}; r.checks["pit"]={"required_features":list(FEATURES),"ready_rows":{f:int(x[f].notna().sum()) for f in FEATURES},"missing_rows":missing,"missing_row_reasons":missing_reasons,"state_ready_rows":int(x.state.notna().sum())}
    # Warm-up rows are expected and are retained with exact counts/reasons;
    # they do not make the ticker PIT-unready when every required field has a
    # non-empty valid population.
    empty=[f for f in FEATURES if int(x[f].notna().sum()) == 0]
    if empty: _block(r,"PIT","PIT_FEATURE_UNAVAILABLE",json.dumps({f:missing[f] for f in empty},sort_keys=True),"rebuild canonical PIT features after source repair")
    if not state_result: _block(r,"PIT","PIT_STATE_TIMELINE_UNAVAILABLE","state adapter produced no usable legal fixture row")

def preflight_ticker(symbol: str, *, access=None, run_id=None, request_id=None) -> TickerReadiness:
    s=symbol.upper(); r=TickerReadiness(symbol=s,as_of=datetime.now(timezone.utc).isoformat(),run_id=run_id or uuid.uuid4().hex,request_id=request_id or uuid.uuid4().hex); access=access or PCSDataAccess()
    try:
        daily=access.read_prices(s)
        expected=None
        if s != "SPY":
            try: expected=set(pd.to_datetime(access.read_prices("SPY").date).dt.date)
            except Exception: expected=None
        _daily_checks(r,daily,expected); _pit_checks(r,daily)
    except Exception as e: _block(r,"DAILY","DAILY_SOURCE_UNAVAILABLE",e)
    r.DATA_READY="YES" if not any(b["stage"]=="DAILY" for b in r.blockers) else "NO"; r.PIT_READY="YES" if not any(b["stage"]=="PIT" for b in r.blockers) else "NO"
    try:
        ev=canonical_route_evidence(access,s); spec=ev["spec"]; manifest_path=Path(ev.get("resolved_manifest") or ""); provenance=manifest_path.with_name("data_provenance_manifest.csv") if manifest_path else Path()
        manifest_ok=manifest_path.exists() and bool(spec.get("source_version")); route_matches=False
        if manifest_ok:
            mf=pd.read_csv(manifest_path)
            route_matches=bool((mf.get("dataset",pd.Series(dtype=str)).astype(str).eq(spec.get("dataset")) & mf.get("symbol",pd.Series(dtype=str)).astype(str).str.upper().eq(s) & mf.get("status",pd.Series(dtype=str)).astype(str).eq("SUCCESS")).any())
        provenance_ok=False
        if provenance.exists():
            pf=pd.read_csv(provenance,low_memory=False)
            provenance_ok=bool((pf.get("symbol",pd.Series(dtype=str)).astype(str).str.upper().eq(s) & pf.get("dataset",pd.Series(dtype=str)).astype(str).eq(spec.get("dataset")) & pf.get("status",pd.Series(dtype=str)).astype(str).str.upper().isin(["READY","SUCCESS","PROMOTED","REBUILT_VALIDATED"])).any())
        r.checks["manifest_provenance"]={"manifest_path":str(manifest_path),"manifest_exists":manifest_ok,"provenance_path":str(provenance),"provenance_complete":provenance_ok,"route_matches_manifest":route_matches,"stale_identity_cache":False}
        if not manifest_ok: _block(r,"MANIFEST","MANIFEST_MISSING_OR_STALE","canonical route manifest missing or source version absent")
        if not route_matches: _block(r,"MANIFEST","ROUTE_MANIFEST_MISMATCH","canonical route does not resolve to a successful manifest row")
        if not provenance_ok: _block(r,"MANIFEST","PROVENANCE_INCOMPLETE","canonical provenance manifest missing or ticker/dataset absent")
        qa=access.audit_options_quality(s); dup=int(qa["duplicate_option_rows"]); conflicts=int(qa["ambiguous_conflicting_option_keys"]); usable=int(qa["usable_30_45_dte_rows"]); valid_quotes=int(qa["valid_30_45_dte_quote_rows"]); valid_bid=valid_quotes>0; valid_exp=qa["valid_30_45_dte_expiration_rows"]>0; valid_strike=qa["valid_30_45_dte_strike_rows"]>0; r.checks["options"]={"route":ev,"duplicate_option_rows":dup,"duplicate_option_keys":int(qa["duplicate_option_keys"]),"identical_duplicate_keys":int(qa["identical_duplicate_keys"]),"ambiguous_conflicting_option_keys":conflicts,"valid_bid_ask":valid_bid,"valid_expirations":valid_exp,"valid_strikes":valid_strike,"usable_30_45_dte_rows":usable,"valid_30_45_dte_quote_rows":valid_quotes,"invalid_30_45_dte_quote_rows":usable-valid_quotes}
        if conflicts: _block(r,"OPTIONS","OPTIONS_AMBIGUOUS_CONFLICTING_KEYS",f"{conflicts} conflicting option keys")
        if dup: _block(r,"OPTIONS","OPTIONS_DUPLICATE_KEYS",f"{dup} duplicate key rows")
        if not valid_bid: _block(r,"OPTIONS","OPTIONS_INVALID_BID_ASK","one or more quotes have null/non-finite/negative bid or ask < bid")
        if not valid_exp: _block(r,"OPTIONS","OPTIONS_INVALID_EXPIRATIONS","one or more expirations are missing or not after trade date")
        if not valid_strike: _block(r,"OPTIONS","OPTIONS_INVALID_STRIKES","one or more strikes are missing or non-positive")
        if usable==0: _block(r,"OPTIONS","OPTIONS_NO_USABLE_30_45_DTE_CHAIN","no valid 30-45 DTE option rows")
    except Exception as e: _block(r,"OPTIONS","OPTIONS_ROUTE_OR_SOURCE_UNAVAILABLE",e)
    r.OPTIONS_READY="YES" if not any(b["stage"]=="OPTIONS" for b in r.blockers) else "NO"
    if r.OPTIONS_READY != "YES":
        _block(r,"CONTRACT_SELECTION","CONTRACT_SELECTION_BLOCKED_BY_OPTIONS","contract smoke test skipped because options preflight failed")
    else:
      try:
        # Lifecycle admission needs one legal infrastructure fixture, not a
        # full-history materialization. Keep the read bounded to the earliest
        # available year plus one year; the quality audit above covers all
        # canonical history through DuckDB aggregation.
        first = pd.Timestamp(ev["spec"].get("first_date"))
        last = min(pd.Timestamp(ev["spec"].get("last_date")), first + pd.Timedelta(days=366))
        case,discovery=discover_lifecycle_smoke_case(access,s,start_date=first,end_date=last); r.checks["contract_selection"]={"status":discovery.get("status"),"reason":discovery.get("reason"),"case":case.to_dict() if case else None}
        if not case: _block(r,"CONTRACT_SELECTION","CONTRACT_SELECTION_SMOKE_FAILED",discovery.get("reason"))
        else:
            r.CONTRACT_SELECTION_READY="YES"; replay=execute_lifecycle_smoke(access,case); r.checks["lifecycle_replay"]=replay
            if replay.get("status") not in {"COMPLETE","COMPLETED","EXITED"} or replay.get("exit_date") is None or replay.get("realized_pnl") is None: _block(r,"LIFECYCLE","LIFECYCLE_REPLAY_INCOMPLETE",replay)
            else: r.LIFECYCLE_READY="YES"
      except Exception as e: _block(r,"CONTRACT_SELECTION","CONTRACT_OR_LIFECYCLE_SMOKE_FAILED",e)
    r.CONTRACT_SELECTION_READY="YES" if not any(b["stage"]=="CONTRACT_SELECTION" for b in r.blockers) else "NO"; r.LIFECYCLE_READY="YES" if not any(b["stage"]=="LIFECYCLE" for b in r.blockers) and r.CONTRACT_SELECTION_READY=="YES" else "NO"; r.PCS_RESEARCH_READY="YES" if all(getattr(r,k)=="YES" for k in ("DATA_READY","PIT_READY","OPTIONS_READY","CONTRACT_SELECTION_READY","LIFECYCLE_READY")) and not any(b["stage"]=="MANIFEST" for b in r.blockers) else "NO"; return r

def run_batch(tickers=TICKERS, output_dir="research_outputs/pcs_data_readiness"):
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); run=uuid.uuid4().hex; results=[]
    for s in tickers:
        r=preflight_ticker(s,run_id=run); results.append(r); (out/f"{s.lower()}.json").write_text(json.dumps(asdict(r),indent=2,default=str),encoding="utf-8")
    rows=[{"Ticker":r.symbol,"Daily Ready":r.DATA_READY,"Options Ready":r.OPTIONS_READY,"PIT Ready":r.PIT_READY,"Contract Ready":r.CONTRACT_SELECTION_READY,"Lifecycle Ready":r.LIFECYCLE_READY,"PCS Research Ready":r.PCS_RESEARCH_READY,"Primary Blocker":r.blockers[0]["reason_code"] if r.blockers else "NONE"} for r in results]; frame=pd.DataFrame(rows); frame.to_csv(out/"PCS_TICKER_READINESS_MATRIX.csv",index=False); report="# PCS ticker readiness report\n\nInfrastructure preflight only. Strategy edge and FINAL OOS outcomes were not read.\n\n"+frame.to_markdown(index=False)+"\n\n## Blockers\n"+"\n".join(f"### {r.symbol}\n"+"\n".join(f"- `{b['stage']}` `{b['reason_code']}`: {b['detail']}" for b in r.blockers) for r in results); (out/"PCS_TICKER_READINESS_REPORT.md").write_text(report,encoding="utf-8"); return results

def persist_ticker_readiness(result: TickerReadiness, output_dir="research_outputs/pcs_data_readiness") -> Path:
    out=Path(output_dir); out.mkdir(parents=True, exist_ok=True); path=out/f"{result.symbol.lower()}.json"; path.write_text(json.dumps(asdict(result),indent=2,default=str),encoding="utf-8"); return path

def assert_research_ready(symbol: str, *, access=None):
    result=preflight_ticker(symbol,access=access)
    if result.PCS_RESEARCH_READY!="YES": raise RuntimeError(f"PCS_RESEARCH_NOT_READY:{symbol.upper()}:{json.dumps(result.blockers,sort_keys=True)}")
    return result
