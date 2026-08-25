"""Isolated exact-duplicate-only rebuild for explicitly approved option quarters."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pcs.data.storage_schema import OPTION_FIELDS

KEY = ["symbol", "trade_date", "expiration_date", "strike", "call_put"]
TARGETS = {
    "TSLA": {(2023, 1), (2023, 2), (2024, 2), (2024, 3), (2024, 4), (2025, 4)},
    "AMZN": {(2014, 1), (2023, 1), (2023, 2), (2024, 2), (2024, 3), (2024, 4), (2025, 4)},
}


def _load(path: Path, symbol: str) -> pd.DataFrame:
    raw = pd.read_csv(path)
    raw = raw.loc[:, ~raw.columns.astype(str).str.match(r"^Unnamed")]
    mapping = {"Trade Date": "trade_date", "Expiry Date": "expiration_date", "Strike": "strike",
               "Call/Put": "call_put", "Last Trade Price": "last", "Bid Price": "bid",
               "Ask Price": "ask", "Bid Implied Volatility": "bid_iv", "Ask Implied Volatility": "ask_iv",
               "Open Interest": "open_interest", "Volume": "volume", "Delta": "delta", "Gamma": "gamma",
               "Vega": "vega", "Theta": "theta", "Rho": "rho"}
    raw = raw.rename(columns=mapping)
    raw["symbol"] = symbol
    for c in ("trade_date", "expiration_date"):
        raw[c] = pd.to_datetime(raw[c], errors="raise").dt.date
    raw["strike"] = pd.to_numeric(raw["strike"], errors="raise")
    raw["call_put"] = raw["call_put"].astype(str).str.lower()
    return raw[OPTION_FIELDS]


def rebuild_quarter(symbol: str, year: int, quarter: int, raw_root="data/raw/options",
                    output_root="data/parquet/options_v2/safe_rebuild_20260820") -> dict:
    symbol = symbol.upper()
    path = Path(raw_root) / symbol / f"{symbol}_{year}_q{quarter}_option_chain.csv"
    frame = _load(path, symbol)
    raw_rows = len(frame)
    full_duplicates = frame.duplicated(OPTION_FIELDS, keep=False)
    per_key = frame.groupby(KEY, dropna=False, sort=False)[OPTION_FIELDS].nunique(dropna=False)
    conflicting = per_key.gt(1).any(axis=1)
    conflicting_keys = int(conflicting.sum())
    if conflicting_keys:
        raise ValueError(f"conflicting quote keys in {path}: {conflicting_keys}")
    rebuilt = frame.drop_duplicates(OPTION_FIELDS, keep="first").sort_values(KEY, kind="mergesort").reset_index(drop=True)
    unique_keys = int(rebuilt[KEY].drop_duplicates().shape[0])
    out = Path(output_root) / f"symbol={symbol}" / f"year={year}" / f"quarter={quarter}" / f"{symbol}_{year}_q{quarter}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    rebuilt.to_parquet(out, index=False)
    check = pd.read_parquet(out)[OPTION_FIELDS]
    normalized = frame.drop_duplicates(OPTION_FIELDS, keep="first").sort_values(KEY, kind="mergesort").reset_index(drop=True)
    equal = check.equals(normalized)
    return {"ticker": symbol, "quarter": f"{year} Q{quarter}", "raw_rows": raw_rows,
            "exact_duplicates_removed": raw_rows - len(rebuilt), "conflicting_keys": conflicting_keys,
            "unique_keys_preserved": unique_keys, "v2_rebuilt": str(out), "pass": bool(equal and not conflicting_keys),
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "output_sha256": hashlib.sha256(out.read_bytes()).hexdigest()}


def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--summary", default="data/manifests/options_v2_safe_rebuild_20260820.json")
    p.add_argument("--provenance", default="data/manifests/options_v2_safe_rebuild_20260820_provenance.csv")
    a = p.parse_args(); results = []
    for symbol, quarters in TARGETS.items():
        for year, quarter in sorted(quarters): results.append(rebuild_quarter(symbol, year, quarter))
    Path(a.summary).parent.mkdir(parents=True, exist_ok=True); Path(a.summary).write_text(json.dumps(results, indent=2), encoding="utf-8")
    provenance = pd.DataFrame([{**r, "dataset": "options_v2/safe_rebuild_20260820", "method": "exact_full_row_identity_only", "created_at": datetime.now(timezone.utc).isoformat()} for r in results])
    provenance.to_csv(a.provenance, index=False)
    print("Ticker | Quarter | Raw rows | Exact duplicates removed | Conflicting keys | Unique keys preserved | v2 rebuilt | PASS/FAIL")
    for r in results: print(f"{r['ticker']} | {r['quarter']} | {r['raw_rows']} | {r['exact_duplicates_removed']} | {r['conflicting_keys']} | {r['unique_keys_preserved']} | {r['v2_rebuilt']} | {'PASS' if r['pass'] else 'FAIL'}")


if __name__ == "__main__": main()
