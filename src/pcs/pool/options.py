"""Point-in-time options shortlist adapter over caller-supplied chain data."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import pandas as pd

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
    open_interest: int
    volume: int
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
                       iv: IVFeatures) -> SpreadCandidate:
    return SpreadCandidate(
        str(symbol).upper(), str(entry.date()), str(expiry.date()), float(short.strike),
        float(long.strike), width, distance, bid_credit, mid_credit, bid_credit / width,
        float(short.delta) if "delta" in short and pd.notna(short.delta) else None,
        int(short.open_interest), int(short.volume), spread,
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
    )


def shortlist_spreads(symbol: str, entry_date, underlying_price: float, atr: float,
                      chain: pd.DataFrame, *, rules: Mapping[str, Any], limit: int = 3,
                      iv_context: Mapping[str, Any] | None = None) -> tuple[SpreadCandidate, ...]:
    """Select exact put spreads from an already validated PIT chain.

    Ordinary strike, DTE, credit, and liquidity filters run first. IV is then
    evaluated only for each pair that survived those filters. When IV input is
    requested, a blocked IV gate excludes that pair from the production
    shortlist; with no IV input the legacy shortlist remains NOT_EVALUATED.
    """
    if chain is None or chain.empty or atr <= 0 or limit < 1:
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
    min_volume, min_oi = int(rules.get("min_option_volume", 0)), int(rules.get("min_open_interest", 0))
    output: list[SpreadCandidate] = []
    generation_id = rules.get("options_generation_id")
    if generation_id is None:
        generation_id = chain.attrs.get("options_generation_id", chain.attrs.get("generation_id"))

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
                mid_credit = ((float(short.bid) + float(short.ask)) / 2 -
                              (float(long.bid) + float(long.ask)) / 2)
                if bid_credit <= 0 or bid_credit / width < min_ratio:
                    continue
                spread = float(short.ask) - float(short.bid)
                iv_requested = _iv_is_requested(rules, iv_context, short, long, generation_id)
                iv = (build_iv_features(
                    short, long, entry_date=entry,
                    context=_candidate_iv_context(rules, iv_context, short, long, generation_id))
                      if iv_requested else IVFeatures())
                if iv_requested and iv.iv_gate_status != "PASS":
                    continue
                output.append(_candidate_from_iv(
                    symbol, entry, expiry, short, long, width=width, distance=distance,
                    bid_credit=bid_credit, mid_credit=mid_credit, spread=spread, iv=iv))
    return tuple(sorted(output, key=lambda x: (-x.credit_efficiency, x.expiration,
                                                x.short_strike, x.long_strike))[:limit])


__all__ = ["SpreadCandidate", "shortlist_spreads"]
