import pandas as pd
from pcs.research.credit_stop import credit_bucket, buffer_bucket, valid_entry, select_pair, _quarter_files, DEFAULT_SAFE_STRIKE_ATR, CONSERVATIVE_SAFE_STRIKE_ATR

def test_quote_and_buckets():
    assert valid_entry({'Bid Price':1,'Ask Price':2})
    assert not valid_entry({'Bid Price':2,'Ask Price':1})
    assert credit_bucket(.15)=="15-18%" and credit_bucket(.20)==">=20%"
    assert buffer_bucket(2.0)=="1.5-2.0 ATR"

def test_downward_pair_selection():
    q=pd.DataFrame({'Expiry Date':pd.to_datetime(['2026-07-01']*3),'Call/Put':['p']*3,'Strike':[100,95,90],'Bid Price':[1]*3,'Ask Price':[2]*3})
    s,l=select_pair(q,pd.Timestamp('2026-07-01'),110,5)
    assert s.Strike==100 and l.Strike==95

def test_quarter_pruning():
    paths=_quarter_files('data/raw/options/NVDA','2025-01-01','2025-03-31')
    assert [p.name for p in paths] == ['NVDA_2025_q1_option_chain.csv']

def test_safe_strike_defaults():
    assert DEFAULT_SAFE_STRIKE_ATR == 2.3
    assert CONSERVATIVE_SAFE_STRIKE_ATR == 2.5

def test_conservative_safe_strike_requires_explicit_selection():
    q=pd.DataFrame({'Expiry Date':pd.to_datetime(['2026-07-01']*4),'Call/Put':['p']*4,'Strike':[100,95,90,85],'Bid Price':[1]*4,'Ask Price':[2]*4})
    default,_=select_pair(q,pd.Timestamp('2026-07-01'),110,5)
    conservative,_=select_pair(q,pd.Timestamp('2026-07-01'),110,5,safe_strike_atr=2.5)
    assert default is not None and conservative is not None
    assert DEFAULT_SAFE_STRIKE_ATR != CONSERVATIVE_SAFE_STRIKE_ATR
