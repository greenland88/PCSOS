"""Deterministic Phase 0 external-data contracts.

This module only describes data availability and lineage.  It does not infer
events, fill prices, or alter any trading rule.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from pcs.data.daily_provider import DailyDataProvider


class EventState(str, Enum):
    EVENT_CONFIRMED = "EVENT_CONFIRMED"
    NO_EVENT_IN_WINDOW = "NO_EVENT_IN_WINDOW"
    EVENT_DATA_MISSING = "EVENT_DATA_MISSING"
    EVENT_DATA_NOT_POINT_IN_TIME_SAFE = "EVENT_DATA_NOT_POINT_IN_TIME_SAFE"
    EVENT = "EVENT_CONFIRMED"


class EventMode(str, Enum):
    STRICT_PIT = "EVENT_MODE_STRICT_PIT"
    EX_POST_HISTORICAL = "EVENT_MODE_EX_POST_HISTORICAL"


@dataclass(frozen=True)
class EventAvailability:
    event_type: str
    symbol: str
    event_date: str | None
    source: str | None
    source_id: str | None
    event_asof: str | None
    state: EventState
    event_data_valid: bool
    reason_codes: tuple[str, ...] = ()

    def to_dict(self):
        out = asdict(self)
        out["state"] = self.state.value
        return out


def normalize_earnings_events(events: pd.DataFrame) -> pd.DataFrame:
    """Normalize source-backed earnings rows to the Phase 0 contract."""
    required = {"event_type", "symbol", "event_date", "source", "source_id", "event_asof"}
    missing = required - set(events.columns)
    if missing:
        raise ValueError(f"earnings data missing required fields: {sorted(missing)}")
    out = events.copy()
    out["event_type"] = out["event_type"].astype(str).str.upper()
    out["symbol"] = out["symbol"].astype(str).str.upper()
    out["event_date"] = pd.to_datetime(out["event_date"], errors="coerce").dt.normalize()
    out["event_asof"] = pd.to_datetime(out["event_asof"], errors="coerce", utc=True)
    if out[["event_date", "event_asof"]].isna().any().any():
        raise ValueError("earnings event_date and event_asof must be populated")
    return out[["event_type", "symbol", "event_date", "source", "source_id", "event_asof"]]


def event_availability(symbol: str, decision_date, events: pd.DataFrame,
                       window_start=None, window_end=None, coverage_end=None) -> EventAvailability:
    """Return an explicit event/no-event/missing-data state for one candidate."""
    symbol = str(symbol).upper()
    date = pd.Timestamp(decision_date).normalize()
    if events is None or events.empty or symbol not in set(events.get("symbol", pd.Series(dtype=str)).astype(str).str.upper()):
        return EventAvailability("EARNINGS", symbol, None, None, None, None,
                                 EventState.EVENT_DATA_MISSING, False, ("EVENT_DATA_MISSING",))
    d = normalize_earnings_events(events)
    lo = pd.Timestamp(window_start).normalize() if window_start is not None else date
    hi = pd.Timestamp(window_end).normalize() if window_end is not None else date
    matches = d[(d.symbol == symbol) & d.event_date.between(lo, hi)]
    if matches.empty:
        if coverage_end is not None and hi > pd.Timestamp(coverage_end).normalize():
            return EventAvailability("EARNINGS", symbol, None, None, None, None,
                                     EventState.EVENT_DATA_MISSING, False, ("EVENT_SOURCE_COVERAGE_END",))
        return EventAvailability("EARNINGS", symbol, None, None, None, None,
                                 EventState.NO_EVENT_IN_WINDOW, True, ("NO_EVENT_IN_WINDOW",))
    row = matches.sort_values("event_date").iloc[0]
    asof = pd.Timestamp(row.event_asof)
    safe = asof.tz_localize(None).normalize() <= date
    return EventAvailability("EARNINGS", symbol, str(row.event_date.date()), str(row.source),
                             str(row.source_id), asof.isoformat(),
                             EventState.EVENT_CONFIRMED if safe else EventState.EVENT_DATA_NOT_POINT_IN_TIME_SAFE,
                             bool(safe), () if safe else ("EVENT_DATA_NOT_POINT_IN_TIME_SAFE",))


def classify_candidate_events(candidates: pd.DataFrame, events: pd.DataFrame,
                              window_start_col: str = "date",
                              window_end_col: str = "expiration", coverage_end=None,
                              event_mode: EventMode = EventMode.STRICT_PIT) -> pd.DataFrame:
    """Assign exactly one required event state to every candidate row."""
    required = {"ticker", window_start_col, window_end_col}
    if not required <= set(candidates.columns):
        raise ValueError(f"candidates missing required fields: {sorted(required-set(candidates.columns))}")
    event_mode = EventMode(event_mode)
    out = candidates.copy()
    states, details = [], []
    for _, row in out.iterrows():
        result = event_availability(row.ticker, row[window_start_col], events,
                                    row[window_start_col], row[window_end_col], coverage_end)
        if event_mode is EventMode.EX_POST_HISTORICAL and result.state is EventState.EVENT_DATA_NOT_POINT_IN_TIME_SAFE:
            result = EventAvailability(result.event_type, result.symbol, result.event_date,
                                       result.source, result.source_id, result.event_asof,
                                       EventState.EVENT_CONFIRMED, True, ("EX_POST_HISTORICAL",))
        states.append(result.state.value); details.append(result.to_dict())
    out["event_state"] = states
    out["event_data_valid"] = [s in {EventState.EVENT_CONFIRMED.value, EventState.NO_EVENT_IN_WINDOW.value} for s in states]
    out["event_source_id"] = [d["source_id"] for d in details]
    out["event_asof"] = [d["event_asof"] for d in details]
    out["event_mode"] = event_mode.value
    out["event_pit_safe"] = [s == EventState.EVENT_CONFIRMED.value and event_mode is EventMode.STRICT_PIT for s in states]
    if coverage_end is not None:
        window_end = pd.to_datetime(out[window_end_col], errors="coerce").dt.normalize()
        coverage = pd.Timestamp(coverage_end).normalize()
        out["event_coverage_complete"] = window_end.le(coverage)
        out["event_readiness"] = [
            "FUTURE_EVENT_WINDOW_UNSUPPORTED" if not complete else (
                "EVENT_DATA_MISSING" if state == "EVENT_DATA_MISSING" else "HISTORICALLY_OBSERVABLE"
            )
            for complete, state in zip(out["event_coverage_complete"], states)
        ]
        out["historical_replay_eligible"] = out["event_coverage_complete"] & out["event_state"].isin([
            EventState.EVENT_CONFIRMED.value, EventState.NO_EVENT_IN_WINDOW.value
        ])
    if out["event_state"].isna().any() or not out["event_state"].isin([s.value for s in EventState]).all():
        raise AssertionError("every candidate must have exactly one recognized event state")
    return out


def audit_ohlcv_coverage(symbol: str, required_dates: Iterable, provider=None) -> dict:
    """Check exact required trading dates through the existing daily provider."""
    dates = pd.DatetimeIndex(pd.to_datetime(list(required_dates))).normalize().unique().sort_values()
    try:
        if provider is None:
            from pcs.data.access import PCSDataAccess
            frame = PCSDataAccess().read_prices(symbol, dates.min(), dates.max())
            source = "PCS_CANONICAL_DATA"
            route = "PCSDataAccess.read_prices"
        else:
            frame = provider.build_daily_series(symbol, start_date=dates.min(), as_of_date=dates.max())
            source = "LEGACY_PROVIDER_INJECTED"
            route = "injected provider"
        present = set(pd.to_datetime(frame["date"]).dt.normalize())
        missing = [str(d.date()) for d in dates if d not in present]
        return {"ticker": symbol.upper(), "coverage_start": str(frame.date.min().date()) if len(frame) else None,
                "coverage_end": str(frame.date.max().date()) if len(frame) else None,
                "missing_trading_dates": missing, "source": source,
                "route_access": route,
                "available": not missing}
    except (FileNotFoundError, ValueError) as exc:
        return {"ticker": symbol.upper(), "coverage_start": None, "coverage_end": None,
                "missing_trading_dates": [str(d.date()) for d in dates], "source": None,
                "route_access": "DailyDataProvider.build_daily_series", "available": False,
                "error": str(exc)}


PROVENANCE_COLUMNS = ["dataset", "ticker", "partition_path", "source", "source_table",
                      "source_version", "source_sha256", "query_start", "query_end",
                      "sync_import_timestamp", "run_id", "request_id"]


def provenance_ref(record: dict) -> str:
    payload = {k: record.get(k) for k in PROVENANCE_COLUMNS}
    return "prov_" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]


def attach_provenance_refs(frame: pd.DataFrame, records: Iterable[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach stable FK references and return the normalized provenance table."""
    registry = []
    refs = []
    for record in records:
        row = {k: record.get(k) for k in PROVENANCE_COLUMNS}
        row["provenance_ref"] = provenance_ref(row)
        registry.append(row); refs.append(row["provenance_ref"])
    if len(refs) not in {1, len(frame)}:
        raise ValueError("provide one provenance record or one per artifact row")
    out = frame.copy()
    out["provenance_ref"] = refs[0] if len(refs) == 1 else refs
    return out, pd.DataFrame(registry)


def resolve_provenance(ref: str, registry: pd.DataFrame) -> dict:
    rows = registry[registry.provenance_ref == ref]
    if len(rows) != 1:
        raise KeyError(f"provenance reference is not unique: {ref}")
    return rows.iloc[0].to_dict()
