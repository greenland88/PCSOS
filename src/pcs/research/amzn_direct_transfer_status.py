"""Record a fail-closed AMZN transfer status when broad replay input is absent."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2" / "cross_ticker_transfer"

def run():
    result = {"ticker":"AMZN", "status":"INSUFFICIENT_DATA",
              "families": {"PCS_TREND_CONTINUATION":"INSUFFICIENT_DATA", "PCS_CONSTRUCTIVE_RECOVERY":"INSUFFICIENT_DATA"},
              "reason_code":"CANONICAL_OPTIONS_AMBIGUOUS_QUOTE_KEYS",
              "detail":"ambiguous option quote keys: 52320",
              "broad_pit_map":{"train_dates":1006,"contract_selected_dates":393},
              "available_artifacts_are":"BROAD_PIT_MAP_BUILT_BUT_AUTHORITATIVE_LIFECYCLE_BLOCKED",
              "thresholds_modified":False, "final_oos_read":False, "production_changes":False}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "amzn_direct_transfer.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__": print(json.dumps(run(), indent=2))
