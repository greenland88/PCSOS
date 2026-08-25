import subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
import os
targets=[1.5,2.0,2.5,3.0]
def run(a):
 t=time.time(); p=subprocess.run([sys.executable,'scripts/run_safe_strike_target_worker.py','--atr',str(a)],capture_output=True,text=True); print({'atr':a,'exit_code':p.returncode,'elapsed_seconds':round(time.time()-t,2),'stdout':p.stdout[-1000:],'stderr':p.stderr[-500:]},flush=True); return p.returncode
with ThreadPoolExecutor(max_workers=max(1, int(os.getenv("PCS_WORKERS", "8")))) as ex: codes=list(ex.map(run,targets))
if any(c!=0 for c in codes): raise SystemExit(1)
