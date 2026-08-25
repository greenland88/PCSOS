from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
import time
import pandas as pd
import numpy as np
import duckdb
from pcs.data.duckdb_store import connect as connect_duckdb, refresh_views as refresh_duckdb_views
from pcs.data.access import PCSDataAccess, DataAccessError
from .ab_comparison import compare_symbol
from .trend_history import build_trend_history
from pcs.trend import calculate_base_indicators, TrendIndicatorConfig

START=pd.Timestamp("2024-06-10"); END=pd.Timestamp("2026-07-31")
DEFAULT_SAFE_STRIKE_ATR=2.3
CONSERVATIVE_SAFE_STRIKE_ATR=2.5

OPTION_COLUMNS=["Trade Date","Expiry Date","Call/Put","Strike","Last Trade Price","Bid Price","Ask Price","Delta","Bid Implied Volatility","Ask Implied Volatility","Open Interest","Volume"]
_DUCK_QUARTER_CACHE={}
_DUCK_CLEAN_QUARTER_CACHE={}

def _quarter_files(root, start, end):
    paths=[]
    symbol = Path(root).name.upper()
    for path in sorted(Path(root).glob(f"{symbol}_????_q?_option_chain.csv")):
        m=re.search(r"_(\d{4})_q([1-4])_",path.name); year,quarter=int(m.group(1)),int(m.group(2)); first=pd.Timestamp(year,quarter*3-2,1); last=first+pd.offsets.QuarterEnd()
        if last>=pd.Timestamp(start) and first<=pd.Timestamp(end): paths.append(path)
    return paths

def load_quotes(root="data/raw/options/NVDA", start=START, end=END):
    raise DataAccessError("LEGACY_RESEARCH_READER_DISABLED: use load_quotes_canonical")
    timing={}; t=time.perf_counter(); paths=_quarter_files(root,start,end); timing["file_discovery_seconds"]=time.perf_counter()-t
    t=time.perf_counter(); parts=[]
    for path in paths:
        df=pd.read_csv(path,usecols=OPTION_COLUMNS); df["Trade Date"]=pd.to_datetime(df["Trade Date"]); df=df[df["Trade Date"].between(start,end)]; df=df[df["Call/Put"].str.lower().eq("p")]
        if not df.empty: parts.append(df)
    timing["csv_read_filter_seconds"]=time.perf_counter()-t; raw=pd.concat(parts,ignore_index=True); key=["Trade Date","Expiry Date","Strike","Call/Put"]
    raw["Expiry Date"]=pd.to_datetime(raw["Expiry Date"]); raw["Call/Put"]=raw["Call/Put"].str.lower()
    grouped=[]; ambiguous=0; deduped=0
    for _,g in raw.groupby(key,sort=False):
        price=g[["Last Trade Price","Bid Price","Ask Price"]].drop_duplicates()
        if len(price)>1: ambiguous+=len(g); continue
        deduped+=len(g)-1; grouped.append(g.iloc[0])
    timing["duplicate_validity_seconds"]=time.perf_counter()-t; clean=pd.DataFrame(grouped).reset_index(drop=True); clean["DTE"]=(clean["Expiry Date"]-clean["Trade Date"]).dt.days; clean["mid"]=(clean["Bid Price"]+clean["Ask Price"])/2
    return clean,{"duplicate_rows_deduped":deduped,"ambiguous_quote_rows_excluded":ambiguous,"quarter_files_opened":len(paths),"option_rows_loaded":len(clean),"timing":timing}

def canonical_option_glob(symbol: str, root: str | Path | None = None) -> str:
    """Resolve one ticker to the canonical partitioned Parquet source."""
    access = (PCSDataAccess() if root is None else
              PCSDataAccess(manifest_path="data/manifests/storage_manifest_options_v2.csv",
                            parquet_root=Path(root).parent))
    return access.resolve_source("options", symbol, START, END).path

