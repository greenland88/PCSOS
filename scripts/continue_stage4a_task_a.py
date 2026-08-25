"""Trace Stage 4A producers and recover only the existing frozen Variant-B IDs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from pcs.research.stage4a_replay import audit_inputs

VARIANT = Path("data/parquet/research/variant_b_full")
OUT = Path("research_outputs/safe_strike_stage4a")
TICKERS = ("NVDA", "AMD", "TSLA", "AMZN")


def candidate_id(row: pd.Series) -> str:
    raw = "|".join((str(row.ticker), pd.Timestamp(row.date).date().isoformat(),
        pd.Timestamp(row.expiration).date().isoformat(), format(float(row.short_strike), ".15g"),
        format(float(row.long_strike), ".15g")))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def frozen_identity(ticker: str) -> dict:
    path = VARIANT / f"{ticker}_full_post2020_2d.parquet"
    frame = pd.read_parquet(path)[["date", "ticker", "expiration", "short_strike", "long_strike"]].copy()
    frame = frame.rename(columns={"date": "decision_date"})
    frame["candidate_id"] = frame.apply(lambda r: candidate_id(r.rename({"decision_date": "date"})), axis=1)
    frame = frame[["ticker", "decision_date", "expiration", "short_strike", "long_strike", "candidate_id"]]
    frame.to_parquet(OUT / "candidate_inputs" / f"{ticker}_frozen_variant_b_identity.parquet", index=False)
    csv = OUT / "candidate_inputs" / f"{ticker}_frozen_variant_b_identity.csv"
    frame.to_csv(csv, index=False)
    canonical = frame.sort_values(list(frame.columns)).to_csv(index=False, lineterminator="\n").encode()
    return {"ticker": ticker, "candidate_count": len(frame), "source_artifact": str(path),
            "date_start": str(pd.to_datetime(frame.decision_date).min().date()),
            "date_end": str(pd.to_datetime(frame.decision_date).max().date()),
            "identity_checksum": hashlib.sha256(canonical).hexdigest()}


def build_stage4a_input_from_variant(ticker: str) -> None:
    """Persist the frozen rows with only fields already present in Variant-B."""
    src = pd.read_parquet(VARIANT / f"{ticker}_full_post2020_2d.parquet").copy()
    daily = pd.read_csv(Path("data/raw/daily_forward_adjusted") / f"{ticker}_daily_qfq.csv")
    daily = daily.rename(columns={"日期": "date", "收盘价": "close"})
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()
    close = daily.set_index("date")["close"]
    out = pd.DataFrame({
        "date": pd.to_datetime(src["date"]).dt.normalize(), "ticker": src["ticker"],
        "expiration": pd.to_datetime(src["expiration"]).dt.normalize(),
        "short_strike": src["short_strike"], "long_strike": src["long_strike"],
        "close": pd.to_datetime(src["date"]).dt.normalize().map(close),
        "initial_credit": src["credit"], "short_delta": src["short_delta"],
        "dte": src["dte"], "atr14": src["atr"],
        "option_volume": src["short_volume"], "open_interest": src["short_oi"],
        "bid_ask_pct": (src["short_ask"] - src["short_bid"]) /
                       ((src["short_ask"] + src["short_bid"]) / 2).clip(lower=1e-12),
    })
    out["candidate_id"] = src.apply(candidate_id, axis=1)
    for field in ("trend_score", "support_level", "normal_daily_move",
                  "nearby_strikes", "later_expirations", "price_confirmation"):
        out[field] = pd.NA
    out.to_parquet(OUT / "candidate_inputs" / f"{ticker}.parquet", index=False)


def attach_existing_trend_fields(ticker: str) -> None:
    """Use the persisted trend-history output, whose producer is the PCS trend pipeline."""
    path = Path("research_outputs/safe_strike_risk_map_v0_1/trend_histories") / f"{ticker}_trend.parquet"
    if not path.exists():
        return
    out_path = OUT / "candidate_inputs" / f"{ticker}.parquet"
    out = pd.read_parquet(out_path)
    trend = pd.read_parquet(path)
    trend["date"] = pd.to_datetime(trend["date"]).dt.normalize()
    trend["support_level"] = trend["support"].map(lambda x: (json.loads(x).get("nearest_support") if isinstance(x, str) else None))
    lookup = trend.drop_duplicates("date").set_index("date")
    dates = pd.to_datetime(out["date"]).dt.normalize()
    out["trend_score"] = out["trend_score"].fillna(dates.map(lookup["trend_score"]))
    out["support_level"] = out["support_level"].fillna(dates.map(lookup["support_level"]))
    out.to_parquet(out_path, index=False)


PRODUCERS = [
    {"field": "support_level", "producer": "pcs.trend.support.analyze_support via pcs.trend.snapshot.build_trend_snapshot", "source": "daily OHLCV + point-in-time indicators/market structure", "status": "REPRODUCIBLE", "notes": "nearest_support; PIT as_of_date"},
    {"field": "normal_daily_move", "producer": "none", "source": "none", "status": "PRODUCER_MISSING", "notes": "only consumed by strike_score; no PCS producer found"},
    {"field": "option_volume", "producer": "pcs.research.variant_b_replay._spread_candidates / entry_candidate_universe", "source": "options_v2 short-leg Volume", "status": "REPRODUCIBLE", "notes": "short-leg volume"},
    {"field": "open_interest", "producer": "pcs.research.variant_b_replay._spread_candidates / entry_candidate_universe", "source": "options_v2 short-leg Open Interest", "status": "REPRODUCIBLE", "notes": "short-leg OI"},
    {"field": "bid_ask_pct", "producer": "pcs.research.variant_b_replay._spread_candidates / entry_candidate_universe", "source": "options_v2 short-leg bid/ask", "status": "REPRODUCIBLE", "notes": "(ask-bid)/max((ask+bid)/2, 1e-12)"},
    {"field": "nearby_strikes", "producer": "none", "source": "none", "status": "PRODUCER_MISSING", "notes": "threshold exists; breadth calculation does not"},
    {"field": "later_expirations", "producer": "none", "source": "none", "status": "PRODUCER_MISSING", "notes": "threshold exists; breadth calculation does not"},
    {"field": "price_confirmation", "producer": "pcs.research.entry_confirmation.analyze_entry_confirmation", "source": "daily OHLCV", "status": "CONTRACT_AMBIGUOUS", "notes": "research score is 0-4, TradeCandidate field is 0-100; no exact mapping"},
]


def main() -> None:
    (OUT / "candidate_inputs").mkdir(parents=True, exist_ok=True)
    identities = [frozen_identity(t) for t in ("AMD", "TSLA")]
    for ticker in ("AMD", "TSLA"):
        build_stage4a_input_from_variant(ticker)
    for ticker in TICKERS:
        attach_existing_trend_fields(ticker)
    (OUT / "producer_trace.json").write_text(json.dumps(PRODUCERS, indent=2), encoding="utf-8")
    (OUT / "frozen_identity_manifest.json").write_text(json.dumps(identities, indent=2), encoding="utf-8")
    audits = []
    for ticker in TICKERS:
        frame = pd.read_parquet(OUT / "candidate_inputs" / f"{ticker}.parquet")
        a = audit_inputs(frame)
        audits.append({"ticker": ticker, "candidate_rows": len(frame), "audit_inputs": a.can_run_decision_engine, "pit_validation": a.lookahead_safe, "missing": list(a.missing), "status": "BLOCKED" if len(frame) == 0 or not a.can_run_decision_engine else "READY"})
    (OUT / "task_a_audit.json").write_text(json.dumps(audits, indent=2), encoding="utf-8")
    print(json.dumps({"producers": PRODUCERS, "identities": identities, "audits": audits}, indent=2))


if __name__ == "__main__":
    main()
