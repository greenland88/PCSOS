"""Research-only validation of PCS entry location and support context.

No result from this module is consumed by production gates or trading logic.
All features are reconstructed as-of the entry date from daily OHLCV data.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .path_risk_validation import OUT, _stats, _daily


ROOT = Path(__file__).resolve().parents[3]
RUNS = {
    "NVDA": OUT / "nvda_postsplit_trades.json",
    "QQQ_2020_2022": OUT / "qqq_2020_2022" / "backtest_trades.csv",
    "QQQ_2023_2026": OUT / "qqq_2023_2026" / "backtest_trades.csv",
    "AMZN": OUT / "amzn_reliable_2022_2026" / "backtest_trades.csv",
    "TSLA": OUT / "tsla_reliable_2017_2026" / "backtest_trades.csv",
}
SYMBOLS = {"NVDA": "NVDA", "QQQ_2020_2022": "QQQ", "QQQ_2023_2026": "QQQ", "AMZN": "AMZN", "TSLA": "TSLA"}


def _load_trades(path: Path) -> pd.DataFrame:
    if path.suffix == ".json":
        frame = pd.json_normalize(json.loads(path.read_text(encoding="utf-8")))
    else:
        frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["stop"] = frame["exit_reason"].eq("STOP")
    frame["profit50"] = frame["exit_reason"].eq("PROFIT50")
    frame["profit70"] = frame.get("events.profit70", pd.Series(index=frame.index)).notna()
    return frame


def _daily_context(symbol: str) -> pd.DataFrame:
    d = _daily(symbol).copy()
    raw = pd.read_csv(ROOT / "data" / "raw" / "daily_forward_adjusted" / f"{symbol}_daily_qfq.csv")
    raw["date"] = pd.to_datetime(raw["日期"])
    raw = raw.sort_values("date").drop_duplicates("date").set_index("date")
    for out, col in [("open", "开盘价"), ("high", "最高价"), ("low", "最低价"), ("volume", "成交量")]:
        d[out] = pd.to_numeric(raw[col], errors="coerce").reindex(d.index)
    prev = d["close"].shift(1)
    d["ma20"] = d.close.rolling(20, min_periods=20).mean()
    d["ma50"] = d.close.rolling(50, min_periods=50).mean()
    d["ma200"] = d.close.rolling(200, min_periods=200).mean()
    d["distance_ma20_atr"] = (d.close - d.ma20) / d.atr14_calc
    d["distance_ma50_atr"] = (d.close - d.ma50) / d.atr14_calc
    d["distance_ma200_atr"] = (d.close - d.ma200) / d.atr14_calc
    d["distance_ma20_abs_atr"] = d.distance_ma20_atr.abs()
    d["distance_ma50_abs_atr"] = d.distance_ma50_atr.abs()
    d["distance_ma200_abs_atr"] = d.distance_ma200_atr.abs()
    d["range20"] = (d.close - d.close.rolling(20, min_periods=20).min()) / (d.close.rolling(20, min_periods=20).max() - d.close.rolling(20, min_periods=20).min())
    d["range50"] = (d.close - d.close.rolling(50, min_periods=50).min()) / (d.close.rolling(50, min_periods=50).max() - d.close.rolling(50, min_periods=50).min())
    d["true_range"] = pd.concat([d.high-d.low, (d.high-prev).abs(), (d.low-prev).abs()], axis=1).max(axis=1)
    d["body_atr"] = (d.close-d.open).abs() / d.atr14_calc
    d["lower_wick_atr"] = (np.minimum(d.open, d.close)-d.low) / d.atr14_calc
    d["upper_wick_atr"] = (d.high-np.maximum(d.open, d.close)) / d.atr14_calc
    d["lower_wick_share"] = (np.minimum(d.open, d.close)-d.low) / d.true_range.replace(0, np.nan)
    d["close_location"] = (d.close-d.low) / (d.high-d.low).replace(0, np.nan)
    d["volume_ratio20"] = d.volume / d.volume.rolling(20, min_periods=20).mean()
    d["prior_close_up"] = (d.close > prev).astype(float)
    local_low = d.close.rolling(10, min_periods=10).min()
    d["recovery_2d_atr"] = (d.close-local_low.shift(2)) / d.atr14_calc
    d["up_closes_since_low"] = d.close.diff().gt(0).rolling(5, min_periods=1).sum()

    # Confirmed pivot lows: pivot date is usable only after right bars elapsed.
    lows = d.low.to_numpy(dtype=float)
    dates = d.index
    pivots = []
    left = right = 3
    for i in range(left, len(d)-right):
        window = lows[i-left:i+right+1]
        if np.isfinite(lows[i]) and lows[i] == np.nanmin(window) and (window == lows[i]).sum() == 1:
            pivots.append((dates[i], dates[i+right], lows[i]))
    d["swing_support"] = np.nan
    d["swing_support_date"] = pd.NaT
    d["swing_support_age"] = np.nan
    d["swing_touch_count"] = np.nan
    d["swing_historical_break"] = pd.Series(False, index=d.index, dtype=bool)
    pivot_dates = np.array([p[0].value for p in pivots], dtype=np.int64)
    pivot_confirmed = np.array([p[1].value for p in pivots], dtype=np.int64)
    pivot_prices = np.array([p[2] for p in pivots], dtype=float)
    atr_values = d.atr14_calc.to_numpy(dtype=float)
    close_values = d.close.to_numpy(dtype=float)
    low_values = d.low.to_numpy(dtype=float)
    for i, date in enumerate(dates):
        candidates = np.flatnonzero((pivot_confirmed <= date.value) & (pivot_prices < close_values[i]))
        if len(candidates) == 0:
            continue
        pivot_i = candidates[-1]
        support_date = pd.Timestamp(pivot_dates[pivot_i])
        price = pivot_prices[pivot_i]
        d.iloc[i, d.columns.get_loc("swing_support")] = price
        d.iloc[i, d.columns.get_loc("swing_support_date")] = support_date
        d.iloc[i, d.columns.get_loc("swing_support_age")] = (date-support_date).days
        prior = d.iloc[:i+1]
        touch = ((prior.low-price).abs() <= prior.atr14_calc*0.5).sum()
        d.iloc[i, d.columns.get_loc("swing_touch_count")] = touch
        d.iloc[i, d.columns.get_loc("swing_historical_break")] = bool((close_values[:i+1] < price-atr_values[:i+1]*0.5).any())
    d["swing_distance_atr"] = (d.close-d.swing_support) / d.atr14_calc
    d["swing_distance_pct"] = (d.close-d.swing_support) / d.close
    d["rolling_low60"] = d.close.rolling(60, min_periods=20).min()
    d["rolling_low_distance_atr"] = (d.close-d.rolling_low60) / d.atr14_calc
    return d


def _trades_with_context() -> pd.DataFrame:
    frames = []
    daily = {s: _daily_context(s) for s in set(SYMBOLS.values())}
    risk = pd.read_csv(OUT / "risk_layer_scored_pass_trades.csv", parse_dates=["date"])
    risk_key = ["run", "date", "expiration", "short_strike", "long_strike"]
    for run, path in RUNS.items():
        t = _load_trades(path)
        t["run"] = run; t["symbol"] = SYMBOLS[run]
        t = t[t.trend_gate.eq("PASS")].copy()
        d = daily[t.symbol.iloc[0]]
        x = d.reindex(t.date)
        for col in [c for c in d.columns if c not in ["date"]]:
            t[col] = x[col].to_numpy()
        t = t.merge(risk[risk.run.eq(run)][risk_key + ["risk_state"]], on=risk_key, how="left")
        frames.append(t)
    return pd.concat(frames, ignore_index=True).drop_duplicates(["run", "date", "expiration", "short_strike", "long_strike"])


def _stats_by(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    rows=[]
    for keys,g in frame.groupby(cols, dropna=False):
        vals=keys if isinstance(keys,tuple) else (keys,); r=dict(zip(cols,vals)); r.update(_stats(g)); rows.append(r)
    return pd.DataFrame(rows)


def _bucket(frame: pd.DataFrame, feature: str, by: list[str], q: int = 5) -> pd.DataFrame:
    rows=[]
    for keys,g in frame.groupby(by, dropna=False) if by else [((),frame)]:
        g=g.dropna(subset=[feature]).copy()
        if len(g)<10: continue
        n=min(q,max(2,len(g)//20)); g["bucket"]=pd.qcut(g[feature].rank(method="first"),n,labels=[f"Q{i}" for i in range(1,n+1)])
        for b,s in g.groupby("bucket",observed=False):
            vals=keys if isinstance(keys,tuple) else (keys,); r=dict(zip(by,vals)); r.update({"feature":feature,"bucket":str(b),"bucket_rank":int(str(b)[1:]),"feature_median":s[feature].median()}); r.update(_stats(s)); rows.append(r)
    return pd.DataFrame(rows)


def run(output_dir: Path = OUT) -> dict[str,pd.DataFrame]:
    df=_trades_with_context()
    tables={}
    definitions=pd.DataFrame([
        {"feature":"distance_ma20_atr","definition":"(close - SMA20) / ATR14","lookahead":"entry date and prior bars only"},
        {"feature":"distance_ma50_atr","definition":"(close - SMA50) / ATR14","lookahead":"entry date and prior bars only"},
        {"feature":"distance_ma200_atr","definition":"(close - SMA200) / ATR14; missing before warmup","lookahead":"entry date and prior bars only"},
        {"feature":"swing_distance_atr","definition":"(close - latest confirmed pivot low below price) / ATR14; pivot right=3","lookahead":"confirmation date <= entry date"},
        {"feature":"rolling_low_distance_atr","definition":"(close - rolling 60-day low) / ATR14","lookahead":"entry date and prior bars only"},
        {"feature":"range20/range50","definition":"close location between rolling low and high","lookahead":"entry date and prior bars only"},
        {"feature":"lower_wick_atr","definition":"lower wick / ATR14","lookahead":"entry candle only"},
        {"feature":"volume_ratio20","definition":"entry volume / trailing 20-day average volume","lookahead":"entry date and prior bars only"},
    ])
    tables["feature_definitions"]=definitions
    tables["support_distance_buckets"]=_bucket(df,"swing_distance_atr",[],5)
    for f in ["distance_ma20_atr","distance_ma50_atr","distance_ma200_atr","range20","range50","lower_wick_atr","volume_ratio20"]:
        tables[f"{f.replace('/','_')}_buckets"]=_bucket(df,f,[],5)
    # Directional MA proximity categories are fixed descriptive zones, not optimized cutoffs.
    for f,name in [("distance_ma20_atr","ma20"),("distance_ma50_atr","ma50"),("distance_ma200_atr","ma200")]:
        df[f"{name}_zone"]=pd.cut(df[f],[-np.inf,-1,1,np.inf],labels=["below_far","near","above_far"])
    df["support_zone"]=pd.cut(df.swing_distance_atr,[-np.inf,0.75,1.5,2.5,np.inf],labels=["below_or_at_support","near","middle","far"],include_lowest=True)
    df["rejection_zone"]=pd.cut(df.lower_wick_atr,[-np.inf,.25,.75,np.inf],labels=["low","medium","high"])
    df["volume_zone"]=pd.cut(df.volume_ratio20,[-np.inf,.8,1.2,np.inf],labels=["low","normal","elevated"])
    tables["ma_proximity"]=_stats_by(df.dropna(subset=["ma20_zone"]),["ma20_zone"])
    tables["range_position"]=_stats_by(df.dropna(subset=["range20"]),["range20"],) if False else _bucket(df,"range20",[],5)
    tables["risk_state_support"]=_stats_by(df.dropna(subset=["risk_state","support_zone"]),["risk_state","support_zone"])
    tables["risk_state_ma20"]=_stats_by(df.dropna(subset=["risk_state","ma20_zone"]),["risk_state","ma20_zone"])
    tables["trend_score_support"]=_stats_by(df.assign(score_band=pd.qcut(df.trend_score,5,labels=["Q1","Q2","Q3","Q4","Q5"],duplicates="drop")).dropna(subset=["score_band","support_zone"]),["score_band","support_zone"])
    interactions=[("support_zone","rejection_zone"),("support_zone","volume_zone"),("support_zone","range20"),("atr_expansion","support_zone")]
    for a,b in interactions:
        if b=="range20": df["range_zone"]=pd.qcut(df[b],3,labels=["low","mid","high"],duplicates="drop"); b2="range_zone"
        else: b2=b
        if a == "atr_expansion":
            df["atr_expansion_zone"] = pd.qcut(df[a],3,labels=["low","mid","high"],duplicates="drop"); a2="atr_expansion_zone"
        else: a2=a
        tables[f"interaction_{a}_{b}"]=_stats_by(df.dropna(subset=[a2,b2]),[a2,b2])
    # Risk-state controlled near/far contrast and R1 deep validation.
    tables["r1_deep"]=_stats_by(df[df.risk_state.eq("R1_NORMAL")].dropna(subset=["support_zone"]),["support_zone"])
    tables["tsla_r1_r2"]=_stats_by(df[(df.symbol.eq("TSLA")) & df.risk_state.isin(["R1_NORMAL","R2_ELEVATED"])].dropna(subset=["support_zone"]),["risk_state","support_zone"])
    amzn = df[df.symbol.eq("AMZN")].copy()
    amzn["regime"] = np.where(amzn.date.dt.year <= 2024, "2023_2024", "2025_2026")
    tables["amzn_regime"]=_stats_by(amzn.dropna(subset=["risk_state","support_zone"]),["regime","risk_state","support_zone"])
    robustness=[]
    for method,col in [("confirmed_swing_low","swing_distance_atr"),("rolling_low_zone","rolling_low_distance_atr"),("ma20","distance_ma20_abs_atr"),("ma50","distance_ma50_abs_atr"),("ma200","distance_ma200_abs_atr")]:
        q=_bucket(df,col,["risk_state"],5)
        if not q.empty:
            q["support_method"]=method; robustness.append(q)
    tables["support_robustness"]=pd.concat(robustness,ignore_index=True) if robustness else pd.DataFrame()
    consistency=[]
    for f in ["swing_distance_atr","distance_ma20_atr","distance_ma50_atr","distance_ma200_atr","range20","lower_wick_atr","volume_ratio20"]:
        signs=[]
        for run,g in df.groupby("run"):
            q=_bucket(g,f,[],5)
            if len(q)>=3: signs.append(np.sign(q.bucket_rank.corr(q.stop_rate,method="spearman")))
        consistency.append({"feature":f,"positive_risk_direction_count":sum(x>0 for x in signs),"symbols_tested":len(signs),"classification":"CONSISTENT" if len(signs)>=4 and sum(x>0 for x in signs)>=4 else "MOSTLY_CONSISTENT" if len(signs)>=3 and sum(x>0 for x in signs)>=3 else "MIXED" if signs else "INSUFFICIENT_DATA"})
    tables["cross_symbol_consistency"]=pd.DataFrame(consistency)
    df.to_csv(output_dir/"entry_context_enriched_pass_trades.csv",index=False)
    for name,frame in tables.items(): frame.to_csv(output_dir/f"entry_context_{name}.csv",index=False)
    return tables


if __name__=="__main__":
    print("generated",len(run()),"entry-context tables")
