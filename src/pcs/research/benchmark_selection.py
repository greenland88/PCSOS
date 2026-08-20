"""Research-only discovery and comparison of daily benchmark candidates."""

from __future__ import annotations

from pathlib import Path
import pandas as pd


def discover_daily_files(root, min_rows=3000):
    candidates = []
    for path in Path(root).glob("*_daily_qfq.csv"):
        try:
            raw = pd.read_csv(path, usecols=lambda c: c in {"date", "日期"})
            date_col = "date" if "date" in raw.columns else "日期"
            dates = pd.to_datetime(raw[date_col], errors="coerce").dropna()
            if len(dates) >= min_rows:
                candidates.append({"symbol": path.name.removesuffix("_daily_qfq.csv"), "path": str(path), "date_start": dates.min(), "date_end": dates.max(), "rows": len(dates)})
        except Exception:
            continue
    return pd.DataFrame(candidates)


def common_return_stats(qqq, candidate):
    q = qqq.set_index("date")["close"].rename("qqq")
    c = candidate.set_index("date")["close"].rename("candidate")
    aligned = pd.concat([q, c], axis=1, join="inner").dropna()
    returns = aligned.pct_change().dropna()
    result = {"common_days": len(aligned), "common_start": aligned.index.min(), "common_end": aligned.index.max(), "daily_corr": returns.qqq.corr(returns.candidate), "beta": returns.qqq.cov(returns.candidate) / returns.candidate.var() if returns.candidate.var() else None}
    for n in (20, 60):
        result[f"corr_{n}d"] = aligned.qqq.pct_change(n).corr(aligned.candidate.pct_change(n))
    result["relative_return_volatility"] = (aligned.qqq.pct_change() - aligned.candidate.pct_change()).std()
    return result