def load_quotes_canonical(symbol: str, start, end, root: str | Path | None = None):
    """Bounded research read from ticker-partitioned canonical Parquet."""
    # Candidate generation must read the active options_v2 route through the
    # canonical Parquet boundary.  The legacy UnifiedDataAccess compatibility
    # facade called ``read_quotes(..., dataset='options')`` and could resolve a
    # legacy CSV manifest for a newly onboarded ticker, causing a binary
    # Parquet file to be opened by a text reader downstream.
    access = (PCSDataAccess() if root is None else
              PCSDataAccess(manifest_path="data/manifests/storage_manifest_options_v2.csv",
                            parquet_root=Path(root).parent))
    try:
        # Resolve the ticker's configured authoritative dataset first.  This
        # preserves legacy canonical routes for existing symbols while using
        # options_v2 for CAT and future onboarded symbols.
        resolved = access.resolve_source("options", symbol, start, end)
        raw = access.read(resolved.dataset, symbol, start, end)
    except Exception as exc:
        raise DataAccessError(
            f"CANONICAL_OPTIONS_READ_ERROR ticker={str(symbol).upper()} "
            f"partition={pd.Timestamp(start).to_period('Q')}..{pd.Timestamp(end).to_period('Q')} "
            f"expected_format=parquet reader=PCSDataAccess.read original_exception={exc}"
        ) from exc
    raw = raw[raw.call_put.astype(str).str.lower().eq("p")]
    raw = raw.rename(columns={"trade_date":"Trade Date", "expiration_date":"Expiry Date",
        "call_put":"Call/Put", "strike":"Strike", "last":"Last Trade Price",
        "bid":"Bid Price", "ask":"Ask Price", "delta":"Delta",
        "bid_iv":"Bid Implied Volatility", "ask_iv":"Ask Implied Volatility",
        "open_interest":"Open Interest", "volume":"Volume"})
    glob = canonical_option_glob(symbol, root)
    clean = _clean_option_frame_fast(_canonical_to_option_frame(raw))
    meta = {"source": "canonical_partitioned_parquet", "symbol": symbol.upper(), "path": glob,
            "reader": "PCSDataAccess.read_parquet_duckdb", "rows_returned": len(clean),
            # Preserve the historical research metadata name while exposing
            # the clearer canonical name used by newer callers.
            "option_rows_loaded": len(clean)}
    return clean, meta

def load_quotes_canonical_index(symbol: str, start, end, root: str | Path | None = None):
    """Load one bounded ticker slice and index it by trade date for a run."""
    quotes, meta = load_quotes_canonical(symbol, start, end, root)
    index = {day: group.copy() for day, group in quotes.groupby("Trade Date", sort=False)}
    meta = {**meta, "scan_count": 1, "rows_returned": len(quotes), "index_dates": len(index)}
    return index, meta

def load_spread_quotes_canonical(symbol: str, entry_date, tracking_end, expiration, strikes):
    """Load lifecycle quotes only from the ticker's active canonical route."""
    quotes, meta = load_quotes_canonical(symbol, entry_date, tracking_end)
    out = quotes[(quotes["Expiry Date"] == pd.Timestamp(expiration)) & quotes["Strike"].isin(list(strikes))]
    return out.reset_index(drop=True), {**meta, "rows_returned": len(out)}

def _canonical_to_option_frame(df):
    names={"trade_date":"Trade Date","expiration_date":"Expiry Date","call_put":"Call/Put","strike":"Strike","last":"Last Trade Price","bid":"Bid Price","ask":"Ask Price","delta":"Delta","bid_iv":"Bid Implied Volatility","ask_iv":"Ask Implied Volatility","open_interest":"Open Interest","volume":"Volume"}
    out=df.rename(columns=names).copy()
    for col in OPTION_COLUMNS:
        if col not in out: out[col]=np.nan
    out["Trade Date"]=pd.to_datetime(out["Trade Date"]); out["Expiry Date"]=pd.to_datetime(out["Expiry Date"]); out["Call/Put"]=out["Call/Put"].astype(str).str.lower()
    return out[OPTION_COLUMNS]

