import pandas as pd
from pcs.research.event_controlled_revalidation import _stats

def test_stats_is_descriptive():
    d=pd.DataFrame({"realized_pnl":[1.,-1.],"stop":[False,True],"close":[10.,10.],"min_low_5d":[9.,8.],"min_low_10d":[9.,7.],"atr14":[1.,1.],"breach_2atr_5d":[False,True],"breach_2atr_10d":[False,True]})
    assert _stats(d)["N"]==2
    assert _stats(d)["STOP"]==.5
