"""Unified, strategy-neutral ticker cold-start/readiness boundary."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date
from typing import Any
import pandas as pd

from .access import PCSDataAccess
from .control_plane import ensure_market_data
from pcs.research.ticker_readiness import preflight_ticker

@dataclass(frozen=True)
class TickerReadyResult:
    ticker: str
    strategy_type: str
    as_of_date: str
    status: str
    ANALYSIS_READY: str
    LIVE_DECISION_READY: str
    RESEARCH_READY: str
    PRODUCTION_AUTOMATION_READY: str
    reason_codes: tuple[str, ...] = ()
    data_timestamp: str | None = None
    details: dict[str, Any] | None = None
    def to_dict(self): return asdict(self)

def _dedupe_daily(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or not frame.duplicated("date").any(): return frame
    x=frame.copy(); x["_complete"]=x[[c for c in ("open","high","low","close","volume") if c in x]].notna().sum(axis=1)
    x["_priority"]=pd.to_numeric(x.get("source_priority", pd.Series(index=x.index)), errors="coerce").fillna(-1)
    x["_updated"]=pd.to_datetime(x.get("updated_at", pd.Series(index=x.index)), errors="coerce")
    return x.sort_values(["date","_priority","_updated","_complete"], ascending=[True,False,False,False]).drop_duplicates("date").drop(columns=["_complete","_priority","_updated"],errors="ignore")

def ensure_ticker_ready(ticker: str, strategy_type: str, as_of_date: str, *, data_access=None) -> TickerReadyResult:
    s=str(ticker).strip().upper(); access=data_access or PCSDataAccess(); asof=pd.Timestamp(as_of_date).normalize()
    # Minimal warmup is strategy-driven: analysis needs indicators, live needs
    # the current session chain; full history is only a research dependency.
    min_history=200 if strategy_type.upper() in {"PCS","CSP","PUT"} else 50
    req={"required_start": str((asof-pd.Timedelta(days=400)).date()), "required_end": str(asof.date()),
         "decision_as_of": str(asof.date()), "datasets": ("daily","options"),
         "required_history_rows": min_history, "consumer":"TICKER_READINESS"}
    recovery=None
    try: recovery=ensure_market_data(s, req, access=access)
    except Exception as exc: recovery={"status":"BLOCKED","reason_codes":["DATA_SOURCE_DISCOVERY_FAILED"],"detail":str(exc)}
    try:
        daily=_dedupe_daily(access.read_prices(s, end_date=asof))
        quotes=access.read_quotes(s, daily.date.max().date().isoformat(), daily.date.max().date().isoformat()) if not daily.empty else pd.DataFrame()
    except Exception as exc:
        return TickerReadyResult(s,strategy_type,str(asof.date()),"BLOCKED","NO","NO","NO","NO",("MINIMAL_DATA_WINDOW_UNAVAILABLE",),details={"recovery":recovery,"detail":str(exc)})
    live=not daily.empty and not quotes.empty
    analysis=len(daily)>=min_history
    research=preflight_ticker(s, access=access, end_date=str(asof.date()))
    reasons=tuple(research.reason_codes)
    return TickerReadyResult(s,strategy_type,str(asof.date()),"READY" if live else "BLOCKED", "YES" if analysis else "NO", "YES" if live else "NO", "YES" if research.PCS_RESEARCH_READY=="YES" else "NO", "YES" if research.PCS_RESEARCH_READY=="YES" and live else "NO", reasons, str(pd.to_datetime(daily.date).max().date()) if not daily.empty else None, {"recovery":recovery,"daily_rows":len(daily),"option_rows":len(quotes),"research":research.to_dict()})

