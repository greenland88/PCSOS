"""Historical stock/option strike scale validation without normalization."""

from __future__ import annotations

import pandas as pd


def atr14(frame):
    close = pd.to_numeric(frame["close"], errors="coerce")
    prev = close.shift(1)
    tr = pd.concat([frame.high - frame.low, (frame.high - prev).abs(), (frame.low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()


def validate_chain_date(symbol, date, stock_close, atr, strikes):
    """Validate one date from a strike array; source arrays are never changed."""
    values = pd.Series(strikes, dtype="float64").dropna()
    values = values[values > 0].sort_values()
    close = float(stock_close); target = close - 2 * float(atr) if pd.notna(atr) else None
    if values.empty or close <= 0 or target is None:
        return {"symbol": symbol, "date": pd.Timestamp(date), "stock_close": close, "atr14": atr, "scale_status": "SUSPICIOUS"}
    nearest = float(values.iloc[(values - close).abs().argmin()]); target_nearest = float(values.iloc[(values - target).abs().argmin()])
    close_dist = abs(nearest - close) / close; target_dist = abs(target_nearest - target) / close
    counts = {f"strikes_within_{pct}pct": int(((values - close).abs() / close <= pct / 100).sum()) for pct in (5, 10, 20)}
    if close_dist <= .05 and target_dist <= .10 and counts["strikes_within_10pct"] >= 1:
        status = "ALIGNED"
    elif close_dist > .20 and counts["strikes_within_20pct"] == 0:
        status = "MISMATCH"
    else:
        status = "SUSPICIOUS"
    return {"symbol": symbol, "date": pd.Timestamp(date), "stock_close": close, "atr14": float(atr), "min_strike": float(values.min()), "max_strike": float(values.max()), "nearest_strike_to_close": nearest, "nearest_close_distance_pct": close_dist, "target_short_2atr": target, "nearest_strike_to_target": target_nearest, "nearest_target_distance_pct": target_dist, **counts, "scale_status": status}


def classify_reliable_range(samples, option_start, option_end):
    """Find a conservative range while ignoring isolated sparse/suspicious dates."""
    frame = pd.DataFrame(samples)
    if frame.empty:
        return {"reliable_start_date": None, "reliable_end_date": None, "reason": "no samples", "excluded_periods": ""}
    mismatch = frame[frame.scale_status == "MISMATCH"]
    start = pd.Timestamp(option_start); end = pd.Timestamp(option_end)
    if len(mismatch) >= 2:
        last = pd.Timestamp(mismatch.date.max())
        later = frame[pd.to_datetime(frame.date) > last]
        if not later.empty and (later.scale_status == "ALIGNED").sum() >= 2:
            start = later.date.min()
    return {"reliable_start_date": start.date().isoformat(), "reliable_end_date": end.date().isoformat(), "reason": "no sustained post-sample scale mismatch; inspect SUSPICIOUS dates", "excluded_periods": ""}
