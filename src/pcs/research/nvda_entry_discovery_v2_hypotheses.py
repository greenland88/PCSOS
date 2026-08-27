"""First simple V2 hypotheses generated after broad descriptive evidence."""
from pathlib import Path
import json
import pandas as pd

def evaluate_first_v2_hypotheses(output_dir="research_outputs/nvda_entry_discovery_agent_v2"):
    out=Path(output_dir); z=pd.read_parquet(out/"pit_feature_outcome_table.parquet"); z.trade_date=pd.to_datetime(z.trade_date); z["year"]=z.trade_date.dt.year
    specs={
      "V2_H001":{"family":"TREND_CONTINUATION","rule":"close above SMA50 and positive 20D NVDA relative strength"},
      "V2_H002":{"family":"SHALLOW_RESET","rule":"close above SMA50 and 20D drawdown no worse than shallow reset"},
      "V2_H003":{"family":"MARKET_CONFIRMED_STRENGTH","rule":"close above SMA50 and QQQ above SMA50"},
      "V2_H004":{"family":"VOLATILITY_STATE","rule":"ATR14 below median and volume not expanding"},
    }
    med_atr=z.nvda_atr14.median(); med_vol=z.nvda_volume_rel20.median()
    masks={"V2_H001":(z.close>z.nvda_sma50)&(z.nvda_relative_strength20>=0),"V2_H002":(z.close>z.nvda_sma50)&(z.nvda_drawdown20>=-.10),"V2_H003":(z.close>z.nvda_sma50)&(z.qqq_close>z.qqq_sma50),"V2_H004":(z.nvda_atr14<=med_atr)&(z.nvda_volume_rel20<=med_vol)}
    rows=[]
    for hid,s in specs.items():
      g=z[masks[hid]].copy(); rows.append({"hypothesis_id":hid,"setup_family":s["family"],"rule":s["rule"],"qualifying_dates":len(g),"executable_trades":int(g.executable_pcs.sum()),"pnl":float(g.realized_pnl.sum()),"expectancy":float(g.realized_pnl.mean()) if len(g) else None,"pf":float(g.loc[g.realized_pnl>0,"realized_pnl"].sum()/abs(g.loc[g.realized_pnl<0,"realized_pnl"].sum())) if (g.realized_pnl<0).any() else None,"win_rate":float((g.realized_pnl>0).mean()) if len(g) else None,"stop_rate":float(g.stopped.mean()) if len(g) else None,"years":sorted(g.year.unique().tolist()),"status":"DESCRIPTIVE_HYPOTHESIS_READY"})
    pd.DataFrame(rows).to_csv(out/"v2_hypothesis_screen.csv",index=False)
    registry={"agent_name":"NVDA_PCS_ENTRY_DISCOVERY_AGENT_V2","active_hypotheses":[*specs.keys()],"hypotheses":specs,"source":"ROUND_001_DESCRIPTIVE_EVIDENCE","old_hypotheses_imported":0,"final_oos_read":False,"validation_read":False}
    (out/"v2_hypothesis_registry.json").write_text(json.dumps(registry,indent=2),encoding="utf-8"); return rows
