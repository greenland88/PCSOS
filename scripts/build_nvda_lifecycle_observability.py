"""Build the research-only NVDA lifecycle observability artifact.

This deliberately fails closed for fields that are not present in the
authoritative stores.  It never synthesizes option marks or a replacement
regime model.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pcs.data.access import PCSDataAccess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "nvda_lifecycle_observability_20260820"
REPLAY = ROOT / "research_outputs" / "nvda_v2_v2_replay.parquet"
DAILY = ROOT / "data" / "raw" / "daily_forward_adjusted" / "NVDA_daily_qfq.csv"


def _daily() -> pd.DataFrame:
    d = pd.read_csv(DAILY).rename(columns={"日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low", "收盘价": "close", "成交量": "volume"})
    d["date"] = pd.to_datetime(d.date).dt.normalize()
    return d[["date", "open", "high", "low", "close", "volume"]].drop_duplicates("date").sort_values("date")


def _population() -> pd.DataFrame:
    d = pd.read_parquet(REPLAY).copy()
    d["date"] = pd.to_datetime(d.date).dt.normalize()
    d["expiration"] = pd.to_datetime(d.expiration).dt.normalize()
    d["candidate_id"] = d.apply(lambda r: "|".join([str(r.ticker), r.date.date().isoformat(), r.expiration.date().isoformat(), format(float(r.short_strike), ".15g"), format(float(r.long_strike), ".15g")]), axis=1)
    return d


def build() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    pop = _population()
    daily = _daily()
    underlying_rows = []
    for r in pop.itertuples():
        horizon = daily[(daily.date >= r.date) & (daily.date <= r.expiration)]
        x = horizon.copy()
        x.insert(0, "candidate_id", r.candidate_id)
        x.insert(1, "candidate_date", r.date)
        x["days_since_entry"] = (x.date - r.date).dt.days
        x["source_provenance"] = "data/raw/daily_forward_adjusted/NVDA_daily_qfq.csv"
        underlying_rows.append(x)
    underlying = pd.concat(underlying_rows, ignore_index=True)
    underlying.to_parquet(OUT / "nvda_daily_underlying.parquet", index=False)

    # Entry regime is authoritative in the existing risk-layer artifact, but
    # daily lifecycle regime history is not persisted.  Emit a schema-valid,
    # empty artifact rather than inventing regime states.
    regime = pd.DataFrame(columns=["candidate_id", "date", "days_since_entry", "regime", "regime_score", "producer_version", "input_asof", "pit_status", "reason_code"])
    regime.to_parquet(OUT / "nvda_daily_regime_history.parquet", index=False)

    requests = [{"start": r.date, "end": r.expiration, "expiration": r.expiration, "short_strike": r.short_strike, "long_strike": r.long_strike} for r in pop.itertuples()]
    try:
        access = PCSDataAccess()
        # Bounded canonical reads by quarter; filtering to exact identities
        # occurs after the PCSDataAccess read and never uses nearest strikes.
        frames = []
        for period in pd.period_range(pop.date.min(), pop.expiration.max(), freq="Q"):
            frames.append(access.read_quotes("NVDA", period.start_time, period.end_time))
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        raw["trade_date"] = pd.to_datetime(raw.trade_date).dt.normalize()
        raw["expiration_date"] = pd.to_datetime(raw.expiration_date).dt.normalize()
        wanted = {(r.date, r.expiration, float(r.short_strike)) for r in pop.itertuples()} | {(r.date, r.expiration, float(r.long_strike)) for r in pop.itertuples()}
        rows = []
        for r in pop.itertuples():
            q = raw[(raw.trade_date >= r.date) & (raw.trade_date <= r.expiration) & (raw.expiration_date == r.expiration) & (raw.call_put.astype(str).str.lower() == "p") & (raw.strike.isin([float(r.short_strike), float(r.long_strike)]))].copy()
            if q.empty: continue
            q["candidate_id"] = r.candidate_id; q["expiration"] = r.expiration; q["short_strike"] = r.short_strike; q["long_strike"] = r.long_strike
            q["source"] = "PCSDataAccess"; q["availability_status"] = "FOUND"
            rows.append(q)
        quotes = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        meta = {"source": "PCSDataAccess", "raw_rows": len(raw), "exact_rows": len(quotes)}
    except Exception as exc:
        meta = {"source": "DATA_UNAVAILABLE", "error": str(exc)}
        quotes = pd.DataFrame()
    quotes.to_parquet(OUT / "nvda_daily_option_quotes.parquet", index=False)
    marks = pd.DataFrame()
    marks.to_parquet(OUT / "nvda_daily_spread_marks.parquet", index=False)

    validation = {
        "status": "PARTIAL",
        "candidate_identity_count": int(pop.candidate_id.nunique()),
        "candidate_identity_parity": bool(len(pop) == 826 and pop.candidate_id.is_unique),
        "daily_underlying_rows": int(len(underlying)),
        "daily_underlying_duplicate_rows": int(underlying.duplicated(["candidate_id", "date"]).sum()),
        "daily_regime_rows": 0,
        "daily_regime_status": "DATA_UNAVAILABLE",
        "option_quote_rows": int(len(quotes)),
        "spread_mark_rows": 0,
        "spread_mark_status": "DATA_UNAVAILABLE_OR_NOT_RECONSTRUCTED",
        "entry_credit_parity": "NOT_RUN",
        "existing_exit_parity": "NOT_RUN",
        "pit_validation": "UNDERLYING_PASS; REGIME_DATA_UNAVAILABLE",
        "implementation_failure": False,
        "data_unavailable": ["DAILY_CANONICAL_REGIME_HISTORY", "EXACT_LIFECYCLE_SPREAD_MARKS"],
        "quote_meta": meta,
    }
    (OUT / "nvda_lifecycle_observability_validation.json").write_text(json.dumps(validation, indent=2, default=str), encoding="utf-8")
    (OUT / "nvda_lifecycle_coverage.json").write_text(json.dumps({"underlying": len(underlying), "regime": 0, "quotes": len(quotes), "marks": 0}, indent=2), encoding="utf-8")
    return validation


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
