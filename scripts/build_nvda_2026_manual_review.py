"""PIT-safe NVDA 2026 Trend/Timing review; no outcomes are read."""
from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.market_context import build_market_context
from pcs.engine.decision_engine import load_rules
from pcs.entry.trend_gate import evaluate_trend_gate
from pcs.entry.pullback_gate import evaluate_pullback_gate

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research_outputs/nvda_pcs_2026_manual_review'

def main():
    access=PCSDataAccess.canonical(); rules=load_rules()
    daily=access.read_prices('NVDA', start_date='2025-01-01', end_date='2026-09-01').sort_values('date')
    signal_start=pd.Timestamp('2026-01-01')
    warmup_rows=int((pd.to_datetime(daily.date)<signal_start).sum())
    feature_min_date=pd.to_datetime(daily.date).min().date().isoformat()
    days=pd.to_datetime(daily.date).dt.normalize(); signal=daily[days.between('2026-01-02','2026-09-01')]['date'].drop_duplicates().tolist()
    rows=[]; packets=[]
    for day in signal:
        day=pd.Timestamp(day).normalize(); frame=daily[daily.date<=day].copy()
        try:
            ctx=build_market_context('NVDA',day,data_access=access,rules=rules,daily_frame=frame)
            tg=evaluate_trend_gate(ctx.score_result,ctx.interpretation,ctx.snapshot); pg=evaluate_pullback_gate(tg,ctx.snapshot,ctx.interpretation); eng=ctx.snapshot.market_structure_engine
            action='ENTRY_READY' if tg.trend_gate_result=='PASS' and pg.pullback_gate_result=='PASS' else 'WATCH' if tg.trend_gate_result=='WATCH' else 'WAIT'
            q=access.read_quotes('NVDA',day,day); qstatus='AVAILABLE' if not q.empty else 'UNAVAILABLE'
            row={'date':day.date().isoformat(),'feature_min_date':feature_min_date,'feature_max_date':str(eng.feature_max_date.date()),'warmup_rows_loaded':warmup_rows,'warmup_rows_before_signal_start':warmup_rows,'signal_rows_evaluated':len(signal),'generation_id':getattr(access,'generation_id',None),'pit_verified':str(eng.feature_max_date.date())<=day.date().isoformat(),'close':ctx.underlying_price,'SMA20':ctx.sma20,'SMA50':ctx.sma50,'EMA200':ctx.sma200,'ATR14':ctx.atr14,'ADX':ctx.adx,'RSI14':ctx.rsi,'structural_trend':eng.structural_trend,'short_term_phase':eng.short_term_phase,'structural_alignment':eng.structural_alignment,'sma20_slope':eng.sma20_slope,'sma50_slope':eng.sma50_slope,'ema200_slope':eng.ema200_slope,'sma20_slope_atr_5d':eng.sma20_slope_atr_5d,'sma50_slope_atr_5d':eng.sma50_slope_atr_5d,'ema200_slope_atr_5d':eng.ema200_slope_atr_5d,'RVOL20':eng.rvol20,'MACD_hist':eng.macd_hist,'MACD_hist_change':eng.macd_hist_change,'reclaim_age':eng.reclaim_age,'close_location':eng.close_location,'upper_wick_atr':eng.upper_wick_atr,'upper_rejection':eng.upper_rejection,'support':ctx.support,'timing_action':action,'trend_gate':tg.trend_gate_result,'pullback_gate':pg.pullback_gate_result,'reason_codes':list(eng.reason_codes),'primary_reason':(list(tg.reasons)+list(pg.reasons))[0] if (tg.reasons or pg.reasons) else 'NONE','secondary_reasons':list(dict.fromkeys([*tg.reasons,*pg.reasons])),'options_status':qstatus}
            rows.append(row)
            if action in {'ENTRY_READY','WATCH'}: packets.append({'ticker':'NVDA','date':row['date'],'mode':'HISTORICAL_PIT_MANUAL_REVIEW','feature_max_date':row['feature_max_date'],'pit_verified':row['pit_verified'],'price':{'close':row['close'],'SMA20':row['SMA20'],'SMA50':row['SMA50'],'EMA200':row['EMA200'],'ATR14':row['ATR14']},'trend':{k:row[k] for k in ['structural_trend','short_term_phase','structural_alignment','sma20_slope','sma50_slope','ema200_slope']},'momentum':{k:row[k] for k in ['RSI14','MACD_hist','MACD_hist_change','ADX']},'volume':{'RVOL':None,'confirmation':'NOT_IN_CONTEXT'},'price_action':{k:row[k] for k in ['close_location','upper_wick_atr','upper_rejection','reclaim_age']},'support':{'primary_support':row['support']},'timing':{'rule_action':action,'primary_reason':row['primary_reason'],'positive_evidence':[],'negative_evidence':row['secondary_reasons'],'confirmation_missing':[],'watch_for':[]},'options':{'status':qstatus},'hard_blocks':[],'uncertainties':[]})
        except Exception as exc:
            rows.append({'date':day.date().isoformat(),'feature_max_date':None,'pit_verified':False,'timing_action':'DATA_BLOCKED','primary_reason':f'{type(exc).__name__}:{exc}','options_status':'NOT_EVALUATED'})
    out=pd.DataFrame(rows); OUT.mkdir(parents=True,exist_ok=True); out.to_csv(OUT/'nvda_pcs_2026_manual_review.csv',index=False); out.to_json(OUT/'nvda_pcs_2026_manual_review.json',orient='records',date_format='iso'); (OUT/'nvda_pcs_2026_ai_packets.jsonl').write_text('\n'.join(json.dumps(x,default=str) for x in packets)+'\n',encoding='utf-8')
    # Avoid optional tabulate dependency while retaining a readable report.
    md=['# NVDA PCS 2026 Manual Review','',f'PIT scope: 2026-01-02 to {out.date.max()}; canonical warmup is included and future outcomes are excluded.','',f"Counts: {out.timing_action.value_counts().to_dict()}",'','## Daily results','', '```csv', out.to_csv(index=False), '```','', '## Human review','','| Date | System action | Trend correct | Phase correct | Volume correct | Support correct | Timing correct | Strike correct | Human verdict | Notes |','|---|---|---|---|---|---|---|---|---|---|','']
    (OUT/'nvda_pcs_2026_manual_review.md').write_text('\n'.join(md),encoding='utf-8'); print({'rows':len(out),'counts':out.timing_action.value_counts().to_dict(),'packets':len(packets),'out':str(OUT)})
if __name__=='__main__': main()
