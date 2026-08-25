"""Research-only empirical safe-strike risk map.

Batch B is intentionally absent from this module.  This diagnostic uses only
OHLCV, current Trend PASS dates, fixed ATR buffers, and existing support output.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
from pcs.data.access import PCSDataAccess

from .batch_trend_history_fast import build_fast_batch_trend_history


DEVELOPMENT_13 = "NVDA QQQ AMZN TSLA AAPL MSFT META GOOGL AVGO AMD INTC MU AMAT".split()
BATCH_A = "SPY SMH XLK XLF XLE IWM GLD TLT JPM BAC GS XOM CAT BA GE COST HD DIS NFLX CRM ORCL CSCO TSM MRVL LLY UNH COIN HOOD F GM".split()
SYMBOLS = list(dict.fromkeys(DEVELOPMENT_13 + BATCH_A))
REPO_ROOT = Path(__file__).resolve().parents[3]
ETF_GROUP = set("SPY QQQ SMH XLK XLF XLE IWM".split())
HIGH_VOL_GROUP = set("NVDA AMD TSLA COIN HOOD MU".split())

BUFFERS = (1.5, 2.0, 2.5, 3.0)
HORIZONS = (5, 10, 20)

OUT = REPO_ROOT / "research_outputs/safe_strike_risk_map_v0_1"
TREND_OUT = OUT / "trend_histories"
DAILY_DIR = REPO_ROOT / "data/parquet/daily"


def _daily(symbol: str) -> pd.DataFrame:
    return PCSDataAccess().read_prices(symbol).sort_values("date").drop_duplicates("date").reset_index(drop=True)


def _trend(symbol: str, benchmark: pd.DataFrame) -> pd.DataFrame:
    TREND_OUT.mkdir(parents=True, exist_ok=True)
    path = TREND_OUT / f"{symbol}_trend.parquet"
    if path.exists():
        return pd.read_parquet(path)
    trend, runtime = build_fast_batch_trend_history(
        _daily(symbol), benchmark, symbol=symbol, benchmark_symbol="QQQ"
    )
    trend["runtime_seconds"] = runtime
    trend.to_parquet(path, index=False)
    print({"ticker": symbol, "trend_rows": len(trend), "trend_runtime": runtime}, flush=True)
    return trend


def _support_price(payload: str | float | None) -> tuple[float | None, str]:
    if payload is None or pd.isna(payload):
        return None, "NO_SUPPORT"
    try:
        data = json.loads(payload)
    except Exception:
        return None, "NO_SUPPORT"
    price = data.get("nearest_support")
    if price is None or pd.isna(price):
        return None, "NO_SUPPORT"
    return float(price), data.get("nearest_support_type") or "SUPPORT"


def _build_observations(symbol: str, trend: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    merged = daily.merge(
        trend[["date", "trend_gate", "atr14", "support"]],
        on="date",
        how="left",
        suffixes=("", "_trend"),
    )
    lows = merged["low"].to_numpy(dtype=float)
    closes = merged["close"].to_numpy(dtype=float)
    opens = merged["open"].to_numpy(dtype=float)
    dates = pd.to_datetime(merged["date"]).to_numpy()
    rows = []
    pass_idx = merged.index[merged["trend_gate"].eq("PASS") & merged["atr14"].notna() & merged["atr14"].gt(0)]
    for pos in pass_idx:
        close = float(closes[pos])
        atr = float(merged.at[pos, "atr14"])
        support, support_type = _support_price(merged.at[pos, "support"])
        support_below = support is not None and support < close
        for k in BUFFERS:
            strike = close - k * atr
            base = {
                "ticker": symbol,
                "date": pd.Timestamp(dates[pos]),
                "year": pd.Timestamp(dates[pos]).year,
                "buffer_atr": k,
                "close": close,
                "atr14": atr,
                "strike": strike,
                "mae_atr": np.nan,
            }
            for h in HORIZONS:
                end = pos + h
                complete = end < len(merged)
                if complete:
                    sl = slice(pos + 1, end + 1)
                    base[f"complete_{h}d"] = True
                    base[f"touch_{h}d"] = bool(np.nanmin(lows[sl]) <= strike)
                    base[f"close_breach_{h}d"] = bool(np.nanmin(closes[sl]) <= strike)
                    base[f"gap_through_{h}d"] = bool(np.nanmin(opens[sl]) <= strike)
                    base[f"mae_{h}d_atr"] = (close - float(np.nanmin(lows[sl]))) / atr
                else:
                    base[f"complete_{h}d"] = False
                    base[f"touch_{h}d"] = np.nan
                    base[f"close_breach_{h}d"] = np.nan
                    base[f"gap_through_{h}d"] = np.nan
                    base[f"mae_{h}d_atr"] = np.nan
            rows.append(base)
        support_strike = close - 2.0 * atr
        if support_below:
            support_strike = min(support_strike, support - 0.5 * atr)
        rows.append(
            _support_row(symbol, merged, pos, close, atr, support, support_type, support_strike)
        )
    return pd.DataFrame(rows)


def _support_row(symbol: str, merged: pd.DataFrame, pos: int, close: float, atr: float, support: float | None, support_type: str, strike: float) -> dict:
    lows = merged["low"].to_numpy(dtype=float)
    closes = merged["close"].to_numpy(dtype=float)
    opens = merged["open"].to_numpy(dtype=float)
    date = pd.Timestamp(merged.at[pos, "date"])
    row = {
        "ticker": symbol,
        "date": date,
        "year": date.year,
        "support_status": support_type if support is not None and support < close else "NO_SUPPORT",
        "nearest_support": support,
        "support_distance_atr": (close - support) / atr if support is not None and support < close else np.nan,
        "support_aware_strike": strike,
        "effective_distance_atr": (close - strike) / atr,
        "additional_atr_distance": (close - strike) / atr - 2.0,
    }
    for h in HORIZONS:
        end = pos + h
        complete = end < len(merged)
        if complete:
            sl = slice(pos + 1, end + 1)
            row[f"baseline_touch_{h}d"] = bool(np.nanmin(lows[sl]) <= close - 2.0 * atr)
            row[f"support_touch_{h}d"] = bool(np.nanmin(lows[sl]) <= strike)
            row[f"baseline_close_breach_{h}d"] = bool(np.nanmin(closes[sl]) <= close - 2.0 * atr)
            row[f"support_close_breach_{h}d"] = bool(np.nanmin(closes[sl]) <= strike)
            row[f"baseline_gap_through_{h}d"] = bool(np.nanmin(opens[sl]) <= close - 2.0 * atr)
            row[f"support_gap_through_{h}d"] = bool(np.nanmin(opens[sl]) <= strike)
        else:
            for prefix in ("baseline_touch", "support_touch", "baseline_close_breach", "support_close_breach", "baseline_gap_through", "support_gap_through"):
                row[f"{prefix}_{h}d"] = np.nan
    return row


def _stats(group: pd.DataFrame, period: str) -> pd.DataFrame:
    rows = []
    for k, g in group.groupby("buffer_atr"):
        row = {"ticker": g["ticker"].iloc[0], "period": period, "buffer_atr": k, "n": len(g)}
        for h in HORIZONS:
            valid = g[g[f"complete_{h}d"].eq(True)]
            row[f"n_{h}d"] = len(valid)
            row[f"touch_rate_{h}d"] = valid[f"touch_{h}d"].mean()
            row[f"close_breach_rate_{h}d"] = valid[f"close_breach_{h}d"].mean()
            row[f"gap_through_rate_{h}d"] = valid[f"gap_through_{h}d"].mean()
            row[f"mae_{h}d_atr_median"] = valid[f"mae_{h}d_atr"].median()
        rows.append(row)
    return pd.DataFrame(rows)


def _target_label(stats: pd.DataFrame, target: float) -> str:
    for k in BUFFERS:
        rate = stats.loc[stats["buffer_atr"].eq(k), "touch_rate_10d"]
        if len(rate) and pd.notna(rate.iloc[0]) and rate.iloc[0] <= target:
            return "<=1.5ATR" if k == 1.5 else f"{k:.1f}ATR"
    return ">3ATR"


def _buffer_targets(stats: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (ticker, period), g in stats.groupby(["ticker", "period"]):
        rows.append(
            {
                "ticker": ticker,
                "period": period,
                "n_10d": int(g["n_10d"].max()) if len(g) else 0,
                "sample_label": _sample_label(int(g["n_10d"].max()) if len(g) else 0),
                "MIN_BUFFER_FOR_20PCT_10D_TOUCH": _target_label(g, 0.20),
                "MIN_BUFFER_FOR_15PCT_10D_TOUCH": _target_label(g, 0.15),
                "MIN_BUFFER_FOR_10PCT_10D_TOUCH": _target_label(g, 0.10),
                "touch_10d_2atr": _rate_at(g, 2.0),
                "touch_10d_3atr": _rate_at(g, 3.0),
            }
        )
    return pd.DataFrame(rows)


def _rate_at(g: pd.DataFrame, k: float) -> float:
    s = g.loc[g["buffer_atr"].eq(k), "touch_rate_10d"]
    return float(s.iloc[0]) if len(s) else np.nan


def _sample_label(n: int) -> str:
    if n < 15:
        return "INSUFFICIENT"
    if n < 30:
        return "SMALL"
    return "USABLE"


def _support_stats(rows: pd.DataFrame, period: str) -> dict:
    out = {"ticker": rows["ticker"].iloc[0], "period": period, "n": len(rows)}
    for h in HORIZONS:
        base = rows[f"baseline_touch_{h}d"].dropna()
        sup = rows[f"support_touch_{h}d"].dropna()
        out[f"baseline_touch_rate_{h}d"] = base.mean() if len(base) else np.nan
        out[f"support_touch_rate_{h}d"] = sup.mean() if len(sup) else np.nan
        out[f"touch_rate_reduction_{h}d"] = out[f"baseline_touch_rate_{h}d"] - out[f"support_touch_rate_{h}d"] if pd.notna(out[f"baseline_touch_rate_{h}d"]) and pd.notna(out[f"support_touch_rate_{h}d"]) else np.nan
    out["mean_effective_distance_atr"] = rows["effective_distance_atr"].mean()
    out["median_effective_distance_atr"] = rows["effective_distance_atr"].median()
    out["mean_additional_atr_distance"] = rows["additional_atr_distance"].mean()
    out["no_support_rate"] = rows["support_status"].eq("NO_SUPPORT").mean()
    return out


def _classify(full_targets: pd.DataFrame, year_targets: pd.DataFrame) -> pd.DataFrame:
    rows = []
    full = full_targets[full_targets["period"].eq("FULL")]
    for r in full.itertuples(index=False):
        years = year_targets[(year_targets["ticker"].eq(r.ticker)) & (year_targets["sample_label"].eq("USABLE"))]
        targets = years["MIN_BUFFER_FOR_15PCT_10D_TOUCH"].tolist()
        distinct = set(targets)
        if r.sample_label == "INSUFFICIENT":
            cls = "INSUFFICIENT"
        elif len(distinct) >= 3 or (">3ATR" in distinct and any(x in distinct for x in ("<=1.5ATR", "2.0ATR"))):
            cls = "REGIME_DEPENDENT"
        elif r.MIN_BUFFER_FOR_15PCT_10D_TOUCH in ("<=1.5ATR", "2.0ATR"):
            cls = "STABLE_LOW_BUFFER"
        elif r.MIN_BUFFER_FOR_15PCT_10D_TOUCH in ("2.5ATR", "3.0ATR"):
            cls = "STABLE_MEDIUM_BUFFER"
        else:
            cls = "STABLE_HIGH_BUFFER"
        rows.append(
            {
                "ticker": r.ticker,
                "safe_buffer_classification": cls,
                "full_15pct_10d_target": r.MIN_BUFFER_FOR_15PCT_10D_TOUCH,
                "full_touch_10d_2atr": r.touch_10d_2atr,
                "usable_years": len(years),
                "year_target_values": "|".join(map(str, targets)),
            }
        )
    return pd.DataFrame(rows)


def run() -> None:
    started = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    benchmark = _daily("QQQ")
    all_buffer = []
    all_support = []
    manifest = []
    for symbol in SYMBOLS:
        try:
            daily = _daily(symbol)
            trend = _trend(symbol, benchmark)
            obs = _build_observations(symbol, trend, daily)
            buffer_obs = obs[obs["buffer_atr"].notna()].copy()
            support_obs = obs[obs["buffer_atr"].isna()].copy()
            all_buffer.append(buffer_obs)
            all_support.append(support_obs)
            manifest.append({"ticker": symbol, "status": "COMPLETE", "pass_rows": int(len(support_obs)), "error": ""})
            print({"ticker": symbol, "status": "COMPLETE", "pass_rows": len(support_obs)}, flush=True)
        except Exception as exc:
            manifest.append({"ticker": symbol, "status": "FAILED", "pass_rows": 0, "error": f"{type(exc).__name__}: {exc}"})
            print(manifest[-1], flush=True)
    buffer_df = pd.concat(all_buffer, ignore_index=True)
    support_df = pd.concat(all_support, ignore_index=True)

    full = pd.concat([_stats(g, "FULL") for _, g in buffer_df.groupby("ticker")], ignore_index=True)
    year = pd.concat([_stats(g, str(year)) for (_, year), g in buffer_df.groupby(["ticker", "year"])], ignore_index=True)
    year["sample_label"] = year["n_10d"].map(_sample_label)
    full["sample_label"] = full["n_10d"].map(_sample_label)

    targets = pd.concat([_buffer_targets(full), _buffer_targets(year)], ignore_index=True)
    support_rows = []
    for ticker, g in support_df.groupby("ticker"):
        support_rows.append(_support_stats(g, "FULL"))
        for year_value, yg in g.groupby("year"):
            support_rows.append(_support_stats(yg, str(year_value)))
    support_comp = pd.DataFrame(support_rows)
    classes = _classify(targets, targets[targets["period"].ne("FULL")])

    full.to_csv(OUT / "safe_strike_ticker_full.csv", index=False)
    year.to_csv(OUT / "safe_strike_ticker_year.csv", index=False)
    targets.merge(classes, on="ticker", how="left").to_csv(OUT / "safe_strike_buffer_targets.csv", index=False)
    support_comp.to_csv(OUT / "safe_strike_support_comparison.csv", index=False)
    pd.DataFrame(manifest).to_csv(OUT / "manifest.csv", index=False)
    print({"runtime_seconds": time.perf_counter() - started, "symbols": len(SYMBOLS)}, flush=True)


if __name__ == "__main__":
    run()