def _clean_option_frame(raw, files_opened=0, timing=None):
    raw=raw.copy(); key=["Trade Date","Expiry Date","Strike","Call/Put"]
    if raw.empty:
        clean=pd.DataFrame(columns=OPTION_COLUMNS+['DTE','mid'])
        return clean,{"duplicate_rows_deduped":0,"ambiguous_quote_rows_excluded":0,"quarter_files_opened":files_opened,"option_rows_loaded":0,"timing":timing or {}}
    grouped=[]; ambiguous=0; deduped=0
    for _,g in raw.groupby(key,sort=False):
        price=g[["Last Trade Price","Bid Price","Ask Price"]].drop_duplicates()
        if len(price)>1: ambiguous+=len(g); continue
        deduped+=len(g)-1; grouped.append(g.iloc[0])
    clean=pd.DataFrame(grouped).reset_index(drop=True); clean["DTE"]=(clean["Expiry Date"]-clean["Trade Date"]).dt.days; clean["mid"]=(clean["Bid Price"]+clean["Ask Price"])/2
    return clean,{"duplicate_rows_deduped":deduped,"ambiguous_quote_rows_excluded":ambiguous,"quarter_files_opened":files_opened,"option_rows_loaded":len(clean),"timing":timing or {}}

def _clean_option_frame_fast(raw):
    if raw.empty: return pd.DataFrame(columns=OPTION_COLUMNS+['DTE','mid'])
    key=["Trade Date","Expiry Date","Strike","Call/Put"]; price=["Last Trade Price","Bid Price","Ask Price"]
    distinct=raw.groupby(key,sort=False)[price].transform("nunique").max(axis=1)
    conflict=distinct.gt(1); usable=raw.loc[~conflict].drop_duplicates(key,keep="first").copy()
    usable["DTE"]=(usable["Expiry Date"]-usable["Trade Date"]).dt.days; usable["mid"]=(usable["Bid Price"]+usable["Ask Price"])/2
    return usable

def load_quotes_duckdb(db_path, symbol, start, end, con=None):
    raise DataAccessError("LEGACY_RESEARCH_READER_DISABLED: use load_quotes_canonical")
    own=con is None
    if con is None: con=connect_duckdb(db_path)
    # The monthly option store is queried directly; refreshing the legacy
    # whole-history view would defeat bounded execution.
    root = str(Path("data/parquet/options_monthly") / f"symbol={symbol.upper()}" / "trade_year=*" / "trade_month=*" / "*.parquet").replace("\\", "/")
    df = con.execute("""
        SELECT
            trade_date AS "Trade Date", expiration AS "Expiry Date",
            option_type AS "Call/Put", strike AS "Strike", last AS "Last Trade Price",
            bid AS "Bid Price", ask AS "Ask Price", delta AS "Delta",
            bid_iv AS "Bid Implied Volatility", ask_iv AS "Ask Implied Volatility",
            open_interest AS "Open Interest", volume AS "Volume",
            DTE
        FROM read_parquet(?, hive_partitioning=true)
        WHERE trade_date BETWEEN ? AND ?
          AND option_type = 'p'
          AND DTE BETWEEN 20 AND 45
          AND bid IS NOT NULL AND ask IS NOT NULL
    """, [root, pd.Timestamp(start).date(), pd.Timestamp(end).date()]).fetchdf()
    df["Trade Date"] = pd.to_datetime(df["Trade Date"])
    df["Expiry Date"] = pd.to_datetime(df["Expiry Date"])
    df["mid"] = (df["Bid Price"] + df["Ask Price"]) / 2
    if own: con.close()
    return df,{"duplicate_rows_deduped":0,"ambiguous_quote_rows_excluded":0,"quarter_files_opened":0,"option_rows_loaded":len(df),"timing":{}}

def load_entry_chain(root, entry_date):
    return load_quotes(root, pd.Timestamp(entry_date), pd.Timestamp(entry_date))

