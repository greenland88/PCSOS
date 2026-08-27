"""Build canonical PIT MarketState inputs from existing daily sources.

This is an input producer only.  It preserves the existing five non-breadth
checks and supplies the legacy ``breadth_positive`` field from the explicit
SPY/QQQ market-confirmation artifact.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import json
import os
import uuid

import pandas as pd

from .access import PCSDataAccess


MODULE = "pcs.data.canonical_market_state"
VERSION = "canonical-market-state-v1"


def _digest(frame: pd.DataFrame) -> str:
    return sha256(frame.sort_values("date").to_csv(index=False, lineterminator="\n").encode()).hexdigest()


def _read_years(access: PCSDataAccess, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = []
    for year in range(start.year, end.year + 1):
        frame = access.read_partition("daily", symbol, f"year={year}", filename=f"{symbol}_{year}.parquet")
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"canonical daily source unavailable for {symbol}")
    out = pd.concat(frames, ignore_index=True)
    out.date = pd.to_datetime(out.date).dt.normalize()
    return out.sort_values("date").drop_duplicates("date", keep=False).reset_index(drop=True)


def build_canonical_market_states(
    access: PCSDataAccess,
    confirmation: pd.DataFrame,
    vix: pd.DataFrame,
    required_dates: Any,
    *,
    run_id: str,
    request_id: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build fully specified, PIT-safe market-state payload rows."""
    required = pd.DatetimeIndex(pd.to_datetime(list(required_dates)).normalize()).unique().sort_values()
    start, end = required.min(), required.max()
    source_start = start - pd.Timedelta(days=365)
    daily = {symbol: _read_years(access, symbol, source_start, end) for symbol in ("QQQ", "SPY", "SOXX")}
    indexed = {symbol: frame.set_index("date") for symbol, frame in daily.items()}
    confirmation = confirmation.copy()
    confirmation.date = pd.to_datetime(confirmation.date).dt.normalize()
    confirmation = confirmation.set_index("date")
    vix = vix.copy()
    vix.date = pd.to_datetime(vix.date).dt.normalize()
    vix = vix.set_index("date")
    rows = []
    missing = []
    for day in required:
        def close(symbol: str) -> float | None:
            return float(indexed[symbol].loc[day, "close"]) if day in indexed[symbol].index else None
        def above(symbol: str, window: int) -> bool | None:
            frame = indexed[symbol].loc[:day].tail(window)
            if len(frame) < window:
                return None
            return bool(float(frame.iloc[-1].close) >= float(frame.close.mean()))
        c = confirmation.loc[day] if day in confirmation.index else None
        v = vix.loc[day] if day in vix.index else None
        qqq20, qqq50, qqq200 = above("QQQ", 20), above("QQQ", 50), above("QQQ", 200)
        spy50, soxx50 = above("SPY", 50), above("SOXX", 50)
        qqq_window = indexed["QQQ"].loc[:day].tail(20)
        drawdown = (1 - float(qqq_window.close.iloc[-1]) / float(qqq_window.close.max())) * 100 if len(qqq_window) == 20 else None
        if any(value is None for value in [qqq20, qqq50, qqq200, spy50, soxx50, drawdown]) or c is None or v is None or pd.isna(c.breadth_positive) or pd.isna(v.vix_close):
            missing.append(str(day.date()))
            continue
        breadth = bool(c.breadth_positive)
        payload = {"qqq_above_20dma": qqq20, "qqq_above_50dma": qqq50, "qqq_above_200dma": qqq200,
                   "spy_above_50dma": spy50, "soxx_above_50dma": soxx50, "breadth_positive": breadth,
                   "recent_drawdown_pct": drawdown, "sharp_selloff": bool(drawdown >= 4), "vix": float(v.vix_close)}
        rows.append({"symbol": "MARKET", "date": day, "market_state": json.dumps(payload, sort_keys=True), "pit_asof": day, "producer_version": VERSION, "pit_status": "PIT_SAFE", "breadth_semantics": "SPY_QQQ_MARKET_CONFIRMATION", "source_vix": str(v.get("source_version", "canonical_vix_daily")), "source_confirmation": str(c.get("source_version", "market_confirmation_daily"))})
    out = pd.DataFrame(rows, columns=["symbol", "date", "market_state", "pit_asof", "producer_version", "pit_status", "breadth_semantics", "source_vix", "source_confirmation"])
    report = {"module": MODULE, "version": VERSION, "required_dates": len(required), "covered_dates": len(out), "missing_dates": missing, "source_start_with_warmup": str(source_start.date()), "source_versions": {"QQQ": _digest(daily["QQQ"]), "SPY": _digest(daily["SPY"]), "SOXX": _digest(daily["SOXX"])}, "breadth_semantics": "SPY_QQQ_MARKET_CONFIRMATION", "producer_version": VERSION, "run_id": run_id, "request_id": request_id}
    return out, report


def write_canonical_market_states(frame: pd.DataFrame, report: dict[str, Any], output: str | Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        if len(pd.read_parquet(temporary)) != len(frame):
            raise ValueError("CANONICAL_MARKET_STATE_WRITE_VERIFICATION_FAILED")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    sidecar = output.with_suffix(".validation.json")
    payload = {**report, "status": "READY" if not report["missing_dates"] else "BLOCKED_SOURCE_COVERAGE", "calculation_version": VERSION, "artifact": str(output), "market_state_semantics": "breadth_positive=SPY_QQQ_MARKET_CONFIRMATION", "created_at": datetime.now(ZoneInfo("UTC")).isoformat()}
    sidecar.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output
