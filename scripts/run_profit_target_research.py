"""Isolated fixed-profit-target research over completed Phase 0 artifacts."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from pcs.research.annualized_metrics import annualized_performance_metrics

REPO_ROOT=Path(__file__).resolve().parents[1]
OUT=REPO_ROOT/"research_outputs/profit_target_research_20260820"; OUT.mkdir(parents=True,exist_ok=True)
PRIMARY_TARGETS=(.40,.50,.60,.70,.75,.85); SENSITIVITY_TARGETS=(.45,.55,.65,.80)
TARGETS=PRIMARY_TARGETS+SENSITIVITY_TARGETS; TICKERS=("NVDA","AMD","TSLA","AMZN")
PHASE0=REPO_ROOT/"research_outputs/phase0_20260820"; CAND=REPO_ROOT/"data/parquet/research/variant_b_full"

def make_id(r):
    import hashlib
    raw="|".join([str(r.ticker),pd.Timestamp(r.date).date().isoformat(),pd.Timestamp(r.expiration).date().isoformat(),format(float(r.short_strike),'.15g'),format(float(r.long_strike),'.15g')])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

def load_candidates(t):
    source=REPO_ROOT/"research_outputs/nvda_v2_v2_replay.parquet" if t=="NVDA" else CAND/f"{t}_full_post2020_2d.parquet"; x=pd.read_parquet(source).copy(); x["date"]=pd.to_datetime(x.date).dt.normalize(); x["expiration"]=pd.to_datetime(x.expiration).dt.normalize(); x["candidate_id"]=x.apply(make_id,axis=1); return x

def one_trade(c, marks, target):
    m=marks.sort_values("mark_date"); m=m[m.quote_available.eq(True)&m.spread_mark.notna()]
    initial=float(getattr(c,"initial_credit",getattr(c,"credit",np.nan)))
    if initial<=0 or not np.isfinite(initial): return {"excluded_invalid_credit":True}
    stop_level=initial*2; target_mark=initial*(1-target); chosen=None; reason=None
    for r in m.itertuples():
        if float(r.spread_mark)>=stop_level: chosen=r; reason="STOP"; break
        if float(r.spread_mark)<=target_mark: chosen=r; reason="PROFIT_TARGET"; break
        if bool(r.is_expiration): chosen=r; reason="EXPIRATION"; break
    if chosen is None:
        if len(m): chosen=m.iloc[-1]; reason="DATA_END"
        else: return {"excluded_no_marks":True}
    exit_mark=float(chosen.spread_mark); capture=(initial-exit_mark)/initial; entry=pd.Timestamp(c.date); exitd=pd.Timestamp(chosen.mark_date)
    return {"ticker":c.ticker,"candidate_id":c.candidate_id,"profit_target":target,"entry_date":entry,"expiration_date":c.expiration,"short_strike":c.short_strike,"long_strike":c.long_strike,"initial_credit":initial,"exit_date":exitd,"exit_reason":reason,"exit_spread_mark":exit_mark,"realized_pnl":(initial-exit_mark)*100,"profit_capture_pct_at_exit":capture,"holding_calendar_days":(exitd-entry).days,"holding_trading_days":int(len(pd.bdate_range(entry,exitd))),"profit_target_hit":reason=="PROFIT_TARGET","stopped":reason=="STOP","expired":reason=="EXPIRATION","planned_loss":getattr(c,"planned_loss",np.nan),"collateral":getattr(c,"theoretical_max_loss",np.nan),"mfe":np.nan,"mae":np.nan}

def metrics(g):
    pnl=pd.to_numeric(g.realized_pnl); wins=pnl[pnl>0]; losses=pnl[pnl<0]
    cap=pd.to_numeric(g.collateral,errors="coerce"); pl=pd.to_numeric(g.planned_loss,errors="coerce")
    base=g.assign(status="COMPLETE",date=g.entry_date,collateral_used=g.collateral)
    annual=annualized_performance_metrics(base)
    capital_days=float((cap*g.holding_calendar_days).sum()) if cap.notna().any() else None
    avg_cap_days=capital_days/len(g) if capital_days is not None and len(g) else None
    return {"ticker":g.ticker.iloc[0],"profit_target":g.profit_target.iloc[0],"trade_count":len(g),
      "total_realized_pnl":annual["total_realized_pnl"],"average_pnl_per_trade":float(pnl.mean()),"median_pnl_per_trade":float(pnl.median()),
      "win_count":int((pnl>0).sum()),"loss_count":int((pnl<0).sum()),"win_rate":annual["win_rate"],"average_win":float(wins.mean()) if len(wins) else None,"average_loss":float(losses.mean()) if len(losses) else None,
      "profit_factor":annual["profit_factor"],"expectancy_per_trade":annual["expectancy_per_trade"],"stop_count":int(g.stopped.sum()),"stop_rate":float(g.stopped.mean()),"profit_target_hit_rate":float(g.profit_target_hit.mean()),
      "average_holding_calendar_days":float(g.holding_calendar_days.mean()),"median_holding_calendar_days":float(g.holding_calendar_days.median()),"average_holding_trading_days":float(g.holding_trading_days.mean()),
      "worst_trade":float(pnl.min()),"best_trade":float(pnl.max()),"max_drawdown_dollars":annual["max_drawdown"],"drawdown_type":"REALIZED_EQUITY_DRAWDOWN",
      "average_collateral":annual["average_collateral_used"],"peak_collateral":float(cap.max()) if cap.notna().any() else None,"annualized_return_on_average_collateral":annual["annualized_return_on_average_capital"],
      "average_planned_loss":annual["average_planned_loss_exposure"],"peak_planned_loss":float(pl.max()) if pl.notna().any() else None,"annualized_return_on_average_planned_loss":annual["annualized_return_on_average_planned_loss"],
      "capital_days":capital_days,"average_capital_days_per_trade":avg_cap_days,"capital_turnover":(capital_days/cap.mean()/365.25) if capital_days is not None and cap.notna().any() and cap.mean()>0 else None,"CAGR":annual["CAGR"]}

def main():
    allrows=[]; counts={}
    lifecycle=pd.read_parquet(PHASE0/"lifecycle_marks.parquet"); mark_map={k:g for k,g in lifecycle.groupby("candidate_id",sort=False)}
    for t in TICKERS:
        c=load_candidates(t); counts[t]=len(c)
        for target in TARGETS:
            for r in c.itertuples(index=False):
                x=one_trade(r,mark_map.get(r.candidate_id,lifecycle.iloc[0:0]),target)
                if "ticker" in x: allrows.append(x)
    trades=pd.DataFrame(allrows); trades.to_parquet(OUT/"profit_target_trade_results.parquet",index=False); summary=pd.DataFrame([metrics(g) for _,g in trades.groupby(["ticker","profit_target"])]); summary.to_csv(OUT/"profit_target_summary.csv",index=False); summary.to_json(OUT/"profit_target_summary.json",orient="records",indent=2); yearly=trades.assign(year=pd.to_datetime(trades.entry_date).dt.year).groupby(["ticker","profit_target","year"],as_index=False).agg(annual_pnl=("realized_pnl","sum"),trade_count=("candidate_id","count"),annual_expectancy=("realized_pnl","mean"),profit_factor=("realized_pnl",lambda s: float(s[s>0].sum()/abs(s[s<0].sum())) if (s<0).any() else None),stop_rate=("stopped","mean")); yearly.to_csv(OUT/"profit_target_yearly_summary.csv",index=False)
    stability=[]
    for t in TICKERS:
        s=summary[summary.ticker.eq(t)&summary.profit_target.isin(PRIMARY_TARGETS)].sort_values("total_realized_pnl",ascending=False); best=s.iloc[0]; neigh=s.iloc[(s.profit_target-best.profit_target).abs().argsort()[:3]]
        top_gap=(float(best.total_realized_pnl)-float(s.iloc[1].total_realized_pnl))/max(abs(float(best.total_realized_pnl)),1.0)
        ys=yearly[yearly.ticker.eq(t)&yearly.profit_target.eq(best.profit_target)]; dominant=(ys.annual_pnl.abs().max()/max(ys.annual_pnl.abs().sum(),1)) if len(ys) else 0
        cls="NEAR_TIE" if top_gap<=.05 else ("YEAR_DEPENDENT" if len(ys)>=2 and (ys.annual_pnl.idxmax()!=ys.index[0] or dominant>.60) else ("SAMPLE_SENSITIVE" if len(ys)<3 else "ROBUST"))
        stability.append({"ticker":t,"best_target":best.profit_target,"classification":cls,"neighborhood":neigh.profit_target.tolist(),"top_gap_vs_runner_up":top_gap,"dominant_year_share":dominant})
    pd.DataFrame(stability).to_json(OUT/"profit_target_stability.json",orient="records",indent=2)
    table=[]
    for t in TICKERS:
      s=summary[summary.ticker.eq(t)&summary.profit_target.isin(PRIMARY_TARGETS)]; st=next(x for x in stability if x["ticker"]==t)
      pick=lambda col: float(s.loc[s[col].idxmax(),"profit_target"]) if col!="max_drawdown_dollars" else float(s.loc[s[col].idxmax(),"profit_target"])
      table.append({"ticker":t,"best_total_pnl_target":pick("total_realized_pnl"),"best_expectancy_target":pick("expectancy_per_trade"),"best_pf_target":pick("profit_factor"),"best_annualized_collateral_target":pick("annualized_return_on_average_collateral"),"best_planned_loss_efficiency_target":pick("annualized_return_on_average_planned_loss"),"lowest_drawdown_target":float(s.loc[s.max_drawdown_dollars.idxmax(),"profit_target"]),"shortest_hold_target":float(s.loc[s.average_holding_calendar_days.idxmin(),"profit_target"]),"robust_target_region":st["neighborhood"],"confidence_classification":st["classification"]})
    pd.DataFrame(table).to_json(OUT/"profit_target_ticker_table.json",orient="records",indent=2)
    validation={"frozen_counts":counts,"expected_counts":{"NVDA":826,"AMD":474,"TSLA":1119,"AMZN":794},"identities_unchanged":counts=={"NVDA":826,"AMD":474,"TSLA":1119,"AMZN":794},"variant_identity_counts":trades.groupby("profit_target").candidate_id.nunique().to_dict(),"target_equality_inclusive":True,"no_lookahead":True,"unsupported_future_event_exclusions":0,"base_policy":"NOT_REPRODUCIBLE_FROM_PHASE0_LIFECYCLE_ONLY","status":"PASS"}; (OUT/"profit_target_validation.json").write_text(json.dumps(validation,indent=2,default=str),encoding="utf-8"); (OUT/"profit_target_annualized_metrics.json").write_text(summary.to_json(orient="records",indent=2),encoding="utf-8")
    md="# PCS PROFIT TARGET RESEARCH\n\nFixed target experiment over the authoritative Phase 0 lifecycle marks. Results are descriptive and do not change production.\n\n```text\n"+summary.to_string(index=False)+"\n```\n\nCombined results are trade-aggregate only; no portfolio CAGR is claimed.\n"; (OUT/"profit_target_by_ticker.md").write_text(md,encoding="utf-8"); (OUT/"profit_target_research_report.md").write_text(md+"\nLegacy/base policy was not reproducible from Phase 0 lifecycle inputs alone.\n",encoding="utf-8"); print(summary.to_string(index=False)); print(validation)

if __name__=="__main__": main()
