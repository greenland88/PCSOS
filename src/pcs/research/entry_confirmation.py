"""Read-only AOI and candle confirmation research helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class EntryConfirmationResult:
    aoi_state: str
    nearest_support_type: str | None
    nearest_support_price: float | None
    distance_to_support_atr: float | None
    bullish_engulfing: bool
    hammer_rejection: bool
    bullish_support_rejection: bool
    volume_confirmation: bool
    confirmation_score: int
    confirmation_state: str

    def to_dict(self):
        return asdict(self)


def _indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = pd.to_numeric(df["close"], errors="coerce")
    high, low = df["high"], df["low"]
    previous_close = close.shift(1)
    tr = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out = pd.DataFrame(index=df.index)
    out["atr14"] = atr
    out["sma20"] = close.rolling(20, min_periods=20).mean()
    out["sma50"] = close.rolling(50, min_periods=50).mean()
    out["sma200"] = close.rolling(200, min_periods=200).mean()
    out["volume_sma20"] = pd.to_numeric(df["volume"], errors="coerce").rolling(20, min_periods=20).mean()
    return out


def analyze_entry_confirmation(ohlcv_df: pd.DataFrame, entry_date=None, indicator_df=None) -> EntryConfirmationResult:
    """Analyze one date using rows through that date only."""
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required.difference(ohlcv_df.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {', '.join(sorted(missing))}")
    source = ohlcv_df.copy(deep=True)
    source["date"] = pd.to_datetime(source["date"], errors="coerce").dt.normalize()
    source = source.sort_values("date").reset_index(drop=True)
    cutoff = source["date"].max() if entry_date is None else pd.Timestamp(entry_date).normalize()
    source = source[source["date"] <= cutoff].reset_index(drop=True)
    if len(source) < 20:
        return EntryConfirmationResult("AOI_NONE", None, None, None, False, False, False, False, 0, "NO_CONFIRMATION")
    # A caller-supplied frame is accepted only when it already represents the
    # same as-of slice; otherwise calculate locally to preserve no-lookahead.
    ind = _indicators(source) if indicator_df is None else indicator_df.iloc[:len(source)].reset_index(drop=True)
    i = len(source) - 1
    current = source.iloc[i]
    atr = float(ind.iloc[i]["atr14"]) if pd.notna(ind.iloc[i]["atr14"]) else None
    if atr is None or atr <= 0:
        return EntryConfirmationResult("AOI_NONE", None, None, None, False, False, False, False, 0, "NO_CONFIRMATION")

    candidates = [("sma20", ind.iloc[i]["sma20"]), ("sma50", ind.iloc[i]["sma50"]), ("sma200", ind.iloc[i]["sma200"])]
    prior = source.iloc[max(0, i - 60):i]
    if not prior.empty:
        candidates.append(("recent_swing_low", prior["low"].min()))
    below = [(name, float(price)) for name, price in candidates if pd.notna(price) and float(price) <= float(current.close)]
    nearest = min(below, key=lambda x: (float(current.close) - x[1]) / atr, default=None)
    distance = (float(current.close) - nearest[1]) / atr if nearest else None
    if distance is None or distance > 1.0:
        aoi = "AOI_NONE"
    elif distance <= 0.5:
        aoi = "AOI_STRONG"
    else:
        aoi = "AOI_MODERATE"

    previous = source.iloc[i - 1]
    body = abs(float(current.close) - float(current.open))
    lower_wick = min(float(current.open), float(current.close)) - float(current.low)
    range_ = float(current.high) - float(current.low)
    engulfing = (float(previous.close) < float(previous.open) and float(current.close) > float(current.open)
                 and float(current.open) <= float(previous.close) and float(current.close) >= float(previous.open))
    hammer = range_ > 0 and lower_wick >= 2 * body and float(current.close) >= float(current.low) + 0.65 * range_
    support_rejection = nearest is not None and distance <= 0.5 and float(current.close) > float(current.open) and range_ > 0 and float(current.close) >= float(current.low) + 0.70 * range_
    volume_confirmation = pd.notna(ind.iloc[i]["volume_sma20"]) and float(current.volume) > float(ind.iloc[i]["volume_sma20"])
    score = sum((engulfing, hammer, support_rejection, volume_confirmation))
    state = "NO_CONFIRMATION" if score <= 1 else "CONFIRMED" if score == 2 else "STRONG_CONFIRMATION"
    return EntryConfirmationResult(aoi, nearest[0] if nearest else None, nearest[1] if nearest else None, distance,
                                   bool(engulfing), bool(hammer), bool(support_rejection), bool(volume_confirmation), score, state)


def attach_confirmations(trades, ohlcv_df):
    """Return copied trade records with research-only AOI fields attached."""
    out = []
    for trade in trades:
        result = analyze_entry_confirmation(ohlcv_df, trade["date"])
        row = dict(trade)
        row.update(result.to_dict())
        out.append(row)
    return out
