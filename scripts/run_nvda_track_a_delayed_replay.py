from pathlib import Path
import json,yaml,pandas as pd
from pcs.research.research_framework import from_mapping
from pcs.research.current_strategy_replay import run_current_strategy_replay
from pcs.data.price_basis import load_corporate_actions
R=Path(__file__).resolve().parents[1]; O=R/'research_outputs/nvda_research_agent/track_a_round23_delayed_replay_20260824'; O.mkdir(parents=True,exist_ok=True)
raw=yaml.safe_load((R/'research_configs/nvda_research_agent_round8_current_replay.yaml').read_text()); raw['research_id']='nvda_track_a_delayed_replay_round23'
dates=pd.read_csv(R/'research_outputs/nvda_research_agent/round20_episode_timeline_20260824/episode_first_state_dates.csv'); date_cols=['first_baseline_entry','first_downside_slowdown','first_stabilizing','first_ma20_reclaim','first_qqq_above50','first_qqq_slowdown']; execution_dates=sorted({str(pd.Timestamp(x).date()) for col in date_cols for x in dates[col].dropna()})
raw['signal_definition']={'creates_new_entry_dates':False,'purpose':'TRACK_A_EXECUTION_ONLY_WITH_FROZEN_EPISODES','track_a_execution_only':True,'execution_dates':execution_dates}; spec=from_mapping(raw)
rep=run_current_strategy_replay(spec,output_dir=O,price_basis_service=load_corporate_actions(R/'config/data/corporate_actions.csv')); base=O/spec.research_id; c=pd.read_parquet(base/'candidates.parquet'); l=pd.read_parquet(base/'lifecycle_results.parquet'); c.to_parquet(O/'track_a_candidates.parquet',index=False); l.to_parquet(O/'track_a_lifecycle.parquet',index=False); (O/'track_a_replay_report.json').write_text(json.dumps(rep,indent=2,default=str)); print({'candidates':len(c),'lifecycle':len(l),'dates':c.date.nunique()})
