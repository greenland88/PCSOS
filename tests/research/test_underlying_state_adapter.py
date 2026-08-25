import pandas as pd
from pcs.research.underlying_state import UnderlyingState, evaluate_as_of, STATE_PRIORITY

def frame(n=260, shift=0):
 d=pd.date_range('2020-01-01',periods=n,freq='B'); c=pd.Series(range(n),dtype=float)+100+shift
 return pd.DataFrame({'date':d,'open':c-.2,'high':c+.5,'low':c-.5,'close':c,'volume':1000.})
def test_ticker_isolation():
 assert evaluate_as_of(frame(260), 'SPY', '2020-12-31')['ticker']=='SPY'
def test_no_future_ohlcv():
 a=frame(260);b=a.copy();b.loc[b.index[-1],'close']=9999
 assert evaluate_as_of(a,'SPY',a.date.iloc[-2])==evaluate_as_of(b,'SPY',b.date.iloc[-2])
def test_unknown_short_lookback(): assert evaluate_as_of(frame(10),'SPY','2020-01-10')['final_underlying_state']=='UNKNOWN'
def test_unknown_preserved(): assert 'INSUFFICIENT_LOOKBACK' in evaluate_as_of(frame(10),'SPY','2020-01-10')['unknown_reason_codes']
def test_state_priority_fixed(): assert STATE_PRIORITY[0]==UnderlyingState.BREAKDOWN
def test_lookahead_flag_pass(): assert evaluate_as_of(frame(),'SPY','2020-12-31')['lookahead_check_result']=='PASS'
def test_support_confirmation_never_precedes_pivot():
 r=evaluate_as_of(frame(),'SPY','2020-12-31')
 if r.get('pivot_date') is not None: assert pd.Timestamp(r['pivot_confirmation_date'])>=pd.Timestamp(r['pivot_date'])
def test_state_is_enum_value(): assert evaluate_as_of(frame(),'QQQ','2020-12-31')['final_underlying_state'] in {x.value for x in UnderlyingState}
def test_no_cross_ticker_copy(): assert evaluate_as_of(frame(260,0),'SPY','2020-12-31')['ticker']!=evaluate_as_of(frame(260,10),'QQQ','2020-12-31')['ticker']
def test_transition_is_deterministic(): assert evaluate_as_of(frame(),'SPY','2020-12-31')==evaluate_as_of(frame(),'SPY','2020-12-31')
def test_state_conflict_field_present(): assert 'state_conflict' in evaluate_as_of(frame(),'SPY','2020-12-31')
def test_recovery_is_not_inferred(): assert evaluate_as_of(frame(),'SPY','2020-12-31').get('recovery_reclaim_result')=='UNKNOWN_NO_PRODUCTION_RECONFIRMATION_PREDICATE'
def test_confirmed_support_first_usable_equals_confirmation():
 r=evaluate_as_of(frame(),'SPY','2020-12-31'); assert r.get('support_first_usable_date')==r.get('pivot_confirmation_date')
def test_no_future_state_is_not_passed_as_unknown(): assert evaluate_as_of(frame(10),'SPY','2020-01-10')['final_underlying_state']!='UPTREND'
