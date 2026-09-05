from pathlib import Path
import json,hashlib
from datetime import datetime,timezone
from pcs.pool.process import ReadOnlyScanRequest,run_read_only_scan

def main():
 out=Path(__file__).parent;root=out.parent
 req=json.loads((root/'request.json').read_text())
 records={s:json.loads((root/'tickers'/f'{s}.json').read_text()) for s in req['symbols']}
 selected=tuple(json.loads((out/'scan_timeout_queue.json').read_text()))
 assert len(selected)==156 and all(records[s]['final']['status']=='READY' for s in selected)
 assert len(records)==2953 and req['session']=='2026-09-04'
 assert all(records[s]['final']['status']=='READY' for s in ['QQQ','SPY'])
 manifest=Path('data/manifests/storage_manifest.csv');before=hashlib.sha256(manifest.read_bytes()).hexdigest()
 request=ReadOnlyScanRequest(symbols=selected,universe_id='frozen_2953_verified_daily_subset',
  as_of=req['started_at'],mode='EOD',max_workers=8,stage_timeout_seconds=900,
  output_directory=str(out/'read_only_scan_resume'))
 from dataclasses import asdict
 (out/'scan_resume_request.json').write_text(json.dumps({'request':asdict(request),'parent_universe_count':2953,
  'parent_session':req['session'],'started_at':datetime.now(timezone.utc).isoformat(),'canonical_manifest_before':before,
  'process_timeout_seconds':1800,'omitted_symbols':sorted(set(req['symbols'])-set(selected))},indent=2))
 print(json.dumps({'event':'READ_ONLY_SCAN_STARTED','daily_ready_requested':len(selected),'parent_universe':2953}),flush=True)
 result=run_read_only_scan(request,timeout_seconds=1800)
 payload=result.to_dict();(out/'scan_resume_result.json').write_text(json.dumps(payload,indent=2,default=str))
 after=hashlib.sha256(manifest.read_bytes()).hexdigest();assert before==after,'READ_ONLY_MANIFEST_CHANGED'
 (out/'scan_resume_read_only_proof.json').write_text(json.dumps({'before':before,'after':after,'canonical_manifest_unchanged':True,
  'completed_at':datetime.now(timezone.utc).isoformat(),'counters':payload.get('counters')},indent=2))
 print(json.dumps(payload['summary']),flush=True)
if __name__=='__main__':main()
