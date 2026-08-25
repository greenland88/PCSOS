"""Research-only Safe Buffer calibration from underlying forward paths."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .path_risk_validation import OUT
from .structural_regime_validation import _daily_structural


ROOT = Path(__file__).resolve().parents[3]
BUFFERS = [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0]
SYMBOL_CLASS = {"QQQ": "LOW", "NVDA": "MEDIUM", "AMZN": "MEDIUM", "TSLA": "HIGH"}


def _load() -> pd.DataFrame:
    t = pd.read_csv(OUT / "entry_context_enriched_pass_trades.csv", parse_dates=["date"])
    t["stop"] = t.exit_reason.eq("STOP")
    t["profit50"] = t.exit_reason.eq("PROFIT50")
    t["regime"] = np.select([t.date.dt.year.le(2022), t.date.dt.year.between(2023, 2024), t.date.dt.year.ge(2025)], ["2020_2022", "2023_2024", "2025_2026"], default="other")
    t["structural_class"] = t.symbol.map(SYMBOL_CLASS)
    return t


def _paths(t: pd.DataFrame) -> pd.DataFrame:
    daily={s:_daily_structural(s) for s in t.symbol.unique()}; rows=[]
    for _,r in t.iterrows():
        d=daily[r.symbol]; i=d.index.searchsorted(r.date)
        if i>=len(d) or d.index[i]!=r.date: continue
        lows=d.low.iloc[i+1:i+21].to_numpy(dtype=float); entry=float(r.close); atr=float(r.atr14)
        x=r.to_dict()
        for h in [3,5,10,20]:
            low=lows[:h]; x[f"min_low_{h}d"] = np.nanmin(low) if len(low) else np.nan
        for b in BUFFERS:
            for h in [3,5,10,20]:
                x[f"breach_{b:g}atr_{h}d"] = bool(x[f"min_low_{h}d"] <= entry-b*atr) if np.isfinite(x[f"min_low_{h}d"]) else np.nan
        rows.append(x)
    return pd.DataFrame(rows)


def _curve(g: pd.DataFrame, group: list[str]) -> pd.DataFrame:
    rows=[]; groups=[((),g)] if not group else g.groupby(group,dropna=False)
    for keys,s in groups:
        vals=keys if isinstance(keys,tuple) else (keys,); base=dict(zip(group,vals))
        for b in BUFFERS:
            r={**base,"buffer_atr":b,"n":len(s)}
            for h in [3,5,10,20]: r[f"breach_{h}d_rate"]=s[f"breach_{b:g}atr_{h}d"].mean()
            rows.append(r)
    return pd.DataFrame(rows)


def _equivalence(curve: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    reference = curve[(curve.symbol == "NVDA") & (curve.buffer_atr == 2.0)].iloc[0]
    for symbol,g in curve.groupby("symbol"):
        for h in [5,10,20]:
            target=reference[f"breach_{h}d_rate"]
            valid=g.dropna(subset=[f"breach_{h}d_rate"]).copy(); valid["abs_error"]=(valid[f"breach_{h}d_rate"]-target).abs(); best=valid.sort_values(["abs_error","buffer_atr"]).iloc[0]
            rows.append({"symbol":symbol,"horizon_days":h,"reference_symbol":"NVDA","reference_buffer":2.0,"reference_breach":target,"equivalent_buffer":best.buffer_atr,"equivalent_breach":best[f"breach_{h}d_rate"],"absolute_error":best.abs_error,"method":"nearest descriptive curve point; not PnL optimized"})
    return pd.DataFrame(rows)


def run(output_dir: Path = OUT) -> dict[str,pd.DataFrame]:
    t=_load(); p=_paths(t); tables={}
    cross=_curve(p,["symbol"]); tables["cross_symbol_curve"]=cross
    period_defs={"QQQ_2020_2022":("QQQ","2020-01-01","2022-12-31"),"QQQ_2023_2024":("QQQ","2023-01-01","2024-12-31"),"QQQ_2025_2026":("QQQ","2025-01-01","2026-07-31"),"AMZN_2023_2024":("AMZN","2023-01-01","2024-12-31"),"AMZN_2025_2026":("AMZN","2025-01-01","2026-07-31"),"NVDA_2024_2025":("NVDA","2024-06-10","2025-12-31"),"NVDA_2026":("NVDA","2026-01-01","2026-07-31"),"TSLA_2023_2024":("TSLA","2023-01-01","2024-12-31"),"TSLA_2025_2026":("TSLA","2025-01-01","2026-07-31")}
    period_rows=[]
    for name,(sym,a,b) in period_defs.items():
        g=p[(p.symbol==sym)&p.date.between(a,b)]; c=_curve(g,[]); c.insert(0,"period",name); period_rows.append(c)
    tables["regime_curve"]=pd.concat(period_rows,ignore_index=True)
    tables["risk_state_curve"]=_curve(p.dropna(subset=["risk_state"]),["risk_state"])
    tables["structural_class_curve"]=_curve(p,["structural_class"])
    tables["risk_equivalent_buffer"]=_equivalence(cross)
    tables["combined_grid"]=_curve(p.dropna(subset=["structural_class","risk_state"]),["structural_class","regime","risk_state"])

    # Existing actual selected spreads are the only option-feasibility evidence
    # available without rerunning option-chain selection for each hypothetical buffer.
    actual=t[["symbol","short_buffer_atr","credit_width_ratio","stop","realized_pnl"]].copy()
    actual["buffer_bucket"]=pd.cut(actual.short_buffer_atr,[-np.inf,1.5,2,2.5,3,np.inf],labels=["<1.5","1.5-2","2-2.5","2.5-3",">3"])
    tables["actual_option_reference"]=actual.groupby(["symbol","buffer_bucket"],observed=False).agg(n=("symbol","size"),median_credit_width=("credit_width_ratio","median"),stop_rate=("stop","mean"),avg_pnl=("realized_pnl","mean")).reset_index()
    tables["option_feasibility"] = pd.DataFrame([{"hypothetical_buffer":"1.5, 2.0, 2.5, 3.0, 3.5 ATR","status":"UNAVAILABLE_WITHOUT_CHAIN_RERUN","reason":"persisted trades contain only selected actual spreads; no hypothetical strike re-selection was performed","production_unchanged":True}])
    # Descriptive retention reference: actual selected trades meeting the frozen floor.
    tables["candidate_retention_reference"] = pd.DataFrame([{ "buffer_reference":"actual_selected_spread","candidate_count":len(t),"tradable_count":int((t.credit_width_ratio>=.15).sum()),"lost_credit_floor_pct":float((t.credit_width_ratio<.15).mean()),"invalid_leg_rate":np.nan,"note":"hypothetical buffer retention unavailable without option-chain rerun"}])
    corr_cols=[c for c in ["atr_pct","atr_expansion","short_buffer_atr","risk_score","mae_5d_atr"] if c in p.columns]
    corr=p[corr_cols].corr(numeric_only=True).reset_index().rename(columns={"index":"feature"})
    tables["double_counting_correlation"]=corr
    tables["feature_definitions"]=pd.DataFrame([{ "feature":"buffer_atr","definition":"hypothetical downside threshold: entry close - buffer * entry ATR14"},{"feature":"breach","definition":"future minimum low <= threshold; no option availability assumed"},{"feature":"risk_equivalent","definition":"nearest observed breach curve point to NVDA 2ATR reference; no PnL optimization"}])
    p.to_csv(output_dir/"safe_buffer_calibration_paths.csv",index=False)
    for name,frame in tables.items(): frame.to_csv(output_dir/f"safe_buffer_{name}.csv",index=False)
    return tables


if __name__=="__main__": print("generated",len(run()),"safe-buffer tables")
