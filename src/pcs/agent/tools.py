"""Read-only, structured access layer. It never reads raw source files directly."""
from pathlib import Path
import json
import pandas as pd
from .models import response
from pcs.data.access import PCSDataAccess, DataAccessError
from pcs.data.derived_store import read_derived, read_backtest_trades

_REPO_ROOT = Path(__file__).resolve().parents[3]

def get_daily_history(symbol, start_date, end_date):
    try:
        df=PCSDataAccess().read_prices(symbol, start_date, end_date)
    except (OSError, ValueError, DataAccessError) as exc:
        return response("UNAVAILABLE", "CANONICAL_ROUTE_UNAVAILABLE", [], symbol,
                        as_of=end_date, reason_codes=["CANONICAL_ROUTE_UNAVAILABLE", type(exc).__name__])
    return response("AVAILABLE" if not df.empty else "UNAVAILABLE", "DATA_AVAILABLE" if not df.empty else "DATA_NOT_FOUND", df.to_dict("records"), symbol, as_of=end_date)

def get_option_chain(symbol, trade_date, expiration_date=None):
    try:
        df=PCSDataAccess().read_option_chain(symbol, trade_date)
    except (OSError, ValueError, DataAccessError) as exc:
        return response("UNAVAILABLE", "CANONICAL_ROUTE_UNAVAILABLE", [], symbol,
                        as_of=trade_date, reason_codes=["CANONICAL_ROUTE_UNAVAILABLE", type(exc).__name__])
    if expiration_date is not None and not df.empty: df=df[pd.to_datetime(df.expiration_date).dt.date==pd.Timestamp(expiration_date).date()]
    return response("AVAILABLE" if not df.empty else "UNAVAILABLE", "DATA_AVAILABLE" if not df.empty else "DATA_NOT_FOUND", df.to_dict("records"), symbol, as_of=trade_date)

def get_option_quotes(symbol, trade_date, expiration_date, strikes):
    result=get_option_chain(symbol,trade_date,expiration_date)
    if result.data: result.data=[r for r in result.data if float(r.get("strike")) in {float(s) for s in strikes}]
    if not result.data: result.status="UNAVAILABLE"; result.reason_code="DATA_NOT_FOUND"
    return result

def get_research_run(run_id):
    json_path=_REPO_ROOT / "data/manifests/research_runs" / f"{run_id}.json"
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return response("UNAVAILABLE", "MANIFEST_CORRUPT", [], run_id=str(run_id),
                            reason_codes=["MANIFEST_CORRUPT", type(exc).__name__])
        return response("AVAILABLE", "DATA_AVAILABLE", [payload], run_id=str(run_id))
    path=_REPO_ROOT / "data/manifests/research_runs.csv"
    if not path.exists(): return response("UNAVAILABLE","DATA_NOT_FOUND", run_id=str(run_id))
    try:
        # Never hide a malformed manifest row: a partial result is not an
        # auditable research run and could silently omit the requested run.
        df=pd.read_csv(path, engine="python")
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return response("UNAVAILABLE", "MANIFEST_CORRUPT", [], run_id=str(run_id), reason_codes=["MANIFEST_CORRUPT", type(exc).__name__])
    rows=df[df.run_id.astype(str)==str(run_id)].to_dict("records")
    return response("AVAILABLE" if rows else "UNAVAILABLE", "DATA_AVAILABLE" if rows else "DATA_NOT_FOUND", rows, run_id=str(run_id))

def get_backtest_trades(run_id):
    try:
        df=read_backtest_trades(run_id)
    except (OSError, ValueError, DataAccessError) as exc:
        return response("UNAVAILABLE", "ARTIFACT_INVALID", [], run_id=str(run_id),
                        reason_codes=["ARTIFACT_INVALID", type(exc).__name__])
    return response("AVAILABLE" if not df.empty else "UNAVAILABLE", "DATA_AVAILABLE" if not df.empty else "DATA_NOT_FOUND", df.to_dict("records"), run_id=str(run_id))

def get_trend_snapshot(symbol, as_of, benchmark=None):
    filters={"symbol":symbol,"date":pd.Timestamp(as_of)}
    if benchmark is not None:
        filters["benchmark_symbol"] = benchmark
    try:
        df=read_derived("trend_history",filters=filters)
    except (OSError, ValueError, DataAccessError) as exc:
        return response("UNAVAILABLE", "ARTIFACT_INVALID", [], symbol, as_of=as_of,
                        reason_codes=["ARTIFACT_INVALID", type(exc).__name__])
    return response("AVAILABLE" if not df.empty else "UNAVAILABLE", "DATA_AVAILABLE" if not df.empty else "CALCULATION_UNAVAILABLE", df.to_dict("records"), symbol, as_of=as_of)

def get_data_compatibility(symbol, as_of):
    # Agent compatibility must reflect the active canonical route, not a
    # hand-maintained ticker/date table that can drift from onboarding.
    ticker = str(symbol).upper()
    try:
        access = PCSDataAccess()
        source = access.resolve_source("daily", ticker)
        date = pd.Timestamp(as_of).normalize()
        start, end = pd.Timestamp(source.first_date), pd.Timestamp(source.last_date)
        available = start <= date <= end
        c = {"symbol": ticker, "as_of": str(date.date()), "data_available": available,
             "pcs_research_compatible": available, "compatibility_status": "COMPATIBLE" if available else "DATA_NOT_FOUND",
             "reliable_start": str(start.date()), "reliable_end": str(end.date()),
             "reason_code": "DATA_AVAILABLE" if available else "DATA_NOT_FOUND",
             "canonical_dataset": source.dataset, "canonical_source_version": source.source_version,
             "canonical_route": "AVAILABLE"}
        status = "AVAILABLE" if c["data_available"] else "UNAVAILABLE"
    except Exception as exc:
        c = {"symbol": ticker, "as_of": str(pd.Timestamp(as_of).date()),
             "data_available": False, "pcs_research_compatible": False,
             "compatibility_status": "DATA_NOT_FOUND", "reason_code": "CANONICAL_ROUTE_UNAVAILABLE",
             "error_type": type(exc).__name__}
        status = "UNAVAILABLE"
    return response(status, c["reason_code"], c, ticker, as_of=as_of)
