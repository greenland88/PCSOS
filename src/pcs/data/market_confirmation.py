"""PIT-safe SPY/QQQ market confirmation input for the regime engine.

``breadth_positive`` is retained as the legacy field name required by
``MarketState``.  Its current contract is explicitly *not* traditional
constituent breadth: it means ``SPY_QQQ_MARKET_CONFIRMATION``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import json
import os
import uuid

import pandas as pd

from .access import PCSDataAccess, DataQualityError


MODULE = "pcs.data.market_confirmation"
VERSION = "market-confirmation-daily-v1"
SEMANTICS = "SPY_QQQ_MARKET_CONFIRMATION"
PIT_MODE_CLOSE_AFTER = "DECISION_AFTER_SESSION_CLOSE"
PIT_MODE_PRE_CLOSE = "DECISION_BEFORE_SESSION_CLOSE"


class ConfirmationStatus(StrEnum):
    READY = "READY"
    INSUFFICIENT_SMA50_HISTORY = "INSUFFICIENT_SMA50_HISTORY"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


@dataclass(frozen=True)
class MarketConfirmationResult:
    module: str
    version: str
    symbol: str
    as_of: str
    status: str
    data_timestamp: str
    calculation_version: str
    run_id: str
    request_id: str
    reason_codes: tuple[str, ...]
    artifact_path: str
    provenance_path: str
    validation_path: str
    required_dates: int
    covered_dates: int
    missing_dates: tuple[str, ...]
    true_days: int
    false_days: int

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["reason_codes"] = list(self.reason_codes)
        return out


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        check = pd.read_parquet(temporary)
        if len(check) != len(frame) or list(check.columns) != list(frame.columns):
            raise DataQualityError("MARKET_CONFIRMATION_WRITE_VERIFICATION_FAILED")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _frame_digest(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values("date").reset_index(drop=True)
    return sha256(ordered.to_csv(index=False, lineterminator="\n").encode("utf-8")).hexdigest()


def _read_daily(access: PCSDataAccess, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Read canonical annual partitions only through PCSDataAccess."""
    frames = []
    for year in range(start.year, end.year + 1):
        frame = access.read_partition("daily", symbol, f"year={year}", filename=f"{symbol}_{year}.parquet")
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"canonical daily source unavailable for {symbol}")
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out = out[(out.date >= start) & (out.date <= end)].sort_values("date").drop_duplicates("date", keep=False).reset_index(drop=True)
    if out.empty:
        raise FileNotFoundError(f"canonical daily source has no requested rows for {symbol}")
    return out


