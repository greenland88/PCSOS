import argparse, hashlib, json, os, sys, time, traceback
from datetime import datetime
from pathlib import Path
import pandas as pd
from run_safe_strike_candidates import stock, WINDOWS
from pcs.research.credit_stop import run_backtest
from pcs.data.access import PCSDataAccess

P=argparse.ArgumentParser(); P.add_argument('--atr',type=float,required=True); a=P.parse_args()
REPO_ROOT=Path(__file__).resolve().parents[1]
atr=a.atr; root=REPO_ROOT/'research_outputs/safe_strike_stage2'/f'{atr:.1f}ATR'; (root/'checkpoints').mkdir(parents=True,exist_ok=True); (root/'chunks').mkdir(exist_ok=True); (root/'logs').mkdir(exist_ok=True)
ck=root/'checkpoints'/f'{atr:.1f}ATR.jsonl'; log=root/'logs'/f'stage2_{atr:.1f}ATR.log'
def emit(x):
 s=json.dumps(x,default=str); print(s,flush=True)
 with log.open('a',encoding='utf-8') as f: f.write(s+'\n'); f.flush()
def months(lo,hi):
 d=pd.Timestamp(lo).to_period('M'); end=pd.Timestamp(hi).to_period('M')
 while d<=end:
  yield max(pd.Timestamp(lo),d.start_time),min(pd.Timestamp(hi),d.end_time.normalize()); d+=1
def work_identity(ticker, start, end):
 access=PCSDataAccess(); payload={"ticker":ticker,"start":str(pd.Timestamp(start).date()),"end":str(pd.Timestamp(end).date()),"atr":float(atr),
          "daily":access.source_data_identity("daily",ticker),"benchmark":access.source_data_identity("daily","QQQ"),
          "options":access.source_data_identity("options",ticker),"runner":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "backtest":hashlib.sha256((REPO_ROOT/"src/pcs/research/credit_stop.py").read_bytes()).hexdigest()}
 return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
done={}
if ck.exists():
 for line in ck.read_text(encoding='utf-8').splitlines():
  try:
   x=json.loads(line)
   part=root/'chunks'/f"{x['ticker']}_{int(x['year']):04d}-{int(x['month']):02d}.parquet"
   if (x.get('status')=='COMPLETE'
       and x.get('identity') == work_identity(x['ticker'], pd.Timestamp(x['year'], x['month'], 1), pd.Timestamp(x['year'], x['month'], 1)+pd.offsets.MonthEnd(1))
       and part.is_file() and x.get('output_checksum') == hashlib.sha256(part.read_bytes()).hexdigest()):
    done[(x['ticker'],x['year'],x['month'])]=x
  except Exception: pass
emit({'event':'worker_start','atr':atr,'pid':os.getpid(),'timestamp':datetime.now().isoformat()})
for ticker,(lo,hi) in WINDOWS.items():
 emit({'event':'ticker_start','atr':atr,'ticker':ticker})
 for start,end in months(lo,hi):
  key=(ticker,start.year,start.month)
  if key in done: continue
  t=time.time(); emit({'event':'month_start','atr':atr,'ticker':ticker,'year':start.year,'month':start.month})
  rec={'atr':atr,'ticker':ticker,'year':start.year,'month':start.month,'identity':work_identity(ticker,start,end),'status':'FAILED','rows_processed':0,'qualified_count':0,'elapsed_seconds':0,'rss_mb':None,'timestamp':datetime.now().isoformat()}
  try:
   r=run_backtest(stock(ticker,hi),stock('QQQ',hi),option_root=f'data/parquet/options_monthly/{ticker}',start=start.strftime('%Y-%m-%d'),end=end.strftime('%Y-%m-%d'),backend='canonical',safe_strike_atr=atr)
   ts=[{**{k:v for k,v in z.items() if k!='events'},'ticker':ticker,'target_buffer_atr':atr,'candidate_status':'TRADE_QUALIFIED'} for z in r['trades'] if z.get('trend_gate')=='PASS']
   out=pd.DataFrame(ts); part=root/'chunks'/f'{ticker}_{start:%Y-%m}.parquet'; tmp=part.with_suffix('.parquet.tmp'); out.to_parquet(tmp,index=False); pd.read_parquet(tmp); tmp.replace(part)
   rec.update(status='COMPLETE',rows_processed=len(ts),qualified_count=len(ts),elapsed_seconds=time.time()-t,output_path=str(part),output_checksum=hashlib.sha256(part.read_bytes()).hexdigest())
   emit({'event':'month_complete',**rec,'output_path':str(part)})
  except Exception as e:
   rec.update(elapsed_seconds=time.time()-t,error=str(e),traceback=traceback.format_exc()); emit({'event':'month_failed',**rec});
  with ck.open('a',encoding='utf-8') as f: f.write(json.dumps(rec,default=str)+'\n'); f.flush()
  if rec['status']=='FAILED': continue
 # assemble ticker atomically from chunks
 parts=sorted((root/'chunks').glob(f'{ticker}_*.parquet')); frames=[pd.read_parquet(p) for p in parts]; out=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(); tmp=root/f'{ticker}.parquet.tmp'; out.to_parquet(tmp,index=False); pd.read_parquet(tmp); tmp.replace(root/f'{ticker}.parquet')
 emit({'event':'ticker_complete','atr':atr,'ticker':ticker,'qualified_count':len(out),'output_path':str(root/f'{ticker}.parquet')})
emit({'event':'worker_complete','atr':atr,'timestamp':datetime.now().isoformat()})
