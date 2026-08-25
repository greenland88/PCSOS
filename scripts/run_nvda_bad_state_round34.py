"""PIT-safe NVDA bad-case profile using all executable TRAIN outcomes."""
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"research_outputs"/"nvda_entry_discovery_agent_v2"

def run():
    d=pd.read_parquet(OUT/"pit_feature_outcome_table.parquet").copy(); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); d.date=pd.to_datetime(d.date).dt.normalize(); d=d[(d.ticker=="NVDA")&d.executable_pcs&d.trade_date.between("2020-01-02","2023-12-31")].copy()
    states={"LOW_PARTICIPATION":d.nvda_volume_rel20<1,"LONG_TERM_WEAKNESS":d.nvda_close_vs_sma200<=0,"DOWNSIDE_ACCELERATION":(d.nvda_ret5<0)&(d.nvda_ret20<0),"DEEP_SELL_OFF":d.nvda_drawdown20<=-.15,"MARKET_UNCONFIRMED":d.qqq_close_vs_sma50<=0}
    bad=d.outcome_class.isin(["NORMAL_LOSS","STOP_LOSS","TAIL_LOSS"]); rows=[]
    for name,mask in states.items():
        x=d[mask]; y=d[~mask]
        rows.append({"state_id":name,"rows_inside":int(len(x)),"bad_inside":int(bad[mask].sum()),"normal_loss_inside":int((d.outcome_class.eq("NORMAL_LOSS")&mask).sum()),"stop_loss_inside":int((d.outcome_class.eq("STOP_LOSS")&mask).sum()),"tail_loss_inside":int((d.outcome_class.eq("TAIL_LOSS")&mask).sum()),"bad_rate_inside":float(bad[mask].mean()) if len(x) else None,"bad_rate_outside":float(bad[~mask].mean()) if len(y) else None,"pnl_inside":float(x.realized_pnl.sum()),"pnl_outside":float(y.realized_pnl.sum()),"conclusion":"NO_RELIABLE_FILTER"})
    pd.DataFrame(rows).to_csv(OUT/"v2_round34_bad_state_all_rows.csv",index=False)
    yearly=[]
    for name,mask in states.items():
        for year,g in d.groupby(d.trade_date.dt.year):
            inside=g[mask.loc[g.index]]; b=inside.outcome_class.isin(["NORMAL_LOSS","STOP_LOSS","TAIL_LOSS"])
            yearly.append({"state_id":name,"year":int(year),"rows_inside":int(len(inside)),"bad_inside":int(b.sum()),"stop_loss_inside":int((inside.outcome_class=="STOP_LOSS").sum()),"tail_loss_inside":int((inside.outcome_class=="TAIL_LOSS").sum()),"bad_rate_inside":float(b.mean()) if len(inside) else None})
    pd.DataFrame(yearly).to_csv(OUT/"v2_round34_bad_state_yearly.csv",index=False)
    out={"module":"pcs.research.nvda_bad_state_round34","version":"1.0","symbol":"NVDA","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","input_rows":int(len(d)),"pit_feature_date_equals_trade_date":bool((d.date==d.trade_date).all()),"outcome_counts":d.outcome_class.value_counts().to_dict(),"conclusion":"NO_RELIABLE_FILTER","validation_read":False,"final_oos_read":False,"production_changes":False,"frozen_rule_families_unchanged":["PCS_TREND_CONTINUATION","PCS_CONSTRUCTIVE_RECOVERY"],"reason_codes":["NVDA_ONLY","TRAIN_ONLY","PIT_SAFE","ALL_EXECUTABLE_ROWS","BAD_CASES_DIRECTLY_COUNTED","NO_FORCED_NO_TRADE","NO_VALIDATION","NO_FINAL_OOS","NO_PRODUCTION_CHANGE"]}
    (OUT/"v2_round34_manifest.json").write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); return out
if __name__=="__main__": print(json.dumps(run(),indent=2,default=str))
