"""PIT-safe Trend/Timing opportunity replay.

This module deliberately stops before option selection.  It consumes a complete
daily OHLCV frame and replays the opportunity state machine one session at a
time, so confirmation and window fields cannot depend on later returns.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import pandas as pd

from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.indicators import calculate_base_indicators, calculate_directional_indicators


class OpportunityState(str, Enum):
    NO_SETUP = "NO_SETUP"
    WATCH = "WATCH"
    CONFIRMING = "CONFIRMING"
    ENTRY_READY = "ENTRY_READY"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


TIMING_ENTRY_READY = "TIMING_ENTRY_READY"


@dataclass(frozen=True)
class OpportunitySnapshot:
    symbol: str
    date: str
    feature_min_date: str
    feature_max_date: str
    structural_trend: str
    trend_strength_phase: str
    short_term_phase: str
    opportunity_id: str | None
    opportunity_path: str | None
    opportunity_state: str
    timing_action: str
    primary_support: float | None
    support_type: str | None
    distance_to_support_atr: float | None
    pullback_depth_atr: float | None
    close_location: float | None
    upper_wick_atr: float | None
    upper_rejection: bool
    overheat_flags: tuple[str, ...]
    late_entry: bool
    confirmation_age: int | None
    entry_window_start: str | None
    entry_window_end: str | None
    invalidation_reason: str | None
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    pit_verified: bool
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    ema200: float | None = None
    atr14: float | None = None
    rsi14: float | None = None
    macd_histogram: float | None = None
    macd_histogram_change: float | None = None
    sma20_slope_atr_5d: float | None = None
    sma50_slope_atr_5d: float | None = None
    ema200_slope_atr_5d: float | None = None
    rvol20: float | None = None
    support_evidence: tuple[str, ...] = ()
    setup_date: str | None = None
    confirmation_deadline: str | None = None
    confirmation_date: str | None = None
    invalidation_date: str | None = None
    late_by_atr: float | None = None
    diagnostics: tuple[str, ...] = ()
    higher_high: bool | None = None
    lower_high: bool | None = None
    higher_low: bool | None = None
    lower_low: bool | None = None
    support_candidates: tuple[dict, ...] = ()
    adx14: float | None = None
    plus_di14: float | None = None
    minus_di14: float | None = None
    adx_change_5d: float | None = None
    decision_scope: str = "TREND_TIMING"
    trade_readiness: str = "NOT_EVALUATED"
    decision_timeframe: str = "daily"
    sma20_timeframe: str = "daily"
    sma50_timeframe: str = "daily"
    ema200_timeframe: str = "daily"
    macd_timeframe: str = "daily"
    support_timeframe: str = "daily"

    def to_dict(self) -> dict:
        return asdict(self)


def replay_opportunities(symbol: str, ohlcv: pd.DataFrame,
                         signal_start: object, signal_end: object | None = None,
                         config: TrendIndicatorConfig | None = None,
                         minimum_warmup_rows: int = 200) -> pd.DataFrame:
    """Replay every signal date using only the prefix ending on that date."""
    cfg = config or TrendIndicatorConfig()
    dates = pd.to_datetime(ohlcv["date"], errors="coerce")
    if dates.isna().any() or not dates.is_monotonic_increasing:
        raise ValueError("OHLCV dates must be valid and increasing")
    frame = ohlcv.copy().reset_index(drop=True)
    frame["date"] = dates.dt.normalize()
    start = pd.Timestamp(signal_start).normalize()
    end = pd.Timestamp(signal_end).normalize() if signal_end is not None else frame.date.max()
    signal = frame[frame.date.between(start, end)]
    warmup = int((frame.date < start).sum())
    if warmup < minimum_warmup_rows:
        raise ValueError(f"WARMUP_INSUFFICIENT:{warmup}<{minimum_warmup_rows}")
    records: list[OpportunitySnapshot] = []
    active: dict | None = None
    for idx in signal.index:
        prefix = frame.loc[:idx].copy()
        rec, active = _evaluate_one(symbol, prefix, frame.date.min(), warmup, active, cfg)
        records.append(rec)
    result = pd.DataFrame([r.to_dict() for r in records])
    if result.empty:
        return result
    # Diagnostics are derived from the prefix replay itself; no outcome or
    # post-signal return is consulted.
    result["confirmation_missing"] = result["opportunity_state"].isin(["WATCH", "CONFIRMING"])
    result["entry_window_age"] = result.groupby("opportunity_id", dropna=False).cumcount()
    signal_dates = result["date"].tolist()
    for pos, row in result.iterrows():
        if row["opportunity_state"] == OpportunityState.ENTRY_READY.value:
            result.at[pos, "entry_window_start"] = row["date"]
            result.at[pos, "entry_window_end"] = signal_dates[min(pos + cfg.entry_window_max_sessions - 1, len(signal_dates) - 1)]
    result["diagnostic_flags"] = result.apply(
        lambda r: ";".join(x for x in (
            "MISSED_SETUP" if r.short_term_phase in {"PULLBACK_IN_PROGRESS", "RECLAIM_DAY_1"} and not r.opportunity_id else "",
            "MISSED_CONFIRMATION" if r.opportunity_state == "CONFIRMING" and r.timing_action != TIMING_ENTRY_READY else "",
            "NO_SUPPORTED_PATH" if r.primary_support is None else "",
        ) if x), axis=1)
    return result


def _evaluate_one(symbol, data, feature_min, warmup, active, cfg):
    i = len(data) - 1
    if active and i - int(active.get("index", i)) > cfg.entry_window_max_sessions:
        active = None
    close = data.close.astype(float)
    high, low, op, vol = (data[x].astype(float) for x in ("high", "low", "open", "volume"))
    sma20, sma50 = close.rolling(20).mean(), close.rolling(50).mean()
    ema200 = close.ewm(span=200, adjust=False, min_periods=1).mean()
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    a = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else None
    s20, s50, e200 = (float(x.iloc[i]) if pd.notna(x.iloc[i]) else None for x in (sma20, sma50, ema200))
    sl20 = (float(sma20.iloc[i]-sma20.iloc[i-5])/a if a and pd.notna(sma20.iloc[i-5]) else None)
    sl50 = (float(sma50.iloc[i]-sma50.iloc[i-5])/a if a and pd.notna(sma50.iloc[i-5]) else None)
    sle = (float(ema200.iloc[i]-ema200.iloc[i-5])/a if a and pd.notna(ema200.iloc[i-5]) else None)
    up = bool(e200 and sle is not None and close.iloc[i] > e200 and sle > 0 and sum([
        bool(s50 and close.iloc[i] > s50), bool(s20 and s50 and s20 > s50), bool(sl50 is not None and sl50 > 0),
        bool(_higher_low(data, i, a))]) >= 3)
    down = bool(e200 and sle is not None and close.iloc[i] < e200 and sle < 0 and sum([
        bool(s50 and close.iloc[i] < s50), bool(s20 and s50 and s20 < s50), bool(sl50 is not None and sl50 < 0),
        bool(_lower_low(data, i, a))]) >= 3)
    higher_low = _higher_low(data, i, a)
    lower_low = _lower_low(data, i, a)
    highs = _confirmed_pivot_highs(data, i)
    higher_high = len(highs) >= 2 and highs[-1] > highs[-2] + .25*(a or 0)
    lower_high = len(highs) >= 2 and highs[-1] <= highs[-2] + .25*(a or 0)
    trend = "STRUCTURAL_DOWNTREND" if down else "STRUCTURAL_UPTREND" if up else "STRUCTURAL_NEUTRAL"
    rng = max(float(high.iloc[i]-low.iloc[i]), 1e-12)
    loc = float((close.iloc[i]-low.iloc[i])/rng)
    uw = float((high.iloc[i]-max(op.iloc[i], close.iloc[i]))/a) if a else None
    rejection = bool(uw is not None and uw >= cfg.upper_wick_rejection_atr and loc <= cfg.upper_rejection_close_location)
    prior_vol = vol.shift(1).rolling(20).mean().iloc[i]
    rvol = float(vol.iloc[i]/prior_vol) if pd.notna(prior_vol) and prior_vol else None
    support, stype, support_candidates = _support(close.iloc[i], a, s20, s50, e200, sl20, sl50, data, i, cfg)
    dist = float((close.iloc[i]-support)/a) if support is not None and a else None
    recent_high = close.iloc[max(0,i-9):i+1].max()
    pull_depth = float((recent_high-close.iloc[i])/a) if a else None
    flags = []
    rsi = _rsi(close, i)
    if rsi is not None and rsi >= cfg.rsi_overheated: flags.append("RSI_OVERHEATED")
    if dist is not None and dist >= 2: flags.append("EXTENDED_FROM_SUPPORT")
    if s20 and a and (close.iloc[i]-s20)/a >= 1.75: flags.append("EXTENDED_FROM_SMA20")
    if rejection: flags.append("UPPER_REJECTION")
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    macd_hist = macd_line - macd_line.ewm(span=9, adjust=False).mean()
    mh = float(macd_hist.iloc[i]) if pd.notna(macd_hist.iloc[i]) else None
    mh_change = float(macd_hist.iloc[i]-macd_hist.iloc[i-1]) if i > 0 and pd.notna(macd_hist.iloc[i-1]) else None
    adx_value = plus_di = minus_di = adx_change = None
    try:
        base_indicators = calculate_base_indicators(data, cfg)
        adx_series = base_indicators[f"adx{cfg.adx_period}"]
        directional = calculate_directional_indicators(data, cfg.adx_period)
        plus_series = directional[f"plus_di{cfg.adx_period}"]
        minus_series = directional[f"minus_di{cfg.adx_period}"]
        adx_value = float(adx_series.iloc[i]) if pd.notna(adx_series.iloc[i]) else None
        plus_di = float(plus_series.iloc[i]) if pd.notna(plus_series.iloc[i]) else None
        minus_di = float(minus_series.iloc[i]) if pd.notna(minus_series.iloc[i]) else None
        adx_change = float(adx_series.iloc[i] - adx_series.iloc[i-5]) if i >= 5 and pd.notna(adx_series.iloc[i-5]) and adx_value is not None else None
    except (ImportError, ValueError):
        pass
    date = data.date.iloc[i].date().isoformat()
    phase, state, path, reason = "NO_SETUP", OpportunityState.NO_SETUP.value, None, "NO_SUPPORTED_PATH"
    reclaim_day = bool(trend == "STRUCTURAL_UPTREND" and s20 is not None and i > 0 and
                       close.iloc[i-1] < sma20.iloc[i-1] <= close.iloc[i] - .1*(a or 0))
    if down: phase, reason = "BREAKDOWN", "SUPPORT_BROKEN"
    elif reclaim_day:
        phase, state, path, reason = "RECLAIM_DAY_1", OpportunityState.WATCH.value, "RECLAIM", "RECLAIM_DAY_1"
    elif trend == "STRUCTURAL_UPTREND" and support is not None and stype == "BREAKOUT_RETEST_LEVEL" and pull_depth is not None and .5 <= pull_depth <= 3 and dist is not None and dist >= -.35:
        phase, path, reason = "SUPPORT_RETEST", "SUPPORT_RETEST", "SUPPORT_RETEST"
        state = OpportunityState.WATCH.value
    elif trend == "STRUCTURAL_UPTREND" and support is not None and pull_depth is not None and .5 <= pull_depth <= 3 and dist is not None and dist >= -.35:
        phase, path, reason = "PULLBACK_IN_PROGRESS", "PULLBACK_HELD", "PULLBACK_ACTIVE"
        state = OpportunityState.WATCH.value
        if loc >= .55 and (i == 0 or close.iloc[i] >= close.iloc[i-1]) and not rejection and (rvol is None or rvol >= .8):
            phase, state, reason = "PULLBACK_HELD", OpportunityState.CONFIRMING.value, "PULLBACK_HELD"
    elif reclaim_day:
        phase, state, path, reason = "RECLAIM_DAY_1", OpportunityState.WATCH.value, "RECLAIM", "RECLAIM_DAY_1"
    elif trend == "STRUCTURAL_UPTREND":
        phase, state, reason = "CONTINUATION", OpportunityState.WATCH.value, "STRUCTURE_UP_CONFIRMED"
    # Continue the same setup across sessions.  A setup is not recreated or
    # promoted merely because the next row still resembles the prior row.
    if active and active.get("path") == "PULLBACK_HELD" and not reclaim_day and trend == "STRUCTURAL_UPTREND" and support is not None:
        if rejection or (dist is not None and dist < -.35):
            phase, state, reason = "BREAKOUT_REJECTED", OpportunityState.INVALIDATED.value, "SUPPORT_BROKEN"
        elif loc >= .55 and (i == 0 or close.iloc[i] >= close.iloc[i-1]) and (rvol is None or rvol >= .8):
            phase, path, state, reason = "PULLBACK_HELD", "PULLBACK_HELD", OpportunityState.ENTRY_READY.value, "PULLBACK_CONFIRMED"
        else:
            phase, path, state, reason = "PULLBACK_IN_PROGRESS", "PULLBACK_HELD", OpportunityState.CONFIRMING.value, "CONFIRMATION_MISSING"
    if active and active.get("path") == "SUPPORT_RETEST" and trend == "STRUCTURAL_UPTREND" and support is not None:
        if rejection or (dist is not None and dist < -.35):
            phase, state, reason = "BREAKDOWN", OpportunityState.INVALIDATED.value, "SUPPORT_BROKEN"
        elif loc >= .60 and (rvol is None or rvol >= .8):
            phase, path, state, reason = "SUPPORT_RETEST_CONFIRMED", "SUPPORT_RETEST", OpportunityState.ENTRY_READY.value, "SUPPORT_RETEST_CONFIRMED"
        else:
            phase, path, state, reason = "SUPPORT_RETEST", "SUPPORT_RETEST", OpportunityState.CONFIRMING.value, "CONFIRMATION_MISSING"
    if active and active.get("path") == "RECLAIM" and trend == "STRUCTURAL_UPTREND":
        if rejection:
            phase, state, reason = "FAILED_FOLLOW_THROUGH", OpportunityState.INVALIDATED.value, "FAILED_FOLLOW_THROUGH"
        elif close.iloc[i] >= (s20 or close.iloc[i]) - .1*(a or 0) and (rvol is None or rvol >= .8):
            phase, path, state, reason = "RECLAIM_CONFIRMED", "RECLAIM", OpportunityState.ENTRY_READY.value, "RECLAIM_CONFIRMED"
        else:
            phase, path, state, reason = "RECLAIM_UNCONFIRMED", "RECLAIM", OpportunityState.CONFIRMING.value, "CONFIRMATION_MISSING"
    if phase not in {"FAILED_FOLLOW_THROUGH", "BREAKOUT_REJECTED"} and flags and ("UPPER_REJECTION" in flags or ("RSI_OVERHEATED" in flags and rsi is not None and rsi >= cfg.rsi_hard_block) or len(flags) >= 2):
        phase, state, reason = "LATE_OR_OVERHEATED", OpportunityState.INVALIDATED.value, "OVERHEATED"
    oid = active.get("id") if active else None
    if state in {OpportunityState.WATCH.value, OpportunityState.CONFIRMING.value} and oid is None:
        oid = hashlib.sha1(f"{symbol}:{date}:{path or phase}".encode()).hexdigest()[:16]
        active = {"id": oid, "setup": date, "path": path or phase, "index": i}
    if state == OpportunityState.CONFIRMING.value and active and active.get("path") == "PULLBACK_HELD" and phase == "PULLBACK_HELD":
        if active.get("setup") != date and not flags and support is not None and loc >= .55:
            state, reason = OpportunityState.ENTRY_READY.value, "PULLBACK_CONFIRMED"
    # One selected support object drives both opportunity qualification and
    # distance gating.  Fail closed for missing/non-finite distance.
    distance_invalid = support is None or dist is None or pd.isna(dist)
    if state == OpportunityState.ENTRY_READY.value and (distance_invalid or dist > cfg.maximum_entry_distance_atr):
        state = OpportunityState.WATCH.value if not distance_invalid else OpportunityState.WAIT.value if hasattr(OpportunityState, "WAIT") else OpportunityState.INVALIDATED.value
        reason = "NO_VALID_SUPPORT" if distance_invalid else "ENTRY_TOO_FAR_FROM_SUPPORT"
    action = TIMING_ENTRY_READY if state == OpportunityState.ENTRY_READY.value else "WATCH" if state in {"WATCH","CONFIRMING"} else "WAIT"
    if state in {OpportunityState.INVALIDATED.value, OpportunityState.EXPIRED.value}: active = None
    setup_date = active.get("setup") if active else None
    confirmation_age = (i - int(active.get("index", i))) if active else None
    confirmation_date = date if state == OpportunityState.ENTRY_READY.value else None
    entry_start = confirmation_date
    entry_end = None
    if confirmation_date:
        end_idx = min(i + cfg.entry_window_max_sessions - 1, len(data) - 1)
        entry_end = data.date.iloc[end_idx].date().isoformat()
    return OpportunitySnapshot(symbol, date, pd.Timestamp(feature_min).date().isoformat(), date, trend,
        "ESTABLISHED_TREND" if adx_value is not None and adx_value >= 20 and plus_di is not None and minus_di is not None and plus_di > minus_di else "EMERGING_TREND" if adx_value is not None and adx_value < 20 and adx_change is not None and adx_change > 1.5 and plus_di is not None and minus_di is not None and plus_di > minus_di else "LOW_ADX_RANGE", phase, oid, path, state, action, support, stype, dist,
        pull_depth, loc, uw, rejection, tuple(flags), False, confirmation_age, entry_start,
        entry_end, reason, (reason, "PIT_VERIFIED"), (f"{date}:close", f"{date}:support"), True,
        float(op.iloc[i]), float(high.iloc[i]), float(low.iloc[i]), float(close.iloc[i]), float(vol.iloc[i]),
            s20, s50, e200, a, rsi, mh, mh_change, sl20, sl50, sle, rvol,
        (f"{date}:{stype}" if stype else "NO_VALID_SUPPORT",), setup_date,
        None, confirmation_date, None, None,
        ("NO_SUPPORTED_PATH" if support is None else "",), higher_high, lower_high, higher_low, lower_low,
        tuple(support_candidates), adx_value, plus_di, minus_di, adx_change), active


def _support(c, a, s20, s50, e200, sl20, sl50, data, i, cfg):
    vals = [(s20, "SMA20", sl20), (s50, "SMA50", sl50), (e200, "EMA200", 0)]
    # A pivot at d is available only from d+2 onward.  We inspect only the
    # prefix ending at i, so no future confirmation can leak into the signal.
    lows = data.low.astype(float)
    for d in range(2, max(2, i - 1)):
        if lows.iloc[d] <= lows.iloc[d-1] and lows.iloc[d] <= lows.iloc[d-2] and lows.iloc[d] < lows.iloc[d+1] and lows.iloc[d] < lows.iloc[d+2]:
            if d + 2 <= i:
                vals.append((float(lows.iloc[d]), "CONFIRMED_PIVOT_LOW", 0))
    if i >= 20:
        prior_high = float(data.high.astype(float).iloc[i-20:i].max())
        if c > prior_high + .10*(a or 0):
            vals.append((prior_high, "BREAKOUT_RETEST_LEVEL", 0))
    vals = [(v,n,s) for v,n,s in vals if v is not None and s is not None and s >= 0 and v <= c + .15*(a or 0)]
    if not vals: return None, None, []
    v,n,_ = min(vals, key=lambda x: abs(c-x[0]))
    candidates = [{"support_type": name, "support_price": float(value), "distance_to_close_atr": float((c-value)/(a or 1)),
                   "support_broken": False, "available_date": None} for value, name, _ in vals]
    return v,n,candidates


def _rsi(c, i):
    if i < 14: return None
    d = c.diff(); gain=d.clip(lower=0).rolling(14).mean().iloc[i]; loss=(-d.clip(upper=0)).rolling(14).mean().iloc[i]
    return float(100 if loss == 0 else 100-(100/(1+gain/loss))) if pd.notna(gain) and pd.notna(loss) else None


def _higher_low(data, i, a):
    pivots = _confirmed_pivot_lows(data, i)
    return len(pivots) >= 2 and pivots[-1] >= pivots[-2] - .25*(a or 0)


def _lower_low(data, i, a):
    pivots = _confirmed_pivot_lows(data, i)
    return len(pivots) >= 2 and pivots[-1] < pivots[-2] - .25*(a or 0)


def _confirmed_pivot_lows(data, i):
    lows = data.low.astype(float)
    values = []
    # d+2 is the earliest PIT availability date for a two-bar pivot.
    for d in range(2, max(2, i - 1)):
        if d + 2 > i:
            continue
        if lows.iloc[d] <= lows.iloc[d-1] and lows.iloc[d] <= lows.iloc[d-2] and lows.iloc[d] < lows.iloc[d+1] and lows.iloc[d] < lows.iloc[d+2]:
            values.append(float(lows.iloc[d]))
    return values[-2:]


def _confirmed_pivot_highs(data, i):
    highs = data.high.astype(float)
    values = []
    for d in range(2, max(2, i - 1)):
        if d + 2 > i:
            continue
        if highs.iloc[d] >= highs.iloc[d-1] and highs.iloc[d] >= highs.iloc[d-2] and highs.iloc[d] > highs.iloc[d+1] and highs.iloc[d] > highs.iloc[d+2]:
            values.append(float(highs.iloc[d]))
    return values[-2:]
