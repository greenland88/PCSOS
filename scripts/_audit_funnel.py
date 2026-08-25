import pandas as pd, json
from pathlib import Path
from pcs.data.access import PCSDataAccess
from pcs.research.entry_candidate_universe import build_historical_setup_context,_atr14
a=PCSDataAccess(); root=Path('research_outputs/amd_current_strategy_replay_train_plumbing')
d=a.read_prices('AMD'); d.date=pd.to_datetime(d.date).dt.normalize(); d=d.sort_values('date'); b=a.read_prices('QQQ',d.date.min(),d.date.max()); b.date=pd.to_datetime(b.date).dt.normalize(); end=pd.Timestamp('2025-12-31'); src=a.resolve_source('options','AMD'); start=max(pd.Timestamp(d.date.min()),pd.Timestamp(src.first_date)); t=d[d.date.between(start,end)].copy(); t['atr']=_atr14(t)
rows=[]
for i,day in enumerate(t.date):
 c=build_historical_setup_context(t,b,day,'AMD','QQQ')
 if not c.get('available'): continue
 s=c['snapshot']; tg=getattr(c.get('trend_gate_result'),'trend_gate_result',None); pg=getattr(c.get('pullback_gate_result'),'pullback_gate_result',None); su=getattr(s.support,'support_confluence_state',None); pr=bool(getattr(s.cleanliness,'available',False)); fs=[]
 for n,v,r in [('trend',tg=='PASS',tg or 'UNKNOWN'),('pullback',pg=='PASS',pg or 'UNKNOWN'),('support',su in {'moderate','strong'},'PASS' if su in {'moderate','strong'} else str(su)),('predictability',pr,'PASS' if pr else 'UNAVAILABLE')]:
  if not v: fs.append(n)
 rows.append({'decision_date':day,'trend_pass':tg=='PASS','pullback_pass':pg=='PASS','support_pass':su in {'moderate','strong'},'predictability_pass':pr,'trend_reason':tg,'pullback_reason':pg,'support_reason':su,'predictability_reason':'PASS' if pr else 'UNAVAILABLE','failed_gate_count':len(fs),'failed_gates':','.join(fs),'setup_eligible':not fs})
 if i%200==0: print(i,flush=True)
s=pd.DataFrame(rows); s.to_parquet(root/'day_level_setup_gate_provenance.parquet',index=False)
summary={'FEATURE_READY_DAYS':len(s),'PASS_ALL':int(s.setup_eligible.sum()),'FAILED_EXACTLY_1_GATE':int((s.failed_gate_count==1).sum()),'FAILED_EXACTLY_2_GATES':int((s.failed_gate_count==2).sum()),'FAILED_3_OR_MORE_GATES':int((s.failed_gate_count>=3).sum())}
for x in ['trend','pullback','support','predictability']: summary['ONLY_'+x.upper()+'_FAILED']=int((s.failed_gates==x).sum())
print(json.dumps(summary,indent=2)); print(s[~s.setup_eligible].failed_gates.value_counts().to_string())
(root/'funnel_setup_summary.json').write_text(json.dumps(summary,indent=2))
