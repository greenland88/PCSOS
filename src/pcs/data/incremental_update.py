"""Idempotent incremental updates for current PCS market-data state.

Frozen research artifacts are intentionally outside this module's write scope.
Only current daily and logical options partitions are changed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import uuid
from typing import Any

import pandas as pd

from .access import PCSDataAccess, DataAccessError, DataQualityError
from .daily_provider import normalize_daily_frame


@dataclass
class UpdateResult:
    module: str = "pcs.data.incremental_update"
    version: str = "1.0"
    symbol: str = ""
    as_of: str | None = None
    status: str = "SUCCESS"
    data_timestamp: str | None = None
    calculation_version: str = "incremental_current_v1"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    current_data_asof: str | None = None
    latest_daily_date: str | None = None
    latest_options_date: str | None = None
    daily_update: str = "NO_OP"
    options_update: str = "NO_OP"
    affected_partitions: list[str] = field(default_factory=list)
    current_route: str | None = None
    frozen_generations_touched: int = 0
    current_derived_artifacts_invalidated: list[str] = field(default_factory=list)
    readiness_refresh_status: str = "NOT_RUN"
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(frame: pd.DataFrame) -> str:
    payload = frame.sort_index(axis=1).sort_values(list(frame.columns), kind="mergesort").to_json(
        orient="records", date_format="iso", default_handler=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _atomic_write(frame: pd.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(tmp, index=False)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _daily_partition(root: Path, symbol: str, year: int) -> Path:
    return root / "daily" / f"symbol={symbol}" / f"year={year}" / f"{symbol}_{year}.parquet"


def invalidate_current_derived(symbol: str, affected_partitions: list[str], *, manifest_path="data/manifests/derived_invalidations.jsonl") -> list[str]:
    """Persist a deterministic downstream invalidation marker.

    Derived artifacts remain immutable; their consumers must reject a cache
    whose source partition overlaps one of these markers and rebuild it.
    """
    symbol = str(symbol).upper()
    target = Path(manifest_path); target.parent.mkdir(parents=True, exist_ok=True)
    marker = {"symbol": symbol, "affected_partitions": sorted(set(affected_partitions)), "created_at": datetime.now(timezone.utc).isoformat()}
    existing = []
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try: existing.append(json.loads(line))
                except json.JSONDecodeError: continue
    semantic = {(x.get("symbol"), tuple(x.get("affected_partitions", []))) for x in existing}
    key = (symbol, tuple(marker["affected_partitions"]))
    if key not in semantic:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(marker, sort_keys=True) + "\n")
    return [f"{symbol}:{part}" for part in marker["affected_partitions"]]


def update_daily_frame(symbol: str, incoming: pd.DataFrame, *, parquet_root="data/parquet", manifest_path="data/manifests/storage_manifest.csv", source_version="incremental") -> tuple[str, list[str], str | None]:
    symbol = symbol.upper()
    incoming = incoming.copy()
    if "symbol" in incoming:
        incoming_symbols = incoming["symbol"].astype(str).str.strip().str.upper()
        if set(incoming_symbols) - {symbol}:
            raise DataQualityError(f"ticker isolation failure for {symbol}")
    else:
        incoming["symbol"] = symbol
    incoming = normalize_daily_frame(incoming)
    incoming["symbol"] = symbol
    access = PCSDataAccess(manifest_path=manifest_path, parquet_root=parquet_root)
    changed: list[str] = []
    for year, new_rows in incoming.groupby(incoming.date.dt.year):
        target = _daily_partition(Path(parquet_root), symbol, int(year))
        old = pd.DataFrame(columns=incoming.columns)
        try:
            record = access.active_generation_record("daily", symbol, f"year={int(year)}")
            old = access.read_pinned_generation("daily", symbol, f"year={int(year)}", str(record["active_generation"]))
        except (DataAccessError, FileNotFoundError, ValueError, KeyError):
            if target.exists():
                old = pd.read_parquet(target)
        if not old.empty:
            prior = normalize_daily_frame(old).set_index('date')
            fresh = normalize_daily_frame(new_rows).set_index('date')
            overlap = prior.index.intersection(fresh.index)
            if len(overlap) and prior.loc[overlap].ne(fresh.loc[overlap]).any().any():
                raise DataQualityError('DAILY_SOURCE_OVERLAP_CONFLICT')
        merged = normalize_daily_frame(new_rows.copy() if old.empty else pd.concat([old, new_rows], ignore_index=True))
        if target.exists() and _sha256(old) == _sha256(merged):
            continue
        access.promote_generation(merged, "daily", symbol, f"year={int(year)}", source_version=source_version)
        changed.append(f"daily/symbol={symbol}/year={int(year)}")
    latest = str(pd.to_datetime(incoming.date).max().date()) if len(incoming) else None
    return ("UPDATED" if changed else "NO_OP", changed, latest)


def update_options_frame(symbol: str, incoming: pd.DataFrame, *, parquet_root="data/parquet", manifest_path="data/manifests/storage_manifest.csv", source_version="incremental", physical_dataset=None) -> tuple[str, list[str], str | None]:
    symbol = symbol.upper()
    incoming = incoming.copy()
    if "symbol" not in incoming:
        incoming["symbol"] = symbol
    incoming["symbol"] = incoming["symbol"].astype(str).str.upper()
    access = PCSDataAccess(manifest_path=manifest_path, parquet_root=parquet_root)
    if physical_dataset is None:
        try:
            physical_dataset, routed_manifest, routed_root = access._resolve_route("options", symbol)
            if access.routing_mode == "isolated" and access._read_manifest(access.manifest_path).empty:
                physical_dataset = None
        except DataAccessError:
            physical_dataset = None
        if physical_dataset is None:
            if Path(manifest_path) != Path("data/manifests/storage_manifest.csv"):
                physical_dataset = "options_v2"
                routed_manifest, routed_root = Path(manifest_path), Path(parquet_root)
            else:
                raise
    else:
        routed_manifest, routed_root = Path(manifest_path), Path(parquet_root)
    access = PCSDataAccess.isolated(manifest_path=routed_manifest, parquet_root=routed_root, source_routes=access.source_routes)
    parquet_root = routed_root
    incoming = access.validate_schema(incoming, "options")
    if set(incoming.symbol) - {symbol}:
        raise DataQualityError(f"ticker isolation failure for {symbol}")
    changed: list[str] = []
    periods = pd.to_datetime(incoming.trade_date).dt.to_period("Q")
    for period, new_rows in incoming.groupby(periods):
        partition = f"year={period.year}/quarter={period.quarter}"
        target_dir = Path(parquet_root) / physical_dataset / f"symbol={symbol}" / partition
        target = next(iter(target_dir.glob("*.parquet")), target_dir / f"{symbol}_{period.year}_q{period.quarter}.parquet")
        old = pd.read_parquet(target) if target.exists() else pd.DataFrame(columns=incoming.columns)
        merged = (new_rows.copy() if old.empty else pd.concat([old, new_rows], ignore_index=True).drop_duplicates(keep="last"))
        merged = access.validate_schema(merged, "options").sort_values(["trade_date", "expiration_date", "call_put", "strike"], kind="mergesort").reset_index(drop=True)
        if target.exists() and _sha256(old) == _sha256(merged):
            continue
        _atomic_write(merged, target)
        access.update_manifest(physical_dataset, symbol, merged, target, source_version, partition, replace_existing=True)
        changed.append(f"options/symbol={symbol}/{partition}")
    latest = str(pd.to_datetime(incoming.trade_date).max().date()) if len(incoming) else None
    return ("UPDATED" if changed else "NO_OP", changed, latest)


def update_ticker(symbol: str, *, daily_frame: pd.DataFrame | None = None, options_frame: pd.DataFrame | None = None, parquet_root="data/parquet", manifest_path="data/manifests/storage_manifest.csv", options_manifest_path="data/manifests/storage_manifest.csv", source_version="incremental", physical_dataset=None, refresh_research_readiness: bool = True) -> dict[str, Any]:
    result = UpdateResult(symbol=symbol.upper(), as_of=datetime.now(timezone.utc).isoformat(), data_timestamp=datetime.now(timezone.utc).isoformat())
    try:
        if daily_frame is not None:
            result.daily_update, parts, result.latest_daily_date = update_daily_frame(symbol, daily_frame, parquet_root=parquet_root, manifest_path=manifest_path, source_version=source_version)
            result.affected_partitions.extend(parts)
        if options_frame is not None:
            result.options_update, parts, result.latest_options_date = update_options_frame(symbol, options_frame, parquet_root=parquet_root, manifest_path=options_manifest_path, source_version=source_version, physical_dataset=physical_dataset)
            result.affected_partitions.extend(parts)
        result.current_data_asof = max([x for x in (result.latest_daily_date, result.latest_options_date) if x], default=None)
        result.current_route = "options" if options_frame is not None else "daily"
        if result.affected_partitions:
            result.current_derived_artifacts_invalidated = invalidate_current_derived(symbol, result.affected_partitions)
            result.reason_codes.append("DERIVED_INVALIDATION_MARKED")
            try:
                if not refresh_research_readiness:
                    result.readiness_refresh_status = "SKIPPED_DAILY_ONLY"
                    raise StopIteration
                if Path(parquet_root) != Path("data/parquet"):
                    result.readiness_refresh_status = "SKIPPED_ISOLATED_STORE"
                    raise StopIteration
                from pcs.research.ticker_readiness import preflight_ticker, persist_ticker_readiness
                refreshed = preflight_ticker(symbol, access=PCSDataAccess(manifest_path=manifest_path, parquet_root=parquet_root))
                persist_ticker_readiness(refreshed)
                result.readiness_refresh_status = "REFRESHED"
            except StopIteration:
                pass
            except Exception as exc:
                result.readiness_refresh_status = "REFRESH_FAILED"
                result.reason_codes.append("READINESS_REFRESH_FAILED:" + type(exc).__name__)
        else:
            result.reason_codes.append("NO_OP_NO_DERIVED_INVALIDATION_REQUIRED")
    except Exception as exc:
        result.status = "FAILED"
        result.reason_codes.append(type(exc).__name__)
        raise
    return result.to_dict()


def load_source(path: str | Path, symbol: str, options: bool = False) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in {".parquet", ".pq"}:
        frame = pd.read_parquet(path)
    else:
        frame = pd.read_csv(path)
    if "symbol" not in frame:
        frame["symbol"] = symbol.upper()
    return frame


__all__ = ["UpdateResult", "update_ticker", "update_daily_frame", "update_options_frame", "invalidate_current_derived", "load_source"]
