"""Offline validation of candidate PCS path-risk features.

This module consumes persisted trades and daily prices only.  It intentionally
does not alter entry, trend, credit, or stop rules.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs"


RUNS = {
    "NVDA": OUT / "nvda_postsplit_trades.json",
    "QQQ_2020_2022": OUT / "qqq_2020_2022" / "backtest_trades.csv",
    "QQQ_2023_2026": OUT / "qqq_2023_2026" / "backtest_trades.csv",
    "AMZN": OUT / "amzn_reliable_2022_2026" / "backtest_trades.csv",
    "TSLA": OUT / "tsla_reliable_2017_2026" / "backtest_trades.csv",
}
SYMBOLS = {"NVDA": "NVDA", "QQQ_2020_2022": "QQQ", "QQQ_2023_2026": "QQQ", "AMZN": "AMZN", "TSLA": "TSLA"}
FEATURES = ["atr_pct", "atr_expansion", "move3_atr", "move5_atr", "drawdown20", "down_streak"]


def _trades(path: Path) -> pd.DataFrame:
    if path.suffix == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
        rows = pd.json_normalize(rows)
    else:
        rows = pd.read_csv(path)
    rows["date"] = pd.to_datetime(rows["date"])
    rows["outcome"] = np.where(rows["exit_reason"].eq("STOP"), "STOP", np.where(rows["exit_reason"].eq("PROFIT50"), "PROFIT50", "OTHER"))
    rows["profit70"] = rows.get("events.profit70", pd.Series(index=rows.index)).notna()
    rows["stop"] = rows["exit_reason"].eq("STOP")
    rows["profit50"] = rows["exit_reason"].eq("PROFIT50")
    return rows


def _daily(symbol: str) -> pd.DataFrame:
    p = ROOT / "data" / "raw" / "daily_forward_adjusted" / f"{symbol}_daily_qfq.csv"
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["日期"])
    df["close"] = pd.to_numeric(df["收盘价"], errors="coerce")
    df = df.sort_values("date").drop_duplicates("date").set_index("date")
    prev = df["close"].shift(1)
    tr = pd.concat([pd.to_numeric(df["最高价"])-pd.to_numeric(df["最低价"]), (pd.to_numeric(df["最高价"])-prev).abs(), (pd.to_numeric(df["最低价"])-prev).abs()], axis=1).max(axis=1)
    # Use the existing trade ATR where available; this is only for derived fields.
    df["atr14_calc"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    df["atr_pct_calc"] = df["atr14_calc"] / df["close"]
    df["atr_expansion"] = df["atr14_calc"] / df["atr14_calc"].rolling(60, min_periods=20).median()
    df["move3_atr"] = (df["close"] - df["close"].shift(3)).abs() / df["atr14_calc"]
    df["move5_atr"] = (df["close"] - df["close"].shift(5)).abs() / df["atr14_calc"]
    df["drawdown20"] = 1 - df["close"] / df["close"].rolling(20, min_periods=5).max()
    down = df["close"].diff().lt(0)
    df["down_streak"] = down.groupby((~down).cumsum()).cumsum().astype(float)
    return df


def load_feature_trades() -> pd.DataFrame:
    frames = []
    daily = {s: _daily(s) for s in set(SYMBOLS.values())}
    for run, path in RUNS.items():
        t = _trades(path)
        t["run"] = run
        t["symbol"] = SYMBOLS[run]
        d = daily[SYMBOLS[run]]
        x = d.reindex(t["date"])
        t["atr_pct"] = t["atr14"] / t["close"]
        t["atr_expansion"] = t["atr14"] / d["atr14_calc"].rolling(60, min_periods=20).median().reindex(t["date"]).to_numpy()
        t["move3_atr"] = ((t["close"] - d["close"].shift(3).reindex(t["date"]).to_numpy()).abs() / t["atr14"])
        t["move5_atr"] = ((t["close"] - d["close"].shift(5).reindex(t["date"]).to_numpy()).abs() / t["atr14"])
        t["drawdown20"] = (1 - t["close"] / d["close"].rolling(20, min_periods=5).max().reindex(t["date"]).to_numpy())
        t["down_streak"] = d["down_streak"].reindex(t["date"]).to_numpy()
        frames.append(t)
    return pd.concat(frames, ignore_index=True)


def _stats(g: pd.DataFrame) -> dict:
    pnl = pd.to_numeric(g["realized_pnl"], errors="coerce")
    pos, neg = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return {"n": len(g), "profit50_rate": g["profit50"].mean(), "profit70_rate": g["profit70"].mean(), "stop_rate": g["stop"].mean(), "avg_pnl": pnl.mean(), "median_pnl": pnl.median(), "profit_factor": pos / neg if neg else np.inf, "avg_winner": pnl[pnl > 0].mean(), "avg_loser": pnl[pnl < 0].mean()}


def _tertile(series: pd.Series) -> pd.Series:
    codes = pd.qcut(series, q=3, labels=False, duplicates="drop")
    return codes.map({0: "low", 1: "mid", 2: "high"})


def _bucket_table(df: pd.DataFrame, group_cols: list[str], feature: str) -> pd.DataFrame:
    out = []
    if not group_cols:
        groups = [((), df)]
    else:
        groups = df.groupby(group_cols, dropna=False)
    for keys, g in groups:
        g = g.dropna(subset=[feature])
        if len(g) < 8:
            continue
        q = min(5, max(2, len(g) // 20))
        try:
            # Rank first so zero-inflated down-streak data still receives
            # deterministic exposure buckets without outcome-driven cuts.
            ranked = g[feature].rank(method="first")
            g = g.assign(bucket=pd.qcut(ranked, q=q, labels=[f"Q{i}" for i in range(1, q+1)], duplicates="drop"))
        except ValueError:
            continue
        for bucket, b in g.groupby("bucket", observed=False):
            values = keys if isinstance(keys, tuple) else (keys,)
            row = dict(zip(group_cols, values))
            row.update({"feature": feature, "bucket": str(bucket), "bucket_rank": int(str(bucket)[1:])})
            row.update(_stats(b)); row["feature_median"] = b[feature].median()
            out.append(row)
    return pd.DataFrame(out)


def run(output_dir: Path = OUT) -> dict[str, pd.DataFrame]:
    df = load_feature_trades()
    passes = df[df["trend_gate"].eq("PASS")].copy()
    tables = {}
    tables["quantiles_global"] = pd.concat([_bucket_table(passes, [], f) for f in FEATURES], ignore_index=True)
    tables["quantiles_symbol"] = pd.concat([_bucket_table(passes, ["run"], f) for f in FEATURES], ignore_index=True)

    # Broad, descriptive boundaries are fixed before looking at outcomes.
    boundaries = {
        "atr_pct": [-np.inf, .015, .025, .04, np.inf],
        "atr_expansion": [-np.inf, .85, 1.0, 1.15, np.inf],
        "move3_atr": [-np.inf, .5, 1.0, 1.5, np.inf],
        "move5_atr": [-np.inf, .75, 1.25, 2.0, np.inf],
        "drawdown20": [-np.inf, .01, .03, .06, np.inf],
        "down_streak": [-np.inf, 0.5, 1.5, 2.5, np.inf],
    }
    fixed = []
    for f, bins in boundaries.items():
        x = passes.dropna(subset=[f]).copy(); x["bucket"] = pd.cut(x[f], bins=bins, labels=["low", "medium", "high", "very_high"])
        for bucket, g in x.groupby("bucket", observed=False):
            r = {"feature": f, "bucket": str(bucket), "boundary": str(bins)}; r.update(_stats(g)); r["feature_median"] = g[f].median(); fixed.append(r)
    tables["fixed_boundaries"] = pd.DataFrame(fixed)

    # Monotonicity on quantile buckets: feature rank is risk rank by construction.
    mono = []
    for f in FEATURES:
        q = tables["quantiles_global"].query("feature == @f").sort_values("bucket_rank")
        mono.append({"feature": f, "spearman_stop_rate": q["bucket_rank"].corr(q["stop_rate"], method="spearman"), "spearman_profit_factor": q["bucket_rank"].corr(q["profit_factor"], method="spearman"), "spearman_avg_pnl": q["bucket_rank"].corr(q["avg_pnl"], method="spearman"), "bucket_count": len(q)})
    tables["monotonicity"] = pd.DataFrame(mono)

    consistency = []
    for f in FEATURES:
        directions = []
        for run, g in passes.groupby("run"):
            q = _bucket_table(g, [], f)
            if q.empty:
                directions.append(np.nan)
                continue
            q = q.sort_values("bucket_rank")
            directions.append(np.sign(q["bucket_rank"].corr(q["stop_rate"], method="spearman")) if len(q) >= 3 else np.nan)
        valid = [x for x in directions if pd.notna(x)]
        label = "INSUFFICIENT_DATA" if len(valid) < 3 else "CONSISTENT" if sum(x > 0 for x in valid) >= 4 else "MOSTLY_CONSISTENT" if sum(x > 0 for x in valid) >= 3 else "MIXED" if sum(x > 0 for x in valid) >= 2 else "INCONSISTENT"
        consistency.append({"feature": f, "consistency": label, "positive_symbol_count": sum(x > 0 for x in valid), "symbols_tested": len(valid)})
    tables["cross_symbol_consistency"] = pd.DataFrame(consistency)

    regimes = {"AMZN": {"2023_2024": ("2023-01-01", "2024-12-31"), "2025_2026": ("2025-01-01", "2026-07-31")}, "QQQ_2020_2022": {"2020_2022": ("2020-01-01", "2022-12-31")}, "QQQ_2023_2026": {"2023_2026": ("2023-01-01", "2026-07-31")}, "TSLA": {"2023_2024": ("2023-01-01", "2024-12-31"), "2025_2026": ("2025-01-01", "2026-07-31")}}
    regime_rows = []
    for run, rs in regimes.items():
        for name, (a, b) in rs.items():
            g = passes[(passes.run == run) & passes.date.between(a, b)]
            for f in FEATURES:
                q = _bucket_table(g, [], f)
                regime_rows.append({"run": run, "regime": name, "feature": f, "n": len(g), "spearman_stop": q["bucket_rank"].corr(q["stop_rate"], method="spearman") if len(q) >= 3 else np.nan, "stop_rate": g.stop.mean(), "feature_median": g[f].median()})
    tables["regime_stability"] = pd.DataFrame(regime_rows)

    combos = [("atr_pct", "atr_expansion"), ("atr_expansion", "drawdown20"), ("drawdown20", "down_streak"), ("move3_atr", "move5_atr"), ("atr_pct", "move5_atr")]
    rows = []
    for a, b in combos:
        x = passes.dropna(subset=[a, b]).copy(); x["a_bucket"] = _tertile(x[a]); x["b_bucket"] = _tertile(x[b])
        for (aa, bb), g in x.groupby(["a_bucket", "b_bucket"], observed=False):
            r = {"feature_a": a, "feature_b": b, "a_bucket": str(aa), "b_bucket": str(bb)}; r.update(_stats(g)); rows.append(r)
    tables["interactions"] = pd.DataFrame(rows)

    # Trend-score matched bands: compare low/high risk halves within score tertiles.
    ind = []
    passes["score_band"] = pd.qcut(passes["trend_score"], 3, labels=["low_score", "mid_score", "high_score"], duplicates="drop")
    for f in ["atr_pct", "atr_expansion", "drawdown20", "down_streak"]:
        for band, g in passes.groupby("score_band", observed=False):
            med = g[f].median();
            for side, h in [("low_feature", g[g[f] <= med]), ("high_feature", g[g[f] > med])]:
                r = {"feature": f, "score_band": str(band), "risk_side": side, "feature_median": h[f].median()}; r.update(_stats(h)); ind.append(r)
    tables["trend_score_independence"] = pd.DataFrame(ind)

    credit = []
    for f in FEATURES:
        q = _bucket_table(passes, [], f)
        for _, r in q.iterrows():
            g = passes[(passes[f] >= passes[f].quantile((int(r.bucket_rank)-1)/5)) & (passes[f] <= passes[f].quantile(int(r.bucket_rank)/5))]
            credit.append({"feature": f, "bucket": r.bucket, "credit_width_median": g.credit_width_ratio.median(), "stop_rate": g.stop.mean(), "profit_factor": _stats(g)["profit_factor"], "n": len(g)})
    tables["credit_compensation"] = pd.DataFrame(credit)

    for name, frame in tables.items():
        frame.to_csv(output_dir / f"path_risk_{name}.csv", index=False)
    df.to_csv(output_dir / "path_risk_enriched_trades.csv", index=False)
    return tables


if __name__ == "__main__":
    result = run()
    print("generated", len(result), "tables")
