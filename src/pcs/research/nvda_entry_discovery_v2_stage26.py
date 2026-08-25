"""Round 26: evidence frontier review of previously replayed distinct families."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs" / "nvda_entry_discovery_agent_v2"

def run():
    candidates = [
        {"id":"V2_H015", "family":"volatility expansion + strength", "episodes":16, "pnl":2652.0, "pf":2.626, "stop_rate":0.125, "years":3, "decision":"INSUFFICIENT_CONCENTRATION_EVIDENCE"},
        {"id":"V2_H016", "family":"market context confirmed", "episodes":16, "pnl":1948.0, "pf":2.089, "stop_rate":0.1875, "years":3, "decision":"INSUFFICIENT_CONCENTRATION_EVIDENCE"},
        {"id":"V2_H021", "family":"recovery with balanced volatility", "episodes":9, "pnl":1421.0, "pf":3.45, "stop_rate":0.2222, "years":4, "decision":"INSUFFICIENT_EPISODES"},
        {"id":"V2_H022", "family":"market participation continuation", "episodes":23, "pnl":3823.0, "pf":2.797, "stop_rate":0.1739, "years":4, "decision":"NOT_INDEPENDENT_FROM_H010_VALIDATION"},
        {"id":"V2_H023", "family":"relative strength continuation", "episodes":22, "pnl":663.0, "pf":1.145, "stop_rate":0.2273, "years":3, "decision":"WEAK_EXPECTANCY_HIGH_WORST_TRADE"},
    ]
    result = {"round":26, "replayed_candidates":candidates,
              "new_family_promoted":False, "h010_h027_modified":False,
              "final_oos_touched":False, "production_changed":False,
              "next_action":"CONTINUE_BROAD_PIT_DISCOVERY_WITHOUT_REFINING_REJECTED_FAMILIES"}
    (OUT / "v2_round26_candidate_frontier_review.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
