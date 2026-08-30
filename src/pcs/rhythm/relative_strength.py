import numpy as np, pandas as pd
def compute_relative_strength(ticker:pd.DataFrame, benchmark:pd.DataFrame, *, sector=None, peers=None)->pd.DataFrame:
    def prep(x): return x.assign(date=pd.to_datetime(x.date)).set_index("date").close.astype(float)
    a,b=prep(ticker),prep(benchmark); out=pd.DataFrame(index=a.index); out["ticker_return_20d"]=np.log(a/a.shift(20)); out["market_return_20d"]=np.log(b/b.shift(20)); out["market_relative_20d"]=out.ticker_return_20d-out.market_return_20d
    if sector is not None: out["sector_relative_20d"]=out.ticker_return_20d-np.log(prep(sector).reindex(out.index)/prep(sector).reindex(out.index).shift(20))
    if peers: out["peer_relative_20d"]=out.ticker_return_20d-pd.concat([np.log(prep(p)/prep(p).shift(20)) for p in peers],axis=1).mean(axis=1)
    out["relative_ols_slope"]=out.market_relative_20d.rolling(20,min_periods=20).apply(lambda x: np.polyfit(np.arange(len(x)),x,1)[0],raw=True); out["relative_acceleration"]=out.market_relative_20d-out.market_relative_20d.shift(5); return out.reset_index()
