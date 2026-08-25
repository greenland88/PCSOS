from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.models import REQUIRED_OHLCV_COLUMNS, TrendIndicatorValidationError


@dataclass(frozen=True)
class SupportResult:
    available: bool
    current_close: Optional[float]
    current_atr: Optional[float]
    supports: list[dict] = field(default_factory=list)
    nearest_support: Optional[float] = None
    nearest_support_type: Optional[str] = None
    nearest_support_distance_pct: Optional[float] = None
    nearest_support_distance_atr: Optional[float] = None
    support_count_nearby: Optional[int] = None
    support_confluence_state: Optional[str] = None
    reasons: tuple[str, ...] = ()
    support_clusters: list[dict] = field(default_factory=list)


def analyze_support(
    ohlcv_df: pd.DataFrame,
    indicator_df: pd.DataFrame,
    market_structure,
    config: TrendIndicatorConfig | None = None,
    as_of_date: object | None = None,
) -> SupportResult:
    config = config or TrendIndicatorConfig()
    config.validate()
    _validate_ohlcv(ohlcv_df)
    _validate_indicators(indicator_df, len(ohlcv_df))
    source = ohlcv_df.copy(deep=True)
    indicators = indicator_df.copy(deep=True)
    dates = _date_values(source)
    cutoff = None
    if as_of_date is not None:
        cutoff = pd.to_datetime(as_of_date, errors="coerce")
        if pd.isna(cutoff):
            raise TrendIndicatorValidationError("as_of_date must be a valid date")
        mask = dates <= cutoff
        source = source.loc[mask].copy(deep=True)
        indicators = indicators.loc[mask].copy(deep=True)
        dates = dates.loc[mask]
    if len(source) < config.pullback_recent_high_lookback:
        return _unavailable_result()
    current_close = float(source["close"].iloc[-1])
    current_atr = float(indicators["atr14"].iloc[-1])
    sma20 = float(indicators["sma20"].iloc[-1])
    sma50 = float(indicators["sma50"].iloc[-1])
    if any(pd.isna(value) for value in (current_close, current_atr, sma20, sma50)) or current_close <= 0 or current_atr <= 0:
        return _unavailable_result()

    candidates = [("sma20", sma20), ("sma50", sma50)]
    candidates.extend(_confirmed_swing_candidates(market_structure, cutoff))
    supports = []
    for support_type, price in candidates:
        if price is None or pd.isna(price):
            continue
        price = float(price)
        distance_pct = (current_close - price) / current_close
        distance_atr = (current_close - price) / current_atr
        active = price <= current_close and (
            distance_atr <= config.support_nearby_atr or distance_pct <= config.support_nearby_pct
        )
        supports.append({
            "type": support_type,
            "price": price,
            "distance_pct": distance_pct,
            "distance_atr": distance_atr,
            "active": bool(active),
        })

    active_supports = [support for support in supports if support["active"]]
    clusters = _cluster_supports(active_supports, current_atr, config.support_cluster_tolerance_atr)
    nearest = min(active_supports, key=lambda support: support["distance_atr"], default=None)
    reasons = _reasons(supports, active_supports, clusters)
    return SupportResult(
        available=True,
        current_close=current_close,
        current_atr=current_atr,
        supports=supports,
        nearest_support=nearest["price"] if nearest else None,
        nearest_support_type=nearest["type"] if nearest else None,
        nearest_support_distance_pct=nearest["distance_pct"] if nearest else None,
        nearest_support_distance_atr=nearest["distance_atr"] if nearest else None,
        support_count_nearby=len(clusters),
        support_confluence_state=_confluence_state(len(clusters), clusters),
        reasons=tuple(reasons),
        support_clusters=clusters,
    )


