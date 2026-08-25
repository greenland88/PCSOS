"""Backfill approved Entry Contract v2 fields without changing candidates."""
from __future__ import annotations

from pathlib import Path
import os
import json
import pandas as pd
from pcs.data.access import PCSDataAccess

from pcs.entry.contract_v2 import ENTRY_CONTRACT_V2, later_expirations, nearby_strikes, normalize_price_confirmation
from pcs.features.expected_move import calculate_expected_move
from pcs.research.stage4a_replay import audit_inputs
from pcs.research.entry_confirmation import analyze_entry_confirmation

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT / "research_outputs/safe_strike_stage4a/candidate_inputs"
TICKERS = tuple(os.getenv("PCS_STAGE4A_TICKERS", "NVDA AMD TSLA AMZN").split())


def load_chain(ticker: str, frame: pd.DataFrame) -> pd.DataFrame:
    out = PCSDataAccess().read_quotes(ticker, pd.to_datetime(frame.date).min(), pd.to_datetime(frame.date).max())
    return out.rename(columns={"trade_date": "trade_date", "expiration_date": "expiration", "call_put": "option_type"})


def backfill(ticker: str) -> dict:
    path = ROOT / f"{ticker}.parquet"
    frame = pd.read_parquet(path).copy()
    prior = frame[[c for c in ("option_volume", "open_interest", "bid_ask_pct") if c in frame]].copy()
    variant = REPO_ROOT / "data/parquet/research/variant_b_full" / f"{ticker}_full_post2020_2d.parquet"
    if variant.exists():
        v = pd.read_parquet(variant)
        identity = ["date", "expiration", "short_strike", "long_strike"]
        same_identity = (len(v) == len(frame) and all(c in v.columns and c in frame.columns for c in identity)
                         and v[identity].sort_values(identity).reset_index(drop=True).equals(
                             frame[identity].sort_values(identity).reset_index(drop=True)))
        if same_identity and {"short_volume", "short_oi", "short_ask", "short_bid"}.issubset(v.columns):
            prior = pd.DataFrame({"option_volume": v["short_volume"], "open_interest": v["short_oi"], "bid_ask_pct": (v["short_ask"] - v["short_bid"]) / ((v["short_ask"] + v["short_bid"]) / 2).clip(lower=1e-12)})
    chain = load_chain(ticker, frame)
    chain["trade_date"] = pd.to_datetime(chain["trade_date"]).dt.normalize()
    chain["expiration"] = pd.to_datetime(chain["expiration"]).dt.normalize()
    daily = PCSDataAccess().read_prices(ticker)
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()
    confirmations = {}
    for day in frame["date"].drop_duplicates():
        result = analyze_entry_confirmation(daily, day)
        confirmations[day] = normalize_price_confirmation(result.confirmation_score)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["expiration"] = pd.to_datetime(frame["expiration"]).dt.normalize()
    chains = {day: group for day, group in chain.groupby("trade_date", sort=False)} if not chain.empty else {}
    frame["expected_move_1d"] = frame.apply(lambda r: calculate_expected_move(
        spot=float(r.close), short_strike=float(r.short_strike), atr=float(r.atr14), dte=int(r.dte)).expected_move_1d, axis=1)
    frame["nearby_strikes"] = frame.apply(lambda r: nearby_strikes(
        chains.get(r["date"], chain.iloc[0:0]), r["expiration"], "p", r["short_strike"]), axis=1)
    frame["later_expirations"] = frame.apply(lambda r: later_expirations(
        chains.get(r["date"], chain.iloc[0:0]), r["expiration"], "p"), axis=1)
    frame["price_confirmation"] = frame["date"].map(confirmations)
    if {"bid", "ask", "volume", "open_interest"}.issubset(chain.columns):
        side_col = "call_put" if "call_put" in chain.columns else "option_type"
        quotes = chain[chain[side_col].astype(str).str.lower().eq("p")].copy()
        quote_key = [c for c in ("symbol", "trade_date", "expiration", side_col, "strike") if c in quotes.columns]
        quotes = quotes.drop_duplicates(quote_key, keep="first")
        qmap = quotes.set_index(["trade_date", "expiration", "strike"])
        def short_quote(r):
            key = (r["date"], r["expiration"], float(r["short_strike"]))
            try: return qmap.loc[key]
            except KeyError: return pd.Series({"volume": pd.NA, "open_interest": pd.NA, "bid": pd.NA, "ask": pd.NA})
        quote_frame = frame.apply(short_quote, axis=1)
        frame["option_volume"] = quote_frame["volume"].to_numpy()
        frame["open_interest"] = quote_frame["open_interest"].to_numpy()
        frame["bid_ask_pct"] = (pd.to_numeric(quote_frame["ask"], errors="coerce") - pd.to_numeric(quote_frame["bid"], errors="coerce")) / ((pd.to_numeric(quote_frame["ask"], errors="coerce") + pd.to_numeric(quote_frame["bid"], errors="coerce")) / 2).clip(lower=1e-12)
        for col in prior.columns:
            frame[col] = frame[col].where(frame[col].notna(), prior[col].to_numpy())
    frame["entry_contract_version"] = ENTRY_CONTRACT_V2
    frame["expected_move_producer"] = "pcs.features.expected_move.calculate_expected_move:v1"
    frame["nearby_strikes_definition"] = "2-below-2-above-distinct:v1"
    frame["later_expirations_definition"] = "distinct-strictly-later:v1"
    frame["calculation_asof"] = frame["date"]
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        frame.to_parquet(temp, index=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    a = audit_inputs(frame)
    return {"ticker": ticker, "candidate_rows": len(frame), "expected_move_1d_populated": int(frame.expected_move_1d.notna().sum()),
            "nearby_strikes_populated": int(frame.nearby_strikes.notna().sum()), "later_expirations_populated": int(frame.later_expirations.notna().sum()),
            "pit": a.lookahead_safe, "audit_inputs": a.can_run_decision_engine, "missing": list(a.missing),
            "status": "BLOCKED" if not a.can_run_decision_engine else "READY"}


if __name__ == "__main__":
    results = [backfill(t) for t in TICKERS]
    target = ROOT / "v2_backfill_audit.json"
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    print(json.dumps(results, indent=2, default=str))
