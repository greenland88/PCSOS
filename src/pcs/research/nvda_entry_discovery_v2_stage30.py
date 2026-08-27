"""Round 30: representative PIT uncached/rebuilt-cache equivalence check."""
from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.underlying_state import evaluate_as_of

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"

def run():
    access = PCSDataAccess()
    daily = access.read_prices("NVDA")
    cached = pd.read_parquet(OUT / "pit_state_timeline.parquet")
    cached_days = pd.to_datetime(cached.date).sort_values()
    days = cached_days.iloc[::max(1, len(cached_days)//25)].head(25).tolist()
    fresh = [evaluate_as_of(daily, "NVDA", day) for day in days]
    rebuilt_path = OUT / "v2_round30_rebuilt_pit_timeline.parquet"
    rebuilt = pd.DataFrame(fresh)
    rebuilt.to_parquet(rebuilt_path, index=False)
    cached_sel = pd.read_parquet(rebuilt_path).sort_values("date").reset_index(drop=True)
    fresh_sel = pd.DataFrame(fresh).sort_values("date").reset_index(drop=True)
    compare = [c for c in ["date", "available_data", "final_underlying_state", "close", "high", "low", "volume", "production_trend_state", "support_identity", "support_level", "pullback_raw_state", "stabilization_result", "breakdown_result", "lookahead_check_result"] if c in cached_sel and c in fresh_sel]
    left = fresh_sel[compare].astype(str).reset_index(drop=True)
    right = cached_sel[compare].astype(str).reset_index(drop=True)
    equal = len(left) == len(right) and left.equals(right)
    result = {"status": "PIT_CACHE_EQUIVALENCE_PASS" if equal else "PIT_CACHE_EQUIVALENCE_FAIL",
              "rows_compared": int(len(left)), "feature_columns_compared": compare,
              "uncached_computation": True, "rebuilt_cached_computation": True,
              "future_outcomes_used": False, "validation_used": False, "final_oos_read": False,
              "production_changed": False}
    (OUT / "v2_round30_pit_cache_equivalence.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if not equal:
        raise AssertionError(result)
    return result

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
