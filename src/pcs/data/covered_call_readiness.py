"""Gap-only canonical readiness resolver for the covered-call universe.

This module is deliberately a planner/executor boundary: inspection is always
safe; ingestion is only attempted for explicitly supplied missing periods.
Valid canonical partitions are never rewritten.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
import json
import pandas as pd

from .access import PCSDataAccess
from .universe import load_market_universe
from .readiness import canonical_route_evidence

ACTIONS = ("REUSE", "REPAIR", "INGEST_GAP", "BLOCK")
# Leveraged ETFs inherit issuer/event risk from their reference instrument;
# this is an explicit mapping, not a ticker-agnostic fallback.
EVENT_RISK_SYMBOL = {"NVDL": "NVDA"}
EARNINGS_NOT_APPLICABLE = {"QQQ", "SPY"}

@dataclass(frozen=True)
class CoveredCallReadiness:
    symbol: str
    daily_status: str
    options_status: str
    route_status: str
    corporate_action_status: str
    earnings_status: str
    action: str
    first_blocker: str | None
    covered_call_ready: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def _metadata_status(path: Path, symbol: str, *, required: bool = False) -> str:
    if not path.exists():
        return "MISSING" if required else "NOT_REQUIRED"
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("current") is False:
                return "INVALID"
        elif path.suffix.lower() == ".csv":
            frame = pd.read_csv(path, low_memory=False)
            if frame.empty and required:
                return "INVALID"
            cols = {str(c).lower() for c in frame.columns}
            symbol_col = next((c for c in frame.columns if str(c).lower() in {"symbol", "ticker"}), None)
            if symbol_col is not None and not frame[symbol_col].astype(str).str.upper().eq(symbol).any():
                return "MISSING" if required else "NOT_REQUIRED"
        return "READY"
    except Exception:
        return "INVALID"

def resolve_ticker_data_readiness(symbol: str, *, access: PCSDataAccess | None = None,
                                  metadata_root: str | Path = "data/manifests") -> CoveredCallReadiness:
    """Inspect one ticker and return the only permitted next action."""
    symbol = str(symbol).strip().upper()
    access = access or PCSDataAccess.canonical()
    root = Path(metadata_root)
    daily = options = route = "MISSING"
    blocker: str | None = None
    try:
        prices = access.read_prices(symbol)
        daily = "READY" if not prices.empty else "MISSING"
    except Exception as exc:
        blocker = "DAILY_EXTERNAL_DATA_UNAVAILABLE" if "not found" in str(exc).lower() else "DAILY_CANONICAL_READ_FAILED"
    try:
        evidence = canonical_route_evidence(access, symbol)
        route = "READY" if evidence.get("source_version") and not evidence.get("legacy_fallback_used") else "INVALID"
        options = "READY" if route == "READY" and access.audit_options_quality(symbol).get("canonical_rows", 0) > 0 else "MISSING"
    except Exception as exc:
        route = "MISSING" if "route" in str(exc).lower() or "not ingested" in str(exc).lower() else "INVALID"
        options = "MISSING" if route == "MISSING" else "INVALID"
        if blocker is None:
            message = str(exc).lower()
            blocker = ("OPTIONS_CANONICAL_FILE_ACCESS_DENIED" if "permission denied" in message or
                       "file_access_denied" in message else
                       "OPTIONS_EXTERNAL_DATA_UNAVAILABLE" if "not ingested" in message else
                       "OPTIONS_CANONICAL_READ_FAILED")
    ca = _metadata_status(root / "corporate_actions.csv", symbol)
    event_symbol = EVENT_RISK_SYMBOL.get(symbol, symbol)
    earnings = ("NOT_REQUIRED" if symbol in EARNINGS_NOT_APPLICABLE else
                _metadata_status(Path("data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv"), event_symbol, required=True))
    statuses = [daily, options, route, ca, earnings]
    invalid = any(x == "INVALID" for x in statuses)
    missing = any(x == "MISSING" for x in statuses)
    if invalid:
        action = "REPAIR"
        blocker = blocker or next(f"{n.upper()}_METADATA_INVALID" for n, x in zip(("daily","options","route","corporate_action","earnings"), statuses) if x == "INVALID")
    elif missing:
        action = "INGEST_GAP" if blocker is None else "BLOCK"
        blocker = blocker or next(f"{n.upper()}_MISSING" for n, x in zip(("daily","options","route","corporate_action","earnings"), statuses) if x == "MISSING")
    else:
        action = "REUSE"
        blocker = None
    ready = action == "REUSE"
    return CoveredCallReadiness(symbol, daily, options, route, ca, earnings, action, blocker, ready)

def resolve_covered_call_universe(symbols: Iterable[str] | None = None, **kwargs) -> list[CoveredCallReadiness]:
    symbols = list(symbols) if symbols is not None else load_market_universe(groups=["pcs_universe"])
    return [resolve_ticker_data_readiness(s, **kwargs) for s in symbols]

__all__ = ["ACTIONS", "CoveredCallReadiness", "resolve_ticker_data_readiness", "resolve_covered_call_universe"]
