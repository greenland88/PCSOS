"""Deterministic, read-only canonical readiness and lifecycle smoke checks."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
from pathlib import Path

import numpy as np
import pandas as pd

from .access import PCSDataAccess, DataAccessError, DataQualityError, SourceSpec


@dataclass(frozen=True)
class LifecycleSmokeCase:
    ticker: str
    entry_date: str
    expiration: str
    short_strike: float
    long_strike: float
    width: float
    entry_credit: float
    downstream_quote_days: int

    @property
    def identity(self) -> str:
        return f"{self.ticker}|{self.entry_date}|{self.expiration}|{self.short_strike:.8f}|{self.long_strike:.8f}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"identity": self.identity}


@dataclass(frozen=True)
class ReadinessResult:
    ticker: str
    canonical_dataset: str
    source_version: str
    date_start: str | None
    date_end: str | None
    canonical_row_count: int
    duplicate_count: int
    conflict_count: int
    feature_warmup_days: int
    feature_ready_days: int
    testable_days: int
    route_ready: bool
    canonical_data_quality: str
    feature_readiness: str
    testable_population_ready: bool
    lifecycle_infrastructure_ready: bool
    smoke_case: dict[str, Any] | None
    smoke_result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _valid_quote_rows(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["trade_date"] = pd.to_datetime(out.trade_date)
    out["expiration_date"] = pd.to_datetime(out.expiration_date)
    out["dte"] = (out.expiration_date - out.trade_date).dt.days
    return out[
        out.trade_date.notna() & out.expiration_date.notna() & out.expiration_date.ge(out.trade_date)
        & out.strike.gt(0) & out.call_put.isin(["p", "c"])
        & out.bid.notna() & out.ask.notna() & np.isfinite(out.bid) & np.isfinite(out.ask)
        & out.bid.ge(0) & out.ask.ge(out.bid) & out.dte.ge(0)
    ].copy()


def discover_lifecycle_smoke_case(
    access: PCSDataAccess, ticker: str, *, start_date: object | None = None,
    end_date: object | None = None,
) -> tuple[LifecycleSmokeCase | None, dict[str, Any]]:
    """Find the earliest deterministic legal two-leg case from canonical data.

    This is an infrastructure check.  It does not apply entry, liquidity,
    Safe Strike, credit, regime, or event predicates.
    """
    try:
        quotes = access.read("options", ticker, start_date, end_date)
    except (DataAccessError, DataQualityError, FileNotFoundError, ValueError) as exc:
        return None, {"status": "UNAVAILABLE", "reason": "CANONICAL_ROUTE_OR_DATA_UNAVAILABLE", "detail": str(exc)}
    q = _valid_quote_rows(quotes)
    if q.empty:
        return None, {"status": "UNAVAILABLE", "reason": "NO_VALID_OPTION_QUOTES"}
    q = q.sort_values(["trade_date", "expiration_date", "strike", "call_put"], kind="mergesort")
    for (day, expiry), chain in q.groupby(["trade_date", "expiration_date"], sort=True):
        dte = int((expiry - day).days)
        if not 30 <= dte <= 45:
            continue
        puts = chain[chain.call_put.eq("p")].sort_values("strike", kind="mergesort")
        if len(puts) < 2:
            continue
        # Deterministic nearest adjacent listed puts; no strategy threshold.
        for i in range(len(puts) - 1, 0, -1):
            short, long = puts.iloc[i], puts.iloc[i - 1]
            if float(short.strike) <= float(long.strike):
                continue
            entry_credit = float(short.bid - long.ask)
            if entry_credit <= 0:
                continue
            pair = q[(q.expiration_date == expiry) & q.strike.isin([short.strike, long.strike]) & (q.trade_date >= day)]
            downstream = pair.groupby("trade_date").filter(lambda g: set(g.strike) == {short.strike, long.strike}).trade_date.nunique()
            if downstream < 2:
                continue
            case = LifecycleSmokeCase(str(ticker).upper(), str(day.date()), str(expiry.date()), float(short.strike), float(long.strike), float(short.strike-long.strike), entry_credit, int(downstream))
            return case, {"status": "FOUND", "reason": "NONE", "identity": case.identity}
    return None, {"status": "UNAVAILABLE", "reason": "NO_COMPLETE_LIFECYCLE_WINDOW"}


def canonical_route_evidence(access: PCSDataAccess, ticker: str) -> dict[str, Any]:
    spec: SourceSpec = access.resolve_source("options_v2", ticker)
    # SourceSpec.source_version is the authoritative route manifest identity.
    # Do not report PCSDataAccess's default manifest when a ticker-specific
    # route resolves through an isolated options manifest.
    manifest = ""
    if ":" in str(spec.source_version):
        manifest = str(spec.source_version).split(":", 1)[1]
    return {"requested_dataset": "options_v2", "resolved_dataset": spec.dataset,
            "resolved_manifest": manifest, "resolved_root": str(access.parquet_root),
            "source_version": spec.source_version, "legacy_fallback_used": False,
            "spec": spec.to_dict()}


def execute_lifecycle_smoke(access: PCSDataAccess, case: LifecycleSmokeCase) -> dict[str, Any]:
    """Run the existing lifecycle engine on the discovered case only."""
    from pcs.research.variant_b_replay import ReplayPolicy, _replay_lifecycle

    q = access.read_quotes(case.ticker, case.entry_date, case.expiration, expirations=[case.expiration], strikes=[case.short_strike, case.long_strike])
    q = q.rename(columns={"trade_date": "Trade Date", "expiration_date": "Expiry Date", "call_put": "Call/Put", "strike": "Strike", "bid": "Bid Price", "ask": "Ask Price", "open_interest": "Open Interest", "volume": "Volume", "delta": "Delta"})
    q["Trade Date"] = pd.to_datetime(q["Trade Date"])
    q["Expiry Date"] = pd.to_datetime(q["Expiry Date"])
    q = q[q["Call/Put"].astype(str).str.lower().eq("p")].copy()
    candidate = {"date": pd.Timestamp(case.entry_date), "expiration": pd.Timestamp(case.expiration), "short_strike": case.short_strike, "long_strike": case.long_strike, "credit": case.entry_credit}
    result = _replay_lifecycle(candidate, q, ReplayPolicy())
    return {"status": result.get("status"), "exit_date": str(result["exit_date"].date()) if result.get("exit_date") is not None else None, "exit_reason": result.get("exit_reason"), "realized_pnl": result.get("realized_pnl"), "mark_count": result.get("mark_count"), "case_identity": case.identity}
