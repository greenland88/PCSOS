"""Deterministic Entry Contract v2 producers.

These functions contain only the definitions explicitly approved for v2. They
require an entry-time chain; callers are responsible for passing a PIT slice.
"""
from __future__ import annotations

import pandas as pd

ENTRY_CONTRACT_V2 = "ENTRY_CONTRACT_V2"
EXPECTED_MOVE_PRODUCER_VERSION = "pcs.features.expected_move.calculate_expected_move:v1"
PRICE_CONFIRMATION_PRODUCER_VERSION = "UNRESOLVED_OWNER_MAPPING"
NEARBY_STRIKES_DEFINITION_VERSION = "2-below-2-above-distinct:v1"
LATER_EXPIRATIONS_DEFINITION_VERSION = "distinct-strictly-later:v1"


def normalize_price_confirmation(confirmation_score: int | float) -> float:
    """Normalize the existing four-boolean research score to the v2 scale."""
    score = float(confirmation_score)
    if score < 0 or score > 4 or score != int(score):
        raise ValueError("confirmation_score must be an integer in [0, 4]")
    return score / 4.0 * 100.0


def _values(chain: pd.DataFrame, expiration, option_type) -> pd.DataFrame:
    """Normalize the minimal chain keys without applying liquidity filters."""
    names = {c.lower().replace(" ", "_"): c for c in chain.columns}
    exp = names.get("expiration") or names.get("expiration_date") or names.get("expiry_date")
    typ = names.get("option_type") or names.get("call_put")
    strike = names.get("strike")
    if not all((exp, typ, strike)):
        raise ValueError("chain requires expiration, option type, and strike")
    x = chain.loc[
        pd.to_datetime(chain[exp]).dt.normalize().eq(pd.Timestamp(expiration).normalize())
        & chain[typ].astype(str).str.lower().eq(str(option_type).lower())
    ]
    return x.assign(_strike=pd.to_numeric(x[strike], errors="raise")).drop_duplicates("_strike")


def nearby_strikes(chain: pd.DataFrame, expiration, option_type, short_strike: float) -> int:
    strikes = sorted(_values(chain, expiration, option_type)["_strike"].unique())
    short = float(short_strike)
    below = [x for x in strikes if x < short][-2:]
    above = [x for x in strikes if x > short][:2]
    return len(below) + len(above)


def later_expirations(chain: pd.DataFrame, expiration, option_type) -> int:
    names = {c.lower().replace(" ", "_"): c for c in chain.columns}
    exp = names.get("expiration") or names.get("expiration_date") or names.get("expiry_date")
    typ = names.get("option_type") or names.get("call_put")
    if not exp or not typ:
        raise ValueError("chain requires expiration and option type")
    dates = pd.to_datetime(chain.loc[chain[typ].astype(str).str.lower().eq(str(option_type).lower()), exp]).dt.normalize().dropna().unique()
    cutoff = pd.Timestamp(expiration).normalize()
    return int(sum(pd.Timestamp(x) > cutoff for x in dates))
