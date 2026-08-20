"""Bounded execution wrapper around the existing PCS option qualification engine."""
from __future__ import annotations

import gc
import json
import time
from pathlib import Path

import pandas as pd

from pcs.data.daily_provider import DailyDataProvider
from pcs.research.credit_stop import run_backtest

WINDOWS = {
    "SPY": ("2020-01-02", "2026-07-31"),
    "QQQ": ("2020-01-01", "2026-07-31"),
    "NVDA": ("2024-06-10", "2026-07-31"),
    "AMZN": ("2022-06-06", "2026-07-31"),
}
ROOT = Path("research_outputs/safe_strike_chunked_population_monthly")

def chunks(start, end):
    cur = pd.Timestamp(start).to_period("M")
    last = pd.Timestamp(end).to_period("M")
    while cur <= last:
        lo = max(pd.Timestamp(start), cur.start_time)
        hi = min(pd.Timestamp(end), cur.end_time)
        yield lo.normalize(), hi.normalize()
        cur += 1

def load_stock(provider, symbol, end):
    frames = [pd.read_parquet(p) for p in sorted((Path("data/parquet/daily") / f"symbol={symbol}").rglob("*.parquet"))]
    x = pd.concat(frames, ignore_index=True).sort_values("date").drop_duplicates("date")
    x["date"] = pd.to_datetime(x["date"]).dt.normalize()
    return x[x.date <= pd.Timestamp(end)].copy()

def flatten(trades, symbol):
    rows = []
    for t in trades:
        row = {k: v for k, v in t.items() if k != "events"}
        row["ticker"] = symbol
        row["entry_date"] = row.get("date")
        row["spot"] = row.get("close")
        row["ATR"] = row.get("atr14")
        row["Trend Gate"] = row.get("trend_gate")
        row["DTE"] = (pd.Timestamp(row["expiration"]) - pd.Timestamp(row["date"])).days
        row["width"] = row["short_strike"] - row["long_strike"]
        row["credit"] = row.get("initial_credit")
        rows.append(row)
    return pd.DataFrame(rows)

def key_frame(df):
    cols = ["ticker", "entry_date", "expiration", "short_strike", "long_strike", "DTE", "credit", "trend_gate", "short_buffer_atr"]
    return df[cols].sort_values(cols[:5]).reset_index(drop=True)

def equivalence_test(symbol="QQQ", start="2026-01-02", end="2026-03-31"):
    provider = DailyDataProvider()
    stock = provider.build_daily_series(symbol, end)
    bench = provider.build_daily_series("QQQ", end)
    old = flatten(run_backtest(stock, bench, option_root=f"data/parquet/options_monthly/{symbol}", start=start, end=end, backend="duckdb")["trades"], symbol)
    parts = []
    for lo, hi in chunks(start, end):
        parts.append(flatten(run_backtest(stock, bench, option_root=f"data/parquet/options_monthly/{symbol}", start=lo, end=hi, backend="duckdb")["trades"], symbol))
    new = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=old.columns)
    a, b = key_frame(old), key_frame(new)
    if not a.equals(b):
        raise RuntimeError("chunk equivalence failed")
    return {"symbol": symbol, "start": start, "end": end, "old_trade_count": len(old), "chunked_trade_count": len(new), "status": "PASS"}

def run_symbol(symbol):
    start, end = WINDOWS[symbol]
    out = ROOT / "qualified" / symbol
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = ROOT / "checkpoints.jsonl"
    provider = DailyDataProvider()
    stock = load_stock(provider, symbol, end)
    bench = load_stock(provider, "QQQ", end)
    for lo, hi in chunks(start, end):
        name = f"{lo:%Y-%m}.parquet"
        target = out / name
        if target.exists():
            continue
        t0 = time.perf_counter()
        result = run_backtest(stock, bench, option_root=f"data/parquet/options_monthly/{symbol}", start=lo, end=hi, backend="duckdb")
        frame = flatten(result["trades"], symbol)
        frame.to_parquet(target, index=False)
        record = {"ticker": symbol, "chunk_start": str(lo.date()), "chunk_end": str(hi.date()), "status": "COMPLETE", "qualified_trades": len(frame), "elapsed_seconds": round(time.perf_counter() - t0, 3), "source_option_files_touched": result["quality"].get("quarter_files_opened"), "rows_scanned": result["quality"].get("option_rows_loaded")}
        with checkpoint.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        del result, frame
        gc.collect()

def validate_symbol(symbol):
    start, end = WINDOWS[symbol]
    files = sorted((ROOT / "qualified" / symbol).glob("*.parquet"))
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True) if files else pd.DataFrame()
    required = ["ticker", "entry_date", "spot", "ATR", "trend_gate", "expiration", "DTE", "short_strike", "long_strike", "width", "credit"]
    missing = [c for c in required if c not in df or df[c].isna().any()]
    keys = ["ticker", "entry_date", "expiration", "short_strike", "long_strike"]
    duplicate_keys = int(df.duplicated(keys).sum()) if len(df) else 0
    dates = pd.to_datetime(df.entry_date) if len(df) else pd.Series(dtype="datetime64[ns]")
    valid_dates = bool(len(df) == 0 or (dates.min() >= pd.Timestamp(start) and dates.max() <= pd.Timestamp(end)))
    return {"ticker": symbol, "chunks": len(files), "trades": len(df), "duplicate_trade_keys": duplicate_keys, "missing_required_fields": missing, "date_range_valid": valid_dates, "status": "PASS" if not missing and not duplicate_keys and valid_dates else "FAIL"}

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    eq = equivalence_test()
    (ROOT / "equivalence_test.json").write_text(json.dumps(eq, indent=2), encoding="utf-8")
    for symbol in WINDOWS:
        run_symbol(symbol)
        print(json.dumps(validate_symbol(symbol)), flush=True)
    manifests = [f for symbol in WINDOWS for f in sorted((ROOT / "qualified" / symbol).glob("*.parquet"))]
    with (ROOT / "unified.parquet").open("wb") as handle:
        import pyarrow as pa
        import pyarrow.parquet as pq
        writer = None
        for f in manifests:
            table = pa.Table.from_pandas(pd.read_parquet(f), preserve_index=False)
            if writer is None: writer = pq.ParquetWriter(handle, table.schema)
            writer.write_table(table)
        if writer is not None: writer.close()

if __name__ == "__main__":
    main()
