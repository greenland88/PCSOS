"""Backfill exact frozen spread quotes from canonical routed options data."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT / "research_outputs/safe_strike_stage4a"
TICKERS = ("NVDA", "AMD", "TSLA", "AMZN")


def backfill(path: Path, ticker: str) -> dict:
    d = pd.read_parquet(path).copy(); d["date"] = pd.to_datetime(d.date).dt.normalize(); d["expiration"] = pd.to_datetime(d.expiration).dt.normalize()
    q = PCSDataAccess().read_quotes(ticker, d.date.min(), d.date.max())
    q.trade_date = pd.to_datetime(q.trade_date).dt.normalize(); q.expiration = pd.to_datetime(q.expiration).dt.normalize(); q.strike = pd.to_numeric(q.strike)
    q = q.drop_duplicates(["trade_date", "expiration", "strike"], keep="first")
    sm = q.rename(columns={"strike":"short_strike", "bid":"short_bid", "ask":"short_ask", "volume":"option_volume", "open_interest":"open_interest"})
    lg = q.rename(columns={"strike":"long_strike", "bid":"long_bid", "ask":"long_ask", "volume":"long_volume", "open_interest":"long_open_interest"})
    keys=["trade_date","expiration"]
    drop_fields=["short_bid","short_ask","long_bid","long_ask","long_volume","long_open_interest","option_volume","open_interest","bid_ask_pct"]
    out=d.drop(columns=[c for c in drop_fields if c in d.columns]).merge(sm[keys+["short_strike","short_bid","short_ask","option_volume","open_interest"]], left_on=["date","expiration","short_strike"], right_on=["trade_date","expiration","short_strike"], how="left").drop(columns=["trade_date"])
    out=out.merge(lg[keys+["long_strike","long_bid","long_ask","long_volume","long_open_interest"]], left_on=["date","expiration","long_strike"], right_on=["trade_date","expiration","long_strike"], how="left").drop(columns=["trade_date"])
    out["bid_ask_pct"]=(pd.to_numeric(out.short_ask)-pd.to_numeric(out.short_bid))/((pd.to_numeric(out.short_ask)+pd.to_numeric(out.short_bid))/2).clip(lower=1e-12)
    out.to_parquet(path,index=False)
    fields=["short_bid","short_ask","long_bid","long_ask","long_volume","long_open_interest"]
    return {"ticker":ticker,"rows":len(out),"missing":{f:int(out[f].isna().sum()) for f in fields},"ranges":{f:[float(out[f].min()),float(out[f].max())] for f in fields},"source":"data/parquet/options_v2","exact_identity":True}


def main():
    results=[]
    for t in TICKERS:
        p=ROOT/"authoritative_amzn_794_entry_contract_v2.parquet" if t=="AMZN" else ROOT/"candidate_inputs"/f"{t}.parquet"
        results.append(backfill(p,t))
    (ROOT/"stage4a_mapping_audit.json").write_text(json.dumps(results,indent=2,default=str),encoding="utf-8"); print(json.dumps(results,indent=2,default=str))

if __name__=="__main__": main()