def load_entry_chain_duckdb_view(db_path, symbol, entry_date, con=None):
    """Disabled legacy view loader; use :func:`load_quotes_canonical`."""
    raise DataAccessError("LEGACY_RESEARCH_READER_DISABLED: use load_quotes_canonical")
    # Kept below only as historical source context; unreachable by contract.
    own = con is None
    if con is None:
        con = duckdb.connect(db_path, read_only=True)
    df = con.execute('''
        SELECT trade_date AS "Trade Date", expiration_date AS "Expiry Date",
               call_put AS "Call/Put", strike AS "Strike", last AS "Last Trade Price",
               bid AS "Bid Price", ask AS "Ask Price", delta AS "Delta",
               bid_iv AS "Bid Implied Volatility", ask_iv AS "Ask Implied Volatility",
               open_interest AS "Open Interest", volume AS "Volume"
        FROM options
        WHERE symbol = ? AND trade_date = ? AND lower(call_put) = 'p'
    ''', [symbol.upper(), pd.Timestamp(entry_date).date()]).fetchdf()
    clean, meta = _clean_option_frame(_canonical_to_option_frame(df))
    meta["source"] = "duckdb_options_view"
    meta["symbol"] = symbol.upper()
    if own:
        con.close()
    return clean, meta

def load_spread_quotes_duckdb_view(db_path, symbol, entry_date, tracking_end,
                                   expiration, strikes, con=None):
    """Disabled legacy view loader; use :func:`load_spread_quotes_canonical`."""
    raise DataAccessError("LEGACY_RESEARCH_READER_DISABLED: use load_spread_quotes_canonical")
    # Kept below only as historical source context; unreachable by contract.
    own = con is None
    if con is None:
        con = duckdb.connect(db_path, read_only=True)
    df = con.execute('''
        SELECT trade_date AS "Trade Date", expiration_date AS "Expiry Date",
               call_put AS "Call/Put", strike AS "Strike", last AS "Last Trade Price",
               bid AS "Bid Price", ask AS "Ask Price", delta AS "Delta",
               bid_iv AS "Bid Implied Volatility", ask_iv AS "Ask Implied Volatility",
               open_interest AS "Open Interest", volume AS "Volume"
        FROM options
        WHERE symbol = ? AND trade_date BETWEEN ? AND ?
          AND expiration_date = ? AND lower(call_put) = 'p'
          AND strike IN (?, ?)
    ''', [symbol.upper(), pd.Timestamp(entry_date).date(), pd.Timestamp(tracking_end).date(),
          pd.Timestamp(expiration).date(), float(strikes[0]), float(strikes[1])]).fetchdf()
    clean, _ = _clean_option_frame(_canonical_to_option_frame(df))
    if own:
        con.close()
    return clean

def load_spread_quotes(root, entry_date, tracking_end, expiration, strikes):
    raise DataAccessError("LEGACY_RESEARCH_READER_DISABLED: use load_spread_quotes_canonical")
    frames=[]
    for path in _quarter_files(root,pd.Timestamp(entry_date),pd.Timestamp(tracking_end)):
        df=pd.read_csv(path,usecols=OPTION_COLUMNS); df["Trade Date"]=pd.to_datetime(df["Trade Date"]); df["Expiry Date"]=pd.to_datetime(df["Expiry Date"])
        df=df[df["Trade Date"].between(pd.Timestamp(entry_date),pd.Timestamp(tracking_end)) & (df["Expiry Date"]==pd.Timestamp(expiration)) & df["Call/Put"].str.lower().eq("p") & df.Strike.isin(list(strikes))]
        if not df.empty: frames.append(df)
    if not frames:return pd.DataFrame(columns=OPTION_COLUMNS+['mid','DTE'])
    out=pd.concat(frames,ignore_index=True).drop_duplicates(["Trade Date","Expiry Date","Strike","Call/Put"]); out["Call/Put"]=out["Call/Put"].str.lower(); out["DTE"]=(out["Expiry Date"]-out["Trade Date"]).dt.days; out["mid"]=(out["Bid Price"]+out["Ask Price"])/2; return out

