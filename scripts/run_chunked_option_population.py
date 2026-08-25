"""Bounded execution wrapper around the existing PCS option qualification engine."""
from __future__ import annotations

import gc
import hashlib
import json
import time
from pathlib import Path

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.research.credit_stop import run_backtest

WINDOWS = {
    "SPY": ("2020-01-02", "2026-07-31"),
    "QQQ": ("2020-01-01", "2026-07-31"),
    "NVDA": ("2024-06-10", "2026-07-31"),
    "AMZN": ("2022-06-06", "2026-07-31"),
}
ROOT = Path(__file__).resolve().parents[1] / "research_outputs/safe_strike_chunked_population_monthly"
RUNNER = Path(__file__).resolve()

def shard_identity(symbol, start, end):
    access = PCSDataAccess()
    payload = {"ticker": symbol, "start": str(pd.Timestamp(start).date()), "end": str(pd.Timestamp(end).date()),
               "daily_source_identity": access.source_data_identity("daily", symbol),
               "benchmark_source_identity": access.source_data_identity("daily", "QQQ"),
               "options_source_identity": access.source_data_identity("options", symbol),
               "runner_sha256": hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
               "backtest_sha256": hashlib.sha256((RUNNER.parents[1] / "src/pcs/research/credit_stop.py").read_bytes()).hexdigest()}
    payload["identity_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return payload
def output_sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024), b""): h.update(block)
    return h.hexdigest()

def valid_shard(path, symbol, start, end):
    try:
        sidecar = path.with_suffix(".identity.json")
        saved=json.loads(sidecar.read_text()) if sidecar.exists() else {}
        if saved.get("inputs") != shard_identity(symbol, start, end) or saved.get("output_sha256") != output_sha(path):
            return False
        frame = pd.read_parquet(path)
        return len(frame) == 0 or (frame.ticker.astype(str).eq(symbol).all() and
            pd.to_datetime(frame.entry_date).between(pd.Timestamp(start), pd.Timestamp(end)).all())
    except Exception:
        return False

def chunks(start, end):
    cur = pd.Timestamp(start).to_period("M")
    last = pd.Timestamp(end).to_period("M")
    while cur <= last:
        lo = max(pd.Timestamp(start), cur.start_time)
        hi = min(pd.Timestamp(end), cur.end_time)
        yield lo.normalize(), hi.normalize()
        cur += 1

def load_stock(access, symbol, end):
    x = access.read_prices(symbol, None, end).sort_values("date").drop_duplicates("date")
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
    access = PCSDataAccess()
    stock = load_stock(access, symbol, end)
    bench = load_stock(access, "QQQ", end)
    old = flatten(run_backtest(stock, bench, option_root=f"data/parquet/options_monthly/{symbol}", start=start, end=end, backend="canonical")["trades"], symbol)
    parts = []
    for lo, hi in chunks(start, end):
        parts.append(flatten(run_backtest(stock, bench, option_root=f"data/parquet/options_monthly/{symbol}", start=lo, end=hi, backend="canonical")["trades"], symbol))
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
    access = PCSDataAccess()
    stock = load_stock(access, symbol, end)
    bench = load_stock(access, "QQQ", end)
    for lo, hi in chunks(start, end):
        name = f"{lo:%Y-%m}.parquet"
        target = out / name
        if target.exists() and valid_shard(target, symbol, lo, hi):
            continue
        t0 = time.perf_counter()
        result = run_backtest(stock, bench, option_root=f"data/parquet/options_monthly/{symbol}", start=lo, end=hi, backend="canonical")
        frame = flatten(result["trades"], symbol)
        frame.to_parquet(target, index=False)
        target.with_suffix(".identity.json").write_text(json.dumps({"inputs": shard_identity(symbol, lo, hi), "output_sha256": output_sha(target)}, indent=2), encoding="utf-8")
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
