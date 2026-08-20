import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
TARGETS=[1.8,2.1,2.2,2.3,2.4,2.6]; ROOT=Path('research_outputs/safe_strike_stage2'); ROOT.mkdir(parents=True,exist_ok=True)
def run(a):
 log=ROOT/f'{a:.1f}ATR'/'logs'/f'stage2_{a:.1f}ATR.log'; log.parent.mkdir(parents=True,exist_ok=True)
 with log.open('a',encoding='utf-8',buffering=1) as f:
  p=subprocess.Popen([sys.executable,'-u','scripts/run_safe_strike_stage2_worker.py','--atr',str(a)],stdout=f,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONUNBUFFERED':'1'})
  while p.poll() is None:
   time.sleep(10); print(json.dumps({'atr':a,'pid':p.pid,'log':str(log),'status':'RUNNING'}),flush=True)
  print(json.dumps({'atr':a,'pid':p.pid,'exit_code':p.returncode,'log':str(log)}),flush=True); return p.returncode
with ThreadPoolExecutor(max_workers=6) as ex: codes=list(ex.map(run,TARGETS))
raise SystemExit(1 if any(c for c in codes) else 0)
