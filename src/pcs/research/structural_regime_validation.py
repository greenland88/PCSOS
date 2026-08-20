"""Research-only decomposition of underlying structural and regime risk."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .path_risk_validation import OUT, _daily, _stats


ROOT = Path(__file__).resolve().parents[3]
RUNS = {"NVDA": "NVDA", "QQQ_2020_2022": "QQQ", "QQQ_2023_2026": "QQQ", "AMZN": "AMZN", "TSLA": "TSLA"}
RELIABLE_START = {"NVDA": "2024-06-10", "QQQ": "2010-11-22", "AMZN": "2022-06-06", "TSLA": "2017-01-03"}


def _daily_structural(symbol: str) -> pd.DataFrame:
    d = _daily(symbol).copy()
    raw = pd.read_csv(ROOT / "data" / "raw" / "daily_forward_adjusted" / f"{symbol}_daily_qfq.csv")
    raw["date"] = pd.to_datetime(raw["日期"])
    raw = raw.sort_values("date").drop_duplicates("date").set_index("date")
    for out, col in [("open", "开盘价"), ("high", "最高价"), ("low", "最低价"), ("volume", "成交量")]:
        d[out] = pd.to_numeric(raw[col], errors="coerce").reindex(d.index)
    prev = d.close.shift(1)
    d["atr_pct"] = d.atr14_calc / d.close
    d["atr_expansion"] = d.atr14_calc / d.atr14_calc.rolling(60, min_periods=20).median()
    d["ret1"] = d.close.pct_change()
    d["adverse1_atr"] = (d.close - d.low) / d.atr14_calc
    d["gap_down_atr"] = (d.open - prev) / d.atr14_calc
    d["adverse3_atr"] = (d.close - d.close.shift(3)) / d.atr14_calc
    d["adverse5_atr"] = (d.close - d.close.shift(5)) / d.atr14_calc
    d["rv20"] = d.ret1.rolling(20, min_periods=10).std() * np.sqrt(252)
    d["atr_change_abs"] = d.atr14_calc.pct_change().abs()
    d["down1_atr"] = (d.close - d.close.shift(1)) / d.atr14_calc
    d["down3_atr"] = (d.close - d.close.shift(3)) / d.atr14_calc
    d["down5_atr"] = (d.close - d.close.shift(5)) / d.atr14_calc
    d["down1_pct"] = d.ret1
    d["down3_pct"] = d.close.pct_change(3)
    d["down5_pct"] = d.close.pct_change(5)
    # A simple deterministic whipsaw proxy: a large down move followed by a
    # positive close in the next three sessions.
    d["down_reversal_3d"] = (d.down1_atr <= -1).rolling(4, min_periods=4).max().shift(1).fillna(0) * d.ret1.gt(0).astype(int)
    return d


def _trades() -> pd.DataFrame:
    p = pd.read_csv(OUT / "entry_context_enriched_pass_trades.csv", parse_dates=["date"])
    p["symbol"] = p["symbol"].astype(str)
    p["stop"] = p.exit_reason.eq("STOP")
    return p


def _future_mae(trades: pd.DataFrame, daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows=[]
    for _,r in trades.iterrows():
        d=daily[r.symbol]; dates=d.index; pos=dates.searchsorted(r.date)
        if pos >= len(dates) or dates[pos] != r.date: continue
        lows=d.low.iloc[pos+1:pos+11].to_numpy(dtype=float)
        closes=d.close.iloc[pos+1:pos+11].to_numpy(dtype=float)
        entry=float(r.close); atr=float(r.atr14)
        out={"run":r.run,"symbol":r.symbol,"date":r.date,"risk_state":r.get("risk_state"),"trend_score":r.trend_score,"credit_width_ratio":r.credit_width_ratio,"short_buffer_atr":r.short_buffer_atr,"stop":bool(r.stop),"profit50":bool(r.profit50),"profit70":bool(r.profit70),"realized_pnl":r.realized_pnl}
        for h in [1,3,5,10]:
            low=lows[:h]
            out[f"mae_{h}d_atr"]=(entry-np.nanmin(low))/atr if len(low) else np.nan
            out[f"breach_2atr_{h}d"]=bool(np.nanmin(low) <= entry-2*atr) if len(low) else False
            out[f"mae_{h}d_pct"]=(entry-np.nanmin(low))/entry if len(low) else np.nan
        rows.append(out)
    return pd.DataFrame(rows)


def _summary(g: pd.DataFrame, group: list[str]) -> pd.DataFrame:
    rows=[]
    groups = [((), g)] if not group else g.groupby(group, dropna=False)
    for keys,s in groups:
        vals=keys if isinstance(keys,tuple) else (keys,); r=dict(zip(group,vals)); r.update(_stats(s));
        for h in [1,3,5,10]: r[f"median_mae_{h}d_atr"]=s[f"mae_{h}d_atr"].median(); r[f"breach_2atr_{h}d_rate"]=s[f"breach_2atr_{h}d"].mean()
        rows.append(r)
    return pd.DataFrame(rows)


def run(output_dir: Path = OUT) -> dict[str,pd.DataFrame]:
    trades=_trades(); daily={s:_daily_structural(s).loc[lambda x: x.index >= RELIABLE_START[s]] for s in set(trades.symbol)}
    # Structural daily distributions and fixed, descriptive tail thresholds.
    structural=[]
    for symbol,d in daily.items():
        vals=d.atr_pct.dropna(); r={"symbol":symbol,"rows":len(vals)}
        for q in [.5,.75,.9,.95]: r[f"atr_pct_p{int(q*100)}"]=vals.quantile(q)
        for name,mask in {
            "down1_le_1atr":d.down1_atr<=-1,"down1_le_1_5atr":d.down1_atr<=-1.5,"down1_le_2atr":d.down1_atr<=-2,
            "down1_le_3pct":d.down1_pct<=-.03,"down1_le_5pct":d.down1_pct<=-.05,"down1_le_7pct":d.down1_pct<=-.07,
            "down3_ge_1_5atr":d.adverse3_atr>=1.5,"down3_ge_2atr":d.adverse3_atr>=2,"down5_ge_2atr":d.adverse5_atr>=2,"down5_ge_3atr":d.adverse5_atr>=3,
            "gap_le_0_5atr":d.gap_down_atr<=-.5,"gap_le_1atr":d.gap_down_atr<=-1,"gap_le_1_5atr":d.gap_down_atr<=-1.5}.items(): r[name]=mask.mean()
        r["atr_expansion_std"]=d.atr_expansion.std(); r["atr_change_abs_median"]=d.atr_change_abs.median(); r["rv20_median"]=d.rv20.median(); r["reversal_3d_rate"]=d.down_reversal_3d.mean(); structural.append(r)
    structural=pd.DataFrame(structural)
    mae=_future_mae(trades,daily)
    by_symbol=_summary(mae,["symbol"])
    by_run=_summary(mae,["run"])
    risk_cross=_summary(mae,["risk_state","symbol"])
    regimes={"QQQ_2020_2022":{"2020_2022":("2020-01-01","2022-12-31")},"QQQ_2023_2026":{"2023_2024":("2023-01-01","2024-12-31"),"2025_2026":("2025-01-01","2026-07-31"),"2026_YTD":("2026-01-01","2026-07-31")},"AMZN":{"2022_partial":("2022-06-06","2022-12-31"),"2023":("2023-01-01","2023-12-31"),"2024":("2024-01-01","2024-12-31"),"2025":("2025-01-01","2025-12-31"),"2026_YTD":("2026-01-01","2026-07-31")},"TSLA":{"2023_2024":("2023-01-01","2024-12-31"),"2025_2026":("2025-01-01","2026-07-31")},"NVDA":{"2024_2025":("2024-06-10","2025-12-31"),"2026":("2026-01-01","2026-07-31")}}
    reg=[]
    for run,periods in regimes.items():
        for name,(a,b) in periods.items():
            g=mae[(mae.run==run)&mae.date.between(a,b)]
            if len(g):
                r={"run":run,"period":name}; r.update(_summary(g,[]).iloc[0].to_dict() if not _summary(g,[]).empty else {}); r["period_n"]=len(g); reg.append(r)
    regime=pd.DataFrame(reg)
    # Buffer equivalence: descriptive breach rates by observed buffer bucket.
    b=mae.copy(); b["buffer_bucket"]=pd.cut(b.short_buffer_atr,[-np.inf,1.5,2,2.5,np.inf],labels=["<1.5","1.5-2","2-2.5",">2.5"])
    buffer=_summary(b,["symbol","buffer_bucket"])
    # Option compensation and outcomes by symbol/period.
    compensation=_summary(mae,["symbol"])[["symbol","n","stop_rate","profit_factor","avg_pnl","median_mae_5d_atr","breach_2atr_5d_rate"]]
    compensation["median_credit_width"]=trades.groupby("symbol").credit_width_ratio.median().reindex(compensation.symbol).to_numpy()
    # Same-symbol regime / risk controls.
    same=_summary(mae,["run","risk_state"])
    transition=[]
    for run,g in trades.sort_values("date").groupby("run"):
        transition.append({"run":run,"pass_rows":len(g),"pass_to_nonpass_rate":np.nan,"pass_mean_duration":np.nan})
    tables={"structural_summary":structural,"forward_mae":by_symbol,"forward_mae_by_run":by_run,"risk_state_cross_symbol":risk_cross,"regime_decomposition":regime,"buffer_equivalence":buffer,"option_compensation":compensation,"same_symbol_risk_regime":same,"trend_persistence":pd.DataFrame(transition),"feature_definitions":pd.DataFrame([
        {"feature":"ATR/Price","definition":"ATR14 / close"},{"feature":"1d/3d/5d tail","definition":"entry-independent daily close/low moves normalized by current ATR"},{"feature":"gap_down_atr","definition":"(open - previous close) / ATR14"},{"feature":"forward MAE","definition":"entry close minus future low, divided by entry ATR14; outcome only"},{"feature":"2ATR breach","definition":"future low <= entry close - 2 * entry ATR14"}])}
    mae.to_csv(output_dir/"structural_regime_forward_mae_trades.csv",index=False)
    for name,frame in tables.items(): frame.to_csv(output_dir/f"structural_regime_{name}.csv",index=False)
    return tables


if __name__=="__main__": print("generated",len(run()),"structural/regime tables")
