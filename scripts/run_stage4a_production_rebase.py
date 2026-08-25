"""Build the isolated structural Stage 4A production opportunity universe."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import pandas as pd
from pcs.research.stage4a_production_universe import generate_structural_put_opportunities
from pcs.data.access import PCSDataAccess

ROOT=Path("research_outputs/safe_strike_stage4a")
OUT=Path("research_outputs/stage4a_production_rebase_20260820")
PATHS={"NVDA":ROOT/"candidate_inputs/NVDA.parquet","AMD":ROOT/"candidate_inputs/AMD.parquet","TSLA":ROOT/"candidate_inputs/TSLA.parquet","AMZN":ROOT/"authoritative_amzn_794_entry_contract_v2.parquet"}

def main():
    OUT.mkdir(parents=True,exist_ok=True); all_rows=[]; report={}
    access = PCSDataAccess()
    for ticker,path in PATHS.items():
        pop=pd.read_parquet(path); dates=pd.to_datetime(pop["date"]).dt.normalize(); start,end=dates.min(),dates.max()
        # Never scan a historical physical options_v2 directory here.  The
        # active route is part of the research input contract and may change
        # per ticker; PCSDataAccess also supplies the authoritative identity.
        raw=access.read_quotes(ticker, start, end)
        raw=raw[(pd.to_datetime(raw.trade_date)>=start)&(pd.to_datetime(raw.trade_date)<=end)] if not raw.empty else raw
        chain=raw.rename(columns={"trade_date":"Trade Date","expiration_date":"Expiry Date","call_put":"Call/Put","strike":"Strike","last":"Last Trade Price","bid":"Bid Price","ask":"Ask Price","delta":"Delta","open_interest":"Open Interest","volume":"Volume"})
        chain["Trade Date"]=pd.to_datetime(chain["Trade Date"]).dt.normalize(); chain["Expiry Date"]=pd.to_datetime(chain["Expiry Date"])
        meta={"option_rows_loaded":len(chain),"source":"PCSDataAccess.resolve_source(options)",
              "source_data_identity":access.source_data_identity("options",ticker)}
        rows=[]; invalid=0
        for day,group in chain.groupby("Trade Date"):
            for row in generate_structural_put_opportunities(group,ticker,day):
                row["opportunity_id"]=hashlib.sha256("|".join([ticker,str(day.date()),str(row["expiration"].date()),str(row["short_strike"]),str(row["long_strike"]),"p"]).encode()).hexdigest()[:24]
                row["pit_asof"]=str(day.date()); row["source_provenance"]=f"canonical_options_v2:{ticker}"; rows.append(row)
        frame=pd.DataFrame(rows); all_rows.append(frame)
        report[ticker]={"decision_dates":int(dates.nunique()),"date_start":str(start.date()),"date_end":str(end.date()),"raw_option_rows":int(len(chain)),"structural_opportunities":int(len(frame)),"duplicate_ids":int(frame.opportunity_id.duplicated().sum()) if not frame.empty else 0,"loader":meta}
    out=pd.concat(all_rows,ignore_index=True) if all_rows else pd.DataFrame()
    out.to_parquet(OUT/"production_opportunity_universe.parquet",index=False)
    (OUT/"production_contract_validation.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8")
    print(json.dumps(report,indent=2,default=str))
if __name__=="__main__": main()
