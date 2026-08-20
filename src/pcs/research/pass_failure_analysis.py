"""Research-only failure analysis for Trend Gate PASS trades."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pandas as pd


def classify_trade(trade):
    events = trade.get("events", {})
    stop, profit = events.get("stop"), events.get("profit50")
    if stop is not None and (profit is None or stop < profit):
        return "FAIL"
    if profit is not None and (stop is None or profit <= stop):
        return "SUCCESS"
    return "NEITHER"


def _as_date(value):
    return pd.Timestamp(value).normalize()


def _series_until(df, date):
    out = df.copy(deep=True)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    return out[out["date"] <= _as_date(date)].sort_values("date").reset_index(drop=True)


def entry_features(ohlcv, entry_date, benchmark=None, semiconductor=None):
    """Calculate features using rows no later than entry_date."""
    df = _series_until(ohlcv, entry_date)
    if len(df) < 15:
        return {}
    close = pd.to_numeric(df["close"], errors="coerce")
    prev_close = close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - prev_close).abs(), (df.low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    ret = close.pct_change()
    i = len(df) - 1
    result = {"entry_date": df.date.iloc[i], "one_day_return": ret.iloc[i],
              "candle_body_pct": abs(df.close.iloc[i] - df.open.iloc[i]) / close.iloc[i],
              "gap_pct": abs(df.open.iloc[i] - prev_close.iloc[i]) / prev_close.iloc[i],
              "range_atr": (df.high.iloc[i] - df.low.iloc[i]) / atr.iloc[i],
              "atr_pct": atr.iloc[i] / close.iloc[i], "realized_vol_5d": ret.iloc[max(0, i - 4):i + 1].std(),
              "realized_vol_10d": ret.iloc[max(0, i - 9):i + 1].std()}
    for n in (3, 5, 10):
        result[f"return_{n}d"] = close.iloc[i] / close.iloc[i - n] - 1 if i >= n else None
    for name, source in (("qqq", benchmark), ("semiconductor", semiconductor)):
        if source is None:
            continue
        b = _series_until(source, entry_date).set_index("date")["close"]
        for n in (1, 3, 5):
            dates = df.date.iloc[i]
            prior = df.date.iloc[max(0, i - n)]
            if dates in b.index and prior in b.index:
                result[f"{name}_return_{n}d"] = b.loc[dates] / b.loc[prior] - 1
        if f"{name}_return_1d" in result:
            result[f"relative_vs_{name}_1d"] = result["one_day_return"] - result[f"{name}_return_1d"]
    return result


def path_features(trade, ohlcv, benchmark=None, semiconductor=None):
    entry = _as_date(trade["date"])
    stop_day = trade.get("events", {}).get("stop")
    if stop_day is None:
        return {}
    stop_day = _as_date(stop_day)
    path = _series_until(ohlcv, stop_day)
    path = path[path.date >= entry].reset_index(drop=True)
    if path.empty:
        return {}
    entry_close = float(path.close.iloc[0]); stop_close = float(path.close.iloc[-1])
    one_day = path.close.pct_change()
    two_day = path.close.pct_change(2)
    out = {"return_to_stop": stop_close / entry_close - 1, "mae_pct": (path.low.min() / entry_close) - 1,
           "max_1d_drop": one_day.min(), "max_2d_drop": two_day.min(), "days_to_stop": trade.get("days_held")}
    if benchmark is not None:
        b = _series_until(benchmark, stop_day).set_index("date")["close"]
        if entry in b.index and stop_day in b.index: out["qqq_return_to_stop"] = b.loc[stop_day] / b.loc[entry] - 1
    if semiconductor is not None:
        b = _series_until(semiconductor, stop_day).set_index("date")["close"]
        if entry in b.index and stop_day in b.index: out["semiconductor_return_to_stop"] = b.loc[stop_day] / b.loc[entry] - 1
    return out


def timing_bucket(days):
    if days <= 3: return "day_1_3"
    if days <= 7: return "day_4_7"
    return "day_8_plus"


def enrich_pass_trades(trades, ohlcv, benchmark=None, semiconductor=None):
    out = []
    for original in trades:
        if original.get("trend_gate") != "PASS" and original.get("current_state") != "PASS":
            continue
        row = deepcopy(original); row["outcome_class"] = classify_trade(row)
        row.update(entry_features(ohlcv, row["date"], benchmark, semiconductor))
        if row["outcome_class"] == "FAIL":
            row.update(path_features(row, ohlcv, benchmark, semiconductor))
            row["stop_timing_bucket"] = timing_bucket(row["days_held"])
        out.append(row)
    return out
