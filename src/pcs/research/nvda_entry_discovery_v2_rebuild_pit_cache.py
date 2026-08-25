"""Rebuild only the V2 PIT context cache with explicit upstream identity."""
from pathlib import Path
from dataclasses import asdict
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.underlying_state import evaluate_as_of
from pcs.trend.config import TrendIndicatorConfig
from pcs.research.pit_cache_identity import build_pit_cache_identity

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"

def run():
    access = PCSDataAccess()
    daily = access.read_prices("NVDA")
    old = pd.read_parquet(OUT / "pit_state_timeline.parquet")
    days = pd.to_datetime(old.date).sort_values().tolist()
    bounded = daily[pd.to_datetime(daily.date).isin(days)].copy()
    states = [evaluate_as_of(daily, "NVDA", day) for day in days]
    source = access.resolve_source("daily", "NVDA", days[0], days[-1])
    identity = build_pit_cache_identity(symbol="NVDA", daily_data_identity=source.source_version,
        date_range={"start": str(days[0].date()), "end": str(days[-1].date())},
        feature_config=asdict(TrendIndicatorConfig()), research_config={"agent": "NVDA_PCS_ENTRY_DISCOVERY_AGENT_V2"})
    frame = pd.DataFrame(states)
    frame["symbol"] = "NVDA"
    frame["ticker"] = "NVDA"
    for k, v in identity.items(): frame[k] = v
    frame["created_at"] = pd.Timestamp.utcnow().isoformat()
    for c in frame.columns:
        if frame[c].map(lambda x: isinstance(x, (list, dict, tuple))).any():
            frame[c] = frame[c].map(lambda x: json.dumps(x, default=str) if isinstance(x, (list, dict, tuple)) else x)
    frame.to_parquet(OUT / "pit_state_timeline.parquet", index=False)
    result = {"status":"REBUILT", "rows":len(frame), "identity":identity,
              "affected_artifact":"pit_state_timeline only", "options_rebuilt":False,
              "canonical_daily_rebuilt":False, "price_basis_rebuilt":False,
              "corporate_actions_rebuilt":False, "lifecycle_rebuilt":False,
              "final_oos_read":False, "production_changed":False}
    (OUT / "v2_pit_cache_rebuild_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
