"""Diagnostic-only audit of the completed frozen COST recovery transfer."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'research_outputs'
OUT=BASE/'cost_frozen_recovery_diagnostic'; OUT.mkdir(parents=True,exist_ok=True)
KINDS={'controlled_reset':'Controlled Reset','recovery_stabilization':'Recovery Stabilization After Reset','sma50_reclaim':'SMA50 Reclaim After Weakness'}

def pct(v): return None if pd.isna(v) else float(v)

def main():
    daily=pd.read_parquet(BASE/'cost_canonical_test_dataset/cost_master_daily_research.parquet').sort_values('date').copy()
    daily.date=pd.to_datetime(daily.date).dt.normalize(); daily['atr_pct'] = daily.atr14.rank(pct=True)
    funnel=[]; reasons=[]; trades=[]; signals=[]
    for kind,name in KINDS.items():
        tr=json.loads((BASE/'cost_frozen_recovery_transfer'/kind/'transfer_report.json').read_text())
        replay=tr['canonical_replay']; n=tr['qualifying_days']; cand=int(replay['funnel'].get('CONTRACT_CANDIDATES',0)); exe=int(replay['funnel'].get('SELECTED_ENTRIES',0)); done=int(replay['funnel'].get('LIFECYCLES_COMPLETED',0))
        funnel.append({'strategy':name,'qualifying':n,'contract_candidate':cand,'executable':exe,'completed':done,'qualifying_to_candidate_pct':100*cand/n,'candidate_to_executable_pct':100*exe/cand if cand else 0,'qualifying_to_executable_pct':100*exe/n})
        # Signal-level reconciliation is exact; gate counters are candidate-level and may overlap.
        rejected=n-cand
        # The replay records credit/width as the dominant candidate rejection; preserve all independent counters separately.
        reasons += [{'strategy':name,'rejection_reason':'CONTRACT_SELECTION_FAIL','count':rejected,'percent_of_qualifying':100*rejected/n,'count_scope':'qualifying signal reconciliation','authoritative':True}]
        for gate,key in [('LIQUIDITY_FAIL','LIQUIDITY_REJECTED'),('CREDIT_EFFICIENCY_FAIL','CREDIT_WIDTH_REJECTED'),('EVENT_BLOCK','EVENT_REJECTED'),('SAFE_STRIKE_FAIL','SAFE_STRIKE_REJECTED'),('NO_VALID_DTE','DTE_REJECTED')]:
            c=int(replay['funnel'].get(key,0)); reasons.append({'strategy':name,'rejection_reason':gate,'count':c,'percent_of_qualifying':100*c/n,'count_scope':'candidate attempts; overlapping/non-additive','authoritative':False})
        lr=pd.read_parquet(BASE/f'cost_frozen_{kind}/lifecycle_results.parquet')
        lr['strategy']=name; lr['qualifying_date']=lr['date']; trades.append(lr)
        q=pd.to_datetime(tr['signal_dates']); qs=daily[daily.date.isin(q)].copy(); qs['strategy']=name; qs['qualifying_date']=qs.date
        close_by_date=daily.set_index('date').close
        for h in [1,3,5,10,20]:
            future=daily[['date','close']].copy(); future['date']=future.date.shift(h); future=future.rename(columns={'close':f'future_close_{h}'})
            qs=qs.merge(future[['date',f'future_close_{h}']],on='date',how='left'); qs[f'forward_{h}d']=qs[f'future_close_{h}']/qs.close-1
        qs['episode']=(qs.date.diff().dt.days.fillna(999)>4).cumsum(); signals.append(qs)
    ft=pd.concat(funnel and [pd.DataFrame(funnel)]); ft.to_csv(OUT/'cost_qualifying_execution_funnel.csv',index=False)
    pd.DataFrame(reasons).to_csv(OUT/'cost_execution_rejection_reasons.csv',index=False)
    t=pd.concat(trades,ignore_index=True); t['short_strike_distance']=t.close-t.short_strike; t['atr_distance']=t.short_strike_distance/t.atr
    t['entry_underlying_price']=t.close; t['stop_or_expiration']=t.exit_reason
    keep=['strategy','qualifying_date','entry_date','expiration_date','short_strike','long_strike','spread_width','credit','dte','entry_underlying_price','short_strike_distance','atr_distance','realized_pnl','exit_reason','holding_trading_days','stop_triggered','expired','mfe','mae','premium_capture']
    t[keep].to_csv(OUT/'cost_completed_trade_autopsy.csv',index=False)
    allq=pd.concat(signals,ignore_index=True); allq['price_vs_sma20']=allq.close/allq.sma20-1; allq['price_vs_sma50']=allq.close/allq.sma50-1; allq['price_vs_sma200']=allq.close/allq.sma200-1; allq['drawdown_from_recent_high']=allq.drawdown60
    allq[['strategy','qualifying_date','close','ret5','ret10','ret20','sma20_slope','sma50_slope','sma200_slope','atr14','atr_pct','volume','drawdown_from_recent_high','forward_1d','forward_3d','forward_5d','forward_10d','forward_20d','price_vs_sma20','price_vs_sma50','price_vs_sma200']].to_csv(OUT/'cost_qualifying_forward_returns.csv',index=False)
    # Union episode clustering; strategy rows are retained so overlapping signals are visible.
    uq=allq[['strategy','qualifying_date']].drop_duplicates().sort_values('qualifying_date'); uq['episode']=(uq.qualifying_date.diff().dt.days.fillna(999)>4).cumsum(); uq.to_csv(OUT/'cost_episode_analysis.csv',index=False)
    summary={'module':'pcs.research.cost_frozen_recovery_diagnostic','version':'1.0','classification':'INSUFFICIENT_EVIDENCE','funnel':funnel,'completed_trades':int(len(t)),'controls':{'frozen_strategy_definitions_changed':False,'thresholds_changed':False,'cost_specific_tuning':False,'production_logic_changed':False,'lifecycle_rules_changed':False,'contract_selection_rules_changed':False,'final_oos_read':False},'notes':['Signal-level rejection reconciliation is qualifying minus canonical contract candidates.','Replay gate counters are candidate-attempt counters and overlap; they are not additive signal counts.','No new trading rule or optimization was run.']}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    lines=['# COST Frozen Recovery Diagnostic','', 'Diagnostic-only audit of the existing frozen transfer; no rules were changed.','', '## Funnel','', ft.to_markdown(index=False),'', '## Interpretation','', 'The canonical artifacts show sparse executable candidates. The persisted replay counters are candidate-attempt counts: credit/width and liquidity failures can overlap on the same signal and therefore must not be summed. The exact signal reconciliation is qualifying minus contract candidates.', '', '## Completed trades','', f'Completed trades: {len(t)}; all observed P&L values: {t.realized_pnl.tolist()}.','', '## Controls','', '- Frozen strategy definitions changed: NO', '- Thresholds changed: NO', '- COST-specific tuning: NO', '- Production logic changed: NO', '- Lifecycle rules changed: NO', '- Contract-selection rules changed: NO', '- FINAL OOS read: NO', '', '## Classification', '', '**INSUFFICIENT_EVIDENCE**: the executable sample is too small to separate underlying signal weakness from PCS expression and lifecycle effects confidently.']
    (OUT/'COST_FROZEN_RECOVERY_DIAGNOSTIC.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
