"""PIT-safe separation of structural trend from current entry phase."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import pandas as pd


class StructuralTrend(str, Enum):
    STRUCTURAL_UPTREND = "STRUCTURAL_UPTREND"
    STRUCTURAL_NEUTRAL = "STRUCTURAL_NEUTRAL"
    STRUCTURAL_DOWNTREND = "STRUCTURAL_DOWNTREND"


class ShortTermPhase(str, Enum):
    CONTINUATION = "CONTINUATION"
    HEALTHY_PULLBACK = "HEALTHY_PULLBACK"
    RECLAIM_UNCONFIRMED = "RECLAIM_UNCONFIRMED"
    RECLAIM_CONFIRMED = "RECLAIM_CONFIRMED"
    BREAKOUT_CONFIRMED = "BREAKOUT_CONFIRMED"
    BREAKOUT_REJECTED = "BREAKOUT_REJECTED"
    UPTREND_EXHAUSTION = "UPTREND_EXHAUSTION"
    DISTRIBUTION = "DISTRIBUTION"
    DOWNTREND_RALLY = "DOWNTREND_RALLY"
    SUPPORT_BREAKDOWN = "SUPPORT_BREAKDOWN"
    RECLAIM_DAY_1 = "RECLAIM_DAY_1"
    FAILED_FOLLOW_THROUGH = "FAILED_FOLLOW_THROUGH"


@dataclass(frozen=True)
class MarketStructureEngineResult:
    available: bool
    feature_max_date: object | None
    structural_trend: str | None
    short_term_phase: str | None
    structural_trend_score: float | None
    momentum_score: float | None
    volume_confirmation_score: float | None
    support_score: float | None
    timing_score: float | None
    higher_high: bool | None
    lower_high: bool | None
    higher_low: bool | None
    lower_low: bool | None
    structural_alignment: bool | None = None
    sma20_slope: float | None = None
    sma50_slope: float | None = None
    ema200_slope: float | None = None
    macd_hist: float | None = None
    macd_hist_change: float | None = None
    reclaim_age: int | None = None
    follow_through_confirmed: bool | None = None
    close_location: float | None = None
    upper_wick_atr: float | None = None
    upper_rejection: bool | None = None
    sma20_slope_atr_5d: float | None = None
    sma50_slope_atr_5d: float | None = None
    ema200_slope_atr_5d: float | None = None
    rvol20: float | None = None
    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    reason_codes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def build_market_structure_engine(snapshot, ohlcv: pd.DataFrame, as_of_date=None) -> MarketStructureEngineResult:
    """Build only from rows dated <= as_of_date; no future pivot confirmation is used."""
    frame = ohlcv.copy()
    dates = pd.to_datetime(frame["date"] if "date" in frame else frame.index)
    cutoff = pd.to_datetime(as_of_date) if as_of_date is not None else dates.max()
    frame = frame.loc[dates <= cutoff].copy()
    if frame.empty or not getattr(snapshot, "available", False):
        return MarketStructureEngineResult(False, cutoff, None, None, None, None, None, None, None, None, None, None, None)
    ma = snapshot.ma_structure
    ms = snapshot.market_structure
    close = float(frame.close.iloc[-1])
    above20 = getattr(ma, "price_above_sma20", None)
    alignment = getattr(ma, "ma_alignment", "mixed")
    slope50 = getattr(getattr(ma, "sma50_slope_20d", None), "slope_state", None)
    slope20_state = getattr(getattr(ma, "sma20_slope_20d", None), "slope_state", None)
    close_s = frame["close"].astype(float)
    sma20 = close_s.rolling(20, min_periods=20).mean()
    sma50 = close_s.rolling(50, min_periods=50).mean()
    ema200 = close_s.ewm(span=200, adjust=False, min_periods=1).mean()
    slope20 = float(sma20.iloc[-1] - sma20.iloc[-6]) if len(frame) >= 25 and pd.notna(sma20.iloc[-6]) else None
    slope50_value = float(sma50.iloc[-1] - sma50.iloc[-6]) if len(frame) >= 55 and pd.notna(sma50.iloc[-6]) else None
    slope200_value = float(ema200.iloc[-1] - ema200.iloc[-6]) if len(frame) >= 7 else None
    structural_alignment = bool(pd.notna(sma20.iloc[-1]) and pd.notna(sma50.iloc[-1]) and close > sma20.iloc[-1] > sma50.iloc[-1] > ema200.iloc[-1])
    # MACD direction is change, not only sign.
    macd = close_s.ewm(span=12, adjust=False).mean() - close_s.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    macd_hist = float(hist.iloc[-1]); macd_hist_change = float(hist.iloc[-1] - hist.iloc[-2]) if len(hist) > 1 else None
    true_range = pd.concat([frame.high-frame.low, (frame.high-close_s.shift()).abs(), (frame.low-close_s.shift()).abs()], axis=1).max(axis=1)
    atr = true_range.rolling(14, min_periods=14).mean(); atr_value = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else None
    day_range = float(frame.high.iloc[-1] - frame.low.iloc[-1]); close_location = float((close-frame.low.iloc[-1])/day_range) if day_range > 0 else None
    upper_wick = float(frame.high.iloc[-1] - max(frame.open.iloc[-1], close)); upper_wick_atr = upper_wick/atr_value if atr_value else None
    upper_rejection = bool(close_location is not None and close_location <= .35 and upper_wick_atr is not None and upper_wick_atr >= .5)
    slope20_atr = slope20 / atr_value if slope20 is not None and atr_value else None
    slope50_atr = slope50_value / atr_value if slope50_value is not None and atr_value else None
    slope200_atr = slope200_value / atr_value if slope200_value is not None and atr_value else None
    prior_volume = frame["volume"].astype(float).shift(1)
    volume_base = prior_volume.rolling(20, min_periods=20).mean().iloc[-1]
    rvol20 = float(frame.volume.iloc[-1] / volume_base) if pd.notna(volume_base) and volume_base else None
    above20 = close_s > sma20
    reclaim_age = 0
    for value in reversed(above20.fillna(False).tolist()):
        if value: reclaim_age += 1
        else: break
    previous_above20 = bool(above20.iloc[-2]) if len(above20) > 1 and pd.notna(above20.iloc[-2]) else False
    follow_through = reclaim_age >= 2 and not upper_rejection
    negative_slopes = ((slope20 is not None and slope50_value is not None and slope20 < 0 and slope50_value < 0)
                       or (slope20_state in {"falling", "strong_falling"} and slope50 in {"falling", "strong_falling"}))
    # Structural direction is deliberately independent from one-day momentum.
    # A bearish result requires the full long-term structure, not merely a
    # negative histogram or a close below SMA20.
    up_evidence = sum(bool(x) for x in (close > sma50.iloc[-1] if pd.notna(sma50.iloc[-1]) else False,
                                        close > sma20.iloc[-1] if pd.notna(sma20.iloc[-1]) else False,
                                        slope50_atr is not None and slope50_atr > 0,
                                        bool(ms.higher_low)))
    down_evidence = sum(bool(x) for x in (close < sma50.iloc[-1] if pd.notna(sma50.iloc[-1]) else False,
                                          close < sma20.iloc[-1] if pd.notna(sma20.iloc[-1]) else False,
                                          slope50_atr is not None and slope50_atr < 0,
                                          bool(ms.lower_low)))
    if ((close < ema200.iloc[-1] and slope200_atr is not None and slope200_atr < 0 and down_evidence >= 3)
            or (ms.lower_high and ms.lower_low and negative_slopes)):
        structural = StructuralTrend.STRUCTURAL_DOWNTREND.value
    elif (close > ema200.iloc[-1] and slope200_atr is not None and slope200_atr > 0 and up_evidence >= 3):
        structural = StructuralTrend.STRUCTURAL_UPTREND.value
    else:
        structural = StructuralTrend.STRUCTURAL_NEUTRAL.value
    above20_now = bool(above20.iloc[-1]) if len(above20) else False
    if structural == StructuralTrend.STRUCTURAL_DOWNTREND.value:
        phase = ShortTermPhase.DOWNTREND_RALLY.value if above20_now else ShortTermPhase.SUPPORT_BREAKDOWN.value
    elif structural == StructuralTrend.STRUCTURAL_UPTREND.value and not previous_above20 and above20.iloc[-1]:
        phase = ShortTermPhase.FAILED_FOLLOW_THROUGH.value if upper_rejection else ShortTermPhase.RECLAIM_DAY_1.value
    elif structural == StructuralTrend.STRUCTURAL_UPTREND.value and reclaim_age >= 2 and upper_rejection:
        phase = ShortTermPhase.FAILED_FOLLOW_THROUGH.value
    elif structural == StructuralTrend.STRUCTURAL_UPTREND.value and reclaim_age >= 2 and follow_through:
        phase = ShortTermPhase.RECLAIM_CONFIRMED.value
    elif getattr(snapshot.pullback, "pullback_state", None) == "healthy_pullback":
        phase = ShortTermPhase.HEALTHY_PULLBACK.value
    elif getattr(snapshot.pullback, "pullback_state", None) == "extended_uptrend":
        phase = ShortTermPhase.UPTREND_EXHAUSTION.value
    elif ms.structure_state == "bullish":
        phase = ShortTermPhase.CONTINUATION.value
    else:
        phase = ShortTermPhase.RECLAIM_UNCONFIRMED.value if above20_now else ShortTermPhase.DISTRIBUTION.value
    reasons = [f"structural_trend_{structural.lower()}", f"short_term_phase_{phase.lower()}"]
    if ms.lower_high and ms.lower_low: reasons.append("lower_high_lower_low_combination")
    if slope50 in {"falling", "strong_falling"}: reasons.append("sma50_slope_falling")
    reason_codes = tuple(dict.fromkeys(["PIT_VERIFIED", *reasons,
                                        "EMA200_RISING" if slope200_atr is not None and slope200_atr > 0 else "EMA200_NOT_RISING",
                                        "VALID_VOLUME_EVIDENCE" if rvol20 is not None and rvol20 >= .8 else "WEAK_VOLUME_EVIDENCE"]))
    return MarketStructureEngineResult(True, cutoff, structural, phase, None, None, None, None, None,
                                       ms.higher_high, ms.lower_high, ms.higher_low, ms.lower_low,
                                       structural_alignment, slope20, slope50_value, slope200_value,
                                       macd_hist, macd_hist_change, reclaim_age, follow_through,
                                       close_location, upper_wick_atr, upper_rejection,
                                       slope20_atr, slope50_atr, slope200_atr, rvol20,
                                       None, None, None, reason_codes, tuple(reasons))
