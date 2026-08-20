"""Research-only, no-lookahead structural stability detector.

This module is deliberately independent of the R1 and Trend production paths.
Its thresholds are diagnostic constants, not trading parameters.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import numpy as np
import pandas as pd


REQUIRED = {"date", "open", "high", "low", "close", "volume"}


@dataclass(frozen=True)
class RegimeBreak:
    ticker: str
    regime_id: str
    candidate_start_date: pd.Timestamp
    confirmed_at: pd.Timestamp
    regime_start_estimate: pd.Timestamp
    end_date: pd.Timestamp | None
    duration: int
    stability_score: float
    main_break_drivers: str


def _validate(df: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"], errors="raise")
    x = x.sort_values("date").reset_index(drop=True)
    if x.date.duplicated().any():
        raise ValueError("duplicate dates")
    for c in REQUIRED - {"date"}:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    if x[list(REQUIRED - {"date"})].isna().any().any():
        raise ValueError("missing numeric OHLCV values")
    if (x[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("non-positive price")
    if (x.volume < 0).any() or (x.high < x[["open", "low", "close"]].max(axis=1)).any():
        raise ValueError("invalid OHLC relationship")
    return x


def build_stability_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build only information available on each row."""
    x = _validate(df)
    prev = x.close.shift(1)
    tr = pd.concat([x.high-x.low, (x.high-prev).abs(), (x.low-prev).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    x["atr_pct"] = x.atr14 / x.close
    x["rv20"] = x.close.pct_change().rolling(20, min_periods=20).std() * np.sqrt(252)
    x["ma200"] = x.close.rolling(200, min_periods=200).mean()
    x["ma200_slope"] = x.ma200.pct_change(20)
    x["price_ma200"] = x.close / x.ma200
    x["distance_ma200_atr"] = (x.close-x.ma200) / x.atr14
    x["move_atr"] = (x.close-x.close.shift(1)).abs() / x.atr14
    x["gap_atr"] = (x.open-prev).abs() / x.atr14
    for n in (63, 126):
        x[f"atr_pct_median_{n}"] = x.atr_pct.rolling(n, min_periods=n).median()
        x[f"atr_pct_iqr_{n}"] = x.atr_pct.rolling(n, min_periods=n).quantile(.75)-x.atr_pct.rolling(n, min_periods=n).quantile(.25)
        x[f"rv_median_{n}"] = x.rv20.rolling(n, min_periods=n).median()
        x[f"rv_iqr_{n}"] = x.rv20.rolling(n, min_periods=n).quantile(.75)-x.rv20.rolling(n, min_periods=n).quantile(.25)
        for threshold, name in ((1.5, "shock15"), (2.0, "shock20"), (3.0, "shock30")):
            x[f"{name}_{n}"] = x.move_atr.ge(threshold).rolling(n, min_periods=n).mean()
        x[f"move5_2atr_{n}"] = ((x.close-x.close.shift(5)).abs()/x.atr14).ge(2).rolling(n, min_periods=n).mean()
        x[f"gap1_{n}"] = x.gap_atr.ge(1).rolling(n, min_periods=n).mean()
        x[f"gap15_{n}"] = x.gap_atr.ge(1.5).rolling(n, min_periods=n).mean()
        x[f"price_ma200_median_{n}"] = x.price_ma200.rolling(n, min_periods=n).median()
        x[f"ma200_slope_median_{n}"] = x.ma200_slope.rolling(n, min_periods=n).median()
    return x


def _severity(recent: pd.Series, previous: pd.Series) -> tuple[str, float]:
    if pd.isna(recent) or pd.isna(previous) or previous == 0:
        return "LOW", 0.0
    change = abs(float(recent / previous - 1))
    if change >= 0.50:
        return "HIGH", change
    if change >= 0.25:
        return "MEDIUM", change
    return "LOW", change


def detect_regime_breaks(df: pd.DataFrame, ticker: str, confirmation_days: int = 30) -> tuple[pd.DataFrame, list[RegimeBreak]]:
    if confirmation_days not in (30, 60):
        raise ValueError("confirmation_days must be 30 or 60")
    x = build_stability_features(df)
    dims = {
        "volatility_level": "atr_pct_median_63", "volatility_distribution": "atr_pct_iqr_63",
        "shock_frequency": "shock20_63", "gap_behavior": "gap1_63", "price_structure": "price_ma200_median_63",
    }
    candidates = []
    for i in range(126 + 63, len(x)):
        row = x.iloc[i]
        severities = {}
        for dim, col in dims.items():
            recent = x[col].iloc[i-62:i+1].median()
            prior = x[col].iloc[i-125:i-62].median()
            severities[dim] = _severity(recent, prior)[0]
        high_dims = [k for k, v in severities.items() if v == "HIGH"]
        if len(high_dims) >= 3:
            candidates.append((i, high_dims))
    breaks = []
    for i, drivers in candidates:
        confirm_i = i + confirmation_days
        if confirm_i >= len(x):
            continue
        # Confirmation uses only the post-candidate period and is recorded at confirm_i.
        confirmed = True
        for col in dims.values():
            if x[col].iloc[confirm_i-62:confirm_i+1].isna().any():
                confirmed = False
        if confirmed:
            breaks.append(RegimeBreak(ticker, f"SRG_{len(breaks)+1:03d}", x.date.iloc[i], x.date.iloc[confirm_i], x.date.iloc[i], None, 0, 0.0, ",".join(drivers)))
    return x, breaks


def analyze_symbol(df: pd.DataFrame, ticker: str, confirmation_days: int = 30) -> tuple[pd.DataFrame, pd.DataFrame]:
    x, breaks = detect_regime_breaks(df, ticker, confirmation_days)
    end = x.date.iloc[-1]
    output = []
    for j, b in enumerate(breaks):
        next_start = breaks[j+1].candidate_start_date if j+1 < len(breaks) else None
        last = next_start - pd.Timedelta(days=1) if next_start is not None else end
        duration = int((x.date <= last).sum() - (x.date < b.confirmed_at).sum())
        score = 100.0
        output.append(asdict(RegimeBreak(b.ticker, b.regime_id, b.candidate_start_date, b.confirmed_at, b.regime_start_estimate, next_start, max(duration, 0), score, b.main_break_drivers)))
    timeline = pd.DataFrame(output)
    current = timeline.tail(1).copy() if len(timeline) else pd.DataFrame()
    if len(current):
        current["current_regime_start_estimate"] = current.regime_start_estimate
        current["current_regime_confirmed_at"] = current.confirmed_at
        current["current_regime_trading_days"] = current.duration
        current["current_regime_calendar_days"] = (end-current.confirmed_at).dt.days
        current["stability_class"] = pd.cut(current.current_regime_trading_days, [-1,126,252,504,np.inf], labels=["SHORT_HISTORY","LIMITED_HISTORY","USABLE_HISTORY","DEEP_HISTORY"]).astype(str)
        current["structural_stability_score"] = 100.0
    return current, timeline


def load_symbol_csv(symbol: str, root: str | Path = "data/raw/daily_forward_adjusted") -> pd.DataFrame:
    p = Path(root) / f"{symbol.upper()}_daily_qfq.csv"
    d = pd.read_csv(p)
    rename = {"日期":"date", "开盘价":"open", "最高价":"high", "最低价":"low", "收盘价":"close", "成交量":"volume"}
    return d.rename(columns=rename)
