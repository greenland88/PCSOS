"""Chunked, read-only audit for purchased historical option chains."""

from __future__ import annotations

from pathlib import Path
import re

import pandas as pd


OPTION_COLUMNS = ["Trade Date", "Strike", "Expiry Date", "Call/Put", "Bid Price", "Ask Price", "Volume", "Open Interest", "Bid Implied Volatility", "Ask Implied Volatility", "Delta"]


def quarter_files(root, symbol):
    return sorted(Path(root, symbol).glob(f"{symbol}_*_option_chain.csv"))


def _stock(path):
    df = pd.read_csv(path)
    rename = {"日期": "date", "收盘价": "close"}
    df = df.rename(columns=rename)
    if "date" not in df or "close" not in df:
        raise ValueError("stock file must contain date and close")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["date", "close"]).drop_duplicates("date").set_index("date")["close"]


def audit_symbol(symbol, option_root="data/raw/options", stock_root="data/raw/daily_forward_adjusted", chunksize=250_000):
    raise RuntimeError("LEGACY_RESEARCH_READER_DISABLED: use PCSDataAccess canonical audit route")
    symbol = symbol.upper()
    files = quarter_files(option_root, symbol)
    stock = _stock(Path(stock_root) / f"{symbol}_daily_qfq.csv")
    stats = {"symbol": symbol, "file_count": len(files), "total_rows": 0, "duplicate_contract_date_rows": 0,
             "zero_bid_rows": 0, "zero_ask_rows": 0, "bid_gt_ask_rows": 0, "missing_bid_rows": 0,
             "missing_ask_rows": 0, "missing_volume_rows": 0, "missing_open_interest_rows": 0,
             "missing_iv_rows": 0, "missing_delta_rows": 0, "stock_aligned_trade_dates": 0,
             "option_trade_dates": set(), "schema_consistent": True, "scale_ratio_samples": []}
    first_schema = None; seen = set()
    for path in files:
        header = list(pd.read_csv(path, nrows=0).columns)
        if first_schema is None: first_schema = header
        stats["schema_consistent"] &= header == first_schema
        for chunk in pd.read_csv(path, usecols=lambda c: c in OPTION_COLUMNS, chunksize=chunksize):
            n = len(chunk); stats["total_rows"] += n
            dates = pd.to_datetime(chunk["Trade Date"], errors="coerce").dt.normalize(); stats["option_trade_dates"].update(dates.dropna().unique())
            for col in ("Bid Price", "Ask Price", "Volume", "Open Interest", "Bid Implied Volatility", "Ask Implied Volatility", "Delta"):
                values = pd.to_numeric(chunk[col], errors="coerce")
                if col == "Bid Price": stats["missing_bid_rows"] += int(values.isna().sum()); stats["zero_bid_rows"] += int((values == 0).sum())
                elif col == "Ask Price": stats["missing_ask_rows"] += int(values.isna().sum()); stats["zero_ask_rows"] += int((values == 0).sum())
                elif col == "Volume": stats["missing_volume_rows"] += int(values.isna().sum())
                elif col == "Open Interest": stats["missing_open_interest_rows"] += int(values.isna().sum())
                elif col in ("Bid Implied Volatility", "Ask Implied Volatility"): stats["missing_iv_rows"] += int(values.isna().sum())
                else: stats["missing_delta_rows"] += int(values.isna().sum())
            bid, ask = pd.to_numeric(chunk["Bid Price"], errors="coerce"), pd.to_numeric(chunk["Ask Price"], errors="coerce")
            stats["bid_gt_ask_rows"] += int((bid > ask).sum())
            keys = pd.MultiIndex.from_frame(chunk[["Trade Date", "Expiry Date", "Strike", "Call/Put"]])
            stats["duplicate_contract_date_rows"] += int(keys.duplicated().sum())
    option_dates = pd.DatetimeIndex(stats.pop("option_trade_dates"))
    aligned = option_dates.intersection(stock.index)
    stats["option_start_date"] = str(option_dates.min().date()) if len(option_dates) else None
    stats["option_end_date"] = str(option_dates.max().date()) if len(option_dates) else None
    stats["stock_start_date"] = str(stock.index.min().date())
    stats["stock_end_date"] = str(stock.index.max().date())
    stats["aligned_trade_dates"] = len(aligned)
    stats["schema"] = ",".join(first_schema or [])
    stats["reliable_start_date"] = max(pd.Timestamp(stats["option_start_date"]), stock.index.min()).date().isoformat() if stats["option_start_date"] else None
    stats["reliable_end_date"] = min(pd.Timestamp(stats["option_end_date"]), stock.index.max()).date().isoformat() if stats["option_end_date"] else None
    stats["reliable_reason"] = "No underlying_price field; scale requires manual/strikes-vs-adjusted-close review"
    return stats
