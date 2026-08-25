"""Canonical, PIT-safe ingestion boundary for Cboe daily VIX history.

The module intentionally produces only a VIX input artifact.  It does not
create a ``MarketState`` or infer market breadth.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo
import json
import os
import uuid

import numpy as np
import pandas as pd


VIX_SOURCE_NAME = "Cboe Volatility Index Historical Data"
VIX_SOURCE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
VIX_SCHEMA_VERSION = "canonical-vix-daily-v1"
PIT_STATUS = "PIT_SAFE_CONSERVATIVE_EOD_ET"
REQUIRED_COLUMNS = ("DATE", "OPEN", "HIGH", "LOW", "CLOSE")
OUTPUT_COLUMNS = ("date", "vix_close", "available_as_of", "pit_status", "source_name", "source_url", "source_version", "raw_filename", "raw_sha256")


class VixInputStatus(StrEnum):
    VIX_INPUT_READY = "VIX_INPUT_READY"
    VIX_INPUT_PARTIAL = "VIX_INPUT_PARTIAL"
    VIX_INPUT_BLOCKED = "VIX_INPUT_BLOCKED"


class VixValidationError(ValueError):
    pass


@dataclass(frozen=True)
class VixIngestResult:
    module: str
    version: str
    symbol: str
    as_of: str
    status: VixInputStatus
    data_timestamp: str
    calculation_version: str
    run_id: str
    request_id: str
    reason_codes: tuple[str, ...]
    row_count: int
    payload_sha256: str
    artifact_path: str
    provenance_path: str
    validation_path: str

    def to_dict(self) -> dict:
        value = asdict(self)
        value["status"] = self.status.value
        value["reason_codes"] = list(self.reason_codes)
        return value


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _atomic_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        check = pd.read_parquet(temporary)
        if len(check) != len(frame) or list(check.columns) != list(frame.columns):
            raise VixValidationError("CANONICAL_WRITE_VERIFICATION_FAILED")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _required_coverage(frame: pd.DataFrame, date_sets: Mapping[str, Iterable]) -> dict:
    available = set(pd.to_datetime(frame["date"]).dt.normalize())
    report = {}
    for name, values in date_sets.items():
        required = set(pd.to_datetime(list(values)).normalize())
        missing = sorted(required - available)
        report[name] = {
            "required_dates": len(required), "covered_dates": len(required) - len(missing),
            "missing_dates": [str(day.date()) for day in missing],
            "coverage_pct": round(100 * (len(required) - len(missing)) / len(required), 8) if required else None,
            "start": str(min(required).date()) if required else None,
            "end": str(max(required).date()) if required else None,
        }
    return report


def canonicalize_vix_csv(raw_path: str | Path) -> tuple[pd.DataFrame, dict]:
    """Validate a Cboe-format CSV and return its stable daily close artifact."""
    raw_path = Path(raw_path)
    raw = pd.read_csv(raw_path)
    if tuple(raw.columns) != REQUIRED_COLUMNS:
        raise VixValidationError(f"VIX_SCHEMA_INVALID:{list(raw.columns)}")
    dates = pd.to_datetime(raw["DATE"], format="%m/%d/%Y", errors="coerce")
    ohlc = raw[["OPEN", "HIGH", "LOW", "CLOSE"]].apply(pd.to_numeric, errors="coerce")
    invalid = {
        "date_nulls": int(dates.isna().sum()), "duplicate_dates": int(dates.duplicated().sum()),
        "numeric_nulls": {key: int(value) for key, value in ohlc.isna().sum().items()},
        "nonfinite_values": int((~np.isfinite(ohlc)).sum().sum()),
        "nonpositive_values": {key: int(value) for key, value in ohlc.le(0).sum().items()},
        "high_below_low": int((ohlc["HIGH"] < ohlc["LOW"]).sum()),
        "close_outside_low_high": int(((ohlc["CLOSE"] < ohlc["LOW"]) | (ohlc["CLOSE"] > ohlc["HIGH"])).sum()),
        # Retained for source QA but non-blocking: Cboe's historical file has
        # legacy OPEN anomalies.  VIX regime logic consumes CLOSE only.
        "open_outside_low_high": int(((ohlc["OPEN"] < ohlc["LOW"]) | (ohlc["OPEN"] > ohlc["HIGH"])).sum()),
    }
    blockers = invalid["date_nulls"] or invalid["duplicate_dates"] or any(invalid["numeric_nulls"].values()) or invalid["nonfinite_values"] or any(invalid["nonpositive_values"].values()) or invalid["high_below_low"] or invalid["close_outside_low_high"]
    if blockers:
        raise VixValidationError("VIX_CLOSE_DATA_QUALITY_INVALID")
    if not dates.is_monotonic_increasing:
        raise VixValidationError("VIX_DATES_NOT_CHRONOLOGICAL")
    # No exact publication timestamp is supplied by the free historical CSV.
    # End-of-calendar-day New York is deliberately later than the session close
    # and therefore prevents a same-day-close replay leak.
    available = pd.DatetimeIndex([pd.Timestamp(datetime.combine(day.date(), time(23, 59, 59), tzinfo=ZoneInfo("America/New_York"))).tz_convert("UTC") for day in dates])
    out = pd.DataFrame({"date": dates.dt.normalize(), "vix_close": ohlc["CLOSE"].astype(float), "available_as_of": [value.isoformat() for value in available], "pit_status": PIT_STATUS})
    out = out.sort_values("date").reset_index(drop=True)
    gaps = dates.diff().dt.days.dropna().value_counts().sort_index().to_dict()
    report = {"schema": list(raw.columns), "rows": len(raw), "coverage_start": str(dates.min().date()),
              "coverage_end": str(dates.max().date()), "chronological_order": True, "invalid": invalid,
              "calendar_gap_days": {str(int(key)): int(value) for key, value in gaps.items()},
              "gaps_over_four_calendar_days": [{"previous_date": str(dates.iloc[index - 1].date()), "next_date": str(dates.iloc[index].date()), "days": int((dates.iloc[index] - dates.iloc[index - 1]).days)} for index in range(1, len(dates)) if (dates.iloc[index] - dates.iloc[index - 1]).days > 4]}
    return out, report


def ingest_historical_vix(raw_path: str | Path, output_dir: str | Path, *, required_date_sets: Mapping[str, Iterable], run_id: str, request_id: str, source_reference_verified: bool) -> VixIngestResult:
    """Persist VIX only; callers must build breadth and MarketState separately."""
    raw_path, output_dir = Path(raw_path), Path(output_dir)
    frame, quality = canonicalize_vix_csv(raw_path)
    raw_hash = _sha256(raw_path)
    source_version = f"cboe_vix_history_csv:sha256:{raw_hash}"
    for column, value in {"source_name": VIX_SOURCE_NAME, "source_url": VIX_SOURCE_URL, "source_version": source_version, "raw_filename": raw_path.name, "raw_sha256": raw_hash}.items():
        frame[column] = value
    frame = frame[list(OUTPUT_COLUMNS)]
    payload_hash = sha256(frame.to_csv(index=False, lineterminator="\n").encode("utf-8")).hexdigest()
    coverage = _required_coverage(frame, required_date_sets)
    gaps = [name for name, result in coverage.items() if result["missing_dates"]]
    status = VixInputStatus.VIX_INPUT_PARTIAL if gaps else VixInputStatus.VIX_INPUT_READY
    reasons = ["REQUIRED_DATE_COVERAGE_GAP"] if gaps else []
    now = datetime.now(ZoneInfo("UTC")).isoformat()
    artifact = output_dir / "canonical_vix_daily.parquet"
    provenance = output_dir / "canonical_vix_daily.provenance.json"
    validation = output_dir / "canonical_vix_daily.validation.json"
    _atomic_parquet(frame, artifact)
    _atomic_json({"source_name": VIX_SOURCE_NAME, "source_url": VIX_SOURCE_URL, "source_version": source_version,
                  "source_reference_verified": source_reference_verified, "raw_filename": raw_path.name, "raw_path": str(raw_path),
                  "file_sha256": raw_hash, "imported_at": now, "schema_version": VIX_SCHEMA_VERSION,
                  "payload_sha256": payload_hash}, provenance)
    validation_payload = {"module": "pcs.data.vix_history", "version": VIX_SCHEMA_VERSION, "symbol": "VIX", "as_of": str(frame.date.max().date()),
                          "status": status.value, "data_timestamp": str(frame.date.max().date()), "calculation_version": VIX_SCHEMA_VERSION,
                          "run_id": run_id, "request_id": request_id, "reason_codes": reasons,
                          "unique_date_key": bool(frame.date.is_unique), "numeric_validity": True, "pit_status": PIT_STATUS,
                          "deterministic_payload_sha256": payload_hash, "provenance_complete": True,
                          "source_reference_verified": source_reference_verified, "quality": quality, "required_period_coverage": coverage,
                          "market_state_status": "MARKET_STATE_STILL_BLOCKED_BY_BREADTH"}
    _atomic_json(validation_payload, validation)
    return VixIngestResult("pcs.data.vix_history", VIX_SCHEMA_VERSION, "VIX", str(frame.date.max().date()), status,
                           str(frame.date.max().date()), VIX_SCHEMA_VERSION, run_id, request_id, tuple(reasons), len(frame), payload_hash,
                           str(artifact), str(provenance), str(validation))