def load_spread_quotes_duckdb(db_path, symbol, entry_date, tracking_end, expiration, strikes, con=None):
    raise DataAccessError("LEGACY_RESEARCH_READER_DISABLED: use load_spread_quotes_canonical")
    own=con is None
    if con is None: con=connect_duckdb(db_path)
    root = str(Path("data/parquet/options_monthly") / f"symbol={symbol.upper()}" / "trade_year=*" / "trade_month=*" / "*.parquet").replace("\\", "/")
    df=con.execute("""
        SELECT trade_date AS "Trade Date", expiration AS "Expiry Date",
               option_type AS "Call/Put", strike AS "Strike", last AS "Last Trade Price",
               bid AS "Bid Price", ask AS "Ask Price", delta AS "Delta",
               bid_iv AS "Bid Implied Volatility", ask_iv AS "Ask Implied Volatility",
               open_interest AS "Open Interest", volume AS "Volume", DTE
        FROM read_parquet(?, hive_partitioning=true)
        WHERE trade_date BETWEEN ? AND ? AND expiration = ? AND option_type='p'
          AND strike IN (?, ?)
    """, [root, pd.Timestamp(entry_date).date(), pd.Timestamp(tracking_end).date(), pd.Timestamp(expiration).date(), float(strikes[0]), float(strikes[1])]).fetchdf()
    if df.empty:
        if own: con.close()
        return pd.DataFrame(columns=OPTION_COLUMNS+['mid','DTE'])
    if own: con.close()
    out=df.drop_duplicates(["Trade Date","Expiry Date","Strike","Call/Put"])
    out["DTE"]=(out["Expiry Date"]-out["Trade Date"]).dt.days; out["mid"]=(out["Bid Price"]+out["Ask Price"])/2
    return out

def precompute_trend_lookup(stock, benchmark, config=None, start=START, end=END):
    """Compute the existing public Trend/Entry pipeline once per as-of date."""
    history=build_trend_history(stock,benchmark,config,start,end)
    return {row.date: {**row.to_dict(),"current_state":row["trend_gate"],"row":{**row.to_dict(),"current_state":row["trend_gate"]}} for _,row in history.iterrows()}

def build_option_indexes(quotes):
    t=time.perf_counter(); result={
        "by_date": {day:g for day,g in quotes.groupby("Trade Date")},
        # Contract identity must retain option type.  Expiry+strike alone
        # aliases a call and a put in the same lifecycle index.
        "by_contract": {(key[0],key[1],str(key[2]).lower()):g
                        for key,g in quotes.groupby(["Expiry Date","Strike","Call/Put"])},
    }
    result["index_build_seconds"]=time.perf_counter()-t; return result

def valid_entry(row): return row["Bid Price"]>0 and row["Ask Price"]>0 and row["Bid Price"]<=row["Ask Price"]
def valid_exit(short,long): return short["Ask Price"]>0 and long["Bid Price"]>=0 and short["Bid Price"]<=short["Ask Price"] and long["Bid Price"]<=long["Ask Price"]

def select_expiration(day_quotes):
    x=day_quotes[day_quotes.DTE.between(20,45)].copy()
    if x.empty:return None
    return sorted(x["Expiry Date"].unique(),key=lambda d:(abs((pd.Timestamp(d)-pd.Timestamp(day_quotes["Trade Date"].iloc[0])).days-30),(pd.Timestamp(d)-pd.Timestamp(day_quotes["Trade Date"].iloc[0])).days))[0]

def select_pair(day_quotes, expiration, close, atr, safe_strike_atr=DEFAULT_SAFE_STRIKE_ATR):
    x=day_quotes[(day_quotes["Expiry Date"]==expiration)&(day_quotes["Call/Put"]=="p")].copy(); x=x[x.Strike<close]; target=close-safe_strike_atr*atr
    for _,short in x.assign(distance=(x.Strike-target).abs()).sort_values("distance").iterrows():
        long=x[x.Strike==short.Strike-5]
        if not long.empty:return short,long.iloc[0]
    return None,None

def credit_bucket(ratio): return "15-18%" if ratio<.18 else "18-20%" if ratio<.20 else ">=20%"
def buffer_bucket(distance): return "1.5-2.0 ATR" if distance<=2 else "2.0-2.5 ATR" if distance<=2.5 else ">2.5 ATR"

