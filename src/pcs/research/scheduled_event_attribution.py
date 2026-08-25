"""Research-only scheduled-event attribution over persisted PASS trades."""
from pathlib import Path
import numpy as np, pandas as pd
from .path_risk_validation import OUT
from .scheduled_event_calendar import load_calendar, EVENT_TYPE_ALIASES
from pcs.data.access import PCSDataAccess

ROOT=Path(__file__).resolve().parents[3]
CAL=ROOT/"research_outputs"/"scheduled_events_v1"/"scheduled_event_calendar_v1.csv"
SHOCK=OUT/"event_risk_shock_calendar.csv"

def _trades():
    d=pd.read_csv(OUT/"safe_buffer_calibration_paths.csv",parse_dates=["date","expiration"])
    d["stop"]=d.exit_reason.eq("STOP"); d["profit50"]=d.exit_reason.eq("PROFIT50"); d["profit70"]=np.nan
    return d

def _tag(d,cal, sessions_by_symbol):
    d=d.copy(); cal=cal.copy(); cal.event_type=cal.event_type.replace(EVENT_TYPE_ALIASES); cal.event_date=pd.to_datetime(cal.event_date)
    for et,key in [("EARNINGS","ER"),("FOMC","FOMC"),("CPI","CPI"),("NFP_EMPLOYMENT","NFP")]:
        vals=[]; known=[]
        for _,r in d.iterrows():
            ev=cal[(cal.event_type==et)&(cal.event_date>=r.date)]
            if et=="EARNINGS": ev=ev[ev.symbol.eq(r.symbol)]
            dates=pd.DatetimeIndex(ev.event_date)
            vals.append(dates); known.append(ev)
        def inside_horizon(events, row, horizon):
            sessions = sessions_by_symbol.get(str(row.symbol).upper())
            if sessions is None:
                return False
            entry = pd.Timestamp(row.date).normalize()
            positions = sessions.searchsorted(entry)
            if positions >= len(sessions) or sessions[positions] != entry:
                return False
            cutoff_index = min(positions + horizon - 1, len(sessions) - 1)
            return bool(events.size and events[0] <= sessions[cutoff_index])
        for h in (3,5,10): d[f"{key}_inside_{h}d"]=[inside_horizon(x,r,h) for x,r in zip(vals,d.itertuples())]
        d[f"{key}_inside_DTE"]=[bool(x.size and x[0] <= r.expiration) for x,r in zip(vals,d.itertuples())]
        d[f"days_to_next_{key}"]=[(x[0]-r.date).days if x.size else np.nan for x,r in zip(vals,d.itertuples())]
    for h in (3,5,10):
        cols=[f"{k}_inside_{h}d" for k in ("ER","FOMC","CPI","NFP")]; d[f"scheduled_event_count_{h}d"]=d[cols].sum(axis=1); d[f"ANY_SCHEDULED_EVENT_inside_{h}d"]=d[f"scheduled_event_count_{h}d"].gt(0)
    cols=[f"{k}_inside_DTE" for k in ("ER","FOMC","CPI","NFP")]; d["scheduled_event_count_DTE"]=d[cols].sum(axis=1); d["ANY_SCHEDULED_EVENT_inside_DTE"]=d["scheduled_event_count_DTE"].gt(0)
    shocks=pd.read_csv(SHOCK,parse_dates=["event_date"]).event_date if SHOCK.exists() else pd.Series(dtype="datetime64[ns]")
    def shock_inside(row):
        sessions = sessions_by_symbol.get(str(row.symbol).upper())
        if sessions is None:
            return False
        entry = pd.Timestamp(row.date).normalize(); pos = sessions.searchsorted(entry)
        if pos >= len(sessions) or sessions[pos] != entry:
            return False
        cutoff = sessions[min(pos + 9, len(sessions) - 1)]
        return bool(((shocks >= entry) & (shocks <= cutoff)).any())
    d["realized_shock_inside_10d"]=[shock_inside(r) for r in d.itertuples()]
    return d

def _stats(g):
    p=pd.to_numeric(g.realized_pnl,errors="coerce"); neg=-p[p<0].sum(); pos=p[p>0].sum()
    mae5=(g.close-g.min_low_5d)/g.atr14; mae10=(g.close-g.min_low_10d)/g.atr14
    return {"N":len(g),"STOP":g.stop.mean(),"Profit50":g.profit50.mean(),"Profit70":np.nan,"PF":pos/neg if neg else np.inf,"Avg_PnL":p.mean(),"Median_PnL":p.median(),"5d_MAE_ATR":mae5.median(),"10d_MAE_ATR":mae10.median(),"5d_2ATR_breach":g["breach_2atr_5d"].mean(),"10d_2ATR_breach":g["breach_2atr_10d"].mean()}

def _groups(d, col):
    rows=[]
    for k,g in d.groupby(col,dropna=False): rows.append({"group":k,**_stats(g)})
    return pd.DataFrame(rows)

def run(output_dir=OUT):
    output_dir=Path(output_dir)
    # Keep attribution on the same validated, source-versioned calendar as
    # replay.  A raw read here used to bypass the calendar contract and could
    # silently accept an unversioned or malformed event file.
    calendar = load_calendar(CAL)
    trades = _trades(); access = PCSDataAccess()
    sessions_by_symbol = {symbol: pd.DatetimeIndex(pd.to_datetime(access.read_prices(symbol)["date"]).dt.normalize().drop_duplicates().sort_values()) for symbol in trades.symbol.dropna().astype(str).str.upper().unique()}
    d=_tag(trades, calendar, sessions_by_symbol)
    r1=d[d.risk_state.eq("R1_NORMAL")]
    tables={"tagged_trades":d}
    rows=[{"group":"R1 ALL",**_stats(r1)}]
    for label,col in [("NO EVENT 3d","ANY_SCHEDULED_EVENT_inside_3d"),("NO EVENT 5d","ANY_SCHEDULED_EVENT_inside_5d"),("NO EVENT 10d","ANY_SCHEDULED_EVENT_inside_10d")]: rows.append({"group":"R1 "+label,**_stats(r1[~r1[col]])})
    for key in ("ER","FOMC","CPI","NFP"): rows.append({"group":"R1 "+key,**_stats(r1[r1[f"{key}_inside_10d"]])})
    rows.append({"group":"R1 REALIZED SHOCK",**_stats(r1[r1.realized_shock_inside_10d])}); rows.append({"group":"R1 NO EVENT + NO SHOCK",**_stats(r1[(~r1.ANY_SCHEDULED_EVENT_inside_10d)&(~r1.realized_shock_inside_10d)])})
    tables["r1_decomposition"]=pd.DataFrame(rows)
    tables["risk_state_event_10d"]=_groups(d.assign(event=d.ANY_SCHEDULED_EVENT_inside_10d),"risk_state")
    tables["event_type_global"]=_groups(pd.concat([d.assign(event_type=k,exposed=d[f"{k}_inside_10d"]) for k in ("ER","FOMC","CPI","NFP")]),"event_type")
    tables["event_density_10d"]=_groups(d.assign(event_density=d.scheduled_event_count_10d.clip(upper=3)),"event_density")
    tables["symbol_event_10d"]=_groups(d.assign(event=d.ANY_SCHEDULED_EVENT_inside_10d),"symbol")
    for name,t in tables.items(): t.to_csv(output_dir/f"scheduled_event_attribution_{name}.csv",index=False)
    return tables

if __name__=="__main__": print({k:len(v) for k,v in run().items()})
