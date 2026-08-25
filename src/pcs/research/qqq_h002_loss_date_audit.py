"""Individual loss-date audit for frozen H002 episodes."""
from pathlib import Path
import json
import os
import uuid
import pandas as pd

ROOT=Path("research_outputs/qqq_entry_discovery_agent_v1"); ART=ROOT/"artifacts"
FIELDS=["close_sma50_atr","close_sma200_atr","ret5","ret10","ret20","drawdown60","atr_pct_rank","vol_pct_rank","volume_ratio20","TREND_WEAKENING","VOLATILITY_EXPANDING","DRAWDOWN_DEEPENING","RECOVERY_AFTER_RESET"]
def run():
    d=pd.read_parquet(ART/"qqq_state_transition_features_train_2020_2023.parquet"); d.trade_date=pd.to_datetime(d.trade_date).dt.normalize(); sessions=pd.DatetimeIndex(d.trade_date.drop_duplicates().sort_values()); positions=pd.Series(range(len(sessions)),index=sessions); h=d[d.vol_pct_rank.between(.429,.753,inclusive="right")].copy(); h["session_index"]=h.trade_date.map(positions); h["episode_id"]=h.session_index.diff().fillna(999).ne(1).cumsum(); e=h.groupby("episode_id",as_index=False).first(); losses=e[e.outcome_class!="GOOD_WIN"]; wins=e[e.outcome_class=="GOOD_WIN"]
    def rows(x):
        return [{"trade_date":str(r.trade_date.date()),"year":int(r.trade_date.year),"outcome_class":r.outcome_class,"realized_pnl":float(r.realized_pnl),**{f:(bool(r[f]) if f.startswith(("TREND_","VOLATILITY_","DRAWDOWN_","RECOVERY_")) else float(r[f])) for f in FIELDS}} for _,r in x.iterrows()]
    flags=[f for f in FIELDS if f.startswith(("TREND_","VOLATILITY_","DRAWDOWN_","RECOVERY_"))]
    out={"module":"pcs.research.qqq_h002_loss_date_audit","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"EXISTING_TRADE","family":"H002_VOLATILITY_REGIME","frozen_rule":"0.429 < vol_pct_rank <= 0.753","independent_episodes":int(len(e)),"loss_episodes":rows(losses),"winner_episodes":rows(wins),"loss_summary":{"count":int(len(losses)),"stops":int((losses.outcome_class=="STOP_LOSS").sum()),"tails":int((losses.outcome_class=="TAIL_LOSS").sum()),"years":sorted(int(y) for y in losses.trade_date.dt.year.unique()),"state_true_counts":{f:int(losses[f].fillna(False).astype(bool).sum()) for f in flags}},"winner_state_true_counts":{f:int(wins[f].fillna(False).astype(bool).sum()) for f in flags},"threshold_mining":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"reason_codes":["PIT_SAFE_FEATURES","ONE_ENTRY_PER_EPISODE","INDIVIDUAL_LOSS_AUDIT","DESCRIPTIVE_ONLY"]}
    target=ART/"h002_loss_date_audit.json"; temp=target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(out,indent=2,default=str),encoding="utf-8"); os.replace(temp,target)
    finally:
        temp.unlink(missing_ok=True)
    print(json.dumps(out,indent=2,default=str)); return out
if __name__=="__main__": run()