def build_market_confirmation(
    access: PCSDataAccess,
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    *,
    sma_window: int = 50,
    required_dates: Any = None,
    run_id: str = "",
    request_id: str = "",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build deterministic confirmation from canonical daily closes.

    The source read begins 365 calendar days before ``start_date`` so the SMA
    warmup is computed from persisted history.  Rows without 50 observations
    remain explicit ``INSUFFICIENT_SMA50_HISTORY`` rows and have a null
    ``breadth_positive``; no value is guessed.
    """
    start, end = pd.Timestamp(start_date).normalize(), pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    source_start = start - pd.Timedelta(days=365)
    spy = _read_daily(access, "SPY", source_start, end)
    qqq = _read_daily(access, "QQQ", source_start, end)
    spy_dates, qqq_dates = set(spy.date), set(qqq.date)
    aligned = spy.merge(qqq, on="date", how="outer", suffixes=("_spy", "_qqq"), indicator=True).sort_values("date")
    aligned["spy_sma50"] = aligned["close_spy"].rolling(sma_window, min_periods=sma_window).mean()
    aligned["qqq_sma50"] = aligned["close_qqq"].rolling(sma_window, min_periods=sma_window).mean()
    aligned["spy_positive"] = (aligned["close_spy"] > aligned["spy_sma50"]).where(aligned["spy_sma50"].notna())
    aligned["qqq_positive"] = (aligned["close_qqq"] > aligned["qqq_sma50"]).where(aligned["qqq_sma50"].notna())
    aligned["breadth_positive"] = (aligned["spy_positive"] & aligned["qqq_positive"]).where(aligned["spy_positive"].notna() & aligned["qqq_positive"].notna())
    aligned["status"] = aligned["breadth_positive"].map(lambda x: ConfirmationStatus.READY.value if pd.notna(x) else ConfirmationStatus.INSUFFICIENT_SMA50_HISTORY.value)
    aligned["pit_status"] = aligned["status"].map(lambda x: "PIT_SAFE" if x == ConfirmationStatus.READY.value else "PIT_NOT_READY")
    # This is an explicit replay convention: date-only decisions are after the
    # session close.  Pre-close callers must use lookup_for_decision instead.
    eastern = ZoneInfo("America/New_York")
    aligned["available_as_of"] = [pd.Timestamp(datetime.combine(d.date(), time(23, 59, 59), tzinfo=eastern)).tz_convert("UTC").isoformat() for d in aligned.date]
    source_version = f"canonical_daily_ohlcv:SPY={_frame_digest(spy)}:QQQ={_frame_digest(qqq)}"
    out = aligned.rename(columns={"close_spy": "spy_close", "close_qqq": "qqq_close"})[
        ["date", "spy_close", "spy_sma50", "spy_positive", "qqq_close", "qqq_sma50", "qqq_positive", "breadth_positive", "available_as_of", "pit_status", "status"]
    ].copy()
    out["source_version"] = source_version
    out = out[(out.date >= start) & (out.date <= end)].reset_index(drop=True)
    required = set(pd.to_datetime(list(required_dates)).normalize()) if required_dates is not None else set(out.date)
    missing = sorted((spy_dates ^ qqq_dates) & required)
    required_missing = sorted(required - set(out.date))
    required_rows = out[out.date.isin(required)]
    report = {
        "semantics": SEMANTICS, "formula": "(SPY close > SPY SMA50) AND (QQQ close > QQQ SMA50)",
        "pit_convention": PIT_MODE_CLOSE_AFTER, "sma_window": sma_window,
        "source_start_with_warmup": str(source_start.date()), "requested_start": str(start.date()), "requested_end": str(end.date()),
        "spy_source_rows": len(spy), "qqq_source_rows": len(qqq), "spy_source_start": str(min(spy_dates).date()), "spy_source_end": str(max(spy_dates).date()),
        "qqq_source_start": str(min(qqq_dates).date()), "qqq_source_end": str(max(qqq_dates).date()),
        "non_trading_alignment_missing_dates": [str(d.date()) for d in missing],
        "required_dates": int(len(required)), "required_dates_covered": int(len(required) - len(required_missing)),
        "required_dates_missing": [str(d.date()) for d in required_missing],
        "trading_dates_observed": int(out.date.nunique()), "warmup_rows": int((out.status == ConfirmationStatus.INSUFFICIENT_SMA50_HISTORY.value).sum()),
        "ready_rows": int((out.status == ConfirmationStatus.READY.value).sum()),
        "true_days": int((required_rows.breadth_positive == True).sum()), "false_days": int((required_rows.breadth_positive == False).sum()),
        "artifact_trading_rows": int(len(out)), "artifact_true_days": int((out.breadth_positive == True).sum()), "artifact_false_days": int((out.breadth_positive == False).sum()),
        "source_version": source_version,
    }
    return out, report


def lookup_for_decision(artifact: pd.DataFrame, decision_timestamp: str | pd.Timestamp, *, after_close: bool) -> pd.Series:
    """Return the latest fully available row under the explicit PIT convention."""
    timestamp = pd.Timestamp(decision_timestamp)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("America/New_York")
    timestamp = timestamp.tz_convert("UTC")
    rows = artifact[pd.to_datetime(artifact.available_as_of, utc=True) <= timestamp]
    if not after_close:
        # The available_as_of guard is the authority; no same-session close is
        # allowed for a pre-close decision even if a caller passes a late date.
        session = timestamp.tz_convert("America/New_York").normalize()
        rows = rows[pd.to_datetime(rows.date).dt.tz_localize("America/New_York") < session]
    rows = rows[rows.status == ConfirmationStatus.READY.value]
    if rows.empty:
        raise LookupError("MARKET_CONFIRMATION_NOT_AVAILABLE_PIT_SAFE")
    return rows.sort_values("date").iloc[-1]


def write_market_confirmation_artifact(frame: pd.DataFrame, report: dict[str, Any], output_dir: str | Path, *, run_id: str, request_id: str) -> MarketConfirmationResult:
    output_dir = Path(output_dir)
    artifact, provenance, validation = output_dir / "market_confirmation_daily.parquet", output_dir / "market_confirmation_daily.provenance.json", output_dir / "market_confirmation_daily.validation.json"
    _atomic_parquet(frame, artifact)
    payload_sha = sha256(frame.to_csv(index=False, lineterminator="\n").encode("utf-8")).hexdigest()
    now = datetime.now(ZoneInfo("UTC")).isoformat()
    _atomic_json({"module": MODULE, "version": VERSION, "semantics": SEMANTICS, "formula": "(SPY close > SPY SMA50) AND (QQQ close > QQQ SMA50)", "run_id": run_id, "request_id": request_id, "imported_at": now, "source_version": report["source_version"], "payload_sha256": payload_sha, "sources": {"SPY": "PCSDataAccess.read_partition daily", "QQQ": "PCSDataAccess.read_partition daily"}}, provenance)
    status = "READY" if not report["non_trading_alignment_missing_dates"] and not report["required_dates_missing"] and report["warmup_rows"] == 0 else "PARTIAL"
    validation_payload = {"module": MODULE, "version": VERSION, "symbol": "SPY_QQQ", "as_of": report["requested_end"], "status": status, "data_timestamp": report["requested_end"], "calculation_version": VERSION, "run_id": run_id, "request_id": request_id, "reason_codes": [] if status == "READY" else ["INPUT_COVERAGE_OR_WARMUP_PARTIAL"], "deterministic_payload_sha256": payload_sha, "provenance_complete": True, "report": report, "market_state_status": "BREADTH_CONTRACT_RESOLVED_MARKET_STATE_GENERATION_ALLOWED"}
    _atomic_json(validation_payload, validation)
    ready = frame[frame.status == ConfirmationStatus.READY.value]
    return MarketConfirmationResult(MODULE, VERSION, "SPY_QQQ", report["requested_end"], status, report["requested_end"], VERSION, run_id, request_id, tuple(validation_payload["reason_codes"]), str(artifact), str(provenance), str(validation), report["required_dates_covered"], report["required_dates_covered"], tuple(report["required_dates_missing"]), report["true_days"], report["false_days"])
