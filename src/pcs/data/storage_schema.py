"""Canonical schemas for derived local storage."""

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
