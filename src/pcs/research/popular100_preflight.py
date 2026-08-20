from pathlib import Path
import hashlib, pandas as pd
from .r1_frozen_validation import R1_FROZEN_V1

ROOT=Path(__file__).resolve().parents[3]
POPULAR="""AAPL MSFT META GOOGL AVGO AMD INTC MU QCOM AMAT LRCX KLAC MRVL TSM ASML SMCI PLTR CRM ORCL ADBE NOW CRWD PANW SNOW DDOG NET IBM CSCO ANET DELL NFLX DIS WBD CMCSA T VZ TMUS SPOT ROKU UBER LYFT ABNB DASH BKNG SBUX MCD CMG COST WMT TGT HD LOW NKE LULU MELI JPM BAC WFC C GS MS SCHW BLK COF AXP V MA PYPL HOOD COIN MSTR MARA RIOT SOFI AFRM BA CAT GE HON RTX LMT NOC DE UPS FDX GM F RIVN XOM CVX COP SLB OXY NEE CEG LLY UNH JNJ ABBV PFE""".split()
def run():
    frozen=pd.read_csv(ROOT/"research_outputs/r1_external_validation_universe_v1.csv"); eligible=[s for s in POPULAR if s in set(frozen.ticker)]; missing=[s for s in POPULAR if s not in set(frozen.ticker)]; checksum=hashlib.sha256("\n".join(eligible).encode()).hexdigest(); pd.DataFrame({"ticker":eligible,"status":"ELIGIBLE"}).to_csv(ROOT/"research_outputs/popular100_eligible_v1.csv",index=False); pd.DataFrame({"ticker":missing,"status":"MISSING_INELIGIBLE"}).to_csv(ROOT/"research_outputs/popular100_missing_v1.csv",index=False); return {"requested":len(POPULAR),"eligible":len(eligible),"missing":missing,"checksum":checksum,"eligible_list":eligible}
if __name__=="__main__": print(run())
