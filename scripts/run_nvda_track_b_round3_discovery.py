from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'research_outputs/nvda_opportunity_expansion_agent/rounds/round_003'; OUT.mkdir(parents=True,exist_ok=True)
d=pd.read_parquet(ROOT/'research_outputs/nvda_opportunity_expansion_agent/pit_state_timeline.parquet'); d.date=pd.to_datetime(d.date).dt.normalize(); base=pd.read_csv(ROOT/'research_outputs/nvda_research_agent/round20_episode_timeline_20260824/baseline_first_entries.csv'); base_dates=set(pd.to_datetime(base.date).dt.normalize())
def eps(ds):
    out=[]
    for x in sorted(pd.Timestamp(v).normalize() for v in ds):
        if not out or (x-out[-1][-1]).days>10: out.append([x])
        else: out[-1].append(x)
    return out
rules={'NVDA_OPP_H004':('sma20_support_rejection',d.support_identity.eq('sma20')&d.trend_result.eq('PASS')&d.pullback_result.ne('PASS')),'NVDA_OPP_H005':('confirmed_swing_support_rejection',d.support_identity.isin(['latest_swing_low','previous_swing_low'])&d.trend_result.eq('PASS')&d.pullback_result.ne('PASS')),'NVDA_OPP_H006':('breakdown_stabilization_transition',d.breakdown_result.eq('PASS')&d.stabilization_result.eq('PASS'))}
rows=[]; er=[]
for hid,(fam,m) in rules.items():
    z=d[m]; e=eps(z.date); overlap=sum(any(x in base_dates for x in q) for q in e); rows.append({'hypothesis_id':hid,'setup_family':fam,'qualifying_dates':len(z),'episodes':len(e),'new_independent_episodes':len(e)-overlap,'overlapping_existing_episodes':overlap,'pit_safe':bool(z.lookahead_check_result.eq('PASS').all()),'years':sorted(z.date.dt.year.unique().tolist())})
    for i,q in enumerate(e,1): er.append({'hypothesis_id':hid,'episode_id':f'{hid}_E{i:03d}','episode_start':q[0],'episode_end':q[-1],'overlaps_baseline':any(x in base_dates for x in q),'new_independent_episode':not any(x in base_dates for x in q)})
pd.DataFrame(rows).to_csv(OUT/'descriptive_hypothesis_results.csv',index=False); pd.DataFrame(er).to_parquet(OUT/'episodes.parquet',index=False); json.dump({'module':'pcs.research.nvda_track_b.discovery','version':'1.0','symbol':'NVDA','as_of':'2023-12-31','status':'DESCRIPTIVE_ONLY','data_timestamp':'2023-12-31','calculation_version':'track-b-round3-v1','run_id':'nvda_opportunity_expansion_round3','request_id':'round3','reason_codes':['PIT_FEATURES_ONLY','FINAL_OOS_NOT_READ','NO_PRODUCTION_CHANGE'],'hypotheses':rows},open(OUT/'discovery_manifest.json','w'),indent=2,default=str); print(pd.DataFrame(rows).to_string(index=False))
