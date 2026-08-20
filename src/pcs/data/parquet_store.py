"""Parquet derived storage; raw files remain authoritative."""
from pathlib import Path
import pandas as pd
from .storage_schema import OPTION_FIELDS, DAILY_FIELDS, option_schema_map
from .access import PCSDataAccess


def read_option_source(path, symbol):
    df = pd.read_csv(path).rename(columns=option_schema_map())
    df["symbol"] = symbol.upper()
    for col in ("trade_date", "expiration_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    for col in ("strike", "last", "bid", "ask", "bid_iv", "ask_iv", "delta", "gamma", "vega", "theta", "rho", "open_interest", "volume"):
        if col in df: df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in OPTION_FIELDS:
        if col not in df: df[col] = None
    return df[OPTION_FIELDS]


def write_option_partition(source_path, symbol, output_root, year, quarter):
    df = read_option_source(source_path, symbol)
    df = df[(pd.to_datetime(df.trade_date).dt.year == year) & (((pd.to_datetime(df.trade_date).dt.month - 1) // 3 + 1) == quarter)]
    access = PCSDataAccess(manifest_path=Path(output_root).parent.parent / "manifests" / "storage_manifest.csv", parquet_root=Path(output_root).parent)
    path = access.write_partition(df, "options", symbol, f"year={year}/quarter={quarter}", source_version=str(source_path), update_manifest=False, filename=f"{symbol.upper()}_{year}_q{quarter}.parquet")
    return path, len(df)


def read_daily_source(path, symbol):
    df = pd.read_csv(path).rename(columns={"日期":"date", "开盘价":"open", "最高价":"high", "最低价":"low", "收盘价":"close", "成交量":"volume"})
    df["symbol"] = symbol.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    for col in DAILY_FIELDS:
        if col not in df: df[col] = None
    return df[DAILY_FIELDS]


def write_daily_partition(source_path, symbol, output_root):
    df = read_daily_source(source_path, symbol); paths=[]
    for year, group in df.groupby(pd.to_datetime(df.date).dt.year):
        target=Path(output_root)/f"symbol={symbol.upper()}"/f"year={year}"; target.mkdir(parents=True,exist_ok=True); path=target/f"{symbol.upper()}_{year}.parquet"; group.to_parquet(path,index=False); paths.append((path,len(group)))
    return paths
