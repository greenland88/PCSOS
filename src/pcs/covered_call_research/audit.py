"""Covered-call consumer audit; intentionally independent of PCS readiness gates."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import pandas as pd

from pcs.data.access import PCSDataAccess


@dataclass(frozen=True)
class CoveredCallDataAudit:
    symbol: str
    status: str
    daily_start: str | None
    daily_end: str | None
    options_start: str | None
    options_end: str | None
    option_rows: int
    field_coverage: dict[str, int]
    corporate_action_rows: int
    earnings_rows: int
    duplicate_keys: int
    unresolved_conflicts: int
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit(symbol: str, *, access: PCSDataAccess | None = None,
          earnings_path: str | Path = "data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv") -> CoveredCallDataAudit:
    symbol = str(symbol).upper()
    access = access or PCSDataAccess.canonical()
    reasons: list[str] = []
    daily = access.read_daily(symbol)
    source = access.resolve_source("options", symbol)
    quotes = access.read_quotes(symbol, source.first_date, source.last_date)
    calls = quotes[quotes.call_put.astype(str).str.lower().isin({"c", "call"})]
    fields = {c: int(calls[c].notna().sum()) for c in
              ("bid", "ask", "expiration_date", "strike", "delta", "bid_iv", "ask_iv", "open_interest", "volume")}
    if any(v != len(calls) for v in fields.values()): reasons.append("CALL_FIELD_COVERAGE_INCOMPLETE")
    quality = access.audit_options_quality(symbol, source.first_date, source.last_date)
    if quality["duplicate_option_keys"]: reasons.append("DUPLICATE_OPTION_KEYS")
    if quality["ambiguous_conflicting_option_keys"]: reasons.append("UNRESOLVED_OPTION_CONFLICTS")
    ca_path = Path("config/data/corporate_actions.csv")
    ev_path = Path(earnings_path)
    ca = pd.read_csv(ca_path) if ca_path.exists() else pd.DataFrame()
    ev = pd.read_csv(ev_path) if ev_path.exists() else pd.DataFrame()
    ca_rows = int(ca.astype(str).apply(lambda c: c.str.upper().eq(symbol)).any(axis=1).sum()) if len(ca) else 0
    ev_rows = int(ev.astype(str).apply(lambda c: c.str.upper().eq(symbol)).any(axis=1).sum()) if len(ev) else 0
    if ca_rows == 0: reasons.append("CORPORATE_ACTIONS_MISSING")
    if ev_rows == 0: reasons.append("EARNINGS_DATES_MISSING")
    basis = access.get_price_basis("daily", symbol)
    if basis.get("price_basis") in (None, "UNKNOWN"): reasons.append("PRICE_BASIS_UNKNOWN")
    return CoveredCallDataAudit(symbol, "READY" if not reasons else "BLOCKED",
        str(pd.to_datetime(daily.date).min().date()), str(pd.to_datetime(daily.date).max().date()),
        str(pd.to_datetime(quotes.trade_date).min()), str(pd.to_datetime(quotes.trade_date).max()),
        len(calls), fields, ca_rows, ev_rows, int(quality["duplicate_option_keys"]),
        int(quality["ambiguous_conflicting_option_keys"]), tuple(sorted(set(reasons))))
