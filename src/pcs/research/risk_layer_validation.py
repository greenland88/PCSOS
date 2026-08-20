"""Research-only PCS path-risk layer prototype and validation.

The output is informational.  It is deliberately disconnected from PCS
entry, sizing, strike, stop, roll, and live-agent code.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .path_risk_validation import FEATURES, OUT, RUNS, _stats, load_feature_trades


TIER1 = ["atr_expansion", "drawdown20", "down_streak"]
TIER2 = ["atr_pct", "move5_atr"]
MODEL_FEATURES = {
    "A": ["atr_expansion"],
    "B": ["atr_expansion", "drawdown20"],
    "C": TIER1,
    "D": TIER1 + ["atr_pct"],
    "E": TIER1 + ["atr_pct", "move5_atr"],
}
STATE_ORDER = {"R1_NORMAL": 1, "R2_ELEVATED": 2, "R3_DEFENSIVE": 3, "R4_HIGH_RISK": 4}


def _state(score: float | None) -> str | None:
    if score is None or not np.isfinite(score):
        return None
    if score < 0.25:
        return "R1_NORMAL"
    if score < 0.50:
        return "R2_ELEVATED"
    if score < 0.75:
        return "R3_DEFENSIVE"
    return "R4_HIGH_RISK"


def _walk_forward(df: pd.DataFrame, features: list[str], min_history: int = 50) -> pd.Series:
    """Return a pooled expanding-percentile score with strict date ordering."""
    ordered = df.sort_values(["date", "run"]).copy()
    history: dict[str, list[float]] = {f: [] for f in features}
    values: dict[int, float] = {}
    for date, day in ordered.groupby("date", sort=True):
        for idx, row in day.iterrows():
            ranks = []
            for f in features:
                value = row[f]
                if pd.isna(value) or len(history[f]) < min_history:
                    continue
                arr = np.asarray(history[f], dtype=float)
                ranks.append(float((arr < value).sum() / len(arr)))
            if len(ranks) == len(features):
                tier1 = [ranks[features.index(f)] for f in features if f in TIER1]
                tier2 = [ranks[features.index(f)] for f in features if f in TIER2]
                values[idx] = float(np.mean(tier1) if not tier2 else np.mean(tier1) * 0.67 + np.mean(tier2) * 0.33)
        # Same-date observations are not available to one another.
        for f in features:
            history[f].extend(day[f].dropna().astype(float).tolist())
    return pd.Series(values, dtype=float)


def _summary(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows = []
    for keys, g in df.groupby(group_cols, dropna=False):
        vals = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(group_cols, vals))
        row.update(_stats(g))
        row["median_days_to_stop"] = np.nan
        row["median_days_to_profit50"] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def _feature_definitions() -> pd.DataFrame:
    return pd.DataFrame([
        {"feature": "atr_expansion", "tier": "Tier1", "definition": "entry-day ATR14 divided by the trailing 60-trading-day median ATR14", "lookback": "60", "higher_is_risk": True, "direction": "non-directional volatility expansion", "lookahead": "prior/current entry-day values only"},
        {"feature": "drawdown20", "tier": "Tier1", "definition": "1 - entry-day close / rolling 20-trading-day maximum close", "lookback": "20", "higher_is_risk": True, "direction": "down from recent high", "lookahead": "entry-day close included; no future bars"},
        {"feature": "down_streak", "tier": "Tier1", "definition": "number of consecutive strictly lower closes ending on entry day", "lookback": "unbounded run", "higher_is_risk": True, "direction": "downside", "lookahead": "entry-day close only"},
        {"feature": "atr_pct", "tier": "Tier2", "definition": "entry-day ATR14 / entry-day close", "lookback": "14 for ATR", "higher_is_risk": True, "direction": "non-directional volatility", "lookahead": "entry-day close only"},
        {"feature": "move5_atr", "tier": "Tier2", "definition": "absolute entry close change over five trading days / entry-day ATR14", "lookback": "5", "higher_is_risk": True, "direction": "absolute shock magnitude, not pure downside direction", "lookahead": "entry-day and prior closes only"},
    ])


def run(output_dir: Path = OUT) -> dict[str, pd.DataFrame]:
    all_trades = load_feature_trades()
    key = ["run", "date", "expiration", "short_strike", "long_strike"]
    dedup = all_trades.drop_duplicates(key, keep="first").copy()
    passes = dedup[dedup["trend_gate"].eq("PASS")].copy()
    passes = passes.sort_values(["date", "run"]).reset_index(drop=True)

    for model, features in MODEL_FEATURES.items():
        score = _walk_forward(passes, features)
        passes[f"risk_score_{model}"] = score.reindex(passes.index)
        passes[f"risk_state_{model}"] = passes[f"risk_score_{model}"].map(_state)
    passes["risk_score"] = passes["risk_score_E"]
    passes["risk_state"] = passes["risk_state_E"]

    tables: dict[str, pd.DataFrame] = {}
    tables["trade_count_audit"] = pd.DataFrame([
        {"source_trade_count": len(all_trades), "deduplicated_trade_count": len(dedup), "pass_trade_count": len(passes), "nvda_count": (dedup.run == "NVDA").sum(), "qqq_2020_2022_count": (dedup.run == "QQQ_2020_2022").sum(), "qqq_2023_2026_count": (dedup.run == "QQQ_2023_2026").sum(), "amzn_count": (dedup.run == "AMZN").sum(), "tsla_count": (dedup.run == "TSLA").sum()}
    ])
    tables["feature_definitions"] = _feature_definitions()
    tables["state_summary"] = _summary(passes.dropna(subset=["risk_state"]), ["risk_state"])
    tables["symbol_summary"] = _summary(passes.dropna(subset=["risk_state"]), ["run", "risk_state"])

    regimes = {
        "QQQ_2020_2022": {"2020_2022": ("2020-01-01", "2022-12-31")},
        "QQQ_2023_2026": {"2023_2024": ("2023-01-01", "2024-12-31"), "2025_2026": ("2025-01-01", "2026-07-31")},
        "AMZN": {"2023_2024": ("2023-01-01", "2024-12-31"), "2025_2026": ("2025-01-01", "2026-07-31")},
        "TSLA": {"2023_2024": ("2023-01-01", "2024-12-31"), "2025_2026": ("2025-01-01", "2026-07-31")},
        "NVDA": {"2024_2025": ("2024-06-10", "2025-12-31"), "2026": ("2026-01-01", "2026-07-31")},
    }
    rr = []
    for run, periods in regimes.items():
        for name, (start, end) in periods.items():
            g = passes[(passes.run == run) & passes.date.between(start, end)].dropna(subset=["risk_state"])
            for state, s in g.groupby("risk_state"):
                r = {"run": run, "regime": name, "risk_state": state}; r.update(_stats(s)); rr.append(r)
    tables["regime_summary"] = pd.DataFrame(rr)

    ind = []
    work = passes.dropna(subset=["risk_state", "trend_score"]).copy()
    work["trend_score_band"] = pd.qcut(work["trend_score"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
    for band, g in work.groupby("trend_score_band", observed=False):
        for state, s in g.groupby("risk_state"):
            r = {"trend_score_band": str(band), "risk_state": state}; r.update(_stats(s)); ind.append(r)
    tables["trend_score_independence"] = pd.DataFrame(ind)

    ab = []
    for model in MODEL_FEATURES:
        g = passes.dropna(subset=[f"risk_state_{model}"]).copy()
        state_rank = g[f"risk_state_{model}"].replace(STATE_ORDER).astype(float)
        r = {"model": model, "features": "+".join(MODEL_FEATURES[model]), "scored_pass_trades": len(g), "spearman_stop": state_rank.corr(g["stop"].astype(float), method="spearman"), "spearman_pnl": state_rank.corr(g["realized_pnl"], method="spearman")}
        g2 = g.drop(columns=["risk_state"], errors="ignore").copy()
        g2["risk_state"] = g[f"risk_state_{model}"].to_numpy()
        by = _summary(g2, ["risk_state"])
        r["r1_stop"] = by.loc[by.risk_state.eq("R1_NORMAL"), "stop_rate"].iloc[0] if (by.risk_state == "R1_NORMAL").any() else np.nan
        r["r4_stop"] = by.loc[by.risk_state.eq("R4_HIGH_RISK"), "stop_rate"].iloc[0] if (by.risk_state == "R4_HIGH_RISK").any() else np.nan
        ab.append(r)
    tables["ablation"] = pd.DataFrame(ab)

    cc = _summary(passes.dropna(subset=["risk_state"]), ["risk_state"])[["risk_state", "n", "stop_rate", "profit_factor", "avg_pnl"]]
    cc["median_credit_width"] = passes.groupby("risk_state")["credit_width_ratio"].median().reindex(cc.risk_state).to_numpy()
    tables["credit_compensation"] = cc

    # Explicitly persist the scored shadow population; no production consumer reads it.
    passes.to_csv(output_dir / "risk_layer_scored_pass_trades.csv", index=False)
    for name, frame in tables.items():
        frame.to_csv(output_dir / f"risk_layer_{name}.csv", index=False)
    return tables


if __name__ == "__main__":
    result = run()
    print("generated", len(result), "risk-layer tables")