def track_trade(entry, quotes, short, long, initial, max_days=20, quote_index=None):
    if quote_index is None:
        q=quotes[(quotes["Expiry Date"]==entry["expiration"])&quotes["Trade Date"].ge(entry["date"])&quotes.Strike.isin([entry["short_strike"],entry["long_strike"]])]
    else:
        q=pd.concat([quote_index.get((entry["expiration"],entry["short_strike"],"p"),pd.DataFrame()),quote_index.get((entry["expiration"],entry["long_strike"],"p"),pd.DataFrame())],ignore_index=True)
        q=q[q["Trade Date"]>=entry["date"]]
    q=q.pivot_table(index="Trade Date",columns="Strike",values=["Bid Price","Ask Price"],aggfunc="first").sort_index().head(max_days)
    events={"profit50":None,"profit70":None,"stop":None}; valid_days=[]; invalid=0
    for day,row in q.iterrows():
        try:
            sb,sa,lb,la=row[("Bid Price",entry["short_strike"])],row[("Ask Price",entry["short_strike"])],row[("Bid Price",entry["long_strike"])],row[("Ask Price",entry["long_strike"])]
        except KeyError: invalid+=1; continue
        if not valid_exit({"Bid Price":sb,"Ask Price":sa},{"Bid Price":lb,"Ask Price":la}): invalid+=1; continue
        mid=(sb+sa)/2-(lb+la)/2; cons=sa-lb; valid_days.append((day,mid,cons))
        if events["profit50"] is None and cons<=initial*.5: events["profit50"]=day
        if events["profit70"] is None and cons<=initial*.3: events["profit70"]=day
        if events["stop"] is None and cons>=initial*2: events["stop"]=day
    if not valid_days:return {"events":events,"exit_reason":"INSUFFICIENT_QUOTES","days_held":None,"exit_cost":None,"realized_pnl":None,"invalid_days":invalid}
    first=[(k,v) for k,v in events.items() if v is not None]; first.sort(key=lambda x:x[1]); reason="PROFIT50" if events["profit50"] and (not events["stop"] or events["profit50"]<=events["stop"]) else "STOP" if events["stop"] else "TIME_EXIT"
    exit_day=events["profit50"] if reason=="PROFIT50" else events["stop"] if reason=="STOP" else valid_days[-1][0]; exit_cost=next(x[2] for x in valid_days if x[0]==exit_day)
    return {"events":events,"exit_reason":reason,"days_held":len([x for x in valid_days if x[0]<=exit_day]),"exit_cost":exit_cost,"realized_pnl":(initial-exit_cost)*100,"invalid_days":invalid}

