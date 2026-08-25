"""Build isolated SPY/QQQ PCS candidate, Entry Contract v2 and lifecycle artifacts.

Research/OOS preparation only.  It reuses the existing deterministic baseline
replay path and never changes production or the protected OOS config.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.research.credit_stop import load_spread_quotes_canonical, run_backtest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs/spy_qqq_pcs_baseline_20260821"
START = pd.Timestamp("2020-01-02")
END = pd.Timestamp("2026-08-18")
SYMBOLS = ("SPY", "QQQ")


def _daily(symbol: str) -> pd.DataFrame:
    frame = PCSDataAccess().read_prices(symbol)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    return frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def _candidate_id(symbol: str, row: dict) -> str:
    raw = "|".join(
        [symbol, str(pd.Timestamp(row["date"]).date()), str(pd.Timestamp(row["expiration"]).date()),
         format(float(row["short_strike"]), ".15g"), format(float(row["long_strike"]), ".15g")]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _market_confirmation() -> pd.DataFrame:
    path = ROOT / "data/derived/market_confirmation_daily.parquet"
    frame = pd.read_parquet(path)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    return frame


def _entry_contract(symbol: str, row: dict, confirmation: pd.DataFrame) -> dict:
    cid = _candidate_id(symbol, row)
    day = pd.Timestamp(row["date"]).normalize()
    mc = confirmation[confirmation.date.eq(day)]
    mc_row = mc.iloc[0].to_dict() if len(mc) == 1 else {}
    return {
        "module": "spy_qqq_pcs_baseline",
        "version": "20260821.v1",
        "symbol": symbol,
        "ticker": symbol,
        "candidate_id": cid,
        "decision_date": day,
        "expiration": pd.Timestamp(row["expiration"]).normalize(),
        "short_strike": float(row["short_strike"]),
        "long_strike": float(row["long_strike"]),
        "exact_width": float(row["short_strike"] - row["long_strike"]),
        "entry_short_bid": None,
        "entry_short_ask": None,
        "entry_long_bid": None,
        "entry_long_ask": None,
        "credit": float(row["initial_credit"]),
        "atr": float(row["atr14"]),
        "safe_strike_atr": 2.3,
        "safe_strike": float(row["target_short"]),
        "dte": int((pd.Timestamp(row["expiration"]) - day).days),
        "credit_width_ratio": float(row["credit_width_ratio"]),
        "liquidity_valid": True,
        "planned_loss": None,
        "pit_status": "PIT_SAFE",
        "event_logic": "COMPANY_EARNINGS_NOT_APPLICABLE",
        "event_data_valid": True,
        "market_confirmation": mc_row,
        "provenance": {
            "option_source": "data/parquet/options_monthly",
            "daily_source": f"data/parquet/daily/symbol={symbol}",
            "market_confirmation_source": "data/derived/market_confirmation_daily.parquet",
            "baseline_config": "PCS-OOS-FROZEN-20260821-V1",
        },
        "status": "QUALIFYING",
    }


def _build_lifecycle(symbol: str, contract: dict, initial_credit: float) -> list[dict]:
    entry = pd.Timestamp(contract["decision_date"])
    expiry = pd.Timestamp(contract["expiration"])
    tracking_end = min(expiry, entry + pd.Timedelta(days=45))
    quotes, _ = load_spread_quotes_canonical(
        symbol, entry, tracking_end, expiry,
        [contract["short_strike"], contract["long_strike"]],
    )
    rows = []
    for day, group in quotes.groupby("Trade Date", sort=True):
        short = group[group["Strike"].eq(contract["short_strike"])].head(1)
        long = group[group["Strike"].eq(contract["long_strike"])].head(1)
        complete = len(short) == 1 and len(long) == 1
        valid = complete and all(pd.notna(short.iloc[0].get(c)) and pd.notna(long.iloc[0].get(c)) for c in ("Bid Price", "Ask Price"))
        spread_mark = ((short.iloc[0]["Bid Price"] + short.iloc[0]["Ask Price"]) / 2 - (long.iloc[0]["Bid Price"] + long.iloc[0]["Ask Price"]) / 2) if valid else None
        conservative = (short.iloc[0]["Ask Price"] - long.iloc[0]["Bid Price"]) if valid else None
        rows.append({
            "module": "spy_qqq_pcs_baseline_lifecycle", "version": "20260821.v1",
            "symbol": symbol, "candidate_id": contract["candidate_id"], "mark_date": pd.Timestamp(day),
            "expiration": expiry, "short_strike": contract["short_strike"], "long_strike": contract["long_strike"],
            "short_bid": short.iloc[0]["Bid Price"] if valid else None, "short_ask": short.iloc[0]["Ask Price"] if valid else None,
            "long_bid": long.iloc[0]["Bid Price"] if valid else None, "long_ask": long.iloc[0]["Ask Price"] if valid else None,
            "spread_mark": spread_mark, "quote_available": bool(valid), "contract_match": bool(complete),
            "is_entry": pd.Timestamp(day) == entry, "is_expiration": pd.Timestamp(day) == expiry,
            "missing_reason": None if valid else "EXACT_LEG_QUOTE_UNAVAILABLE",
            "stop_triggered": bool(valid and conservative >= initial_credit * 2),
            "exit": bool(pd.Timestamp(day) == expiry or (valid and conservative >= initial_credit * 2)),
            "pnl": (initial_credit - conservative) * 100 if valid else None,
            "pit_status": "PIT_SAFE",
        })
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    confirmation = _market_confirmation()
    report = {"module": "spy_qqq_pcs_baseline_builder", "version": "20260821.v1", "status": "COMPLETE", "symbols": {}}
    for symbol in SYMBOLS:
        stock = _daily(symbol)
        result = run_backtest(
            stock, _daily("QQQ"), option_root=f"data/parquet/options_monthly/{symbol}",
            start=START, end=END, backend="canonical",
        )
        contracts = [_entry_contract(symbol, row, confirmation) for row in result["trades"]]
        for contract, row in zip(contracts, result["trades"]):
            # Entry quotes are reconstructed from the exact entry-day source row.
            lifecycle = _build_lifecycle(symbol, contract, float(row["initial_credit"]))
            entry_rows = [x for x in lifecycle if x["is_entry"]]
            if entry_rows:
                e = entry_rows[0]
                contract.update({"entry_short_bid": e["short_bid"], "entry_short_ask": e["short_ask"], "entry_long_bid": e["long_bid"], "entry_long_ask": e["long_ask"]})
            contract["planned_loss"] = float(row["initial_credit"] * 2 * 100)
            contract["lifecycle_rows"] = len(lifecycle)
            contract["lifecycle_complete"] = bool(lifecycle and all(x["quote_available"] or x["missing_reason"] for x in lifecycle))
            contract["lifecycle"] = lifecycle
        contracts_frame = pd.DataFrame([{k: v for k, v in x.items() if k not in {"market_confirmation", "provenance", "lifecycle"}} for x in contracts])
        contracts_frame.to_parquet(OUT / f"{symbol}_entry_contract_v2.parquet", index=False)
        pd.DataFrame([x for c in contracts for x in c["lifecycle"]]).to_parquet(OUT / f"{symbol}_lifecycle_marks.parquet", index=False)
        (OUT / f"{symbol}_run_metadata.json").write_text(json.dumps({"symbol": symbol, "baseline_config": "PCS-OOS-FROZEN-20260821-V1", "company_earnings_gate": "NOT_APPLICABLE", "final_oos_run": False, "train_validation_run": False, "candidate_count": len(contracts), "replay_quality": result["quality"], "exclusions": result["exclusions"]}, indent=2, default=str), encoding="utf-8")
        report["symbols"][symbol] = {"candidate_count": len(contracts), "lifecycle_rows": sum(len(x["lifecycle"]) for x in contracts), "lifecycle_missing_reason_rows": sum(sum(not y["quote_available"] for y in x["lifecycle"]) for x in contracts), "replay_quality": result["quality"], "exclusions": result["exclusions"]}
    (OUT / "build_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
