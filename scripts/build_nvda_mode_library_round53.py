"""Round 53: auditable NVDA mode-library evidence ledger."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"
def run():
 modes=[
  {"mode_id":"PULLBACK_PCS","evidence":"round31","status":"REJECTED_NEGATIVE_TRAIN"},
  {"mode_id":"SUPPORT_RECLAIM_PCS","evidence":"round31","status":"ZERO_QUALIFYING_DATES"},
  {"mode_id":"POST_SELLOFF_PCS","evidence":"round31","status":"REJECTED_NEGATIVE_TRAIN"},
  {"mode_id":"RANGE_CONSOLIDATION_PCS","evidence":"round31","status":"REJECTED_NEGATIVE_TRAIN"},
  {"mode_id":"VOLATILITY_OPPORTUNITY_PCS","evidence":"round43","status":"FROZEN_FAMILY_OVERLAP;NEGATIVE_OUTSIDE"},
  {"mode_id":"MARKET_CONFIRMED_PCS","evidence":"round43","status":"FROZEN_FAMILY_OVERLAP;NEGATIVE_OUTSIDE"},
  {"mode_id":"RELATIVE_STRENGTH_DIVERGENCE","evidence":"round40","status":"FROZEN_FAMILY_OVERLAP;ONE_INDEPENDENT_EPISODE"},
  {"mode_id":"RANGE_BREAKOUT","evidence":"round45-46","status":"FROZEN_FAMILY_OVERLAP;THREE_INDEPENDENT_EPISODES"},
  {"mode_id":"SUPPORT_SMA50_RECLAIM","evidence":"round48","status":"WEAK_EDGE;INSUFFICIENT"},
  {"mode_id":"QUIET_TO_VOLATILITY_EXPANSION","evidence":"round52","status":"SIX_EPISODES;PNL_CONCENTRATED"},
  {"mode_id":"SINGLE_STOCK_STRENGTH_MARKET_WEAK","evidence":"round44","status":"REJECTED_NEGATIVE_TRAIN"},
  {"mode_id":"POST_SELLOFF_RECLAIM","evidence":"round47","status":"REJECTED_NEGATIVE_TRAIN"},
 ]
 bad={"normal_loss":"2 TRAIN rows; no recurring signature","stop_loss":"190 TRAIN rows; down-day/ATR states weakly elevated but no reliable filter","tail_loss":"1 TRAIN row; too sparse for inference","disposition":"NO_RELIABLE_FILTER","forced_no_trade":False}
 out={"module":"pcs.research.nvda_mode_library_round53","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","authoritative_train_executable_dates":623,"preserved_rule_families":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"modes":modes,"bad_state_disposition":bad,"next_branch":"OUTCOME_CONDITIONED_PIT_STATE_COMBINATIONS_ONLY_IF_PREDECLARED","validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":True,"reason_codes":["NVDA_ONLY","MODE_LIBRARY_AUDIT","FROZEN_623_DATE_TRAIN_UNIVERSE","NO_AUTO_PROMOTION","NO_FORCED_NO_TRADE","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}
 (OUT/"v2_round53_mode_library.json").write_text(json.dumps(out,indent=2),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2))
