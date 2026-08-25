import json
import os
from pathlib import Path
import pandas as pd
from pcs.research.stage4a_replay import audit_inputs

path = Path("research_outputs/safe_strike_stage4a/authoritative_amzn_794_entry_contract_v2.parquet")
out = pd.read_parquet(path)
source = pd.read_parquet("data/parquet/research/variant_b_full/AMZN_full_post2020_2d.parquet")
trend = pd.read_parquet("research_outputs/safe_strike_risk_map_v0_1/trend_histories/AMZN_trend.parquet")
trend["date"] = pd.to_datetime(trend["date"]).dt.normalize()
if "candidate_id" not in source.columns or "candidate_id" not in out.columns:
    raise ValueError("AMZN_CANDIDATE_ID_MISSING_FOR_FINALIZE")
if set(source.candidate_id.astype(str)) != set(out.candidate_id.astype(str)):
    raise ValueError("AMZN_CANDIDATE_IDENTITY_MISMATCH_FOR_FINALIZE")
source_by_id = source.drop_duplicates("candidate_id").assign(_candidate_key=lambda x: x.candidate_id.astype(str)).set_index("_candidate_key")
out_ids = out.candidate_id.astype(str)
out["initial_credit"] = out_ids.map(source_by_id["credit"])
out["atr14"] = out_ids.map(source_by_id["atr"])
out["trend_score"] = out["date"].map(trend.drop_duplicates("date").set_index("date")["trend_score"])
parquet_tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
try:
    out.to_parquet(parquet_tmp, index=False)
    os.replace(parquet_tmp, path)
finally:
    parquet_tmp.unlink(missing_ok=True)
audit = audit_inputs(out)
report = {
    "ticker": "AMZN", "candidate_count": len(out), "identity_match": True,
    "expected_move_populated": int(out.expected_move_1d.notna().sum()),
    "support_populated": int(out.support_level.notna().sum()),
    "support_unavailable": int(out.support_level.isna().sum()),
    "option_volume_populated": int(out.option_volume.notna().sum()),
    "open_interest_populated": int(out.open_interest.notna().sum()),
    "bid_ask_populated": int(out.bid_ask_pct.notna().sum()),
    "nearby_populated": int(out.nearby_strikes.notna().sum()),
    "later_expirations_populated": int(out.later_expirations.notna().sum()),
    "price_confirmation_populated": int(out.price_confirmation.notna().sum()),
    "pit": audit.lookahead_safe, "audit_inputs": audit.can_run_decision_engine,
    "audit_missing": list(audit.missing),
    "legacy_309_status": "LEGACY_SAFE_STRIKE_STAGE2_RESEARCH_ONLY",
    "canonical_source": "data/parquet/options_v2", "batch2_direct_read": False,
    "status": "READY" if audit.can_run_decision_engine else "BLOCKED"
}
meta = Path("research_outputs/safe_strike_stage4a/authoritative_amzn_794_entry_contract_v2_readiness.json")
meta_tmp = meta.with_name(f".{meta.name}.{os.getpid()}.tmp")
try:
    meta_tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(meta_tmp, meta)
finally:
    meta_tmp.unlink(missing_ok=True)
print(json.dumps(report, indent=2))
