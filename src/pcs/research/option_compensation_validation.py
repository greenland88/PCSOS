"""Research-only validation of PCS premium compensation versus path risk."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .path_risk_validation import OUT, _stats


ROOT = Path(__file__).resolve().parents[3]


def _load() -> pd.DataFrame:
    p = pd.read_csv(OUT / "entry_context_enriched_pass_trades.csv", parse_dates=["date"])
    mae = pd.read_csv(OUT / "structural_regime_forward_mae_trades.csv", parse_dates=["date"])
    keys = ["run", "date", "symbol"]
    p = p.merge(mae[[*keys, "mae_5d_atr", "mae_10d_atr", "breach_2atr_5d", "breach_2atr_10d"]], on=keys, how="left")
    p["stop"] = p.exit_reason.eq("STOP")
    p["profit50"] = p.exit_reason.eq("PROFIT50")
    p["profit70"] = p.get("events.profit70", pd.Series(index=p.index)).notna()
    p["width"] = p.short_strike - p.long_strike
    p["dte"] = (pd.to_datetime(p.expiration) - p.date).dt.days
    p["credit_per_atr"] = p.initial_credit / p.atr14
    p["regime"] = np.select([p.date.dt.year.le(2022), p.date.dt.year.between(2023, 2024), p.date.dt.year.ge(2025)], ["2020_2022", "2023_2024", "2025_2026"], default="other")
    p["dte_bucket"] = pd.cut(p.dte, [19, 27, 35, 45], labels=["20-27", "28-35", "36-45"])
    p["buffer_bucket"] = pd.cut(p.short_buffer_atr, [-np.inf, 1.5, 2.0, 2.5, np.inf], labels=["<1.5", "1.5-2", "2-2.5", ">2.5"])
    return p


def _metric(g: pd.DataFrame) -> dict:
    r = _stats(g)
    for f in ["mae_5d_atr", "mae_10d_atr"]: r[f"median_{f}"] = g[f].median()
    r["breach_2atr_5d_rate"] = g.breach_2atr_5d.mean()
    r["breach_2atr_10d_rate"] = g.breach_2atr_10d.mean()
    r["median_credit_width"] = g.credit_width_ratio.median()
    r["p25_credit_width"] = g.credit_width_ratio.quantile(.25)
    r["p75_credit_width"] = g.credit_width_ratio.quantile(.75)
    r["median_credit_per_atr"] = g.credit_per_atr.median()
    return r


def _group(g: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    rows=[]; groups=[((),g)] if not cols else g.groupby(cols,dropna=False)
    for keys,s in groups:
        vals=keys if isinstance(keys,tuple) else (keys,); r=dict(zip(cols,vals)); r.update(_metric(s)); rows.append(r)
    return pd.DataFrame(rows)


def _quantiles(g: pd.DataFrame, feature: str, cols: list[str] | None = None) -> pd.DataFrame:
    cols=cols or []; rows=[]
    groups=[((),g)] if not cols else g.groupby(cols,dropna=False)
    for keys,s in groups:
        s=s.dropna(subset=[feature]).copy()
        if len(s)<10: continue
        q=min(5,max(2,len(s)//20)); s["premium_bucket"]=pd.qcut(s[feature].rank(method="first"),q,labels=[f"Q{i}" for i in range(1,q+1)])
        for b,x in s.groupby("premium_bucket",observed=False):
            vals=keys if isinstance(keys,tuple) else (keys,); r=dict(zip(cols,vals)); r.update({"feature":feature,"premium_bucket":str(b),"bucket_rank":int(str(b)[1:]),"feature_median":x[feature].median()}); r.update(_metric(x)); rows.append(r)
    return pd.DataFrame(rows)


def run(output_dir: Path = OUT) -> dict[str,pd.DataFrame]:
    df=_load(); tables={}
    tables["availability"] = pd.DataFrame([
        {"field":f,"available":f in df.columns and df[f].notna().any(),"non_null_rate":df[f].notna().mean() if f in df else 0.0,"note":"historical quote field unavailable" if f in {"short_iv","long_iv","iv_rank","iv_percentile","implied_move","expected_move"} else ""}
        for f in ["initial_credit","width","credit_width_ratio","dte","short_strike","long_strike","atr14","short_buffer_atr","short_iv","long_iv","iv_rank","iv_percentile","implied_move","expected_move"]
    ])
    tables["premium_definitions"] = pd.DataFrame([
        {"metric":"credit_width_ratio","definition":"initial conservative credit / spread width"},
        {"metric":"credit_per_atr","definition":"initial credit / entry ATR14"},
        {"metric":"risk_adjusted_premium_descriptive","definition":"Credit/Width divided by grouped 5d 2ATR breach probability; descriptive only"},
    ])
    tables["global_quantiles"]=_quantiles(df,"credit_width_ratio")
    tables["symbol_quantiles"]=_quantiles(df,"credit_width_ratio",["symbol"])
    tables["risk_state_premium"]=_quantiles(df,"credit_width_ratio",["risk_state"])
    tables["risk_state_summary"]=_group(df,["risk_state"])
    tables["symbol_summary"]=_group(df,["symbol"])
    tables["dte_interaction"]=_group(df.dropna(subset=["dte_bucket"]),["dte_bucket"])
    tables["buffer_interaction"]=_group(df.dropna(subset=["buffer_bucket"]),["buffer_bucket"])
    floor=df.copy(); floor["floor_bucket"]=pd.cut(floor.credit_width_ratio,[.149,.175,.20,.225,.25,np.inf],labels=["15-17.5%","17.5-20%","20-22.5%","22.5-25%","25%+"])
    tables["floor_analysis"]=_group(floor.dropna(subset=["floor_bucket"]),["floor_bucket"])
    tables["floor_by_risk"]=_group(floor.dropna(subset=["floor_bucket","risk_state"]),["risk_state","floor_bucket"])
    tables["qqq_regime"]=_group(df[df.symbol.eq("QQQ") & df.regime.isin(["2020_2022","2023_2024","2025_2026"])],["regime"])
    tables["amzn_regime"]=_group(df[df.symbol.eq("AMZN") & df.regime.isin(["2023_2024","2025_2026"])],["regime"])
    tables["nvda_regime"]=_group(df[df.symbol.eq("NVDA")].assign(period=np.where(df[df.symbol.eq("NVDA")].date.dt.year<=2025,"2024_2025","2026")),["period"])
    tables["tsla_premium"]=_quantiles(df[df.symbol.eq("TSLA")],"credit_width_ratio",["risk_state"])
    tables["structural_control_nvda_tsla"]=_group(df[df.symbol.isin(["NVDA","TSLA"])],["symbol","risk_state"])
    # Premium versus path-risk frontier: fixed descriptive MAE/breach bins.
    frontier=df.copy(); frontier["risk_bin"]=pd.cut(frontier.mae_5d_atr,[-np.inf,.75,1.0,1.25,np.inf],labels=["low","moderate","elevated","high"])
    frontier["premium_bin"]=pd.cut(frontier.credit_width_ratio,[.149,.175,.20,.225,.25,np.inf],labels=["15-17.5%","17.5-20%","20-22.5%","22.5-25%","25%+"])
    tables["risk_premium_frontier"]=_group(frontier.dropna(subset=["risk_bin","premium_bin"]),["risk_bin","premium_bin"])
    # Expected-value decomposition by premium bucket.
    ev=[]
    for _,r in tables["global_quantiles"].iterrows():
        g=df[(df.credit_width_ratio>=df.credit_width_ratio.quantile((r.bucket_rank-1)/5)) & (df.credit_width_ratio<=df.credit_width_ratio.quantile(r.bucket_rank/5))]
        ev.append({"premium_bucket":r.premium_bucket,"n":len(g),"win_rate":(g.realized_pnl>0).mean(),"avg_winner":g.loc[g.realized_pnl>0,"realized_pnl"].mean(),"avg_loser":g.loc[g.realized_pnl<0,"realized_pnl"].mean(),"stop_rate":g.stop.mean(),"avg_pnl":g.realized_pnl.mean()})
    tables["expected_value_decomposition"]=pd.DataFrame(ev)
    # Risk-controlled premium correlation/monotonicity.
    mono=[]
    for state,g in df.groupby("risk_state"):
        q=_quantiles(g,"credit_width_ratio")
        mono.append({"risk_state":state,"bucket_count":len(q),"spearman_stop":q.bucket_rank.corr(q.stop_rate,method="spearman") if len(q)>=3 else np.nan,"spearman_pf":q.bucket_rank.corr(q.profit_factor,method="spearman") if len(q)>=3 else np.nan,"spearman_avg_pnl":q.bucket_rank.corr(q.avg_pnl,method="spearman") if len(q)>=3 else np.nan})
    tables["risk_controlled_monotonicity"]=pd.DataFrame(mono)
    df.to_csv(output_dir/"option_compensation_enriched_pass_trades.csv",index=False)
    for name,frame in tables.items(): frame.to_csv(output_dir/f"option_compensation_{name}.csv",index=False)
    return tables


if __name__=="__main__": print("generated",len(run()),"option-compensation tables")
