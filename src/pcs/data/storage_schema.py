"""Canonical schemas for derived local storage."""

import numpy as np
import pandas as pd

OPTIONS_SCHEMA_VERSION = 1
DAILY_SCHEMA_VERSION = 1
OPTION_FIELDS = ["symbol", "trade_date", "expiration_date", "strike", "call_put", "last", "bid", "ask", "bid_iv", "ask_iv", "open_interest", "volume", "delta", "gamma", "vega", "theta", "rho"]
DAILY_FIELDS = ["symbol", "date", "open", "high", "low", "close", "volume"]
OPTIONS_REQUIRED_FIELDS = ["symbol", "trade_date", "expiration_date", "strike", "call_put"]
OPTIONS_OPTIONAL_FIELDS = [field for field in OPTION_FIELDS if field not in OPTIONS_REQUIRED_FIELDS] + ["underlying_price", "bid_size", "ask_size", "quote_time"]
DAILY_REQUIRED_FIELDS = list(DAILY_FIELDS)
DAILY_OPTIONAL_FIELDS = []
OPTIONS_SCHEMA_VERSIONS = {
    1: list(OPTION_FIELDS),
    2: list(OPTION_FIELDS) + ["underlying_price", "bid_size", "ask_size", "quote_time"],
}
DAILY_SCHEMA_VERSIONS = {1: list(DAILY_FIELDS)}


def option_schema_map():
    return {"Trade Date": "trade_date", "Expiry Date": "expiration_date", "Strike": "strike", "Call/Put": "call_put", "Last Trade Price": "last", "Bid Price": "bid", "Ask Price": "ask", "Bid Implied Volatility": "bid_iv", "Ask Implied Volatility": "ask_iv", "Open Interest": "open_interest", "Volume": "volume", "Delta": "delta", "Gamma": "gamma", "Vega": "vega", "Theta": "theta", "Rho": "rho"}


OPTION_KEY_FIELDS = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]

OPTION_QUARANTINE_REASON_CODES = (
    "QUOTE_BID_INVALID", "QUOTE_ASK_INVALID", "QUOTE_CROSSED",
    "OPTION_IDENTITY_INVALID", "OPTION_EXPIRATION_INVALID", "OPTION_STRIKE_INVALID",
    "OPTION_DUPLICATE_IDENTITY", "OPTION_CONFLICTING_IDENTITY",
)


