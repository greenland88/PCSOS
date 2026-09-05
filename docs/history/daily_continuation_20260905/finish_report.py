from pathlib import Path
import json,csv,hashlib
from collections import Counter
import pandas as pd
from pcs.data.access import PCSDataAccess
out=Path(__file__).parent;root=out.parent
request=json.loads((root/'request.json').read_text());scan=json.loads((out/'scan_result.json').read_text())
session_verified=scan['snapshot'].get('effective_daily_session')==request['session']
assert not any(r['final_action']=='PCS_TRADE_READY' for r in scan['ticker_results'])
assert all(scan['counters'].get(k,0)==0 for k in ['provider_calls','promotion_calls','recovery_calls'])
results={r['symbol']:r for r in scan['ticker_results']};assert len(results)==len(scan['ticker_results'])
segments=[scan]
resume_path=out/'scan_resume_result.json'
if resume_path.exists():
 resume=json.loads(resume_path.read_text());segments.append(resume)
 expected={r['symbol'] for r in scan['ticker_results'] if r['timing_status']=='NOT_EVALUATED'}
 assert {r['symbol'] for r in resume['ticker_results']}==expected
 for field in ['code_revision','engine_version','effective_daily_session','as_of']:
  assert scan['snapshot'][field]==resume['snapshot'][field],field
 for row in resume['ticker_results']:results[row['symbol']]=row
 assert all(part['counters'].get(k,0)==0 for part in segments for k in ['provider_calls','promotion_calls','recovery_calls'])
records={s:json.loads((root/'tickers'/f'{s}.json').read_text()) for s in request['symbols']}
ready={s for s,r in records.items() if r['final']['status']=='READY'};assert set(results)==ready
old={r['symbol']:r for r in csv.DictReader((root/'per_ticker.csv').open(encoding='utf-8-sig'))}
q=json.loads((out/'remaining_queue.json').read_text());zero=set(q['unchanged_zero_rows_not_retried']);partial=set(json.loads((out/'before_final_summary.json').read_text())['blocked_source_symbols_by_status']['IMPORTED_CANONICAL_NOT_READY'])
access=PCSDataAccess.canonical();manifest=access._read_manifest(access.manifest_path)
rows=[]
for sym in request['symbols']:
 r=records[sym];f=r['final'];i=f.get('identity',{});s=results.get(sym);row=old[sym].copy()
 row['round_before_daily']=json.loads((out/'baseline_tickers'/f'{sym}.json').read_text())['final']['status']
 row.update(final_own_daily=f['status'],generation_ids=i.get('generation_id',''),dataset_fingerprint=i.get('dataset_fingerprint',''),checksum=i.get('checksum',''),price_basis=i.get('price_basis',''),corporate_action_version=i.get('corporate_action_version',''),actions=';'.join(a['action'] for a in r['actions']),receipt_sha256=hashlib.sha256((root/'tickers'/f'{sym}.json').read_bytes()).hexdigest())
 if sym in ready:status='DAILY_READY';missing=[]
 else:
  status='CONFLICT' if r['primary']=='D' else 'SOURCE_ZERO_ROWS' if sym in zero else 'SOURCE_PARTIAL_COVERAGE' if sym in partial else 'RATE_LIMITED' if sym in q['rate_limited'] else 'SOURCE_NOT_QUERIED_RATE_DEFERRED'
  missing=f.get('missing_sessions',row['missing_sessions'].split(';') if row['missing_sessions'] else [])
 if sym=='TLT':missing=json.loads((out/'TLT_partial_readback.json').read_text())['missing_sessions']
 row.update(final_status=status,missing_sessions=';'.join(missing),warmup_session_rows=200-len(missing),target_session_present=request['session'] not in missing,reason_codes=';'.join(f.get('reason_codes',[])))
 active=manifest[(manifest.dataset=='daily')&(manifest.symbol==sym)&pd.to_numeric(manifest.year).between(2025,2026)]
 row['active_partition_identities']=json.dumps([{'partition':str(a.partition_ids),'generation':str(a.active_generation),'checksum':str(a.content_hash),'file_hash':str(a.file_hash)} for a in active.itertuples() if str(a.active_generation).lower() not in {'','nan','none'}],separators=(',',':'))
 row['scan_requested']=s is not None
 row['scan_run_id']=s['run_id'] if s else ''
 for key in ['eligibility_status','timing_status','options_status','event_status','portfolio_status','final_action','spread_count','feature_max_date']:
  row['scan_'+key]=s.get(key) if s else 'NOT_EVALUATED'
 row['scan_reason_codes']=';'.join(s.get('reason_codes',[])) if s else 'DAILY_DATA_BLOCKED'
 row['resume_condition']='none' if sym in ready else 'source-origin/adjustment/version evidence required' if status=='CONFLICT' else q['condition'] if status in {'RATE_LIMITED','SOURCE_NOT_QUERIED_RATE_DEFERRED'} else 'new evidence of source coverage; no unchanged-source download'
 rows.append(row)
with (out/'per_ticker_final.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
valid_timing=[r for r in results.values() if r['timing_status']!='NOT_EVALUATED' and 'TIMING_EVIDENCE_UNAVAILABLE' not in r['reason_codes']]
summary={'session_verified':session_verified,'requested_universe':2953,'daily_ready_scan_requested':len(ready),'daily_data_blocked':2953-len(ready),
 'timing_successfully_evaluated':len(valid_timing),'timing_not_successfully_evaluated':len(results)-len(valid_timing),
 'timing_statuses':dict(Counter(r['timing_status'] for r in results.values())),
 'final_actions':dict(Counter(r['final_action'] for r in results.values())),
 'strategy_rejected':sum(r['final_action']=='REJECTED' for r in results.values()),
 'timing_pass_symbols':[r['symbol'] for r in results.values() if r['timing_status']=='TIMING_ENTRY_READY'],
 'options_statuses':dict(Counter(r['options_status'] for r in results.values())),
 'options_successfully_evaluated':sum(r['options_status'] in {'PASS','DISCOVERED','REJECT'} for r in results.values()),
 'event_statuses':dict(Counter(r['event_status'] for r in results.values())),
 'portfolio_statuses':dict(Counter(r['portfolio_status'] for r in results.values())),
 'reason_counts':dict(Counter(c for r in results.values() for c in r['reason_codes'])),
 'source_counters':dict(Counter({k:sum(part['counters'].get(k,0) for part in segments) for k in scan['counters']})),'run_id':scan['snapshot']['run_id'],'snapshot':scan['snapshot'],'run_status':'AUDIT_OF_SCAN_AND_SCOPED_RESUME' if len(segments)>1 else scan['summary'].get('run_status'),
 'segments':[{'run_id':part['snapshot']['run_id'],'status':part['summary'].get('run_status'),'requested':len(part['ticker_results']),'stage_latency_ms':part['stage_latency_ms']} for part in segments],
 'remaining_timeout_symbols':[r['symbol'] for r in results.values() if any(c in r['reason_codes'] for c in ['WORKER_TIMEOUT','STAGE_DEADLINE_NOT_STARTED','POOL_SCAN_TIMEOUT'])],
 'one_result_per_original_ticker':len(rows)==2953==len({r['symbol'] for r in rows})}
(out/'scan_summary.json').write_text(json.dumps(summary,indent=2));(out/'timing_candidates.json').write_text(json.dumps([r for r in results.values() if r['timing_status']=='TIMING_ENTRY_READY'],indent=2))
print(json.dumps({k:v for k,v in summary.items() if k not in ['snapshot','reason_counts']}))
