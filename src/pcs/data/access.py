"""Single PCS data-access boundary.

Physical storage is private to this module. Callers receive validated frames
and provenance metadata, never paths that they must interpret themselves.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import csv
import os
import uuid
from typing import Any

import duckdb
import pandas as pd
import yaml

from .storage_schema import OPTION_FIELDS, DAILY_FIELDS, OPTIONS_REQUIRED_FIELDS


class DataAccessError(RuntimeError):
    """Base class for canonical data boundary failures."""


class DataQualityError(DataAccessError):
    """Canonical data violates identity, schema, or quote-quality rules."""


@dataclass(frozen=True)
class SourceSpec:
    dataset: str
    symbol: str
    backend: str
    path: str
    first_date: str
    last_date: str
    row_count: int
    source_version: str
    schema_version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PCSDataAccess:
    """Canonical read/write API for market data and persisted PCS artifacts."""

    def __init__(self, manifest_path="data/manifests/storage_manifest.csv", parquet_root="data/parquet", source_routes_path="config/data_source_routes.yaml", source_routes=None):
        self.manifest_path = Path(manifest_path)
        self.provenance_manifest_path = self.manifest_path.with_name("data_provenance_manifest.csv")
        self.parquet_root = Path(parquet_root)
        self._manifest = pd.read_csv(self.manifest_path) if self.manifest_path.exists() else pd.DataFrame()
        self.source_routes_path = Path(source_routes_path) if source_routes_path else None
        self.source_routes = source_routes if source_routes is not None else self._load_source_routes(self.source_routes_path)

    @staticmethod
    def _load_source_routes(path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def _resolve_route(self, dataset: str, symbol: str) -> tuple[str, Path, Path]:
        # Explicit manifests are isolated stores (tests, fixtures, or
        # caller-provided datasets); production per-ticker routes must not
        # redirect them.
        if self.manifest_path != Path("data/manifests/storage_manifest.csv"):
            return dataset, self.manifest_path, self.parquet_root
        routes = self.source_routes.get(dataset, {}).get("by_symbol", {})
        route = routes.get(self._symbol(symbol), {})
        return (
            str(route.get("dataset", dataset)),
            Path(route.get("manifest_path", self.manifest_path)),
            Path(route.get("parquet_root", self.parquet_root)),
        )

    @staticmethod
    def _read_manifest(path: Path) -> pd.DataFrame:
        return pd.read_csv(path) if path.exists() else pd.DataFrame()

    @staticmethod
    def _symbol(symbol: str) -> str:
        value = str(symbol).strip().upper()
        if not value:
            raise ValueError("symbol must be a non-empty ticker")
        return value

    def resolve_source(self, dataset: str, symbol: str, start_date=None, end_date=None) -> SourceSpec:
        symbol = self._symbol(symbol)
        resolved_dataset, manifest_path, parquet_root = self._resolve_route(dataset, symbol)
        manifest = self._manifest if manifest_path == self.manifest_path else self._read_manifest(manifest_path)
        if manifest.empty:
            raise FileNotFoundError(f"canonical {dataset} source unavailable for {symbol}")
        dataset_match = manifest.dataset == resolved_dataset
        if resolved_dataset.startswith("options_v2"):
            dataset_match = dataset_match | (manifest.dataset == "options")
        rows = manifest[
            dataset_match
            & (manifest.symbol.astype(str).str.upper() == symbol)
            & (manifest.status == "SUCCESS")
        ]
        if rows.empty:
            raise FileNotFoundError(f"canonical {dataset} source unavailable for {symbol}")
        lo, hi = pd.Timestamp(rows.min_date.min()), pd.Timestamp(rows.max_date.max())
        if resolved_dataset.startswith("options_v2"):
            # A v2 manifest may predate a validated incremental append. Use
            # physical active partitions to extend coverage metadata without
            # changing the manifest or accepting arbitrary descendants.
            v2_files = [str(p) for p in (parquet_root / "options_v2" / f"symbol={symbol}").glob("year=*/quarter=*/*.parquet")]
            if v2_files:
                physical = duckdb.connect().execute(
                    "select min(trade_date), max(trade_date) from read_parquet(?)", [v2_files]
                ).fetchone()
                if physical[0] is not None:
                    lo, hi = min(lo, pd.Timestamp(physical[0])), max(hi, pd.Timestamp(physical[1]))
        if (start_date is not None and pd.Timestamp(start_date) < lo) or (end_date is not None and pd.Timestamp(end_date) > hi):
            raise ValueError(f"requested {symbol} {dataset} range is outside {lo.date()}..{hi.date()}")
        if resolved_dataset == "options":
            path = parquet_root / "options" / f"symbol={symbol}" / "year=*" / "quarter=*" / "*.parquet"
        elif resolved_dataset.startswith("options_v2"):
            # v2 is partitioned exactly one level below symbol=... .  Do not
            # use ** here: recursive discovery can make DuckDB scan the same
            # logical partition through overlapping descendants.
            symbol_root = parquet_root / resolved_dataset / f"symbol={symbol}"
            active_files: list[Path] = []
            requested_periods = None
            if start_date is not None and end_date is not None:
                requested_periods = set(pd.period_range(pd.Timestamp(start_date), pd.Timestamp(end_date), freq="Q"))             
            for partition_dir in symbol_root.glob("year=*/quarter=*"):
                if requested_periods is not None:
                    try:
                        year = int(partition_dir.parent.name.split("=", 1)[1])
                        quarter = int(partition_dir.name.split("=", 1)[1])
                        if pd.Period(f"{year}Q{quarter}") not in requested_periods:
                            continue
                    except (ValueError, IndexError):
                        raise DataQualityError(f"invalid option partition directory: {partition_dir}")
                files = list(partition_dir.glob("*.parquet"))
                if len(files) > 1:
                    raise DataQualityError(
                        f"multiple active option files for {symbol} {partition_dir.name}: "
                        f"{[str(x) for x in files]}"
                    )
                active_files.extend(files)
            # Prefer manifest-listed physical files for the requested
            # partitions when the manifest provides a complete, existing set.
            manifest_files: list[Path] = []
            if "parquet_path" in rows.columns:
                for raw_path in rows.parquet_path.dropna().astype(str):
                    candidate = Path(raw_path)
                    if not candidate.is_absolute():
                        candidate = Path.cwd() / candidate
                    if candidate.exists() and candidate.suffix == ".parquet":
                        manifest_files.append(candidate)
                if manifest_files and requested_periods is not None:
                    manifest_periods = {pd.Period(f"{p.parent.parent.name.split('=', 1)[1]}Q{p.parent.name.split('=', 1)[1]}") for p in manifest_files}
                    if requested_periods.issubset(manifest_periods):
                        active_files = [p for p in manifest_files if pd.Period(f"{p.parent.parent.name.split('=', 1)[1]}Q{p.parent.name.split('=', 1)[1]}") in requested_periods]
            if not active_files:
                raise FileNotFoundError(f"no active option partitions for {symbol}")
            # Pass an explicit file list to DuckDB; this is the authoritative
            # discovery result and cannot be re-expanded recursively.
            path = Path(";".join(str(x) for x in sorted(active_files)))
        elif resolved_dataset == "daily":
            path = parquet_root / "daily" / f"symbol={symbol}" / "**" / "*.parquet"
        else:
            path = parquet_root / resolved_dataset / f"symbol={symbol}" / "**" / "*.parquet"
        schema = rows.schema_version.iloc[0] if "schema_version" in rows else "1"
        return SourceSpec(resolved_dataset, symbol, "partitioned_parquet", str(path).replace("\\", "/"), str(lo.date()), str(hi.date()), int(rows.row_count.sum()), f"storage_manifest_v1:{manifest_path}", str(schema))

    def validate_schema(self, frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
        is_options = dataset == "options" or dataset.startswith("options")
        required = OPTIONS_REQUIRED_FIELDS if is_options else DAILY_FIELDS if dataset == "daily" else list(frame.columns)
        missing = [c for c in required if c not in frame.columns]
        if missing:
            raise DataQualityError(f"{dataset} missing required columns: {missing}")
        out = frame.copy()
        if is_options:
            out["symbol"] = out["symbol"].astype(str).str.upper()
            for c in ("trade_date", "expiration_date"):
                out[c] = pd.to_datetime(out[c], errors="coerce").dt.date
            out["call_put"] = out["call_put"].astype(str).str.lower()
            out["strike"] = pd.to_numeric(out["strike"], errors="coerce")
            if out[required].isna().any().any():
                raise DataQualityError("options contain null identity fields")
            key = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
            dup = out[out.duplicated(key, keep=False)]
            if not dup.empty:
                quote = [c for c in ("last", "bid", "ask") if c in out.columns]
                conflicting = dup.groupby(key, dropna=False)[quote].nunique(dropna=False).max(axis=1).gt(1)
                if conflicting.any():
                    raise DataQualityError(f"ambiguous option quote keys: {int(conflicting.sum())}")
                raise DataQualityError(f"duplicate option keys: {int(len(dup))}")
        return out

    def validate_coverage(self, frame: pd.DataFrame, symbol: str, start_date=None, end_date=None, date_column="trade_date") -> None:
        symbol = self._symbol(symbol)
        if "symbol" in frame and set(frame.symbol.astype(str).str.upper()) - {symbol}:
            raise DataQualityError(f"ticker isolation failure for {symbol}")
        if date_column in frame and len(frame):
            dates = pd.to_datetime(frame[date_column])
            if start_date is not None and dates.min() < pd.Timestamp(start_date):
                raise DataQualityError("read returned data before requested start date")
            if end_date is not None and dates.max() > pd.Timestamp(end_date):
                raise DataQualityError("read returned data after requested end date")

    def read(self, dataset: str, symbol: str, start_date=None, end_date=None) -> pd.DataFrame:
        spec = self.resolve_source(dataset, symbol, start_date, end_date)
        con = duckdb.connect()
        try:
            column = "trade_date" if spec.dataset == "options" or spec.dataset.startswith("options") else "date"
            parquet_input: str | list[str] = spec.path
            if ";" in spec.path:
                parquet_input = spec.path.split(";")
            out = con.execute(f"SELECT * FROM read_parquet(?, hive_partitioning=true) WHERE symbol=? AND {column} BETWEEN ? AND ?", [parquet_input, spec.symbol, pd.Timestamp(start_date or spec.first_date).date(), pd.Timestamp(end_date or spec.last_date).date()]).fetchdf()
        finally:
            con.close()
        self.validate_coverage(out, spec.symbol, start_date, end_date, column)
        if spec.dataset == "options" or spec.dataset.startswith("options"):
            out = self.validate_schema(out, spec.dataset)
        return out

    def read_option_chain(self, symbol: str, trade_date, expiration=None) -> pd.DataFrame:
        out = self.read("options", symbol, trade_date, trade_date)
        if expiration is not None:
            out = out[out.expiration_date == pd.Timestamp(expiration).date()]
        return out.reset_index(drop=True)

    def read_quotes(self, symbol: str, start_date, end_date, expirations=None, strikes=None) -> pd.DataFrame:
        out = self.read("options", symbol, start_date, end_date)
        if expirations is not None:
            out = out[out.expiration_date.isin([pd.Timestamp(x).date() for x in expirations])]
        if strikes is not None:
            out = out[out.strike.isin([float(x) for x in strikes])]
        return out.reset_index(drop=True)

    def read_partition(self, dataset: str, symbol: str, partition: str, filename: str | None = None) -> pd.DataFrame:
        """Read one physical partition through the data-access boundary."""
        symbol = self._symbol(symbol)
        path = self.parquet_root / dataset / f"symbol={symbol}" / partition
        path = path / (filename or f"{symbol}_{partition.replace('=', '_').replace('/', '_')}.parquet")
        if not path.exists():
            return pd.DataFrame()
        out = pd.read_parquet(path)
        return self.validate_schema(out, dataset)

    def read_prices(self, symbol: str, start_date=None, end_date=None) -> pd.DataFrame:
        return self.read("daily", symbol, start_date, end_date)

    def write(self, frame: pd.DataFrame, dataset: str, symbol: str, partition: str, *, source_version: str, allow_overwrite=False, update_manifest=True, filename=None, replace_manifest=False) -> Path:
        symbol = self._symbol(symbol)
        checked = self.validate_schema(frame, dataset)
        self.validate_coverage(checked, symbol, date_column="trade_date" if dataset == "options" or dataset.startswith("options") else "date")
        target = self.parquet_root / dataset / f"symbol={symbol}" / partition
        target.mkdir(parents=True, exist_ok=True)
        path = target / (filename or f"{symbol}_{partition.replace('=', '_').replace('/', '_')}.parquet")
        if path.exists() and not allow_overwrite:
            raise FileExistsError(f"trusted canonical target exists: {path}")
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        checked.to_parquet(tmp, index=False)
        verify = pd.read_parquet(tmp)
        if len(verify) != len(checked):
            tmp.unlink(missing_ok=True)
            raise DataQualityError("row-count verification failed after write")
        os.replace(tmp, path)
        if update_manifest:
            self.update_manifest(dataset, symbol, checked, path, source_version, partition, replace_existing=replace_manifest)
        return path

    def append(self, frame: pd.DataFrame, dataset: str, symbol: str, partition: str, *, source_version: str) -> Path:
        if (self.parquet_root / dataset / f"symbol={self._symbol(symbol)}" / partition).exists():
            raise FileExistsError("append requires a new partition; refusing trusted overwrite")
        return self.write(frame, dataset, symbol, partition, source_version=source_version)

    def upsert(self, *args, **kwargs):
        raise NotImplementedError("upsert is intentionally disabled for trusted canonical data; write a new version")

    def write_partition(self, frame, dataset, symbol, partition, *, source_version, allow_overwrite=False, update_manifest=True, filename=None, replace_manifest=False):
        return self.write(frame, dataset, symbol, partition, source_version=source_version, allow_overwrite=allow_overwrite, update_manifest=update_manifest, filename=filename, replace_manifest=replace_manifest)

    def update_manifest(self, dataset, symbol, frame, path, source_version, partition=None, replace_existing=False):
        fields = ["dataset","symbol","source_file","source_size","source_modified_time","row_count","min_date","max_date","year","quarter","parquet_path","schema_version","import_timestamp","status"]
        row = {k: None for k in fields}; row.update(dataset=dataset, symbol=self._symbol(symbol), source_file=source_version, row_count=len(frame), parquet_path=str(path), schema_version="1", import_timestamp=datetime.now(timezone.utc).isoformat(), status="SUCCESS")
        if partition:
            for part in str(partition).split('/'):
                if '=' in part:
                    key, value = part.split('=', 1)
                    if key in row: row[key] = value
        date_col = "trade_date" if dataset == "options" or dataset.startswith("options") else "date"
        if len(frame): row.update(min_date=str(pd.to_datetime(frame[date_col]).min().date()), max_date=str(pd.to_datetime(frame[date_col]).max().date()))
        current = self._manifest.copy() if not self._manifest.empty else pd.DataFrame(columns=fields)
        for field in fields:
            if field not in current:
                current[field] = None
        if replace_existing:
            current = current[~((current.dataset == dataset)
                                & (current.symbol.astype(str).str.upper() == self._symbol(symbol))
                                & (current.year.astype(str) == str(row["year"]))
                                & (current.quarter.astype(str) == str(row["quarter"])))]
        updated = pd.concat([current[fields], pd.DataFrame([row], columns=fields)], ignore_index=True)
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_name(f".{self.manifest_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            updated.to_csv(tmp, index=False)
            os.replace(tmp, self.manifest_path)
        finally:
            tmp.unlink(missing_ok=True)
        self._manifest = updated

    def record_provenance(self, record: dict[str, Any], path: str | Path | None = None) -> Path:
        """Atomically append one machine-readable source/provenance record."""
        target = Path(path) if path is not None else self.provenance_manifest_path
        current = pd.read_csv(target) if target.exists() else pd.DataFrame()
        updated = pd.concat([current, pd.DataFrame([record])], ignore_index=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            updated.to_csv(tmp, index=False)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return target

    def get_provenance(self, dataset: str, symbol: str) -> list[dict[str, Any]]:
        resolved_dataset, manifest_path, _ = self._resolve_route(dataset, symbol)
        manifest = self._manifest if manifest_path == self.manifest_path else self._read_manifest(manifest_path)
        if manifest.empty: return []
        rows = manifest[(manifest.dataset == resolved_dataset) & (manifest.symbol.astype(str).str.upper() == self._symbol(symbol))]
        return rows.to_dict("records")

    def write_artifact(self, frame: pd.DataFrame, namespace: str, name: str, root="data/parquet", metadata=None) -> Path:
        """Persist a derived/research frame under an explicit non-canonical namespace."""
        metadata = metadata or {}
        out = frame.copy()
        for key, value in metadata.items(): out[key] = value
        out["created_at"] = metadata.get("created_at", datetime.now(timezone.utc).isoformat())
        target = Path(root) / namespace
        target.mkdir(parents=True, exist_ok=True)
        path = target / name
        if path.suffix != ".parquet": path = path.with_suffix(".parquet")
        if path.exists(): raise FileExistsError(f"trusted artifact target exists: {path}")
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        out.to_parquet(tmp, index=False)
        if len(pd.read_parquet(tmp)) != len(out):
            tmp.unlink(missing_ok=True)
            raise DataQualityError("artifact row-count verification failed")
        os.replace(tmp, path)
        return path

    def read_artifact(self, namespace: str, name: str, root="data/parquet", filters=None) -> pd.DataFrame:
        path = Path(root) / namespace / name
        if not path.exists(): return pd.DataFrame()
        out = pd.read_parquet(path)
        for key, value in (filters or {}).items():
            if key not in out: raise DataQualityError(f"artifact filter column missing: {key}")
            out = out[out[key] == value]
        return out.reset_index(drop=True)


__all__ = ["PCSDataAccess", "SourceSpec", "DataAccessError", "DataQualityError"]
