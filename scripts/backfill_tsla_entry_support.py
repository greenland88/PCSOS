"""Research-only PIT-safe TSLA entry-support backfill.

The frozen population is the identity authority.  Support is read from the
existing canonical PIT trend history and classified by the shared entry
contract; no candidate rows are created and no support logic is reimplemented.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pcs.data.daily_provider import DailyDataProvider
from pcs.entry.support_contract import SUPPORT_PRODUCER_VERSION, classify_support

ROOT = Path("research_outputs/tsla_specialized_pcs_20260820")
FROZEN = Path("data/parquet/research/variant_b_full/TSLA_full_post2020_2d.parquet")
TREND = Path("research_outputs/safe_strike_risk_map_v0_1/trend_histories/TSLA_trend.parquet")


def candidate_id(row: pd.Series) -> str:
    raw = "|".join(str(row.get(x, "")) for x in ("ticker", "date", "expiration", "short_strike", "long_strike"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def backfill(root: Path = ROOT) -> dict:
    frozen = pd.read_parquet(FROZEN).copy()
    if len(frozen) != 1119:
        raise ValueError(f"FROZEN_POPULATION_COUNT_MISMATCH:{len(frozen)}")
    frozen["decision_date"] = pd.to_datetime(frozen["date"]).dt.normalize()
    frozen["candidate_id"] = frozen.apply(candidate_id, axis=1)
    if frozen["candidate_id"].duplicated().any():
        raise ValueError("DUPLICATE_FROZEN_CANDIDATE_IDS")

    trend = pd.read_parquet(TREND).copy()
    trend["date"] = pd.to_datetime(trend["date"]).dt.normalize()
    payloads = trend.drop_duplicates("date").set_index("date")["support"]
    daily = DailyDataProvider().build_daily_series(
        "TSLA", as_of_date=frozen["expiration"].max(), start_date=frozen["decision_date"].min()
    )
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()
    bars = daily.drop_duplicates("date").set_index("date")

    rows = []
    for r in frozen.itertuples(index=False):
        payload = None
        if r.decision_date in payloads.index and isinstance(payloads.loc[r.decision_date], str):
            payload = json.loads(payloads.loc[r.decision_date])
        state, level, reason = classify_support(payload)
        bar = bars.loc[r.decision_date] if r.decision_date in bars.index else None
        rows.append({
            "candidate_id": r.candidate_id,
            "decision_date": r.decision_date,
            "support_state": state.value,
            "entry_support_level": level,
            "support_reason": reason,
            "support_producer": "pcs.trend.support.analyze_support",
            "support_producer_version": SUPPORT_PRODUCER_VERSION,
            "support_asof": r.decision_date,
            "ohlcv_asof": r.decision_date,
            "open": None if bar is None else bar.get("open"),
            "high": None if bar is None else bar.get("high"),
            "low": None if bar is None else bar.get("low"),
            "close": None if bar is None else bar.get("close"),
            "volume": None if bar is None else bar.get("volume"),
            "ohlcv_source": "DailyDataProvider:purchased_qfq",
            "pit_status": "PIT" if bar is not None and payload is not None else "PIT_DATA_MISSING",
        })
    out = pd.DataFrame(rows)
    out.to_parquet(root / "tsla_entry_support_backfill.parquet", index=False)

    variants_path = root / "tsla_specialized_candidate_variants.parquet"
    variants = pd.read_parquet(variants_path).copy()
    # Make reruns idempotent after a prior join.
    joined_columns = ["candidate_id", "support_state", "entry_support_level", "support_reason",
                      "support_producer", "support_producer_version", "support_asof",
                      "ohlcv_asof", "open", "high", "low", "close", "volume",
                      "ohlcv_source", "pit_status"]
    variants = variants.drop(columns=[c for c in joined_columns if c in variants.columns])
    before = set(variants["base_candidate_id"].dropna())
    join_payload = out.drop(columns=["decision_date"])
    joined = variants.merge(join_payload, left_on="base_candidate_id", right_on="candidate_id", how="left", validate="many_to_one")
    joined.to_parquet(variants_path, index=False)
    future_leakage = int(
        ((pd.to_datetime(out["support_asof"]) > pd.to_datetime(out["decision_date"])) |
         (pd.to_datetime(out["ohlcv_asof"]) > pd.to_datetime(out["decision_date"]))).sum()
    )
    validation = {
        "research_only": True, "frozen_population": 1119,
        "identity_parity": int(len(out) == 1119 and out.candidate_id.nunique() == 1119),
        "identity_rows": len(out), "identity_unique": int(out.candidate_id.nunique()),
        "future_leakage": future_leakage,
        "duplicate_candidate_ids": int(out.candidate_id.duplicated().sum()),
        "variant_identity_unchanged": before == set(joined.base_candidate_id.dropna()),
        "variant_rows_before": len(variants), "variant_rows_after": len(joined),
        "support_producer": "pcs.trend.support.analyze_support",
        "support_producer_version": SUPPORT_PRODUCER_VERSION,
        "counts": out.support_state.value_counts().reindex(["SUPPORT_FOUND", "NO_SUPPORT", "SUPPORT_DATA_MISSING"], fill_value=0).astype(int).to_dict(),
        "pit_status_counts": out.pit_status.value_counts().to_dict(),
        "status": "PASS" if len(out) == 1119 and out.candidate_id.nunique() == 1119 and out.candidate_id.duplicated().sum() == 0 and future_leakage == 0 else "FAIL",
    }
    (root / "tsla_entry_support_validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    return validation


if __name__ == "__main__":
    print(json.dumps(backfill(), indent=2))
