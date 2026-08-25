"""Read-only diagnosis of daily OHLCV source files."""

from pathlib import Path
import pandas as pd

RENAME = {"日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low", "收盘价": "close", "成交量": "volume"}


def diagnose_daily_file(path):
    source = pd.read_csv(Path(path)).rename(columns=RENAME)
    required = ["date", "open", "high", "low", "close", "volume"]
    missing_cols = [c for c in required if c not in source.columns]
    if missing_cols:
        raise ValueError(f"missing columns: {missing_cols}")
    source["date"] = pd.to_datetime(source["date"], errors="coerce")
    issues = []
    for i, row in source.iterrows():
        base = {"date": row["date"], "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"], "volume": row["volume"], "row_index": i}
        def add(kind, detail):
            issues.append({**base, "issue_type": kind, "issue_detail": detail})
        if pd.isna(row["date"]): add("MISSING_VALUE", "invalid date")
        for c in required[1:]:
            if pd.isna(row[c]): add("MISSING_VALUE", c)
        for c, kind in (("open", "HIGH_BELOW_OPEN"), ("close", "HIGH_BELOW_CLOSE"), ("low", "HIGH_BELOW_LOW")):
            if pd.notna(row["high"]) and pd.notna(row[c]) and row["high"] < row[c]: add(kind, f"high={row['high']} < {c}={row[c]}")
        for c, kind in (("open", "LOW_ABOVE_OPEN"), ("close", "LOW_ABOVE_CLOSE"), ("high", "LOW_ABOVE_HIGH")):
            if pd.notna(row["low"]) and pd.notna(row[c]) and row["low"] > row[c]: add(kind, f"low={row['low']} > {c}={row[c]}")
        if any(pd.notna(row[c]) and row[c] <= 0 for c in ("open", "high", "low", "close")): add("NEGATIVE_OR_ZERO_PRICE", "one or more prices <= 0")
        if pd.notna(row["volume"]) and row["volume"] < 0: add("NEGATIVE_VOLUME", f"volume={row['volume']}")
        if i > 0 and pd.notna(row["date"]) and pd.notna(source.iloc[i-1]["date"]) and row["date"] <= source.iloc[i-1]["date"]: add("DATE_ORDER", "date is not strictly increasing")
        if source["date"].duplicated(keep=False).iloc[i]: add("DUPLICATE_DATE", "date occurs more than once")
    result = pd.DataFrame(issues)
    if not result.empty:
        for offset, label in ((-2, "previous_2"), (-1, "previous_1"), (1, "next_1"), (2, "next_2")):
            for column in ("date", "open", "high", "low", "close", "volume"):
                result[f"{label}_{column}"] = result["row_index"].map(lambda i, o=offset, c=column: source.iloc[i + o][c] if 0 <= i + o < len(source) else None)
        result["open_minus_high_pct"] = (result["open"] - result["high"]) / result["open"].abs()
        result["close_minus_high_pct"] = (result["close"] - result["high"]) / result["close"].abs()
        result["low_minus_open_pct"] = (result["low"] - result["open"]) / result["open"].abs()
        result["low_minus_close_pct"] = (result["low"] - result["close"]) / result["close"].abs()
    return source, result


def summarize_issues(source, issues):
    counts = issues["issue_type"].value_counts().to_dict() if not issues.empty else {}
    dates = pd.to_datetime(issues["date"], errors="coerce").dropna() if not issues.empty else pd.Series(dtype="datetime64[ns]")
    invalid_rows = int(issues["row_index"].nunique()) if not issues.empty else 0
    return {"total_rows": len(source), "invalid_rows": invalid_rows, "invalid_rate": invalid_rows / len(source) if len(source) else 0, "first_invalid_date": dates.min() if not dates.empty else None, "last_invalid_date": dates.max() if not dates.empty else None, "issue_counts": counts}
