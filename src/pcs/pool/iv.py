"""Bounded point-in-time IV features for shortlisted PCS spreads.

The public builder accepts the two contracts that already survived ordinary
shortlist filters plus caller-supplied, point-in-time context. It never reads
an options chain, a data-access object, or another contract to fill a feature.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math
from numbers import Real
from typing import Any, Mapping

import pandas as pd


IV_CALCULATION_VERSION = "pcs.pool.iv.v1"


class IVGateStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    PASS = "PASS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class IVFeatures:
    """Validated IV bundle attached to one exact put spread."""

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
    iv_gate_status: IVGateStatus = IVGateStatus.NOT_EVALUATED
    reason_codes: tuple[str, ...] = ()
    options_generation_id: str | None = None
    calculation_version: str = IV_CALCULATION_VERSION
    iv_data_as_of: str | None = None

    @property
    def iv_reason_codes(self) -> tuple[str, ...]:
        return self.reason_codes

    @property
    def generation_id(self) -> str | None:
        return self.options_generation_id

    @property
    def short_iv(self) -> float | None:
        return self.short_put_iv

    @property
    def long_iv(self) -> float | None:
        return self.long_put_iv

    @property
    def atm30d_iv(self) -> float | None:
        return self.atm_iv_30d

    @property
    def rv20(self) -> float | None:
        return self.realized_vol_20d

    @property
    def rv60(self) -> float | None:
        return self.realized_vol_60d

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        payload["iv_reason_codes"] = list(self.reason_codes)
        return payload


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _positive(value: Any) -> float | None:
    value = _number(value)
    return value if value is not None and value > 0 else None


def _nonnegative(value: Any) -> float | None:
    value = _number(value)
    return value if value is not None and value >= 0 else None


def _row_value(row: Any, name: str) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(name)
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


def _missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except (TypeError, ValueError):
        return False


def _read_bid_ask(row: Any, prefix: str, reasons: list[str]) -> tuple[float | None, float | None]:
    """Read both IV sides from one shortlisted contract and validate order."""
    bid_raw, ask_raw = _row_value(row, "bid_iv"), _row_value(row, "ask_iv")
    bid, ask = _positive(bid_raw), _positive(ask_raw)
    if bid is None:
        reasons.extend(("IV_MISSING" if _missing(bid_raw) else "IV_INVALID",
                        f"{prefix.upper()}_BID_IV_INVALID"))
    if ask is None:
        reasons.extend(("IV_MISSING" if _missing(ask_raw) else "IV_INVALID",
                        f"{prefix.upper()}_ASK_IV_INVALID"))
    if bid is not None and ask is not None and bid > ask:
        reasons.extend(("IV_BID_ASK_INVERTED", f"{prefix.upper()}_IV_BID_ASK_INVERTED"))
        return None, None
    return bid, ask


def _as_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if context is None:
        return {}
    nested = context.get("iv_context") if isinstance(context, Mapping) else None
    merged = dict(nested) if isinstance(nested, Mapping) else {}
    merged.update(dict(context))
    return merged


def _context_number(context: Mapping[str, Any], keys: tuple[str, ...], reasons: list[str], *,
                    positive: bool = False, nonnegative: bool = False,
                    bounded: bool = False) -> float | None:
    present = next((key for key in keys if key in context), None)
    if present is None:
        return None
    raw = context[present]
    value = (_positive(raw) if positive else
             _nonnegative(raw) if nonnegative else _number(raw))
    if value is None:
        reasons.append(f"{keys[0].upper()}_INVALID")
    elif bounded and not 0 <= value <= 1:
        reasons.append(f"{keys[0].upper()}_OUT_OF_RANGE")
        value = None
    return value


def _pit_timestamp(raw: Any) -> pd.Timestamp | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        stamp = pd.Timestamp(raw)
    except (TypeError, ValueError):
        return pd.NaT
    if pd.isna(stamp):
        return pd.NaT
    if stamp.tzinfo is not None:
        stamp = stamp.tz_localize(None)
    return stamp.normalize()


def _pit_check(row: Any, entry_date: pd.Timestamp, reasons: list[str]) -> None:
    for field in ("quote_as_of", "trade_date", "as_of"):
        observed = _pit_timestamp(_row_value(row, field))
        if observed is None:
            continue
        if pd.isna(observed):
            reasons.append("IV_PIT_TIMESTAMP_INVALID")
        elif observed > entry_date:
            reasons.append("IV_NOT_POINT_IN_TIME")


def _history_values(history: Any, entry_date: pd.Timestamp) -> list[float]:
    if history is None:
        return []
    if isinstance(history, pd.DataFrame):
        frame = history.copy()
        value_col = next((c for c in ("iv", "atm_iv_30d", "value") if c in frame.columns), None)
        if value_col is None:
            return []
        if "date" in frame.columns:
            dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
            frame = frame[dates < entry_date]
        values = frame[value_col].tolist()
    else:
        values = []
        for item in history:
            if isinstance(item, Mapping):
                raw_date = item.get("date", item.get("as_of"))
                if raw_date is not None:
                    stamp = _pit_timestamp(raw_date)
                    if stamp is None or pd.isna(stamp) or stamp >= entry_date:
                        continue
                values.append(item.get("iv", item.get("atm_iv_30d", item.get("value"))))
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                stamp = _pit_timestamp(item[0])
                if stamp is None or pd.isna(stamp) or stamp >= entry_date:
                    continue
                values.append(item[1])
            else:
                values.append(item)
    return [value for value in (_positive(item) for item in values) if value is not None]


def _realized_vol(close_history: Any, window: int, entry_date: pd.Timestamp) -> float | None:
    if close_history is None:
        return None
    if isinstance(close_history, pd.DataFrame):
        frame = close_history.copy()
        if "date" in frame.columns:
            dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
            frame = frame[dates <= entry_date]
        if "close" not in frame.columns:
            return None
        prices = pd.to_numeric(frame["close"], errors="coerce")
    else:
        prices = pd.to_numeric(pd.Series(close_history), errors="coerce")
    returns = prices.pct_change().dropna()
    if len(returns) < window:
        return None
    value = float(returns.tail(window).std(ddof=1) * math.sqrt(252))
    return value if math.isfinite(value) and value >= 0 else None


def _row_context(short_row: Any, long_row: Any) -> dict[str, Any]:
    """Copy only feature columns from the two selected rows."""
    names = (
        "atm_iv_30d", "atm30d_iv", "realized_vol_20d", "rv20",
        "realized_vol_60d", "rv60", "iv_rank_252", "iv_rank",
        "iv_percentile_252", "iv_percentile", "put_skew", "term_structure",
        "event_iv_distortion", "options_generation_id", "generation_id",
        "iv_estimate_method", "quote_as_of", "data_timestamp",
    )
    result: dict[str, Any] = {}
    for name in names:
        value = _row_value(short_row, name)
        if value is not None and not _missing(value):
            result[name] = value
    for name in names:
        if name not in result:
            value = _row_value(long_row, name)
            if value is not None and not _missing(value):
                result[name] = value
    return result


def build_iv_features(short_row: Any, long_row: Any, *, entry_date: Any,
                      context: Mapping[str, Any] | None = None) -> IVFeatures:
    """Build and validate PIT IV features for exactly two selected contracts.

    Direct IV values come only from ``short_row`` and ``long_row``. ATM30D,
    RV20/RV60, rank/percentile, skew, term structure, event distortion, and
    generation identity come from caller-supplied context or those same rows.
    Set ``iv_gate_enabled`` in context to require every diagnostic field;
    otherwise missing diagnostics remain nullable while malformed supplied
    values still fail closed.
    """
    entry = _pit_timestamp(entry_date)
    if entry is None or pd.isna(entry):
        raise ValueError("IV_ENTRY_DATE_INVALID")
    ctx = _row_context(short_row, long_row)
    ctx.update(_as_context(context))
    reasons: list[str] = []

    short_bid, short_ask = _read_bid_ask(short_row, "short_put", reasons)
    long_bid, long_ask = _read_bid_ask(long_row, "long_put", reasons)
    _pit_check(short_row, entry, reasons)
    _pit_check(long_row, entry, reasons)
    for field in ("data_timestamp", "iv_data_timestamp", "options_data_timestamp"):
        if field in ctx:
            observed = _pit_timestamp(ctx[field])
            if observed is None or pd.isna(observed):
                reasons.append("IV_PIT_TIMESTAMP_INVALID")
            elif observed > entry:
                reasons.append("IV_NOT_POINT_IN_TIME")

    method = str(ctx.get("iv_estimate_method", "MIDPOINT")).upper()
    if method not in {"MIDPOINT", "CONSERVATIVE"}:
        reasons.append("IV_ESTIMATE_METHOD_INVALID")
        method = "MIDPOINT"

    def estimate(bid: float | None, ask: float | None) -> float | None:
        if bid is None or ask is None:
            return None
        return max(bid, ask) if method == "CONSERVATIVE" else (bid + ask) / 2

    short_iv, long_iv = estimate(short_bid, short_ask), estimate(long_bid, long_ask)
    atm_iv = _context_number(ctx, ("atm_iv_30d", "atm30d_iv"), reasons, positive=True)
    rv20 = _context_number(ctx, ("realized_vol_20d", "rv20"), reasons, nonnegative=True)
    rv60 = _context_number(ctx, ("realized_vol_60d", "rv60"), reasons, nonnegative=True)
    close_history = ctx.get("close_history", ctx.get("underlying_history"))
    if rv20 is None and close_history is not None:
        rv20 = _realized_vol(close_history, 20, entry)
    if rv60 is None and close_history is not None:
        rv60 = _realized_vol(close_history, 60, entry)
    if close_history is not None and rv20 is None:
        reasons.append("RV20_HISTORY_INSUFFICIENT")
    if close_history is not None and rv60 is None:
        reasons.append("RV60_HISTORY_INSUFFICIENT")

    rank = _context_number(ctx, ("iv_rank_252", "iv_rank"), reasons, bounded=True)
    percentile = _context_number(ctx, ("iv_percentile_252", "iv_percentile"), reasons, bounded=True)
    if rank is None and atm_iv is not None and ctx.get("iv_history_252") is not None:
        history = _history_values(ctx["iv_history_252"], entry)
        if history:
            low, high = min(history), max(history)
            rank = 0.5 if high == low else min(1.0, max(0.0, (atm_iv - low) / (high - low)))
            percentile = min(1.0, max(0.0, sum(value <= atm_iv for value in history) / len(history)))
        else:
            reasons.append("IV_HISTORY_252_UNAVAILABLE")

    generation = ctx.get("options_generation_id", ctx.get("generation_id"))
    generation = str(generation).strip() if generation is not None else ""
    if not generation:
        reasons.append("OPTIONS_GENERATION_ID_MISSING")
    if ctx.get("generation_id_mismatch"):
        reasons.append("OPTIONS_GENERATION_ID_MISMATCH")

    iv_minus_rv = short_iv - rv20 if short_iv is not None and rv20 is not None else None
    iv_to_rv_ratio = short_iv / rv20 if short_iv is not None and rv20 not in (None, 0) else None
    if short_iv is not None and rv20 == 0:
        reasons.append("RV20_ZERO")

    supplied_skew = _context_number(ctx, ("put_skew",), reasons)
    skew = supplied_skew if supplied_skew is not None else (
        short_iv - atm_iv if short_iv is not None and atm_iv is not None else None)
    term = _context_number(ctx, ("term_structure",), reasons)
    distortion = _context_number(ctx, ("event_iv_distortion",), reasons)

    strict = bool(ctx.get("iv_gate_enabled", False))
    if strict:
        required_context = (
            ("atm_iv_30d", atm_iv), ("realized_vol_20d", rv20),
            ("realized_vol_60d", rv60), ("iv_rank_252", rank),
            ("iv_percentile_252", percentile), ("put_skew", skew),
            ("term_structure", term), ("event_iv_distortion", distortion),
        )
        for name, value in required_context:
            if value is None and not any(code.startswith(name.upper() + "_") for code in reasons):
                reasons.append(f"{name.upper()}_MISSING")

    blocking = {
        "IV_MISSING", "IV_INVALID", "IV_BID_ASK_INVERTED", "IV_NOT_POINT_IN_TIME",
        "IV_PIT_TIMESTAMP_INVALID", "OPTIONS_GENERATION_ID_MISSING",
        "OPTIONS_GENERATION_ID_MISMATCH",
        "IV_ESTIMATE_METHOD_INVALID", "RV20_ZERO", "IV_HISTORY_252_UNAVAILABLE",
        "RV20_HISTORY_INSUFFICIENT", "RV60_HISTORY_INSUFFICIENT",
    }
    status = (IVGateStatus.BLOCKED if strict and any(
        code in blocking or code.endswith("_INVALID") or
        code.endswith("_OUT_OF_RANGE") or code.endswith("_MISSING")
        for code in reasons) else (
        IVGateStatus.BLOCKED if any(code in blocking for code in reasons)
        else IVGateStatus.PASS))
    iv_data_as_of = ctx.get("iv_data_as_of", ctx.get("iv_data_timestamp",
                                                        ctx.get("options_data_timestamp")))
    return IVFeatures(short_bid, short_ask, long_bid, long_ask, short_iv, long_iv,
                      atm_iv, rv20, rv60, iv_minus_rv, iv_to_rv_ratio, rank,
                      percentile, skew, term, distortion, status,
                      tuple(dict.fromkeys(reasons)), generation or None,
                      IV_CALCULATION_VERSION,
                      str(iv_data_as_of) if iv_data_as_of is not None else None)


calculate_iv_features = build_iv_features
evaluate_iv_gate = build_iv_features


__all__ = ["IV_CALCULATION_VERSION", "IVGateStatus", "IVFeatures", "build_iv_features",
           "calculate_iv_features", "evaluate_iv_gate"]
