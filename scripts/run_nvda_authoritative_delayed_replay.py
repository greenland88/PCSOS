from pathlib import Path
import json, yaml, pandas as pd
from pcs.research.research_framework import from_mapping
from pcs.research.current_strategy_replay import run_current_strategy_replay
from pcs.data.price_basis import load_corporate_actions
R=Path(__file__).resolve().parents[1]; OUT=R/'research_outputs/nvda_research_agent/round21_authoritative_delayed_replay_20260824'; OUT.mkdir(parents=True,exist_ok=True)
raw=yaml.safe_load((R/'research_configs/nvda_research_agent_round8_current_replay.yaml').read_text()); raw['research_id']='nvda_authoritative_delayed_replay_round21'; frozen=R/'research_outputs/nvda_research_agent/round21_authoritative_delayed_replay_20260824/authoritative_candidates.parquet'; old_dates=sorted(pd.to_datetime(pd.read_parquet(frozen)['date']).dt.strftime('%Y-%m-%d').unique().tolist()) if frozen.exists() else []; raw['signal_definition']={**raw.get('signal_definition',{}),'benchmark_symbol':'QQQ','execution_dates':old_dates,'track_a_execution_only':True}; spec=from_mapping(raw)
report=run_current_strategy_replay(spec,output_dir=OUT,price_basis_service=load_corporate_actions(R/'config/data/corporate_actions.csv'))
base=OUT/spec.research_id; cand=pd.read_parquet(base/'candidates.parquet'); life=pd.read_parquet(base/'lifecycle_results.parquet') if (base/'lifecycle_results.parquet').exists() else pd.DataFrame(); cand.to_parquet(OUT/'authoritative_candidates.parquet',index=False); life.to_parquet(OUT/'authoritative_lifecycle.parquet',index=False); (OUT/'authoritative_replay_report.json').write_text(json.dumps(report,indent=2,default=str)); print(json.dumps({'candidates':len(cand),'lifecycle_results':len(life),'base':str(base)},indent=2))
