import math

import pandas as pd


def calculate_market_features(rows: list[dict], predictability_weights: dict | None = None) -> list[dict]:
    if not rows:
        return []
    weights = predictability_weights or {
        "positive_dma20_slope": 15,
        "positive_dma50_slope": 15,
        "price_above_dma20": 15,
        "dma20_above_dma50": 15,
        "dma50_above_dma200": 15,
        "low_crossings": 10,
        "stable_volatility": 15,
    }
    df = pd.DataFrame(rows).sort_values(["symbol", "date"]).copy()
    out = []
    for symbol, g in df.groupby("symbol", sort=False):
        g = g.copy()
        close = g["close"]
        prev_close = close.shift(1)
        tr = pd.concat([(g["high"] - g["low"]), (g["high"] - prev_close).abs(), (g["low"] - prev_close).abs()], axis=1).max(axis=1)
        g["dma20"] = close.rolling(20, min_periods=20).mean()
        g["dma50"] = close.rolling(50, min_periods=50).mean()
        g["dma200"] = close.rolling(200, min_periods=200).mean()
        g["dma20_slope"] = g["dma20"].diff(5) / 5
        g["dma50_slope"] = g["dma50"].diff(10) / 10
        g["atr5"] = tr.rolling(5, min_periods=5).mean()
        g["atr14"] = tr.rolling(14, min_periods=14).mean()
        returns = close.pct_change()
        g["realized_vol_20d"] = returns.rolling(20, min_periods=2).std() * math.sqrt(252)
        rolling_high = close.cummax()
        g["drawdown"] = (close / rolling_high - 1) * 100
        crossings = ((close > g["dma20"]) != (close.shift(1) > g["dma20"].shift(1))).rolling(20, min_periods=1).sum()
        # Expanding rank is PIT-safe: appending future rows cannot change a
        # historical percentile.  A full-frame rank leaks future volatility.
        vol_rank = g["realized_vol_20d"].expanding(min_periods=1).rank(pct=True, ascending=False).fillna(0.5)
        score = (
            (g["dma20_slope"] > 0).astype(int) * weights["positive_dma20_slope"]
            + (g["dma50_slope"] > 0).astype(int) * weights["positive_dma50_slope"]
            + (close > g["dma20"]).astype(int) * weights["price_above_dma20"]
            + (g["dma20"] > g["dma50"]).astype(int) * weights["dma20_above_dma50"]
            + (g["dma50"] > g["dma200"]).astype(int) * weights["dma50_above_dma200"]
            + (crossings <= 4).astype(int) * weights["low_crossings"]
            + (vol_rank >= 0.4).astype(int) * weights["stable_volatility"]
        )
        g["predictability_score"] = score.clip(0, 100)
        g["trend_score"] = (score * 0.7 + (close > g["dma50"]).astype(int) * 30).clip(0, 100)
        g["symbol"] = symbol
        out.extend(g.where(pd.notnull(g), None).to_dict("records"))
    return out
