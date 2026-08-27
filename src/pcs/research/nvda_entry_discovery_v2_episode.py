from pathlib import Path
import pandas as pd

def screen_one_entry_episodes(output_dir="research_outputs/nvda_entry_discovery_agent_v2"):
    out=Path(output_dir); z=pd.read_parquet(out/"pit_feature_outcome_table.parquet"); z.trade_date=pd.to_datetime(z.trade_date); z=z.sort_values("trade_date")
    masks={"V2_H001":(z.close>z.nvda_sma50)&(z.nvda_relative_strength20>=0),"V2_H002":(z.close>z.nvda_sma50)&(z.nvda_drawdown20>=-.10),"V2_H003":(z.close>z.nvda_sma50)&(z.qqq_close>z.qqq_sma50),"V2_H004":(z.nvda_atr14<=z.nvda_atr14.median())&(z.nvda_volume_rel20<=z.nvda_volume_rel20.median())}
    rows=[]
    for hid,m in masks.items():
      g=z[m & z.executable_pcs].copy(); g["gap"]=g.trade_date.diff().dt.days.fillna(999); g["episode"]=(g.gap>10).cumsum(); first=g.groupby("episode",as_index=False).head(1)
      rows.append({"hypothesis_id":hid,"episodes":int(first.episode.nunique()),"trades":len(first),"pnl":float(first.realized_pnl.sum()),"expectancy":float(first.realized_pnl.mean()),"pf":float(first.loc[first.realized_pnl>0,"realized_pnl"].sum()/abs(first.loc[first.realized_pnl<0,"realized_pnl"].sum())) if (first.realized_pnl<0).any() else None,"win_rate":float((first.realized_pnl>0).mean()),"stop_rate":float(first.stopped.mean()),"worst_trade":float(first.realized_pnl.min()),"years":sorted(first.trade_date.dt.year.unique().tolist())})
    pd.DataFrame(rows).to_csv(out/"v2_one_entry_episode_screen.csv",index=False); return rows
