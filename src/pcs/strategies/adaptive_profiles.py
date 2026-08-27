"""Research-only resolution of generic PCS configuration from PIT behavior.

The resolver intentionally uses only underlying/option observations available
as of the requested date.  It does not inspect trades, exits, or performance,
and it never changes the canonical execution constants.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
import pandas as pd


@dataclass(frozen=True)
class TickerCharacteristics:
    realized_volatility: float
    normal_pullback_depth: float
    trend_persistence: float
    recovery_speed_days: float
    volume_ratio_median: float
    option_quote_coverage: float | None = None


@dataclass(frozen=True)
class ResolvedStrategyConfig:
    strategy_id: str
    ticker: str
    as_of: str
    characteristics: TickerCharacteristics
    momentum_window_days: int
    recovery_window_days: int
    pullback_depth: float
    volume_ratio_floor: float
    dte_min: int = 30
    dte_max: int = 45
    safe_strike_atr: float = 2.3
    min_credit_width: float = 0.10
    module: str = "pcs.strategies.adaptive_profiles"
    version: str = "1.0"
    calculation_version: str = "pit-behavior-v1"
    reason_codes: tuple[str, ...] = ("PIT_CHARACTERISTICS", "RESEARCH_ONLY", "NO_PNL_INPUT")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any, default: float) -> float:
    try:
        value = float(value)
        return value if pd.notna(value) else default
    except (TypeError, ValueError):
        return default


def _require_finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CHARACTERISTIC_INVALID:{name}") from exc
    if not pd.notna(result) or not pd.api.types.is_number(result) or result <= 0:
        raise ValueError(f"CHARACTERISTIC_INVALID:{name}")
    return result


def measure_characteristics(daily: pd.DataFrame, *, as_of: str | None = None,
                            options: pd.DataFrame | None = None) -> TickerCharacteristics:
    """Measure PIT-safe ticker behavior from a daily frame ending at ``as_of``."""
    required = {"date", "close", "high", "low"}
    missing = required - set(daily.columns)
    if missing:
        raise ValueError(f"MISSING_DAILY_COLUMNS:{','.join(sorted(missing))}")
    x = daily.copy(); x["date"] = pd.to_datetime(x["date"]); x = x.sort_values("date").reset_index(drop=True)
    if as_of is not None:
        x = x[x["date"] <= pd.Timestamp(as_of)]
    if len(x) < 60:
        raise ValueError("INSUFFICIENT_PIT_HISTORY")
    close = pd.to_numeric(x["close"], errors="coerce")
    ret = close.pct_change()
    rolling_high = close.rolling(60, min_periods=20).max()
    drawdown = close / rolling_high - 1
    atr_pct = (pd.to_numeric(x["high"]) - pd.to_numeric(x["low"])) / close
    direction = ret.gt(0).astype(int)
    persistence = direction.rolling(20, min_periods=20).mean().sub(0.5).abs().mul(2)
    if "volume" in x.columns:
        volume_ratio = pd.to_numeric(x["volume"], errors="coerce") / pd.to_numeric(x["volume"], errors="coerce").rolling(20, min_periods=20).mean()
    else:
        volume_ratio = pd.Series(1.0, index=x.index)
    # Recovery speed is descriptive: median days from a 5% drawdown to a new
    # rolling high, with no use of trade outcomes.
    recoveries = []
    for i in drawdown[drawdown <= -0.05].index:
        future = close.loc[i:]
        prior_peak = close.loc[:i].rolling(60, min_periods=1).max().iloc[-1]
        hit = future[future >= prior_peak]
        if len(hit): recoveries.append(int(hit.index[0] - i))
    quote_coverage = None
    if options is not None and len(options):
        date_col = "trade_date" if "trade_date" in options.columns else "date" if "date" in options.columns else None
        if date_col:
            od = pd.to_datetime(options[date_col]).dt.normalize()
            daily_dates = x["date"].dt.normalize().drop_duplicates()
            quote_coverage = float(od[od.isin(daily_dates)].nunique() / max(1, daily_dates.nunique()))
    return TickerCharacteristics(
        realized_volatility=_require_finite(ret.std() * (252 ** .5), "realized_volatility"),
        normal_pullback_depth=_finite(drawdown[drawdown < 0].quantile(0.5), -0.02),
        trend_persistence=_finite(persistence.median(), 0.5),
        recovery_speed_days=_finite(pd.Series(recoveries).median() if recoveries else 10, 10),
        volume_ratio_median=_finite(volume_ratio.median(), 1.0),
        option_quote_coverage=quote_coverage,
    )


def resolve_strategy_config(strategy_id: str, ticker: str, daily: pd.DataFrame, *,
                             as_of: str | None = None, options: pd.DataFrame | None = None) -> ResolvedStrategyConfig:
    c = measure_characteristics(daily, as_of=as_of, options=options)
    resolved_as_of = pd.Timestamp(as_of or pd.to_datetime(daily.date).max()).date().isoformat()
    vol_scale = min(1.5, max(0.75, c.realized_volatility / 0.35))
    return ResolvedStrategyConfig(
        strategy_id=strategy_id, ticker=str(ticker).upper(),
        as_of=resolved_as_of,
        characteristics=c, momentum_window_days=int(round(5 * vol_scale)),
        recovery_window_days=max(3, min(20, int(round(c.recovery_speed_days)))),
        pullback_depth=round(min(-0.01, c.normal_pullback_depth), 6),
        volume_ratio_floor=round(max(0.8, min(1.2, c.volume_ratio_median * 0.9)), 6),
    )


__all__ = ["TickerCharacteristics", "ResolvedStrategyConfig", "measure_characteristics", "resolve_strategy_config"]
