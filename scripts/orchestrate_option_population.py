from __future__ import annotations
import json, subprocess, sys, time, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd

WINDOWS={'SPY':('2020-01-02','2026-07-31'),'QQQ':('2020-01-01','2026-07-31'),'NVDA':('2024-06-10','2026-07-31'),'AMZN':('2022-06-06','2026-07-31')}
MAX_PARALLEL_TICKERS=2
MAX_PARALLEL_MONTHS_PER_TICKER=1
ROOT=Path('research_outputs/safe_strike_process_isolated'); OUT=ROOT/'qualified'; CK=ROOT/'checkpoint.jsonl'; ROOT.mkdir(parents=True,exist_ok=True)
def months(a,b):
 c=pd.Timestamp(a).to_period('M'); z=pd.Timestamp(b).to_period('M')
 while c<=z:
  yield max(pd.Timestamp(a),c.start_time).normalize(),min(pd.Timestamp(b),c.end_time).normalize(); c+=1
def valid(path,symbol,a,b):
 try:
  x=pd.read_parquet(path); req=['ticker','entry_date','spot','ATR','expiration','DTE','short_strike','long_strike','width','credit']; return all(c in x and not x[c].isna().any() for c in req) and (len(x)==0 or (pd.to_datetime(x.entry_date).between(a,b).all() and x.ticker.eq(symbol).all())) and x.duplicated(['ticker','entry_date','expiration','short_strike','long_strike']).sum()==0
 except Exception:return False
def main():
 lock=threading.Lock()
 def run_ticker(s):
  a,b=WINDOWS[s]
  records=[]
  for lo,hi in months(a,b):
   p=OUT/s/f'{lo:%Y-%m}.parquet'; p.parent.mkdir(parents=True,exist_ok=True)
   if p.exists() and valid(p,s,lo,hi): records.append({'ticker':s,'year_month':f'{lo:%Y-%m}','status':'COMPLETE','row_count':len(pd.read_parquet(p)),'output_path':str(p)}); continue
   start=time.time(); status='FAILED'; stdout=''; stderr=''; code=None
   print(json.dumps({'ticker':s,'current_month':f'{lo:%Y-%m}','max_parallel_months_per_ticker':MAX_PARALLEL_MONTHS_PER_TICKER}),flush=True)
   for attempt in (1,2):
    cp=subprocess.run([sys.executable,'scripts/run_option_qualification_chunk.py','--ticker',s,'--start',str(lo.date()),'--end',str(hi.date()),'--output',str(p)],capture_output=True,text=True)
    stdout,stderr,code=cp.stdout,cp.stderr,cp.returncode
    if code==0 and valid(p,s,lo,hi): status='COMPLETE'; break
   rec={'ticker':s,'year_month':f'{lo:%Y-%m}','status':status,'row_count':len(pd.read_parquet(p)) if status=='COMPLETE' else 0,'output_path':str(p),'child_exit_code':code,'stdout':stdout[-2000:],'stderr':stderr[-2000:],'runtime_seconds':time.time()-start}
   with lock:
    with CK.open('a',encoding='utf-8') as f:f.write(json.dumps(rec,default=str)+'\n')
   print(json.dumps(rec),flush=True)
   if status!='COMPLETE': raise SystemExit(f'failed {s} {lo:%Y-%m}')
  return s
 with ThreadPoolExecutor(max_workers=MAX_PARALLEL_TICKERS) as pool:
  list(pool.map(run_ticker, WINDOWS))
 files=[p for s in WINDOWS for p in sorted((OUT/s).glob('*.parquet'))]
 import pyarrow as pa, pyarrow.parquet as pq
 writer=None
 for p in files:
  t=pa.Table.from_pandas(pd.read_parquet(p),preserve_index=False)
  if writer is None: writer=pq.ParquetWriter(ROOT/'unified_four_ticker_option_qualified.parquet',t.schema)
  writer.write_table(t)
 if writer: writer.close()
 print(json.dumps({'status':'COMPLETE','completed_months':len(files),'unified_path':str((ROOT/'unified_four_ticker_option_qualified.parquet').resolve())}))
if __name__=='__main__':main()
