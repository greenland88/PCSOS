"""Canonical PCS executable data boundary resolution."""
from __future__ import annotations
from datetime import date
from typing import Any, Mapping

DEFAULT_EXECUTABLE_START_DATE = date(2018, 1, 1)

def resolve_executable_start_date(ticker: str, source_routes: Mapping[str, Any] | None = None) -> date:
    routes = source_routes or {}
    boundary = routes.get("executable_boundary", {}) or {}
    default = date.fromisoformat(str(boundary.get("default_start_date", DEFAULT_EXECUTABLE_START_DATE.isoformat())))
    override = (boundary.get("ticker_overrides", {}) or {}).get(str(ticker).upper())
    if override is None:
        override = (routes.get("data_quality", {}).get("by_symbol", {}).get(str(ticker).upper(), {})
                    .get("executable_start_date"))
    if override is None:
        return default
    resolved = date.fromisoformat(str(override))
    if resolved < default:
        raise ValueError(f"EXECUTABLE_BOUNDARY_CANNOT_MOVE_EARLIER:{ticker}")
    return resolved