def audit_option_frame(frame, *, source=None, source_file=None, source_member=None,
                       source_version=None, partition=None, structural_threshold=0.20):
    """Partition raw option rows into executable and quarantined populations.

    This is the single canonical quote-quality boundary.  It never repairs a
    source row.  Every row participating in a duplicate or conflicting
    identity is quarantined, so executable reads cannot select a first row
    implicitly.
    """
    raw = frame.copy()
    for field in OPTION_FIELDS:
        if field not in raw:
            raw[field] = pd.NA
    raw = raw[OPTION_FIELDS + [c for c in raw.columns if c not in OPTION_FIELDS]].copy()
    raw["symbol"] = raw["symbol"].where(raw["symbol"].notna(), pd.NA)
    raw["symbol"] = raw["symbol"].astype("string").str.strip().str.upper()
    raw["trade_date"] = pd.to_datetime(raw["trade_date"], errors="coerce").dt.date
    raw["expiration_date"] = pd.to_datetime(raw["expiration_date"], errors="coerce").dt.date
    raw["call_put"] = raw["call_put"].astype("string").str.strip().str.lower()
    raw["strike"] = pd.to_numeric(raw["strike"], errors="coerce")
    raw["bid"] = pd.to_numeric(raw["bid"], errors="coerce")
    raw["ask"] = pd.to_numeric(raw["ask"], errors="coerce")
    reasons = pd.Series(pd.NA, index=raw.index, dtype="string")
    reasons = reasons.mask(raw.symbol.isna() | ~raw.symbol.str.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", na=False), "OPTION_IDENTITY_INVALID")
    reasons = reasons.mask(reasons.isna() & raw.trade_date.isna(), "OPTION_IDENTITY_INVALID")
    reasons = reasons.mask(reasons.isna() & raw.expiration_date.isna(), "OPTION_EXPIRATION_INVALID")
    reasons = reasons.mask(reasons.isna() & raw.expiration_date.le(raw.trade_date), "OPTION_EXPIRATION_INVALID")
    reasons = reasons.mask(reasons.isna() & ~raw.call_put.isin(["p", "c"]), "OPTION_IDENTITY_INVALID")
    reasons = reasons.mask(reasons.isna() & (raw.strike.isna() | ~np.isfinite(raw.strike) | raw.strike.le(0)), "OPTION_STRIKE_INVALID")
    reasons = reasons.mask(reasons.isna() & (raw.bid.isna() | ~np.isfinite(raw.bid) | raw.bid.lt(0)), "QUOTE_BID_INVALID")
    reasons = reasons.mask(reasons.isna() & (raw.ask.isna() | ~np.isfinite(raw.ask) | raw.ask.lt(0)), "QUOTE_ASK_INVALID")
    reasons = reasons.mask(reasons.isna() & raw.ask.lt(raw.bid), "QUOTE_CROSSED")
    eligible = reasons.isna()
    # Use native multi-column duplicate detection. Building a string key for
    # every historical quote row is materially slower and allocates a large
    # temporary object array on dense ticker histories.
    duplicate = eligible & raw[OPTION_KEY_FIELDS].duplicated(keep=False)
    if duplicate.any():
        payload = [c for c in OPTION_FIELDS if c not in OPTION_KEY_FIELDS]
        duplicate_rows = raw.loc[duplicate]
        varying = duplicate_rows.groupby(OPTION_KEY_FIELDS, sort=False, dropna=False)[payload].nunique(dropna=False).gt(1).any(axis=1)
        conflict_keys = varying[varying].index
        duplicate_index = pd.MultiIndex.from_frame(raw[OPTION_KEY_FIELDS])
        conflict_mask = duplicate_index.isin(conflict_keys)
        reasons = reasons.mask(duplicate & conflict_mask, "OPTION_CONFLICTING_IDENTITY")
        reasons = reasons.mask(duplicate & ~conflict_mask, "OPTION_DUPLICATE_IDENTITY")
    meta = {"source": source, "source_file": source_file, "source_member": source_member,
            "source_version": source_version, "partition": partition}
    quarantine = raw.loc[reasons.notna()].copy()
    for field, value in meta.items(): quarantine[field] = value
    quarantine["reason_code"] = reasons.loc[reasons.notna()].astype(str).to_numpy()
    # Keep optional vendor columns on valid rows; only the executable quality
    # decision is canonicalized.  Raw/source payload is never repaired.
    valid = raw.loc[reasons.isna()].reset_index(drop=True)
    invalid = int(len(quarantine)); total = int(len(raw))
    summary = {
        "raw_rows": total, "canonical_rows": len(valid), "quarantined_rows": invalid,
        "executable_rows": len(valid), "executable_invalid_rows": 0,
        "reason_breakdown": quarantine.reason_code.value_counts().astype(int).to_dict() if len(quarantine) else {},
        "affected_dates": sorted({str(x) for x in quarantine.trade_date.dropna().unique()}),
        "affected_partitions": sorted({str(x) for x in quarantine.partition.dropna().unique()}),
        "affected_percentage": (100.0 * invalid / total) if total else 0.0,
        "partition_status": "PARTITION_REPAIR_REQUIRED" if total and invalid / total >= structural_threshold else "VALIDATED",
    }
    return valid, quarantine.reset_index(drop=True), summary


def canonicalize_option_frame(frame):
    """Normalize and deterministically remove exact option-row duplicates.

    Duplicate contract keys with different quote payloads are unsafe to select
    implicitly and therefore fail closed. Exact duplicates retain the first
    source row, preserving deterministic source precedence and row provenance.
    """
    out = frame.copy()
    out = out.loc[:, ~out.columns.astype(str).str.match(r"^Unnamed")]
    for field in OPTION_FIELDS:
        if field not in out:
            out[field] = None
    out = out[OPTION_FIELDS].copy()
    out["symbol"] = out["symbol"].astype(str).str.upper()
    for field in ("trade_date", "expiration_date"):
        out[field] = pd.to_datetime(out[field], errors="coerce").dt.date
    out["call_put"] = out["call_put"].astype(str).str.lower()
    out["strike"] = pd.to_numeric(out["strike"], errors="coerce")
    if out[OPTION_KEY_FIELDS].isna().any().any():
        raise ValueError("options contain null identity fields")
    duplicate = out.duplicated(OPTION_KEY_FIELDS, keep=False)
    if duplicate.any():
        duplicated = out.loc[duplicate]
        payload = [field for field in OPTION_FIELDS if field not in OPTION_KEY_FIELDS]
        varying = duplicated.groupby(OPTION_KEY_FIELDS, sort=False, dropna=False)[payload].nunique(dropna=False).gt(1).any(axis=1)
        if varying.any():
            raise ValueError(f"conflicting option payloads for {int(varying.sum())} keys")
        out = out.drop_duplicates(OPTION_KEY_FIELDS, keep="first")
    return out.reset_index(drop=True)
