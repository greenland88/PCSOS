"""Authoritative ticker state view over routes, manifests, provenance, and readiness."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
from typing import Any, Iterable

from .access import PCSDataAccess


@dataclass(frozen=True)
class TickerState:
    ticker: str
    daily_route: str | None
    options_route: str | None
    daily_coverage_start: str | None
    daily_coverage_end: str | None
    options_coverage_start: str | None
    options_coverage_end: str | None
    canonical_data_version: str | None
    manifest: str | None
    provenance: str | None
    PIT_ready_through: str | None
    DATA_READY: str
    OPTIONS_READY: str
    PIT_READY: str
    CONTRACT_SELECTION_READY: str
    LIFECYCLE_READY: str
    PCS_RESEARCH_READY: str
    PRIMARY_BLOCKER: str
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_readiness(ticker: str, readiness_dir: str | Path) -> dict[str, Any]:
    path = Path(readiness_dir) / f"{ticker.lower()}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def get_ticker_state(ticker: str, *, access: PCSDataAccess | None = None,
                     readiness_dir: str | Path = "research_outputs/pcs_data_readiness") -> TickerState:
    symbol = str(ticker).strip().upper()
    access = access or PCSDataAccess()
    daily = options = None
    daily_error = options_error = None
    try:
        daily = access.resolve_source("daily", symbol)
    except Exception as exc:
        daily_error = str(exc)
    try:
        options = access.resolve_source("options", symbol)
    except Exception as exc:
        options_error = str(exc)
    readiness = _read_readiness(symbol, readiness_dir)
    checks = readiness.get("checks", {})
    daily_check = checks.get("daily", {})
    options_check = checks.get("options", {})
    blockers = readiness.get("blockers", [])
    saved_identity = options_check.get("options_identity") or {}
    identity_matches = bool(options and saved_identity.get("dataset") == options.dataset
                            and saved_identity.get("source_version") == options.source_version)
    stale = bool(readiness and not identity_matches)
    if stale:
        blockers = list(blockers) + [{"reason_code": "STALE_READINESS_SOURCE_IDENTITY", "detail": "readiness artifact does not match current canonical options route"}]
    primary = blockers[0].get("reason_code", "UNKNOWN_READINESS") if blockers else "NONE"
    pit = checks.get("pit", {})
    pit_through = None
    if pit.get("state_ready_rows") and daily_check.get("coverage_end"):
        pit_through = daily_check.get("coverage_end")
    return TickerState(
        ticker=symbol,
        daily_route=daily.path if daily else None,
        options_route=options.path if options else None,
        daily_coverage_start=daily.first_date if daily else None,
        daily_coverage_end=daily.last_date if daily else None,
        options_coverage_start=options.first_date if options else None,
        options_coverage_end=options.last_date if options else None,
        canonical_data_version=options.source_version if options else (daily.source_version if daily else None),
        manifest=(options.source_version.split(":", 1)[1] if options and ":" in options.source_version else None),
        provenance=checks.get("manifest_provenance", {}).get("provenance_path"),
        PIT_ready_through=pit_through,
        DATA_READY=readiness.get("DATA_READY", "NO" if daily_error else "UNKNOWN"),
        OPTIONS_READY="NO" if stale else readiness.get("OPTIONS_READY", "NO" if options_error else "UNKNOWN"),
        PIT_READY="NO" if stale else readiness.get("PIT_READY", "NO"),
        CONTRACT_SELECTION_READY="NO" if stale else readiness.get("CONTRACT_SELECTION_READY", "NO"),
        LIFECYCLE_READY="NO" if stale else readiness.get("LIFECYCLE_READY", "NO"),
        PCS_RESEARCH_READY="NO" if stale else readiness.get("PCS_RESEARCH_READY", "NO"),
        PRIMARY_BLOCKER=primary,
        reason_codes=tuple(readiness.get("reason_codes", [])) + (("STALE_READINESS_SOURCE_IDENTITY",) if stale else ()),
    )


def list_ticker_states(tickers: Iterable[str], **kwargs: Any) -> list[TickerState]:
    return [get_ticker_state(ticker, **kwargs) for ticker in sorted({str(x).upper() for x in tickers})]


__all__ = ["TickerState", "get_ticker_state", "list_ticker_states"]
