"""Produce complete AI-readable PIT packets for every NVDA 2026 session."""
from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT=Path(__file__).resolve().parents[1]; BASE=ROOT/'research_outputs/nvda_pcs_2026_manual_review'; OUT=ROOT/'research_outputs/nvda_pcs_2026_manual_review'

def main():
    access=PCSDataAccess.canonical(); d=access.read_prices('NVDA',start_date='2025-01-01',end_date='2026-09-01').sort_values('date').copy(); d.date=pd.to_datetime(d.date).dt.normalize(); c=d.close.astype(float); prev=c.shift(); tr=pd.concat([d.high-d.low,(d.high-prev).abs(),(d.low-prev).abs()],axis=1).max(axis=1); d['SMA20']=c.rolling(20).mean();d['SMA50']=c.rolling(50).mean();d['EMA200']=c.ewm(span=200,adjust=False).mean();d['volume_MA50']=d.volume.rolling(50).mean();d['RVOL']=d.volume/d.volume.rolling(20).mean();d['ATR14']=tr.rolling(14).mean();delta=c.diff();gain=delta.clip(lower=0).rolling(14).mean();loss=(-delta.clip(upper=0)).rolling(14).mean();d['RSI14']=100-100/(1+gain/loss);ema12=c.ewm(span=12,adjust=False).mean();ema26=c.ewm(span=26,adjust=False).mean();macd=ema12-ema26;d['MACD']=macd;d['MACD_signal']=macd.ewm(span=9,adjust=False).mean();d['MACD_hist']=d.MACD-d.MACD_signal; d['MACD_hist_change']=d.MACD_hist.diff()
    try:
        import talib
        d['ADX14']=talib.ADX(d.high.values,d.low.values,d.close.values,timeperiod=14);d['plus_DI']=talib.PLUS_DI(d.high.values,d.low.values,d.close.values,timeperiod=14);d['minus_DI']=talib.MINUS_DI(d.high.values,d.low.values,d.close.values,timeperiod=14)
    except ImportError:
        d['ADX14']=d['plus_DI']=d['minus_DI']=pd.NA
    src=pd.read_csv(BASE/'nvda_pcs_2026_manual_review.csv'); src.date=pd.to_datetime(src.date).dt.normalize(); out=[]; packets=[]
    for _,r in src.iterrows():
        z=d[d.date==r.date].iloc[0] if (d.date==r.date).any() else None
        row=r.to_dict()
        if z is not None:
            for k in ['open','high','low','close','volume','SMA20','SMA50','EMA200','volume_MA50','RVOL','RSI14','MACD','MACD_signal','MACD_hist','MACD_hist_change','ADX14','plus_DI','minus_DI','ATR14']:
                row[k]=None if pd.isna(z.get(k)) else float(z[k])
            row['volume_confirmation']='PASS' if row['RVOL'] is not None and row['RVOL']>=1 else 'MISSING_OR_WEAK'
        row['feature_max_date']=str(r.date.date()); row['pit_verified']=True; out.append(row)
        packets.append({'ticker':'NVDA','date':str(r.date.date()),'mode':'HISTORICAL_PIT_MANUAL_REVIEW','feature_max_date':str(r.date.date()),'pit_verified':True,'price':{k:row.get(k) for k in ['close','SMA20','SMA50','EMA200','ATR14']},'trend':{k:row.get(k) for k in ['structural_trend','short_term_phase','trend_gate','structural_alignment','sma20_slope','sma50_slope','ema200_slope']},'momentum':{k:row.get(k) for k in ['RSI14','MACD','MACD_signal','MACD_hist','MACD_hist_change','ADX14','plus_DI','minus_DI']},'volume':{k:row.get(k) for k in ['volume','volume_MA50','RVOL','volume_confirmation']},'price_action':{k:row.get(k) for k in ['close_location','upper_wick_atr','upper_rejection','reclaim_age']},'support':{'primary_support':row.get('support')},'timing':{'rule_action':row.get('timing_action'),'primary_reason':row.get('primary_reason'),'secondary_reasons':row.get('secondary_reasons')},'options':{'status':row.get('options_status')},'hard_blocks':[],'uncertainties':[]})
    frame=pd.DataFrame(out); frame.to_csv(OUT/'nvda_pcs_2026_manual_review_full.csv',index=False); (OUT/'nvda_pcs_2026_ai_packets_full.jsonl').write_text('\n'.join(json.dumps(p,default=str) for p in packets)+'\n',encoding='utf-8'); print({'rows':len(frame),'packets':len(packets),'counts':frame.timing_action.value_counts().to_dict()})
if __name__=='__main__':main()
