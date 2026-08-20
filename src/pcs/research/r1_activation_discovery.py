"""Research-only R1 activation discovery on Batch A.

This module intentionally uses Batch A only. Batch B remains sealed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


BATCH_A = "SPY SMH XLK XLF XLE IWM GLD TLT JPM BAC GS XOM CAT BA GE COST HD DIS NFLX CRM ORCL CSCO TSM MRVL LLY UNH COIN HOOD F GM".split()
OUT_DIR = Path("research_outputs/r1_activation_discovery")
SRC_DIR = Path("research_outputs/batch_a_r1_validation")
DAILY_DIR = Path("data/parquet/daily")


def _daily(symbol: str) -> pd.DataFrame:
    df = pd.read_parquet(DAILY_DIR / f"symbol={symbol}")
    df = df.rename(
        columns={
            "日期": "date",
            "开盘价": "open",
            "最高价": "high",
            "最低价": "low",
            "收盘价": "close",
            "成交量": "volume",
        }
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def _add_basic_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    prev_close = d["close"].shift()
    tr = pd.concat(
        [
            d["high"] - d["low"],
            (d["high"] - prev_close).abs(),
            (d["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    d["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    d["atr_pct"] = d["atr14"] / d["close"]
    d["atr_expansion"] = d["atr14"] / d["atr14"].rolling(60, min_periods=20).median()
    d["sma20"] = d["close"].rolling(20, min_periods=20).mean()
    d["sma50"] = d["close"].rolling(50, min_periods=50).mean()
    d["sma200"] = d["close"].rolling(200, min_periods=200).mean()
    d["ma20_slope_21"] = (d["sma20"] / d["sma20"].shift(21)) - 1
    d["ma50_slope_21"] = (d["sma50"] / d["sma50"].shift(21)) - 1
    d["ret1"] = d["close"].pct_change()
    d["rv20"] = d["ret1"].rolling(20, min_periods=15).std() * np.sqrt(252)
    d["ret21_atr"] = (d["close"] - d["close"].shift(21)) / d["atr14"]
    d["ret63_atr"] = (d["close"] - d["close"].shift(63)) / d["atr14"]
    d["drawdown63"] = 1 - d["close"] / d["close"].rolling(63, min_periods=20).max()
    d["up_day_frac_63"] = d["ret1"].gt(0).rolling(63, min_periods=20).mean()
    d["gap_abs_63"] = ((d["open"] - prev_close).abs() / prev_close).rolling(63, min_periods=20).mean()
    return d


def _period_label(classification: str) -> str:
    if classification in {"STRONG_USE", "MODERATE_USE"}:
        return "USEFUL"
    if classification in {"NEUTRAL", "AVOID"}:
        return "NOT_USEFUL"
    if classification == "WEAK_USE":
        return "WEAK"
    return "EXCLUDE"


def _period_features(symbol: str, metrics: pd.DataFrame, pass_rows: pd.DataFrame) -> list[dict]:
    daily = _add_basic_features(_daily(symbol))
    pass_dates = set(pd.to_datetime(pass_rows["date"]).dt.normalize())
    daily["trend_pass_proxy"] = daily["date"].dt.normalize().isin(pass_dates).astype(float)
    rows: list[dict] = []
    for row in metrics.itertuples(index=False):
        period = str(row.period)
        if period == "FULL":
            continue
        try:
            year = int(period)
        except ValueError:
            continue
        label = _period_label(getattr(row, "classification", ""))
        start = pd.Timestamp(year=year, month=1, day=1)
        hist = daily[daily["date"] < start].tail(126)
        if len(hist) < 80:
            continue
        last = hist.iloc[-1]
        rows.append(
            {
                "ticker": symbol,
                "period": year,
                "r1_n": row.r1_n,
                "non_r1_n": row.non_r1_n,
                "classification": getattr(row, "classification", ""),
                "label": label,
                "pass_frac_126": hist["trend_pass_proxy"].mean(),
                "ma_bull_aligned": float(last["sma20"] > last["sma50"] > last["sma200"]),
                "ma20_slope_21": last["ma20_slope_21"],
                "ma50_slope_21": last["ma50_slope_21"],
                "atr_pct_median_63": hist["atr_pct"].tail(63).median(),
                "atr_pct_median_126": hist["atr_pct"].median(),
                "atr_expansion_median_63": hist["atr_expansion"].tail(63).median(),
                "rv20_median_63": hist["rv20"].tail(63).median(),
                "ret21_atr": last["ret21_atr"],
                "ret63_atr": last["ret63_atr"],
                "drawdown63": last["drawdown63"],
                "up_day_frac_63": hist["up_day_frac_63"].tail(63).median(),
                "gap_abs_63": hist["gap_abs_63"].tail(63).median(),
                "diff_5d_mae": row.diff_5d_mae,
                "diff_10d_mae": row.diff_10d_mae,
                "diff_5d_breach_pp": row.diff_5d_breach_pp,
                "diff_10d_breach_pp": row.diff_10d_breach_pp,
            }
        )
    return rows


def _summaries(features: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    eval_df = features[features["label"].isin(["USEFUL", "NOT_USEFUL"])].copy()
    cols = [
        "pass_frac_126",
        "ma_bull_aligned",
        "ma20_slope_21",
        "ma50_slope_21",
        "atr_pct_median_63",
        "atr_expansion_median_63",
        "rv20_median_63",
        "ret21_atr",
        "ret63_atr",
        "drawdown63",
        "up_day_frac_63",
        "gap_abs_63",
    ]
    summary_rows = []
    for col in cols:
        useful = eval_df.loc[eval_df["label"].eq("USEFUL"), col].dropna()
        bad = eval_df.loc[eval_df["label"].eq("NOT_USEFUL"), col].dropna()
        summary_rows.append(
            {
                "feature": col,
                "useful_n": len(useful),
                "not_useful_n": len(bad),
                "useful_median": useful.median(),
                "not_useful_median": bad.median(),
                "median_diff_useful_minus_not": useful.median() - bad.median(),
                "useful_p25": useful.quantile(0.25),
                "useful_p75": useful.quantile(0.75),
                "not_useful_p25": bad.quantile(0.25),
                "not_useful_p75": bad.quantile(0.75),
            }
        )
    summary = pd.DataFrame(summary_rows)

    # One deliberately simple, interpretable candidate: persistent bullish trend
    # without strong upside acceleration or elevated volatility.
    eval_df["simple_active"] = (
        (eval_df["pass_frac_126"] >= 0.55)
        & (eval_df["ma_bull_aligned"] >= 1)
        & (eval_df["ma50_slope_21"] >= 0)
        & (eval_df["ret63_atr"] <= 8.0)
        & (eval_df["atr_expansion_median_63"] <= 1.15)
    )
    confusion = (
        eval_df.groupby(["simple_active", "label"])
        .size()
        .reset_index(name="periods")
        .sort_values(["simple_active", "label"])
    )
    by_ticker = (
        eval_df.groupby(["ticker", "simple_active", "label"])
        .size()
        .reset_index(name="periods")
        .sort_values(["ticker", "simple_active", "label"])
    )
    return summary, confusion, by_ticker


def run() -> dict[str, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for symbol in BATCH_A:
        metrics_path = SRC_DIR / f"{symbol}_metrics.csv"
        rows_path = SRC_DIR / f"{symbol}_rows.csv"
        if not metrics_path.exists() or not rows_path.exists():
            continue
        rows.extend(_period_features(symbol, pd.read_csv(metrics_path), pd.read_csv(rows_path)))
    features = pd.DataFrame(rows)
    summary, confusion, by_ticker = _summaries(features)
    feature_path = OUT_DIR / "batch_a_period_activation_features.csv"
    summary_path = OUT_DIR / "feature_separation_summary.csv"
    confusion_path = OUT_DIR / "simple_rule_confusion.csv"
    by_ticker_path = OUT_DIR / "simple_rule_by_ticker.csv"
    features.to_csv(feature_path, index=False)
    summary.to_csv(summary_path, index=False)
    confusion.to_csv(confusion_path, index=False)
    by_ticker.to_csv(by_ticker_path, index=False)
    return {
        "features": feature_path,
        "summary": summary_path,
        "confusion": confusion_path,
        "by_ticker": by_ticker_path,
    }


if __name__ == "__main__":
    paths = run()
    for name, path in paths.items():
        print(f"{name}: {path}")
