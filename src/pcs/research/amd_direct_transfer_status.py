"""Fail-closed AMD transfer status when no broad PCS outcome artifact is present."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2" / "cross_ticker_transfer"

def run():
    result = {"ticker":"AMD", "status":"INSUFFICIENT_DATA",
              "families": {"PCS_TREND_CONTINUATION":"INSUFFICIENT_DATA", "PCS_CONSTRUCTIVE_RECOVERY":"INSUFFICIENT_DATA"},
              "reason_code":"BROAD_PIT_OUTCOME_UNIVERSE_NOT_AVAILABLE_FOR_DIRECT_TRANSFER",
              "available_artifacts_are":"CURRENT_STRATEGY_OR_STOP_RECONSTRUCTION_NOT_BROAD_NEW_ENTRY",
              "thresholds_modified":False, "final_oos_read":False, "production_changes":False}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "amd_direct_transfer.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__": print(json.dumps(run(), indent=2))
