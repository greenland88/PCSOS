from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from .daily_fetcher import update_live_daily
from .daily_provider import DailyDataProvider
from .universe import load_market_universe, merge_symbols

def update_symbol(symbol, historical_root="data/raw/daily_forward_adjusted", live_root="data/live/daily", fetcher=None):
    symbol = symbol.upper()
    provider = DailyDataProvider(historical_root, live_root)
    hist_path = Path(historical_root) / f"{symbol}_daily_qfq.csv"
    if not hist_path.exists(): return {"symbol": symbol, "status": "FAILED", "warnings": ["historical_base_missing"]}
    try:
        historical = provider.build_daily_series(symbol)
        historical_last = historical.date.max().date()
        live = update_live_daily(symbol, historical_root, live_root, fetcher=fetcher)
        after = provider.build_daily_series(symbol)
        new_rows = int((live.date > pd.Timestamp(historical_last)).sum())
        latest = after.date.max().date()
        status = "UPDATED" if new_rows else "NO_NEW_DATA"
        return {"symbol": symbol, "historical_last_date": str(historical_last), "live_last_date": str(live.date.max().date()), "rows_fetched": len(live), "rows_inserted": new_rows, "rows_updated": 0, "latest_available_date": str(latest), "status": status, "warnings": list(live.attrs.get("warnings", []))}
    except Exception as exc:
        return {"symbol": symbol, "status": "FAILED", "warnings": [str(exc)]}

def update_symbols(symbols, **kwargs):
    return [update_symbol(symbol, **kwargs) for symbol in symbols]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="*")
    parser.add_argument("--universe", choices=["default", "benchmarks", "pcs_universe"])
    parser.add_argument("--lookback-trading-days", type=int, default=10)
    args = parser.parse_args()
    universe = load_market_universe([args.universe]) if args.universe else []
    symbols = merge_symbols(universe, args.symbols)
    if not symbols: parser.error("provide symbols or --universe")
    for result in update_symbols(symbols): print(result)

if __name__ == "__main__": main()
