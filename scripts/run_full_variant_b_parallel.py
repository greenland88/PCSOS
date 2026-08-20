from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import json
import pandas as pd

from pcs.research.variant_b_replay import replay_dates, ReplayPolicy
import pcs.research.entry_candidate_universe as m

ROOT = Path("data/raw/daily_forward_adjusted")
OUT = Path("data/parquet/research/variant_b_full")
OUT.mkdir(parents=True, exist_ok=True)
CAL = "data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv"
TICKERS = ["AAPL", "AMD", "AMZN", "AVGO", "CRM", "GOOGL", "HOOD", "META",
           "MSFT", "MU", "NFLX", "NVDA", "QQQ", "SPY", "TSLA", "VRT"]


def run_ticker(ticker: str) -> dict:
    output = OUT / f"{ticker}_full_post2020_2d.parquet"
    if output.exists():
        frame = pd.read_parquet(output)
        return {"ticker": ticker, "reused": True, "rows": len(frame)}
    daily = ROOT / f"{ticker}_daily_qfq.csv"
    stock = m._daily(daily)
    start_year = 2021 if ticker == "HOOD" else 2020
    dates = list(stock.loc[stock.date.dt.year.ge(start_year), "date"])
    frame = replay_dates(
        ticker, daily, f"data/raw/options/{ticker}", dates,
        ROOT / "QQQ_daily_qfq.csv", CAL,
        policy=ReplayPolicy(reject_expiration_crossing=False,
                            pre_earnings_exit_days=2),
    )
    frame.to_parquet(output, index=False)
    return {
        "ticker": ticker,
        "reused": False,
        "rows": len(frame),
        "dates": int(frame.date.nunique()) if not frame.empty and "date" in frame else 0,
        "complete": int((frame.status == "COMPLETE").sum())
        if not frame.empty and "status" in frame else 0,
    }


if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(run_ticker, ticker): ticker for ticker in TICKERS}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                print(json.dumps(future.result()), flush=True)
            except Exception as exc:
                print(json.dumps({"ticker": ticker, "error": type(exc).__name__,
                                  "message": str(exc)}), flush=True)