def run_backtest(stock, benchmark, config=None, option_root="NVDA", start=START, end=END, progress_callback=None, backend="canonical", duckdb_path="data/duckdb/pcs.duckdb", safe_strike_atr=DEFAULT_SAFE_STRIKE_ATR):
    if backend != "canonical":
        raise DataAccessError("LEGACY_OPTION_BACKEND_DISABLED: use PCSDataAccess canonical route")
    from .ticker_readiness import assert_research_ready
    assert_research_ready(Path(option_root).name)
    start,end=pd.Timestamp(start),pd.Timestamp(end); stock=stock.copy(); t=time.perf_counter(); trend_lookup=precompute_trend_lookup(stock,benchmark,config,start,end); trend_seconds=time.perf_counter()-t; by_date={d:v["row"] for d,v in trend_lookup.items()}; trades=[]; exclusions=Counter(); option_rows_loaded=0; files_opened=set()
    sim_start=time.perf_counter(); option_loading_seconds=0.0; duck_con=None
    if backend=="duckdb":
        duck_con=connect_duckdb(duckdb_path)
    for processed_day,(day,row) in enumerate(by_date.items(), 1):
        if day<start or day>end:continue
        load_start=time.perf_counter(); day_quotes,quality=load_quotes_canonical(Path(option_root).name,day,day); option_loading_seconds+=time.perf_counter()-load_start; option_rows_loaded+=quality.get("rows_returned",0); exclusions.update({k:v for k,v in quality.items() if k not in {"timing","rows_returned"}}); exp=select_expiration(day_quotes); stockrow=stock[stock.date==day]
        if progress_callback and (processed_day == 1 or processed_day % 10 == 0): progress_callback(day, processed_day, len(trades), files_opened, sim_start)
        if exp is None or stockrow.empty: exclusions["missing_expiration_or_stock"]=exclusions["missing_expiration_or_stock"]+1; continue
        close=float(stockrow.close.iloc[0]); atr=float(trend_lookup[day]["atr14"])
        if not atr or np.isnan(atr): exclusions["missing_atr"]=exclusions["missing_atr"]+1; continue
        short,long=select_pair(day_quotes,exp,close,atr,safe_strike_atr=safe_strike_atr)
        if short is None: exclusions["missing_spread_leg"]=exclusions["missing_spread_leg"]+1; continue
        if not valid_entry(short) or not valid_entry(long): exclusions["invalid_entry_quote"]=exclusions["invalid_entry_quote"]+1; continue
        mid=(short.mid-long.mid); cons=short["Bid Price"]-long["Ask Price"]
        if cons<=0: exclusions["nonpositive_conservative_credit"]=exclusions["nonpositive_conservative_credit"]+1; continue
        ratio=cons/5
        if ratio<.15: exclusions["credit_width_below_15pct"]=exclusions["credit_width_below_15pct"]+1; continue
        entry={"date":day,"expiration":exp,"short_strike":short.Strike,"long_strike":long.Strike}
        tracking_end=min(pd.Timestamp(exp),day+pd.Timedelta(days=45)); load_start=time.perf_counter(); spread_quotes,spread_meta=load_spread_quotes_canonical(Path(option_root).name,day,tracking_end,exp,[short.Strike,long.Strike]); option_loading_seconds+=time.perf_counter()-load_start; option_rows_loaded+=spread_meta.get("rows_returned",0); path=track_trade(entry,spread_quotes,short,long,cons); trades.append({**row,"expiration":exp,"short_strike":short.Strike,"long_strike":long.Strike,"target_short":close-safe_strike_atr*atr,"target_buffer_atr":safe_strike_atr,"short_buffer_atr":(close-short.Strike)/atr,"short_buffer_pct":(close-short.Strike)/close,"mid_credit":mid,"initial_credit":cons,"credit_width_ratio":ratio,"credit_bucket":credit_bucket(ratio),"buffer_bucket":buffer_bucket((close-short.Strike)/atr),"short_delta":short.get("Delta"),**path})
    if duck_con is not None: duck_con.close()
    return {"trades":trades,"exclusions":dict(exclusions),"quality":{"candidate_days":len(by_date),"option_rows_loaded":option_rows_loaded,"quarter_files_opened":len(files_opened),"timing":{"trend_precompute_seconds":trend_seconds,"option_loading_seconds":option_loading_seconds,"pcs_simulation_seconds":time.perf_counter()-sim_start-option_loading_seconds}}}

def summarize(trades,key):
    result={}
    for group in sorted({t.get(key) for t in trades}):
        x=[t for t in trades if t.get(key)==group]; p50=[t for t in x if t["events"]["profit50"] and (not t["events"]["stop"] or t["events"]["profit50"]<=t["events"]["stop"])]
        p70=[t for t in x if t["events"]["profit70"] and (not t["events"]["stop"] or t["events"]["profit70"]<=t["events"]["stop"])]
        stops=[t for t in x if t["events"]["stop"] and (not t["events"]["profit50"] or t["events"]["stop"]<t["events"]["profit50"])]
        pnl=[t["realized_pnl"] for t in x if t["realized_pnl"] is not None]
        result[group]={"sample_count":len(x),"profit50_before_stop_rate":len(p50)/len(x) if x else None,"profit70_before_stop_rate":len(p70)/len(x) if x else None,"stop_before_profit50_rate":len(stops)/len(x) if x else None,"average_realized_pnl":float(np.mean(pnl)) if pnl else None,"median_realized_pnl":float(np.median(pnl)) if pnl else None,"median_days_to_profit50":float(np.median([t["days_held"] for t in p50])) if p50 else None,"median_days_to_stop":float(np.median([t["days_held"] for t in stops])) if stops else None}
    return result
