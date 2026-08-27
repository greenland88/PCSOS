"""Fail-closed readiness report for the TSLA specialized research dataset."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import pandas as pd

ROOT = Path("research_outputs/tsla_specialized_pcs_20260820")
FROZEN = Path("data/parquet/research/variant_b_full/TSLA_full_post2020_2d.parquet")

def cid(row):
    raw = "|".join(str(row.get(x, "")) for x in ("ticker", "date", "expiration", "short_strike", "long_strike"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

def main():
    frozen = pd.read_parquet(FROZEN).copy(); frozen["candidate_id"] = frozen.apply(cid, axis=1)
    variants = pd.read_parquet(ROOT / "tsla_specialized_candidate_variants.parquet")
    backfill = pd.read_parquet(ROOT / "tsla_entry_support_backfill.parquet")
    quotes = pd.read_parquet(ROOT / "tsla_specialized_daily_quotes.parquet")
    marks = pd.read_parquet(ROOT / "tsla_specialized_spread_marks.parquet")
    progress = json.loads((ROOT / "tsla_specialized_quote_progress.json").read_text())
    profiles = sorted(variants.research_variant.dropna().unique())
    support_ok = len(backfill) == 1119 and backfill.candidate_id.nunique() == 1119 and backfill.support_state.eq("SUPPORT_FOUND").all()
    underlying = pd.read_parquet(ROOT / "tsla_specialized_daily_underlying.parquet")
    rows = []
    for profile in profiles:
        p = variants[variants.research_variant.eq(profile)]
        q = quotes[quotes.research_variant.eq(profile)]
        m = marks[marks.research_variant.eq(profile)]
        ef = pd.read_parquet(ROOT / "tsla_specialized_entry_features.parquet")
        ef = ef[ef.research_variant.eq(profile)]
        rows.append({
            "atr_profile": profile,
            "exact_spreads": int(p.status.eq("VALID").sum()),
            "underlying_complete": bool(set(p.base_candidate_id).issubset(set(underlying.base_candidate_id))),
            "quote_lifecycle_complete": bool(len(q) > 0 and q.availability.eq("AVAILABLE").all()),
            "spread_marks_complete": bool(len(m) > 0 and m.mark_valid.all()),
            "support_complete": support_ok,
            "compensation_complete": bool(len(ef) > 0 and ef.credit_per_ATR.notna().all() and ef.credit_width_ratio.notna().all() and ef.credit_per_expected_move.notna().all() and ef.credit_per_strike_buffer_ATR.notna().all()),
            "credit_atr_coverage": float(ef.credit_per_ATR.notna().mean()) if len(ef) else 0.0,
            "credit_width_coverage": float(ef.credit_width_ratio.notna().mean()) if len(ef) else 0.0,
            "credit_expected_move_coverage": float(ef.credit_per_expected_move.notna().mean()) if len(ef) else 0.0,
            "credit_strike_buffer_atr_coverage": float(ef.credit_per_strike_buffer_ATR.notna().mean()) if len(ef) else 0.0,
        })
    q23 = variants[variants.research_variant.eq("ATR_2.3")]
    matched = q23.merge(frozen, left_on="base_candidate_id", right_on="candidate_id", suffixes=("_reconstructed", "_authoritative"))
    mismatch = {}
    for a, b in [("decision_date_reconstructed", "date"), ("expiration_reconstructed", "expiration"), ("short_strike", "short_strike"), ("long_strike", "long_strike"), ("width", "spread_width"), ("initial_credit", "credit")]:
        if a in matched and b in matched:
            left, right = matched[a], matched[b]
            if "date" in a or "date" in b or "expiration" in a or "expiration" in b: left, right = pd.to_datetime(left), pd.to_datetime(right)
            else: left, right = pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce")
            mismatch[a] = int((left.fillna(-999999) != right.fillna(-999999)).sum())
    missing_quote_days = int(sum(x["missing"] for x in progress))
    parity = {"status": "DATA_UNAVAILABLE", "classification": "DATA_UNAVAILABLE", "matched_reconstructed_rows": len(matched), "frozen_rows": len(frozen), "mismatch_counts": mismatch, "exit_date_mismatch": "NOT_REPLAYED_DUE_TO_QUOTE_GAPS", "exit_reason_mismatch": "NOT_REPLAYED_DUE_TO_QUOTE_GAPS", "realized_pnl_mismatch": "NOT_REPLAYED_DUE_TO_QUOTE_GAPS", "causes": [x for x in progress if x["missing"]], "missing_quote_days": missing_quote_days}
    table = pd.DataFrame(rows); table.to_json(ROOT / "tsla_specialized_data_readiness.json", orient="records", indent=2)
    report = {"research_only": True, "baseline_23_parity": parity, "structure_aware_stop_readiness": "BLOCKED_DATA_UNAVAILABLE", "compensation_coverage": rows, "final_verdict": "TSLA SPECIALIZED DATASET PARTIAL — QUOTE COVERAGE", "profile_replay_ready": False}
    (ROOT / "tsla_specialized_final_readiness.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))

if __name__ == "__main__": main()
