"""Deterministic read-only SPY January 2025 as-of state pilot."""
from pathlib import Path
from hashlib import sha256
import json
from scripts.run_spy_qqq_underlying_state_research import daily
from pcs.research.underlying_state import evaluate_as_of

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'research_outputs'/'spy_qqq_underlying_state_research_20260821'
def main():
 OUT.mkdir(parents=True,exist_ok=True); d=daily('SPY',__import__('pandas').Timestamp('2025-01-31'));r=[evaluate_as_of(d,'SPY',x) for x in d[d.date.between('2025-01-01','2025-01-31')].date];payload=json.dumps(r,default=str,sort_keys=True);(OUT/'pilot_spy_2025_01.json').write_text(json.dumps({'rows':len(r),'hash':sha256(payload.encode()).hexdigest(),'final_oos_read':False},indent=2));print(sha256(payload.encode()).hexdigest())
if __name__=='__main__':main()