def _validate_ohlcv(df: pd.DataFrame) -> None:
    if not isinstance(df, pd.DataFrame):
        raise TrendIndicatorValidationError("OHLCV input must be a pandas DataFrame")
    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing:
        raise TrendIndicatorValidationError(f"missing required OHLCV columns: {', '.join(missing)}")
    for column in REQUIRED_OHLCV_COLUMNS:
        if not pd.api.types.is_numeric_dtype(df[column]) or df[column].isna().any():
            raise TrendIndicatorValidationError(f"invalid OHLCV column: {column}")
    dates = _date_values(df)
    if dates.isna().any() or not dates.is_monotonic_increasing:
        raise TrendIndicatorValidationError("OHLCV dates must be valid and increasing")


def _validate_indicators(df: pd.DataFrame, expected_rows: int) -> None:
    if not isinstance(df, pd.DataFrame) or len(df) != expected_rows:
        raise TrendIndicatorValidationError("indicator and OHLCV inputs must have the same row count")
    missing = [column for column in ("sma20", "sma50", "atr14") if column not in df.columns]
    if missing:
        raise TrendIndicatorValidationError(f"missing required indicator columns: {', '.join(missing)}")
    for column in ("sma20", "sma50", "atr14"):
        if not pd.api.types.is_numeric_dtype(df[column]):
            raise TrendIndicatorValidationError(f"indicator column must be numeric: {column}")


def _date_values(df: pd.DataFrame) -> pd.Series:
    values = df["date"] if "date" in df.columns else pd.Series(df.index, index=df.index)
    return pd.to_datetime(values, errors="coerce")


def _confirmed_swing_candidates(market_structure, cutoff):
    swings = getattr(market_structure, "confirmed_swings", ())
    candidates = []
    low_count = 0
    for swing in reversed(swings):
        confirmed_at = pd.to_datetime(getattr(swing, "confirmed_at", None), errors="coerce")
        if cutoff is not None and (pd.isna(confirmed_at) or confirmed_at > cutoff):
            continue
        if getattr(swing, "swing_type", None) != "low" or low_count >= 2:
            continue
        candidates.append(("latest_swing_low" if low_count == 0 else "previous_swing_low", float(swing.price)))
        low_count += 1
    return candidates


def _cluster_supports(active_supports, current_atr, tolerance_atr):
    clusters = []
    for support in sorted(active_supports, key=lambda item: item["price"]):
        if not clusters or abs(support["price"] - clusters[-1]["representative_price"]) > tolerance_atr * current_atr:
            clusters.append({"representative_price": support["price"], "sources": [support["type"]], "distance_atr": support["distance_atr"], "strength": 1, "_distances": [support["distance_atr"]], "_prices": [support["price"]]})
        else:
            cluster = clusters[-1]
            cluster["sources"].append(support["type"])
            cluster["_prices"].append(support["price"])
            cluster["_distances"].append(support["distance_atr"])
            cluster["representative_price"] = sum(cluster["_prices"]) / len(cluster["_prices"])
            cluster["distance_atr"] = sum(cluster["_distances"]) / len(cluster["_distances"])
            cluster["strength"] = len(cluster["sources"])
    for cluster in clusters:
        cluster.pop("_distances")
        cluster.pop("_prices")
    return clusters


def _confluence_state(cluster_count, clusters):
    max_strength = max((cluster["strength"] for cluster in clusters), default=0)
    if max_strength >= 3:
        return "strong"
    if max_strength == 2:
        return "moderate"
    if max_strength == 1:
        return "weak"
    return "none"


def _reasons(supports, active_supports, clusters):
    if not active_supports:
        return ["no_nearby_support"]
    reasons = ["nearby_support_present"]
    max_strength = max(cluster["strength"] for cluster in clusters)
    if max_strength >= 3:
        reasons.append("multiple_support_sources_clustered")
    elif max_strength == 2:
        reasons.append("two_support_sources_clustered")
    else:
        reasons.append("single_support_source")
    if all(not support["active"] for support in supports):
        reasons.append("price_below_all_supports")
    return reasons


def _unavailable_result() -> SupportResult:
    return SupportResult(False, None, None)
