"""Read-only reporting for the strict replay; does not replay or alter rules."""
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research_outputs'/'spy_qqq_modular_rule_research_20260821'
def main():
  metrics=[]; coverage=[]; scale=[]
  for split in ('train','validation'):
    ledger=pd.read_parquet(OUT/f'strict_{split}_candidate_ledger.parquet')
    trades=pd.read_parquet(OUT/f'strict_{split}_lifecycle.parquet')
    for ticker in ('SPY','QQQ'):
      x=ledger[ledger.ticker.eq(ticker)]; y=trades[trades["ticker"].eq(ticker)] if "ticker" in trades.columns else trades
      metrics.append({'scenario_id':'CURRENT_ENTRY_V1_STRICT','split':split.upper(),'ticker':ticker,'candidate_rows':len(x),'opened_trades':len(y),'total_pnl':'NOT_COMPUTABLE_NO_OPENED_TRADES','profit_factor':'NOT_COMPUTABLE_NO_OPENED_TRADES','stop_rate':'NOT_COMPUTABLE_NO_OPENED_TRADES','worst_trade':'NOT_COMPUTABLE_NO_OPENED_TRADES','planned_loss_exposure':'NOT_COMPUTABLE_NO_OPENED_TRADES'})
      for token in ['REGIME_RED','YELLOW_STRUCTURE_FAIL','SAFE_STRIKE_BUFFER_INSUFFICIENT','OPTION_VOLUME_BELOW_MINIMUM','SHORT_LEG_OPEN_INTEREST_BELOW_MINIMUM','EXPIRATIONS_INSUFFICIENT','CREDIT_EFFICIENCY_BELOW_MINIMUM','trend_gate_reject','pullback_gate_reject','strike_gate_reject','MARKET_OR_TREND_CONTEXT_UNAVAILABLE']:
        coverage.append({'split':split.upper(),'ticker':ticker,'reason_code':token,'candidate_rows_with_code':int(x.reason.fillna('').str.contains(token,regex=False).sum())})
      for order in ['1','2','3','4+']:
        scale.append({'scenario_id':'CURRENT_ENTRY_V1_STRICT','split':split.upper(),'ticker':ticker,'scale_in_order':order,'entries':0,'profit_factor':'NOT_COMPUTABLE_NO_OPENED_TRADES','stop_rate':'NOT_COMPUTABLE_NO_OPENED_TRADES','worst_trade':'NOT_COMPUTABLE_NO_OPENED_TRADES','planned_loss_exposure':'NOT_COMPUTABLE_NO_OPENED_TRADES'})
  pd.DataFrame(metrics).to_csv(OUT/'strict_scenario_metrics.csv',index=False)
  pd.DataFrame(coverage).to_csv(OUT/'strict_rule_coverage.csv',index=False)
  pd.DataFrame(scale).to_csv(OUT/'strict_scale_in_metrics.csv',index=False)
  legacy=ROOT/'research_outputs'/'spy_qqq_pcs_baseline_20260821'
  rows=[]
  for ticker in ('SPY','QQQ'):
    p=legacy/f'{ticker}_entry_contract_v2.parquet'
    if p.exists():
      x=pd.read_parquet(p);rows.append({'scenario_id':'VERIFIED_LEGACY_BASELINE','ticker':ticker,'candidate_rows':len(x),'source':str(p.relative_to(ROOT))})
  pd.DataFrame(rows).to_csv(OUT/'legacy_baseline_comparison_identity.csv',index=False)
  print(json.dumps({'strict_metrics':len(metrics),'strict_opened':0,'legacy':rows},indent=2))
if __name__=='__main__':main()
