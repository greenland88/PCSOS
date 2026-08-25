"""Research-only event/shock attribution.

Scheduled event attribution is intentionally unavailable until a versioned
historical calendar is supplied.  Price-based systemic shock is computed from
the local QQQ daily series with strictly prior-data thresholds.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .path_risk_validation import OUT
from .structural_regime_validation import _daily_structural


ROOT = Path(__file__).resolve().parents[3]


def _shock_calendar() -> pd.DataFrame:
    d = _daily_structural("QQQ")
    prior_q = d.atr_expansion.rolling(252, min_periods=60).quantile(.95).shift(1)
    shock = (d.down1_atr <= -2) | (d.gap_down_atr <= -1) | (d.atr_expansion >= prior_q)
    return pd.DataFrame({"event_date": d.index[shock], "event_type": "PRICE_SYSTEMIC_SHOCK", "source": "QQQ_daily_qfq", "source_version": "research_v0.1_prior_252_p95"})


def _trades() -> pd.DataFrame:
    t = pd.read_csv(OUT / "safe_buffer_calibration_paths.csv", parse_dates=["date"])
    t["stop"] = t.exit_reason.eq("STOP")
    t["profit50"] = t.exit_reason.eq("PROFIT50")
    return t


def _tag(t: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    event_dates = pd.DatetimeIndex(events.event_date)
    rows=[]
    for _,r in t.iterrows():
        dates=pd.date_range(r.date, periods=21, freq="B")
        # Use actual QQQ trading event dates only; date-range membership is
        # conservative for research and does not create future features.
        offsets=[]
        expiry = pd.to_datetime(r.expiration) if pd.notna(r.get("expiration")) else dates[-1]
        for ed in event_dates:
            if ed >= r.date and ed <= expiry:
                offsets.append((ed-r.date).days)
        x=r.to_dict(); x["shock_count_inside_20d"]=len(offsets)
        for h in [3,5,10]: x[f"shock_inside_first_{h}d"]=any(o <= h-1 for o in offsets)
        x["shock_inside_DTE"]=bool(offsets)
        rows.append(x)
    return pd.DataFrame(rows)


def _stats(g: pd.DataFrame) -> dict:
    pnl=g.realized_pnl
    pos=pnl[pnl>0].sum(); neg=-pnl[pnl<0].sum()
    return {"n":len(g),"stop_rate":g.stop.mean(),"profit50_rate":g.profit50.mean(),"avg_pnl":pnl.mean(),"profit_factor":pos/neg if neg else np.inf,"breach_2atr_5d_rate":g["breach_2atr_5d"].mean(),"breach_2atr_10d_rate":g["breach_2atr_10d"].mean()}


def _group(g: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    rows=[]
    for keys,s in g.groupby(cols,dropna=False):
        vals=keys if isinstance(keys,tuple) else (keys,); r=dict(zip(cols,vals)); r.update(_stats(s)); rows.append(r)
    return pd.DataFrame(rows)


def run(output_dir: Path = OUT) -> dict[str,pd.DataFrame]:
    trades=_trades(); shocks=_shock_calendar(); tagged=_tag(trades,shocks); tables={}
    tables["event_data_availability"]=pd.DataFrame([
        {"event_type":"EARNINGS","available":False,"source":"not present in repository","coverage":"unavailable","note":"no historical company event calendar; no dates inferred"},
        {"event_type":"FOMC","available":False,"source":"not present in repository","coverage":"unavailable","note":"no dates inferred"},
        {"event_type":"CPI","available":False,"source":"not present in repository","coverage":"unavailable","note":"no dates inferred"},
        {"event_type":"NFP","available":False,"source":"not present in repository","coverage":"unavailable","note":"no dates inferred"},
        {"event_type":"PRICE_SYSTEMIC_SHOCK","available":True,"source":"QQQ daily qfq","coverage":"local historical range","note":"prior-data rolling threshold"},
    ])
    tables["event_calendar_schema"]=pd.DataFrame(columns=["event_date","event_type","source","source_version"])
    tables["shock_calendar"]=shocks
    tables["shock_summary"]=_group(tagged.assign(shock_exposure=tagged.shock_inside_first_10d),["shock_exposure"])
    tables["shock_by_symbol"]=_group(tagged,["symbol","shock_inside_first_10d"])
    tables["shock_by_risk"]=_group(tagged,["risk_state","shock_inside_first_10d"])
    tagged["event_density"]=tagged["shock_count_inside_20d"]
    tables["shock_density"]=_group(tagged,["event_density"])
    tables["pre_event_vs_post_damage"]=_group(tagged,["shock_inside_first_3d"])
    tables["no_event_control"] = pd.DataFrame([{"status":"UNAVAILABLE","reason":"scheduled event calendar is missing; cannot label NO_ER/NO_MACRO trades without inventing dates"}])
    tables["event_buffer_curves"] = pd.DataFrame([{"status":"UNAVAILABLE","reason":"scheduled event exposure unavailable; price-shock-only tags are available in shock_calendar and tagged trades"}])
    tagged.to_csv(output_dir/"event_risk_tagged_trades.csv",index=False)
    for name,frame in tables.items(): frame.to_csv(output_dir/f"event_risk_{name}.csv",index=False)
    return tables


if __name__=="__main__": print("generated",len(run()),"event-risk tables")
