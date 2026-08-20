"""Read-only, structured access layer. It never reads raw source files directly."""
from pathlib import Path
import json
import pandas as pd
from .models import response
from pcs.data.duckdb_store import connect, refresh_views, query_daily, query_option_chain
from pcs.data.derived_store import read_derived, read_backtest_trades
from pcs.research.compatibility import compatibility

def _con(symbols=None):
    con=connect(":memory:"); refresh_views(con, symbols=symbols); return con

def get_daily_history(symbol, start_date, end_date):
    con=_con([symbol]); df=query_daily(con,symbol,start_date,end_date); con.close()
    return response("AVAILABLE" if not df.empty else "UNAVAILABLE", "DATA_AVAILABLE" if not df.empty else "DATA_NOT_FOUND", df.to_dict("records"), symbol)

def get_option_chain(symbol, trade_date, expiration_date=None):
    con=_con([symbol]); df=query_option_chain(con,symbol,trade_date); con.close()
    if expiration_date is not None and not df.empty: df=df[pd.to_datetime(df.expiration_date).dt.date==pd.Timestamp(expiration_date).date()]
    return response("AVAILABLE" if not df.empty else "UNAVAILABLE", "DATA_AVAILABLE" if not df.empty else "DATA_NOT_FOUND", df.to_dict("records"), symbol)

def get_option_quotes(symbol, trade_date, expiration_date, strikes):
    result=get_option_chain(symbol,trade_date,expiration_date)
    if result.data: result.data=[r for r in result.data if float(r.get("strike")) in {float(s) for s in strikes}]
    if not result.data: result.status="UNAVAILABLE"; result.reason_code="DATA_NOT_FOUND"
    return result

def get_research_run(run_id):
    json_path=Path("data/manifests/research_runs")/f"{run_id}.json"
    if json_path.exists(): return response("AVAILABLE","DATA_AVAILABLE",[json.loads(json_path.read_text(encoding="utf-8"))])
    path=Path("data/manifests/research_runs.csv")
    if not path.exists(): return response("UNAVAILABLE","DATA_NOT_FOUND")
    df=pd.read_csv(path,engine="python",on_bad_lines="skip"); rows=df[df.run_id.astype(str)==str(run_id)].to_dict("records")
    return response("AVAILABLE" if rows else "UNAVAILABLE", "DATA_AVAILABLE" if rows else "DATA_NOT_FOUND", rows)

def get_backtest_trades(run_id):
    df=read_backtest_trades(run_id)
    return response("AVAILABLE" if not df.empty else "UNAVAILABLE", "DATA_AVAILABLE" if not df.empty else "DATA_NOT_FOUND", df.to_dict("records"))

def get_trend_snapshot(symbol, as_of, benchmark=None):
    df=read_derived("trend_history",filters={"symbol":symbol,"date":pd.Timestamp(as_of)})
    return response("AVAILABLE" if not df.empty else "UNAVAILABLE", "DATA_AVAILABLE" if not df.empty else "CALCULATION_UNAVAILABLE", df.to_dict("records"), symbol)

def get_data_compatibility(symbol, as_of):
    c=compatibility(symbol,as_of)
    return response("AVAILABLE" if c["data_available"] else "UNAVAILABLE",c["reason_code"],c,symbol)
