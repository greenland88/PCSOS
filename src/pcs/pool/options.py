"""Point-in-time options shortlist adapter over caller-supplied chain data."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping
import pandas as pd


@dataclass(frozen=True)
class SpreadCandidate:
    symbol: str
    entry_date: str
    expiration: str
    short_strike: float
    long_strike: float
    width: float
    short_distance_atr: float
    bid_credit: float
    mid_credit: float
    credit_efficiency: float
    short_delta_diagnostic: float | None
    open_interest: int
    volume: int
    bid_ask_spread: float
    quote_as_of: str | None
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def shortlist_spreads(symbol: str, entry_date, underlying_price: float, atr: float,
                      chain: pd.DataFrame, *, rules: Mapping[str, Any], limit: int = 3) -> tuple[SpreadCandidate, ...]:
    """Select exact put spreads from an already validated PIT chain.

    The adapter does not read data and does not repair duplicate contract keys;
    duplicate identities are rejected explicitly.
    """
    if chain is None or chain.empty or atr <= 0 or limit < 1:
        return ()
    required = {"expiration", "strike", "option_type", "bid", "ask", "volume", "open_interest"}
    if not required.issubset(chain.columns):
        return ()
    frame = chain.copy()
    frame["expiration"] = pd.to_datetime(frame["expiration"], errors="coerce").dt.normalize()
    frame["option_type"] = frame["option_type"].astype(str).str.lower().replace({"put": "p"})
    frame = frame[frame.option_type.isin({"p"})].copy()
    if frame.empty:
        return ()
    keys = ["expiration", "strike", "option_type"]
    if frame.duplicated(keys).any():
        raise ValueError("DUPLICATE_OPTION_CONTRACT_KEY")
    entry = pd.Timestamp(entry_date).normalize()
    dte_min, dte_max = int(rules.get("dte_min", 30)), int(rules.get("dte_max", 45))
    min_ratio = float(rules.get("min_credit_width_ratio", .10))
    min_volume = int(rules.get("min_option_volume", 0))
    min_oi = int(rules.get("min_open_interest", 0))
    output: list[SpreadCandidate] = []
    for expiry, rows in frame.groupby("expiration", sort=True):
        dte = (expiry - entry).days
        if not dte_min <= dte <= dte_max:
            continue
        rows = rows.sort_values("strike", ascending=False)
        for _, short in rows.iterrows():
            distance = (float(underlying_price) - float(short.strike)) / float(atr)
            if distance < float(rules.get("safe_strike_atr", 2.3)):
                continue
            if float(short.volume) < min_volume or float(short.open_interest) < min_oi:
                continue
            for _, long in rows[rows.strike < short.strike].iterrows():
                width = float(short.strike) - float(long.strike)
                if width <= 0:
                    continue
                bid_credit = float(short.bid) - float(long.ask)
                mid_credit = (float(short.bid) + float(short.ask)) / 2 - (float(long.bid) + float(long.ask)) / 2
                if bid_credit <= 0 or bid_credit / width < min_ratio:
                    continue
                spread = float(short.ask) - float(short.bid)
                output.append(SpreadCandidate(str(symbol).upper(), str(entry.date()), str(expiry.date()),
                    float(short.strike), float(long.strike), width, distance, bid_credit, mid_credit,
                    bid_credit / width, float(short.delta) if "delta" in short and pd.notna(short.delta) else None,
                    int(short.open_interest), int(short.volume), spread,
                    str(short.quote_as_of) if "quote_as_of" in short and pd.notna(short.quote_as_of) else None,
                    ("PIT_CHAIN_SUPPLIED",)))
    return tuple(sorted(output, key=lambda x: (-x.credit_efficiency, x.expiration, x.short_strike, x.long_strike))[:limit])
