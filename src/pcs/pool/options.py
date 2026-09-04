"""Point-in-time options shortlist adapter over caller-supplied chain data."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any, Mapping
from types import MappingProxyType

import pandas as pd

from pcs.engine.decision_engine import load_rules
from pcs.entry.contract_v2 import later_expirations, nearby_strikes
from .iv import IVFeatures, build_iv_features


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
    open_interest: int | None
    volume: int | None
    bid_ask_spread: float
    quote_as_of: str | None
    # Keep the original terminal field position stable for positional callers.
    reason_codes: tuple[str, ...] = ()
    short_put_bid_iv: float | None = None
    short_put_ask_iv: float | None = None
    long_put_bid_iv: float | None = None
    long_put_ask_iv: float | None = None
    short_put_iv: float | None = None
    long_put_iv: float | None = None
    atm_iv_30d: float | None = None
    realized_vol_20d: float | None = None
    realized_vol_60d: float | None = None
    iv_minus_rv: float | None = None
    iv_to_rv_ratio: float | None = None
    iv_rank_252: float | None = None
    iv_percentile_252: float | None = None
    put_skew: float | None = None
    term_structure: float | None = None
    event_iv_distortion: float | None = None
    iv_gate_status: str = "NOT_EVALUATED"
    iv_reason_codes: tuple[str, ...] = ()
    options_generation_id: str | None = None
    iv_calculation_version: str = "pcs.pool.iv.v1"
    iv_data_as_of: str | None = None
    dte: int = 0
    short_bid_ask_pct: float | None = None
    nearby_strike_count: int = 0
    later_expiration_count: int = 0
    reference_flags: tuple[str, ...] = ()

    @property
    def short_iv(self) -> float | None:
        return self.short_put_iv

    @property
    def long_iv(self) -> float | None:
        return self.long_put_iv

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_IV_CONTEXT_KEYS = {
    "iv_gate_enabled", "iv_estimate_method", "atm_iv_30d", "atm30d_iv",
    "realized_vol_20d", "rv20", "realized_vol_60d", "rv60", "iv_rank_252",
    "iv_rank", "iv_percentile_252", "iv_percentile", "put_skew",
    "term_structure", "event_iv_distortion", "iv_history_252", "close_history",
    "underlying_history", "data_timestamp", "iv_data_timestamp",
    "options_data_timestamp", "options_generation_id", "generation_id",
}


_REQUIRED_OPTION_RULES = {
    "hard_dte_min": ("entry", "hard_dte_min"),
    "hard_dte_max": ("entry", "hard_dte_max"),
    "preferred_dte_min": ("entry", "preferred_dte_min"),
    "preferred_dte_max": ("entry", "preferred_dte_max"),
    "safe_strike_atr": ("entry", "safe_strike_atr"),
    "min_credit_width_ratio": ("entry", "min_credit_width_ratio"),
    "min_option_volume": ("liquidity", "min_option_volume"),
    "min_open_interest": ("liquidity", "min_open_interest"),
    "max_bid_ask_pct": ("liquidity", "max_bid_ask_pct"),
    "min_nearby_strikes": ("liquidity", "min_nearby_strikes"),
    "min_later_expirations": ("liquidity", "min_later_expirations"),
    "long_leg_min_option_volume": ("liquidity", "long_leg_min_option_volume"),
    "long_leg_min_open_interest": ("liquidity", "long_leg_min_open_interest"),
}


def normalize_pool_option_rules(rules: Mapping[str, Any]) -> Mapping[str, Any]:
    """Flatten the canonical PCS rules for the pool shortlist."""
    missing = []
    values = {}
    for name, (section, key) in _REQUIRED_OPTION_RULES.items():
        if section in rules and isinstance(rules[section], Mapping) and key in rules[section]:
            values[name] = rules[section][key]
        elif name in rules:
            values[name] = rules[name]
        else:
            missing.append(name)
    if missing:
        raise ValueError("POOL_OPTION_RULES_INCOMPLETE:" + ",".join(missing))
    values.update({
        "dte_min": values["hard_dte_min"],
        "dte_max": values["hard_dte_max"],
    })
    return MappingProxyType(values)


def load_pool_option_rules(path: str | Path = "config/pcs_rules.yaml") -> Mapping[str, Any]:
    return normalize_pool_option_rules(load_rules(path))


def _value(row: Any, name: str) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


def _present(value: Any) -> bool:
    if value is None:
        return False
    try:
        missing = pd.isna(value)
        return not bool(missing) if not hasattr(missing, "__len__") else True
    except (TypeError, ValueError):
        return True


def _generation(row: Any) -> str | None:
    for name in ("options_generation_id", "generation_id"):
        value = _value(row, name)
        if _present(value):
            return str(value).strip()
    return None


def _candidate_iv_context(rules: Mapping[str, Any], iv_context: Mapping[str, Any] | None,
                         short: Any, long: Any, generation_id: Any) -> dict[str, Any]:
    context = dict(rules)
    if iv_context is not None:
        context["iv_context"] = dict(iv_context)
    short_generation, long_generation = _generation(short), _generation(long)
    ordered_generations = []
    for value in (generation_id, short_generation, long_generation):
        if value is not None and str(value).strip() and str(value).strip() not in ordered_generations:
            ordered_generations.append(str(value).strip())
    if ordered_generations:
        context["options_generation_id"] = ordered_generations[0]
    if len(ordered_generations) > 1:
        context["generation_id_mismatch"] = True
    return context


def _iv_is_requested(rules: Mapping[str, Any], iv_context: Mapping[str, Any] | None,
                     short: Any, long: Any, generation_id: Any) -> bool:
    if iv_context is not None or any(key in rules for key in _IV_CONTEXT_KEYS):
        return True
    if generation_id is not None:
        return True
    return any(_present(_value(row, name)) for row in (short, long)
               for name in ("bid_iv", "ask_iv"))


def _candidate_from_iv(symbol: str, entry: pd.Timestamp, expiry: pd.Timestamp,
                       short: Any, long: Any, *, width: float, distance: float,
                       bid_credit: float, mid_credit: float, spread: float,
                       dte: int, short_bid_ask_pct: float,
                       nearby_count: int, later_count: int,
                       reference_flags: tuple[str, ...], iv: IVFeatures) -> SpreadCandidate:
    def optional_int(value):
        try:
            return int(value) if pd.notna(value) else None
        except (TypeError, ValueError, OverflowError):
            return None

    return SpreadCandidate(
        str(symbol).upper(), str(entry.date()), str(expiry.date()), float(short.strike),
        float(long.strike), width, distance, bid_credit, mid_credit, bid_credit / width,
        float(short.delta) if "delta" in short and pd.notna(short.delta) else None,
        optional_int(short.open_interest), optional_int(short.volume), spread,
        str(short.quote_as_of) if "quote_as_of" in short and pd.notna(short.quote_as_of) else None,
        reason_codes=tuple(dict.fromkeys(("PIT_CHAIN_SUPPLIED", *iv.reason_codes))),
        short_put_bid_iv=iv.short_put_bid_iv, short_put_ask_iv=iv.short_put_ask_iv,
        long_put_bid_iv=iv.long_put_bid_iv, long_put_ask_iv=iv.long_put_ask_iv,
        short_put_iv=iv.short_put_iv, long_put_iv=iv.long_put_iv,
        atm_iv_30d=iv.atm_iv_30d, realized_vol_20d=iv.realized_vol_20d,
        realized_vol_60d=iv.realized_vol_60d, iv_minus_rv=iv.iv_minus_rv,
        iv_to_rv_ratio=iv.iv_to_rv_ratio, iv_rank_252=iv.iv_rank_252,
        iv_percentile_252=iv.iv_percentile_252, put_skew=iv.put_skew,
        term_structure=iv.term_structure, event_iv_distortion=iv.event_iv_distortion,
        iv_gate_status=iv.iv_gate_status, iv_reason_codes=iv.reason_codes,
        options_generation_id=iv.options_generation_id,
        iv_data_as_of=iv.iv_data_as_of,
        iv_calculation_version=iv.calculation_version,
        dte=dte, short_bid_ask_pct=short_bid_ask_pct,
        nearby_strike_count=nearby_count, later_expiration_count=later_count,
        reference_flags=reference_flags,
    )


def discover_spreads(symbol: str, entry_date, underlying_price: float, atr: float,
                     chain: pd.DataFrame, *, rules: Mapping[str, Any], limit: int | None = None,
                     iv_context: Mapping[str, Any] | None = None) -> tuple[SpreadCandidate, ...]:
    """Discover every structurally valid positive-credit put spread.

    Configured PCS thresholds are reference evidence for later contract
    decision, not Stage-B exclusion gates.
    """
    if chain is None or chain.empty or (limit is not None and limit < 1):
        return ()
    try:
        if not math.isfinite(float(atr)) or float(atr) <= 0 or not math.isfinite(float(underlying_price)):
            return ()
    except (TypeError, ValueError):
        return ()
    chain = chain.copy()
    aliases = {"expiration_date": "expiration", "call_put": "option_type", "trade_date": "quote_as_of"}
    chain.rename(columns={source: target for source, target in aliases.items()
                          if source in chain.columns and target not in chain.columns}, inplace=True)
    required = {"expiration", "strike", "option_type", "bid", "ask", "volume", "open_interest"}
    if not required.issubset(chain.columns):
        return ()
    frame = chain.copy()
    frame["expiration"] = pd.to_datetime(frame["expiration"], errors="coerce").dt.normalize()
    frame["strike"] = pd.to_numeric(frame["strike"], errors="coerce")
    frame["option_type"] = frame["option_type"].astype(str).str.lower().replace({"put": "p"})
    frame = frame[frame.option_type.isin({"p"}) & frame.expiration.notna() &
                  frame.strike.notna() & frame.strike.map(math.isfinite)].copy()
    if frame.empty:
        return ()
    keys = ["expiration", "strike", "option_type"]
    if frame.duplicated(keys).any():
        raise ValueError("DUPLICATE_OPTION_CONTRACT_KEY")
    entry = pd.Timestamp(entry_date).normalize()
    required = ("dte_min", "dte_max", "safe_strike_atr", "min_credit_width_ratio",
                "min_option_volume", "min_open_interest", "max_bid_ask_pct",
                "min_nearby_strikes", "min_later_expirations",
                "long_leg_min_option_volume", "long_leg_min_open_interest")
    if any(name not in rules for name in required):
        raise ValueError("POOL_OPTION_RULES_INCOMPLETE")
    hard_dte_min, hard_dte_max = int(rules["dte_min"]), int(rules["dte_max"])
    preferred_dte_min = int(rules.get("preferred_dte_min", hard_dte_min))
    preferred_dte_max = int(rules.get("preferred_dte_max", hard_dte_max))
    min_ratio = float(rules["min_credit_width_ratio"])
    min_volume, min_oi = int(rules["min_option_volume"]), int(rules["min_open_interest"])
    output: list[SpreadCandidate] = []
    generation_id = rules.get("options_generation_id")
    if generation_id is None:
        generation_id = chain.attrs.get("options_generation_id", chain.attrs.get("generation_id"))

    for expiry, rows in frame.groupby("expiration", sort=True):
        dte = (expiry - entry).days
        if dte <= 0:
            continue
        rows = rows.sort_values("strike", ascending=False)
        for _, short in rows.iterrows():
            distance = (float(underlying_price) - float(short.strike)) / float(atr)
            if any(pd.isna(float(short[name])) for name in ("bid", "ask")):
                continue
            if float(short.bid) <= 0 or float(short.ask) < float(short.bid):
                continue
            nearby_count = nearby_strikes(frame, expiry, "p", float(short.strike))
            later_count = later_expirations(frame, expiry, "p")
            for _, long in rows[rows.strike < short.strike].iterrows():
                width = float(short.strike) - float(long.strike)
                if width <= 0:
                    continue
                if any(pd.isna(float(long[name])) for name in ("bid", "ask")):
                    continue
                if float(long.bid) < 0 or float(long.ask) < 0 or float(long.ask) < float(long.bid):
                    continue
                bid_credit = float(short.bid) - float(long.ask)
                mid_credit = ((float(short.bid) + float(short.ask)) / 2 -
                              (float(long.bid) + float(long.ask)) / 2)
                if bid_credit <= 0:
                    continue
                spread = float(short.ask) - float(short.bid)
                short_bid_ask_pct = spread / float(short.bid)
                flags = []
                flags.append("DTE_PREFERRED" if preferred_dte_min <= dte <= preferred_dte_max
                             else "DTE_REFERENCE_RANGE" if hard_dte_min <= dte <= hard_dte_max
                             else "DTE_OUTSIDE_REFERENCE_RANGE")
                flags.append("ATR_REFERENCE_MET" if distance >= float(rules["safe_strike_atr"])
                             else "ATR_BELOW_REFERENCE")
                flags.append("CREDIT_EFFICIENCY_REFERENCE_MET" if bid_credit / width >= min_ratio
                             else "CREDIT_EFFICIENCY_BELOW_REFERENCE")
                flags.append("VOLUME_UNAVAILABLE" if pd.isna(short.volume) else
                             "VOLUME_REFERENCE_MET" if float(short.volume) >= min_volume else "VOLUME_BELOW_REFERENCE")
                flags.append("OI_UNAVAILABLE" if pd.isna(short.open_interest) else
                             "OI_REFERENCE_MET" if float(short.open_interest) >= min_oi else "OI_BELOW_REFERENCE")
                flags.append("BID_ASK_REFERENCE_MET" if short_bid_ask_pct <= float(rules["max_bid_ask_pct"])
                             else "BID_ASK_ABOVE_REFERENCE")
                flags.append("NEARBY_STRIKES_REFERENCE_MET" if nearby_count >= int(rules["min_nearby_strikes"])
                             else "NEARBY_STRIKES_BELOW_REFERENCE")
                flags.append("LATER_EXPIRATIONS_REFERENCE_MET" if later_count >= int(rules["min_later_expirations"])
                             else "LATER_EXPIRATIONS_BELOW_REFERENCE")
                iv_requested = _iv_is_requested(rules, iv_context, short, long, generation_id)
                iv = (build_iv_features(
                    short, long, entry_date=entry,
                    context=_candidate_iv_context(rules, iv_context, short, long, generation_id))
                      if iv_requested else IVFeatures())
                if iv_requested and "OPTIONS_GENERATION_ID_MISMATCH" in iv.reason_codes:
                    continue
                output.append(_candidate_from_iv(
                    symbol, entry, expiry, short, long, width=width, distance=distance,
                    bid_credit=bid_credit, mid_credit=mid_credit, spread=spread,
                    dte=dte, short_bid_ask_pct=short_bid_ask_pct,
                    nearby_count=nearby_count, later_count=later_count,
                    reference_flags=tuple(flags), iv=iv))
    ordered = tuple(sorted(output, key=lambda x: (x.expiration, x.short_strike, x.long_strike)))
    return ordered if limit is None else ordered[:limit]


def shortlist_spreads(symbol: str, entry_date, underlying_price: float, atr: float,
                      chain: pd.DataFrame, *, rules: Mapping[str, Any], limit: int = 3,
                      iv_context: Mapping[str, Any] | None = None) -> tuple[SpreadCandidate, ...]:
    """Compatibility wrapper over the canonical discovery authority."""
    return discover_spreads(symbol, entry_date, underlying_price, atr, chain,
                            rules=rules, limit=limit, iv_context=iv_context)


__all__ = ["SpreadCandidate", "discover_spreads", "shortlist_spreads",
           "load_pool_option_rules", "normalize_pool_option_rules"]
