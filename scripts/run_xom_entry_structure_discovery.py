"""PIT-only descriptive XOM entry-structure discovery; never a production rule."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from pcs.data.access import PCSDataAccess

OUT = Path("research_outputs/xom_entry_structure_discovery_train_20260825")

def main():
    access = PCSDataAccess()
    daily = access.read_prices("XOM", start_date="2018-01-01", end_date="2025-12-31").copy()
    daily["date"] = pd.to_datetime(daily.date).dt.normalize()
    daily = daily.sort_values("date").reset_index(drop=True)
    close = daily.close.astype(float)
    high = daily.high.astype(float)
    low = daily.low.astype(float)
    daily["sma20"] = close.rolling(20, min_periods=20).mean()
    daily["sma50"] = close.rolling(50, min_periods=50).mean()
    daily["sma200"] = close.rolling(200, min_periods=200).mean()
    tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    daily["atr14"] = tr.rolling(14, min_periods=14).mean()
    for n in (3, 5, 10, 20, 60): daily[f"ret{n}"] = close.pct_change(n)
    daily["drawdown60"] = close / close.rolling(60, min_periods=60).max() - 1
    daily["close_location"] = (close-low) / (high-low).replace(0, np.nan)
    daily["support20_distance_atr"] = (close-low.rolling(20, min_periods=20).min()) / daily.atr14
    for n in (5, 20): daily[f"future_ret{n}"] = close.shift(-n) / close - 1
    feature_cols = ["sma20","sma50","sma200","atr14","ret3","ret5","ret10","ret20","ret60","drawdown60","close_location","support20_distance_atr"]
    ready = daily.dropna(subset=feature_cols + ["future_ret5","future_ret20"]).copy()
    ready = ready[ready.date <= "2025-12-31"]
    ready["trend_bucket"] = np.select([ready.close > ready.sma200, ready.close < ready.sma200], ["ABOVE_SMA200", "BELOW_SMA200"], default="AT_SMA200")
    ready["sma50_bucket"] = np.select([ready.close > ready.sma50, ready.close < ready.sma50], ["ABOVE_SMA50", "BELOW_SMA50"], default="AT_SMA50")
    ready["direction_bucket"] = np.select([ready.ret5 > 0, ready.ret5 < 0], ["UP_5D", "DOWN_5D"], default="FLAT_5D")
    ready["close_location_bucket"] = pd.cut(ready.close_location, [-np.inf, .33, .67, np.inf], labels=["LOW","MID","HIGH"]).astype(str)
    ready["drawdown_bucket"] = pd.cut(ready.drawdown60, [-np.inf, -.15, -.05, np.inf], labels=["DEEP","MODERATE","SHALLOW_OR_NONE"]).astype(str)
    group_cols = ["trend_bucket","sma50_bucket","direction_bucket","close_location_bucket","drawdown_bucket"]
    rows=[]
    for col in group_cols:
        for key, g in ready.groupby(col, observed=True, sort=True):
            rows.append({"feature":col,"state":str(key),"rows":int(len(g)),"mean_future_ret5":float(g.future_ret5.mean()),"mean_future_ret20":float(g.future_ret20.mean()),"positive_future_ret5_rate":float((g.future_ret5>0).mean()),"positive_future_ret20_rate":float((g.future_ret20>0).mean())})
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT/"feature_state_summary.csv", index=False)
    summary={"module":"pcs.research.xom_entry_structure_discovery","version":"1.0","symbol":"XOM","as_of":"2025-12-31","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA","research_mode":"NEW_ENTRY","population":"FULL_PIT_DAILY_CALENDAR","train_rows":int(len(ready)),"feature_columns":feature_cols,"feature_state_summary":"feature_state_summary.csv","candidate_signal_dates_created":False,"validation_read":False,"final_oos_read":False,"production_changes":False,"thresholds_changed":False,"frozen_artifacts_changed":False,"reason_codes":["FULL_PIT_CALENDAR","PIT_OBSERVABLE_FEATURES_ONLY","DESCRIPTIVE_ONLY","NO_SIGNAL_PROMOTION","NO_FINAL_OOS"]}
    (OUT/"discovery_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))

if __name__ == "__main__": main()
