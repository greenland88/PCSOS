"""Fail-closed correctness boundary for canonical OHLCV inputs."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import pandas as pd

@dataclass(frozen=True)
class PriceInputSummary:
    dataset: str; symbol: str; read_mode: str; generation_id: str; dataset_fingerprint: str
    input_row_count: int; unique_key_count: int; duplicate_key_count: int
    min_date: str; max_date: str; schema_version: str; price_basis: str
    corporate_action_version: str; source_component_count: int
    warmup_start: str | None = None; signal_start: str | None = None; signal_end: str | None = None
    def to_dict(self): return asdict(self)

class DataCorrectnessError(RuntimeError):
    def __init__(self, reason_code: str): self.reason_code=reason_code; super().__init__(reason_code)

def _missing(x: Any) -> bool:
    return x is None or (isinstance(x, float) and pd.isna(x)) or str(x).strip().lower() in {"", "nan", "none", "null"}

def validate_price_input(dataframe: pd.DataFrame, verified_handle: Any, requested_symbol: str,
                        requested_start: object | None = None, requested_end: object | None = None,
                        *, as_of: object | None = None, required_warmup_rows: int = 0) -> PriceInputSummary:
    """Validate all identity, schema, coverage and OHLC invariants before features."""
    if verified_handle is None: raise DataCorrectnessError("UNPINNED_INPUT")
    mode=str(getattr(verified_handle,"read_mode","PINNED_GENERATION")); symbol=str(requested_symbol).upper()
    if mode not in {"PINNED_GENERATION","COMPOSITE_VERIFIED"}: raise DataCorrectnessError("UNPINNED_INPUT")
    gid=getattr(verified_handle,"generation_id",None); fp=getattr(verified_handle,"dataset_fingerprint",None)
    if _missing(gid): raise DataCorrectnessError("GENERATION_ID_MISMATCH")
    if _missing(fp): raise DataCorrectnessError("DATASET_FINGERPRINT_MISMATCH")
    required={"symbol","date","open","high","low","close","volume"}
    if not required.issubset(dataframe.columns): raise DataCorrectnessError("SCHEMA_MISMATCH")
    out=dataframe.copy(); out["date"]=pd.to_datetime(out["date"],errors="coerce")
    if out["symbol"].astype(str).str.upper().ne(symbol).any(): raise DataCorrectnessError("SYMBOL_MISMATCH")
    keys=out[["symbol","date"]]; dup=int(keys.duplicated().sum())
    if dup: raise DataCorrectnessError("DUPLICATE_CANONICAL_PRICE_KEY")
    if not out.date.is_monotonic_increasing: raise DataCorrectnessError("NON_MONOTONIC_TRADING_DATES")
    if out[["open","high","low","close","volume"]].isna().any().any(): raise DataCorrectnessError("INVALID_OHLC_RELATIONSHIP")
    if (out.volume<0).any() or (out.high<out.low).any() or (out.high<out[["open","close"]].max(axis=1)).any() or (out.low>out[["open","close"]].min(axis=1)).any(): raise DataCorrectnessError("INVALID_OHLC_RELATIONSHIP")
    if requested_start is not None and out.date.min()>pd.Timestamp(requested_start): raise DataCorrectnessError("INSUFFICIENT_DATE_COVERAGE")
    if requested_end is not None and out.date.max()<pd.Timestamp(requested_end): raise DataCorrectnessError("INSUFFICIENT_DATE_COVERAGE")
    if len(out)<required_warmup_rows: raise DataCorrectnessError("INSUFFICIENT_FEATURE_WARMUP")
    if as_of is not None and out.date.max()>pd.Timestamp(as_of): raise DataCorrectnessError("FEATURE_ASOF_VIOLATION")
    return PriceInputSummary("daily",symbol,mode,str(gid),str(fp),len(out),len(keys),dup,str(out.date.min().date()),str(out.date.max().date()),str(getattr(verified_handle,"schema_version","")),str(getattr(verified_handle,"price_basis","")),str(getattr(verified_handle,"corporate_action_version","")),len(getattr(verified_handle,"partitions",()) or ()))
