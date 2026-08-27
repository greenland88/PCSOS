"""Structural Stage 4A production opportunities.

This module deliberately stops before production eligibility.  It constructs
exact listed put spreads; DecisionEngine owns all admission gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from decimal import Decimal
import pandas as pd

from pcs.research.credit_stop import load_entry_chain

STRUCTURAL_OPPORTUNITY_COLUMNS = (
    "ticker", "date", "expiration", "option_type", "short_strike", "long_strike", "spread_width",
    "short_bid", "short_ask", "short_volume", "short_oi", "short_delta", "long_bid", "long_ask",
    "long_volume", "long_oi", "construction_policy",
)


@dataclass(frozen=True)
class ProductionOpportunityPolicy:
    version: str = "stage4a-production-opportunity-v1"
    spread_widths: tuple[float, ...] = (5.0, 10.0, 2.0)


def generate_structural_put_opportunities(chain: pd.DataFrame, ticker: str,
                                          decision_date: str | pd.Timestamp,
                                          policy: ProductionOpportunityPolicy | None = None) -> list[dict[str, Any]]:
    """Return exact listed spreads without applying DecisionEngine gates."""
    policy = policy or ProductionOpportunityPolicy()
    day = pd.Timestamp(decision_date).normalize()
    if chain.empty:
        return []
    puts = chain[chain["Call/Put"].astype(str).str.lower().eq("p")].copy()
    puts["Expiry Date"] = pd.to_datetime(puts["Expiry Date"]).dt.normalize()
    puts = puts[puts["Expiry Date"] > day]
    out: list[dict[str, Any]] = []
    for expiry, exp in puts.groupby("Expiry Date", sort=True):
        exp = exp.sort_values("Strike").drop_duplicates("Strike", keep="first")
        strike_rows = {Decimal(str(row["Strike"])): row for _, row in exp.iterrows()}
        for _, short in exp.iterrows():
            short_strike = float(short["Strike"])
            for width in policy.spread_widths:
                long_key = Decimal(str(short_strike)) - Decimal(str(width))
                if long_key not in strike_rows:
                    continue
                long = strike_rows[long_key]
                long_strike = float(long["Strike"])
                out.append({
                    "ticker": ticker, "date": day, "expiration": expiry,
                    "option_type": "p", "short_strike": short_strike,
                    "long_strike": long_strike, "spread_width": float(width),
                    "short_bid": short["Bid Price"], "short_ask": short["Ask Price"],
                    "short_volume": short["Volume"], "short_oi": short["Open Interest"],
                    "short_delta": short["Delta"], "long_bid": long["Bid Price"],
                    "long_ask": long["Ask Price"], "long_volume": long["Volume"],
                    "long_oi": long["Open Interest"],
                    "construction_policy": policy.version,
                })
    return out


def empty_structural_opportunities() -> pd.DataFrame:
    """Return the stable schema for a valid zero-opportunity PIT partition."""
    return pd.DataFrame(columns=STRUCTURAL_OPPORTUNITY_COLUMNS)


def generate_for_date(ticker: str, option_root: str, decision_date: str | pd.Timestamp,
                      policy: ProductionOpportunityPolicy | None = None) -> pd.DataFrame:
    chain, _ = load_entry_chain(option_root, decision_date)
    rows = generate_structural_put_opportunities(chain, ticker, decision_date, policy)
    return pd.DataFrame(rows, columns=STRUCTURAL_OPPORTUNITY_COLUMNS)
