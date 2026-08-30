from __future__ import annotations
import numpy as np, pandas as pd

def compute_features(frame: pd.DataFrame, *, symbol:str="", price_basis:str="UNKNOWN", calculation_version:str="rhythm_v1") -> pd.DataFrame:
    """Compute only information available on each row; input must be canonical daily data."""
    x=frame.copy(); x["date"]=pd.to_datetime(x["date"]); x=x.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if "close" not in x: raise ValueError("CLOSE_REQUIRED")
    c=pd.to_numeric(x.close, errors="coerce"); logc=np.log(c.where(c>0)); r=logc.diff()
    x["log_return_1d"]=r
    for h in (5,20,60,200):
        x[f"log_return_{h}d"]=logc-logc.shift(h)
        x[f"ols_slope_{h}d"]=logc.rolling(h,min_periods=h).apply(lambda a: np.polyfit(np.arange(len(a)),a,1)[0], raw=True)
        x[f"normalized_slope_{h}d"]=x[f"ols_slope_{h}d"]/(r.rolling(h,min_periods=h).std()*np.sqrt(252))
    x["acceleration_short"]=x.normalized_slope_5d-x.normalized_slope_20d; x["acceleration_medium"]=x.normalized_slope_20d-x.normalized_slope_60d
    for h in (5,20,60): x[f"rv{h}"]=r.rolling(h,min_periods=h).std()*np.sqrt(252)
    x["rv5_rv20"]=x.rv5/x.rv20; x["downside_rv"]=r.where(r<0).rolling(20,min_periods=20).std()*np.sqrt(252)
    if "high" in x and "low" in x: x["atr14_close"]=(pd.to_numeric(x.high)-pd.to_numeric(x.low)).rolling(14,min_periods=14).mean()/c
    if "volume" in x:
        v=pd.to_numeric(x.volume,errors="coerce"); med=v.rolling(20,min_periods=20).median(); clipped=v.clip(lower=v.rolling(20,min_periods=20).quantile(.01),upper=v.rolling(20,min_periods=20).quantile(.99)); x["volume_median20_ratio"]=clipped/med; x["up_volume_share_5"]=clipped.where(r>0).rolling(5,min_periods=5).sum()/clipped.rolling(5,min_periods=5).sum(); x["up_volume_share_20"]=clipped.where(r>0).rolling(20,min_periods=20).sum()/clipped.rolling(20,min_periods=20).sum(); x["down_volume_expansion"]=clipped.where(r<0)/med; x["price_volume_confirmation"]=np.sign(r)*np.sign(clipped/med-1)
    return x
