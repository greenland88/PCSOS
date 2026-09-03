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
import json
import hashlib
from contextlib import contextmanager
from typing import Any

import duckdb
import pandas as pd

@dataclass(frozen=True)
class PromotionReceipt:
    dataset: str; ticker: str; generation_id: str; promoted_partitions: tuple[str, ...]
    checksum: str; row_count: int; manifest_version: str; promotion_timestamp: str
    path: str
    read_back_generation_id: str = ""
    read_back_checksum: str = ""
    read_back_row_count: int = 0
    staging_generation_id: str = ""
    manifest_active_generation_id: str = ""
    source_lineage: tuple[dict[str, Any], ...] = ()
    created_at: str = ""
    partition_ids: tuple[str, ...] = ()
    dataset_fingerprint: str = ""
    snapshot_descriptor: dict[str, Any] | None = None
    @property
    def promoted_generation_id(self) -> str:
        return self.generation_id
    @property
    def dataset_type(self) -> str:
        return self.dataset
    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_type": self.dataset,
            "ticker": self.ticker,
            "staging_generation_id": self.staging_generation_id or self.generation_id,
            "promoted_generation_id": self.generation_id,
            "manifest_active_generation_id": self.manifest_active_generation_id or self.generation_id,
            "read_back_generation_id": self.read_back_generation_id,
            "promoted_partitions": list(self.promoted_partitions),
            "partition_ids": list(self.partition_ids or self.promoted_partitions),
            "checksum": self.checksum,
            "read_back_checksum": self.read_back_checksum,
            "row_count": self.row_count,
            "read_back_row_count": self.read_back_row_count,
            "manifest_version": self.manifest_version,
            "source_lineage": list(self.source_lineage),
            "created_at": self.created_at,
            "promotion_timestamp": self.promotion_timestamp,
            "path": self.path,
            "dataset_fingerprint": self.dataset_fingerprint,
            "snapshot_descriptor": self.snapshot_descriptor,
        }
    def __fspath__(self): return self.path
    def __str__(self): return self.path
    def __getattr__(self, name): return getattr(Path(self.path), name)
import yaml

from .storage_schema import OPTION_FIELDS, DAILY_FIELDS, OPTIONS_REQUIRED_FIELDS, audit_option_frame
from .executable_boundary import resolve_executable_start_date
from .correctness_gate import validate_price_input


class DataAccessError(RuntimeError):
    """Base class for canonical data boundary failures."""


class DataQualityError(DataAccessError):
    """Canonical data violates identity, schema, or quote-quality rules."""


class CanonicalFileAccessError(DataQualityError):
    """Registered canonical files exist but cannot be opened by this process."""

    reason_code = "CANONICAL_FILE_ACCESS_DENIED"

    def __init__(self, dataset: str, symbol: str, failures: list[dict[str, str]]):
        self.dataset = str(dataset)
        self.symbol = str(symbol).upper()
        self.failures = tuple(dict(item) for item in failures)
        super().__init__(
            f"{self.reason_code}:{self.dataset}:{self.symbol}:{list(self.failures)[:3]}"
        )


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

@dataclass(frozen=True)
class DatasetReadinessResult:
    """Machine-readable result of generic data-layer admission/recovery."""
    dataset: str
    symbol: str
    status: str
    reason_codes: tuple[str, ...] = ()
    coverage: tuple[dict[str, Any], ...] = ()
    sources_checked: tuple[dict[str, Any], ...] = ()
    stages: tuple[dict[str, Any], ...] = ()
    canonical_dataset: str | None = None
    run_id: str = ""
    request_id: str = ""


class PCSDataAccess:
    """Canonical read/write API for market data and persisted PCS artifacts."""

    def __init__(self, manifest_path="data/manifests/storage_manifest.csv", parquet_root="data/parquet", source_routes_path="config/data_source_routes.yaml", source_routes=None, routing_mode: str | None = None):
        default_manifest = Path("data/manifests/storage_manifest.csv")
        if routing_mode is None:
            # Backward-compatible inference for existing isolated fixtures.
            routing_mode = "canonical" if Path(manifest_path).resolve() == default_manifest.resolve() else "isolated"
        if routing_mode not in {"canonical", "isolated"}:
            raise ValueError(f"UNKNOWN_ROUTING_MODE:{routing_mode}")
        if routing_mode == "canonical" and Path(manifest_path).resolve() != default_manifest.resolve():
            raise DataAccessError("CANONICAL_MODE_REQUIRES_DEFAULT_MANIFEST")
        self.routing_mode = routing_mode
        self.manifest_path = Path(manifest_path)
        self.provenance_manifest_path = self.manifest_path.with_name("data_provenance_manifest.csv")
        self.parquet_root = Path(parquet_root)
        self._manifest = pd.read_csv(self.manifest_path) if self.manifest_path.exists() else pd.DataFrame()
        self.source_routes_path = Path(source_routes_path) if source_routes_path else None
        self.source_routes = source_routes if source_routes is not None else self._load_source_routes(self.source_routes_path)
        metadata_path = self.manifest_path.with_name("price_basis_metadata.csv")
        if not metadata_path.exists() and self.manifest_path.parent == Path("data/manifests"):
            metadata_path = Path("data/manifests/price_basis_metadata.csv")
        self._price_basis_metadata = pd.read_csv(metadata_path) if metadata_path.exists() else pd.DataFrame()
        from .generation_cache import GenerationCache
        self.generation_cache = GenerationCache()

    @classmethod
    def canonical(cls, **kwargs):
        kwargs["routing_mode"] = "canonical"
        return cls(**kwargs)

    def ensure_ready(self, dataset: str, symbol: str, start_date=None,
                     end_date=None, as_of=None, required_warmup_sessions: int = 0) -> DatasetReadinessResult:
        """Ensure a dataset through the shared control plane, then re-read it.

        ``options_v2`` is a logical request; routing and source selection remain
        generic and are owned by ``MarketDataControlPlane``.

        The historical positional form is ``(dataset, symbol, start, end,
        as_of)``.  The public decision form is also accepted as
        ``(symbol, dataset, as_of, required_warmup_sessions)``; the latter is
        normalized here so every caller still uses the same control plane.
        """
        from .control_plane import MarketDataControlPlane, ImportStatus
        # Normalize the user-facing decision entry point without breaking
        # existing data/control-plane callers. Dataset names are controlled
        # vocabulary, so this is not ticker-specific logic.
        known_datasets = {"daily", "events", "options", "options_v2", "fundamentals"}
        if str(dataset).lower() not in known_datasets and str(symbol).lower() in known_datasets:
            public_symbol, public_dataset = dataset, symbol
            public_as_of = start_date
            public_warmup = end_date if end_date is not None else required_warmup_sessions
            dataset, symbol = public_dataset, public_symbol
            start_date, end_date, as_of = None, None, public_as_of
            required_warmup_sessions = int(public_warmup or 0)
        logical = "options" if str(dataset) == "options_v2" else str(dataset)
        req = {"datasets": (logical,), "start": start_date, "end": end_date,
               "as_of": as_of, "decision_as_of": as_of,
               "symbol": str(symbol).upper(),
               "required_warmup_sessions": required_warmup_sessions}
        result = MarketDataControlPlane(access=self).ensure_market_data(req)
        status = str(result.status)
        if status in {ImportStatus.READY.value, ImportStatus.ALREADY_COMPLETE.value}:
            final = "DATASET_READY"
        elif "PROVIDER_PROBE_TIMEOUT" in result.reason_codes:
            final = "PROVIDER_PROBE_TIMEOUT"
        elif any("SOURCE" in str(x) and "UNAVAILABLE" in str(x) for x in result.reason_codes):
            final = "SOURCE_TRULY_UNAVAILABLE"
        elif any("VALID" in str(x) for x in result.reason_codes):
            final = "VALIDATION_FAILED"
        elif any("PROMOT" in str(x) for x in result.reason_codes):
            final = "PROMOTION_FAILED"
        else:
            final = "ROUTE_MISSING_RECOVERABLE" if status == ImportStatus.PARTIAL.value else "VALIDATION_FAILED"
        return DatasetReadinessResult(logical, str(symbol).upper(), final,
            tuple(dict.fromkeys((final, *result.reason_codes))),
            tuple(result.provider_coverage), tuple(result.source_inventory),
            tuple({"dataset": logical, "action": x} for x in result.stages.items()),
            logical if final == "DATASET_READY" else None,
            result.run_id, result.request_id)

    @classmethod
    def isolated(cls, *, manifest_path, **kwargs):
        kwargs["manifest_path"] = manifest_path
        kwargs["routing_mode"] = "isolated"
        return cls(**kwargs)

    def get_price_basis(self, dataset: str, symbol: str) -> dict[str, Any]:
        """Return explicit basis metadata; absent metadata is UNKNOWN."""
        if self._price_basis_metadata.empty:
            return {"price_basis": "UNKNOWN", "validation_status": "UNKNOWN"}
        rows = self._price_basis_metadata[
            self._price_basis_metadata.dataset.astype(str).eq(str(dataset))
            & self._price_basis_metadata.symbol.astype(str).str.upper().eq(self._symbol(symbol))
        ]
        if len(rows) != 1:
            return {"price_basis": "UNKNOWN", "validation_status": "UNKNOWN"}
        return rows.iloc[0].to_dict()

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
        if self.routing_mode == "isolated":
            if dataset == "options":
                manifest = self._read_manifest(self.manifest_path)
                if not manifest.empty and "dataset" in manifest and manifest.dataset.astype(str).str.startswith("options_").any():
                    dataset = str(manifest.loc[manifest.dataset.astype(str).str.startswith("options_"), "dataset"].iloc[0])
            return dataset, self.manifest_path, self.parquet_root
        # ``options`` is the logical name and resolves through the configured
        # per-ticker route. Physical dataset names are an internal routing
        # concern and are not part of normal caller behavior.
        route_dataset = dataset
        routes = self.source_routes.get(route_dataset, {}).get("by_symbol", {})
        if not routes and dataset in {"options_v2", "options_v3"}:
            # Migration/repair compatibility for explicit physical callers.
            route_dataset = "options"
            routes = self.source_routes.get(route_dataset, {}).get("by_symbol", {})
        route = routes.get(self._symbol(symbol), {})
        if dataset in {"options", "options_v2", "options_v3"} and not route:
            # Canonical v2 is manifest-driven by default.  Keep explicit
            # routes for true dataset/version exceptions (for example v3),
            # but do not require a hand-written ticker entry when the ticker
            # is already present in the active canonical v2 manifest.
            if dataset in {"options", "options_v2", "options_v3"}:
                candidates = [self.manifest_path]
                if self.manifest_path == Path("data/manifests/storage_manifest.csv"):
                    candidates.extend([
                        Path("data/manifests/storage_manifest_options_v2.csv"),
                        Path("data/manifests/storage_manifest_v2.csv"),
                        Path("data/manifests/storage_manifest_options_v3.csv"),
                    ])
                matches = []
                identities = set()
                for candidate_manifest in candidates:
                    manifest = self._read_manifest(candidate_manifest)
                    if manifest.empty or "dataset" not in manifest.columns:
                        continue
                    allowed_datasets = ({"options", "options_v2", "options_v3"}
                                        if dataset == "options" else {dataset})
                    found = manifest[
                        manifest.dataset.astype(str).isin(allowed_datasets)
                        & manifest.symbol.astype(str).str.upper().eq(self._symbol(symbol))
                        & manifest.status.astype(str).str.upper().eq("SUCCESS")
                    ]
                    if not found.empty:
                        for _, row in found.iterrows():
                            # Schema/source versions are partition metadata,
                            # not logical-route identities. A canonical
                            # dataset may legitimately evolve from v1 to v2
                            # across quarters without becoming two routes.
                            identity = (str(row.dataset), str(candidate_manifest))
                            identities.add(identity)
                            matches.append((str(row.dataset), candidate_manifest))
                physical_versions = {name for name, _ in matches if name in {"options", "options_v2", "options_v3"}}
                if len(physical_versions) > 1 or len(identities) > 1:
                    raise DataAccessError(
                        f"AMBIGUOUS_CANONICAL_OPTIONS_ROUTE: symbol={self._symbol(symbol)} "
                        f"identities={sorted(identities)}"
                    )
                if matches:
                    dataset_name, manifest_name = sorted(matches, key=lambda x: (x[0], str(x[1])))[0]
                    return dataset_name, manifest_name, self.parquet_root
            raise DataAccessError(
                f"canonical route unavailable: requested_dataset={dataset} "
                f"symbol={self._symbol(symbol)} legacy_fallback_used=NO "
                f"reason=DATA_NOT_INGESTED_OR_CANONICAL_MANIFEST_MISSING"
            )
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

    @staticmethod
    @contextmanager
    def _file_lock(target: Path):
        """Process/thread lock around metadata read-modify-write transactions."""
        lock_path = target.with_name(target.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            handle.seek(0)
            try:
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            yield
        finally:
            try:
                unlock()
            except Exception:
                pass
            handle.close()

    @staticmethod
    def _norm(value):
        if pd.isna(value):
            return None
        if isinstance(value, (pd.Timestamp, datetime)):
            return pd.Timestamp(value).isoformat()
        return value.item() if hasattr(value, "item") else value

    @classmethod
    def _semantic_record(cls, record: dict[str, Any]) -> dict[str, Any]:
        volatile = {"created_at", "promoted_at", "import_timestamp", "run_id", "request_id", "timestamp", "updated_at"}
        out = {}
        for k, v in record.items():
            if str(k) in volatile or str(k) == "provenance_key":
                continue
            value = cls._norm(v)
            if isinstance(value, float) and value.is_integer(): value = int(value)
            out[str(k)] = None if value is None else str(value)
        return out

    @classmethod
    def _provenance_key(cls, record: dict[str, Any]) -> str:
        identity_fields = ("dataset", "symbol", "partition", "year", "quarter", "source_version", "source", "source_path", "source_file", "source_member", "source_sha256", "raw_sha256", "artifact_sha256", "final_quarter_checksum", "parquet_path")
        identity = {k: cls._norm(record.get(k)) for k in identity_fields if record.get(k) is not None and not pd.isna(record.get(k))}
        if not identity:
            identity = cls._semantic_record(record)
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def semantic_content_hash(cls, frame: pd.DataFrame) -> str:
        volatile = {"created_at", "import_timestamp", "run_id", "request_id", "timestamp", "updated_at", "semantic_hash"}
        out = frame.drop(columns=[c for c in frame.columns if c in volatile], errors="ignore").copy()
        out = out.reindex(sorted(out.columns), axis=1)
        for col in out.columns:
            if pd.api.types.is_datetime64_any_dtype(out[col]):
                out[col] = out[col].map(lambda x: x.isoformat() if pd.notna(x) else None)
        if len(out):
            out = out.sort_values(list(out.columns), kind="mergesort", na_position="first").reset_index(drop=True)
        payload = out.to_json(orient="records", date_format="iso", default_handler=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def resolve_source(self, dataset: str, symbol: str, start_date=None, end_date=None) -> SourceSpec:
        symbol = self._symbol(symbol)
        resolved_dataset, manifest_path, parquet_root = self._resolve_route(dataset, symbol)
        # The canonical manifest is atomically replaced by control-plane
        # promotions and may be updated by another worker. Never admit a
        # route from a process-local snapshot after a repair/re-check cycle.
        manifest = self._read_manifest(manifest_path)
        if manifest.empty:
            if dataset.startswith("options_v2"):
                raise DataAccessError(
                    f"canonical route unavailable: requested_dataset={dataset} "
                    f"symbol={symbol} legacy_fallback_used=NO"
                )
            if dataset == "options":
                raise DataAccessError(
                    f"canonical route unavailable: requested_dataset=options "
                    f"symbol={symbol} legacy_fallback_used=NO"
                )
            raise FileNotFoundError(f"canonical {dataset} source unavailable for {symbol}")
        dataset_match = manifest.dataset == resolved_dataset
        if resolved_dataset.startswith("options_v2"):
            dataset_match = dataset_match | (manifest.dataset == "options")
        rows = manifest[
            dataset_match
            & (manifest.symbol.astype(str).str.upper() == symbol)
            & (manifest.status == "SUCCESS")
        ]
        if "lifecycle_status" in rows.columns:
            rows = rows[rows.lifecycle_status.astype(str).str.upper().ne("SUPERSEDED")]
        if resolved_dataset == "daily" and not rows.empty and "active_generation" in rows:
            # A promoted partition supersedes its legacy physical files.
            # Leaving both in the glob creates false duplicate-date failures.
            active_ids = rows.active_generation.astype(str).str.strip()
            active_rows = rows[rows.active_generation.notna() & active_ids.ne("") & active_ids.str.lower().ne("nan")]
            if not active_rows.empty:
                active_rows = active_rows.copy()
                active_rows["_lo"] = pd.to_datetime(active_rows.min_date, errors="coerce")
                active_rows["_hi"] = pd.to_datetime(active_rows.max_date, errors="coerce")
                overlap_pairs = []
                for left_idx, left in active_rows.iterrows():
                    for right_idx, right in active_rows.iterrows():
                        if left_idx >= right_idx or pd.isna(left["_lo"]) or pd.isna(right["_lo"]):
                            continue
                        if left["_lo"] <= right["_hi"] and right["_lo"] <= left["_hi"]:
                            left_gid, right_gid = str(left.active_generation), str(right.active_generation)
                            overlap_pairs.append((left_gid, right_gid))
                if overlap_pairs:
                    raise DataQualityError(f"ACTIVE_GENERATION_OVERLAP_CONFLICT:{symbol}:{overlap_pairs[:3]}")
                rows = pd.concat([rows[~rows.index.isin(active_rows.index)], active_rows.drop(columns=["_lo", "_hi"])], ignore_index=True)
        if resolved_dataset.startswith("options_v2"):
            integrity = self.audit_manifest_physical_integrity(
                symbol, dataset=resolved_dataset, start_date=start_date, end_date=end_date
            )
            blocking = [x for x in integrity if x["blocking"]]
            if blocking:
                raise DataQualityError(
                    f"MANIFEST_PHYSICAL_MISMATCH:{symbol}:{blocking[:3]}"
                )
        # Daily partitions migrated by the canonical universe pipeline may be
        # recorded in the migration manifest before they are folded into the
        # compact storage manifest.  Resolve that manifest generically by
        # ticker; never guess a ticker-specific path or bypass PCSDataAccess.
        if rows.empty and resolved_dataset == "daily" and self.manifest_path == Path("data/manifests/storage_manifest.csv"):
            migration_path = self.manifest_path.with_name("daily_universe_migration.csv")
            migration = self._read_manifest(migration_path)
            migrated = migration[
                migration.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(symbol)
                & migration.get("status", pd.Series(dtype=str)).astype(str).eq("SUCCESS")
            ] if not migration.empty else pd.DataFrame()
            if not migrated.empty:
                symbol_root = parquet_root / "daily" / f"symbol={symbol}"
                files = sorted(symbol_root.glob("year=*/*.parquet"))
                if files:
                    physical = duckdb.connect().execute(
                        "select min(date), max(date), count(*) from read_parquet(?)", [[str(p) for p in files]]
                    ).fetchone()
                    if physical[0] is not None:
                        return SourceSpec("daily", symbol, "partitioned_parquet",
                            ";".join(str(p).replace("\\", "/") for p in files),
                            str(pd.Timestamp(physical[0]).date()), str(pd.Timestamp(physical[1]).date()),
                            int(physical[2]), "daily_universe_migration.csv", "1")
        if rows.empty:
            raise FileNotFoundError(f"canonical {dataset} source unavailable for {symbol}")
        lo, hi = pd.Timestamp(rows.min_date.min()), pd.Timestamp(rows.max_date.max())
        # A readiness/executable window may begin before a ticker's physical
        # route starts. Clamp the lower bound to available canonical coverage;
        # retain fail-closed behavior for an end date beyond source coverage.
        if end_date is not None and pd.Timestamp(end_date) < lo:
            raise ValueError(f"requested {symbol} {dataset} range is outside {lo.date()}..{hi.date()}")
        if start_date is not None and pd.Timestamp(start_date) < lo:
            migration_available = False
            if resolved_dataset == "daily" and self.manifest_path == Path("data/manifests/storage_manifest.csv"):
                migration_path = self.manifest_path.with_name("daily_universe_migration.csv")
                migration = self._read_manifest(migration_path)
                migration_available = bool(not migration.empty and migration.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(symbol).any())
            if self.routing_mode == "canonical" and not migration_available:
                start_date = lo
            elif not migration_available:
                raise ValueError(f"requested {symbol} {dataset} range is outside {lo.date()}..{hi.date()}")
        if end_date is not None and pd.Timestamp(end_date) > hi:
            raise ValueError(f"requested {symbol} {dataset} range is outside {lo.date()}..{hi.date()}")
        if resolved_dataset == "options":
            listed = []
            path_rows = rows
            if start_date is not None and {"year", "quarter"} <= set(rows.columns):
                effective_end = pd.Timestamp(end_date) if end_date is not None else pd.Timestamp(start_date)
                requested = {(period.year, period.quarter) for period in
                             pd.period_range(pd.Timestamp(start_date), effective_end, freq="Q")}
                years = pd.to_numeric(rows.year, errors="coerce")
                quarters = pd.to_numeric(rows.quarter, errors="coerce")
                path_rows = rows[[((int(y), int(q)) in requested) if pd.notna(y) and pd.notna(q) else False
                                  for y, q in zip(years, quarters)]]
            for raw_path in path_rows.get("parquet_path", pd.Series(dtype=str)).dropna().astype(str):
                candidate = Path(raw_path)
                if not candidate.is_absolute(): candidate = Path.cwd() / candidate
                if candidate.exists(): listed.append(candidate)
            if start_date is not None and not listed:
                raise FileNotFoundError(f"no active option partitions for {symbol} in requested window")
            path = Path(";".join(str(x) for x in sorted(set(listed)))) if listed else parquet_root / "options" / f"symbol={symbol}" / "year=*" / "quarter=*" / "*.parquet"
        elif resolved_dataset.startswith("options_v2"):
            # v2 is partitioned exactly one level below symbol=... .  Do not
            # use ** here: recursive discovery can make DuckDB scan the same
            # logical partition through overlapping descendants.
            symbol_root = parquet_root / resolved_dataset / f"symbol={symbol}"
            active_files: list[Path] = [Path(str(p)) for p in rows.get("parquet_path", pd.Series(dtype=str)).dropna().tolist()]
            active_files = [p if p.is_absolute() else Path.cwd() / p for p in active_files if p.exists()]
            requested_periods = None
            if start_date is not None:
                effective_end = pd.Timestamp(end_date) if end_date is not None else pd.Timestamp(rows.max_date.max())
                requested_periods = set(pd.period_range(pd.Timestamp(start_date), effective_end, freq="Q"))
            # Only SUCCESS rows in the selected manifest define readable
            # partitions. Files merely present on disk are unregistered.
            if requested_periods is not None:
                def _period(p):
                    parts = [x.name for x in p.parents]
                    year = next((x.split('=', 1)[1] for x in parts if x.startswith('year=')), None)
                    quarter = next((x.split('=', 1)[1] for x in parts if x.startswith('quarter=')), None)
                    return pd.Period(f"{year}Q{quarter}") if year and quarter else None
                active_files = [p for p in active_files if _period(p) in requested_periods]
                # A manifest row cannot make an ambiguous physical partition
                # safe. Reject multiple direct files before any manifest
                # preference is applied; recursive descendants are excluded.
                for period in requested_periods:
                    direct = sorted((symbol_root / f"year={period.year}" / f"quarter={period.quarter}").glob("*.parquet"))
                    if len(direct) > 1:
                        raise DataQualityError(f"multiple active option files for {symbol} {period}")
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
                    manifest_periods = {_period(p) for p in manifest_files}
                    if requested_periods.issubset(manifest_periods):
                        active_files = [p for p in manifest_files if _period(p) in requested_periods]
            if not active_files:
                raise FileNotFoundError(f"no active option partitions for {symbol}")
            for manifest_row in rows.to_dict("records"):
                generation = str(manifest_row.get("active_generation") or "")
                expected_hash = str(manifest_row.get("content_hash") or "")
                if generation and not expected_hash:
                    raise DataQualityError("CANONICAL_MANIFEST_INVALID")
                if generation and expected_hash and generation != expected_hash[:len(generation)]:
                    raise DataQualityError("CANONICAL_CONTENT_HASH_MISMATCH")
            unreadable = []
            for candidate in active_files:
                try:
                    with candidate.open("rb"):
                        pass
                except (OSError, PermissionError) as exc:
                    unreadable.append({"path": str(candidate), "error": str(exc)})
            if unreadable:
                raise CanonicalFileAccessError("options", symbol, unreadable)
            # Pass an explicit file list to DuckDB; this is the authoritative
            # discovery result and cannot be re-expanded recursively.
            path = Path(";".join(str(x) for x in sorted(active_files)))
        elif resolved_dataset == "daily":
            # Use manifest-selected files.  Recursive globbing would re-read
            # legacy files shadowed by an active generation.
            daily_files = []
            seen_daily_files: set[str] = set()
            for raw_path in rows.parquet_path.tolist():
                if str(raw_path) in {"", "nan", "None"}:
                    continue
                candidate = Path(str(raw_path))
                if not candidate.is_absolute():
                    candidate = Path.cwd() / candidate
                key = str(candidate.resolve()).casefold()
                if key not in seen_daily_files:
                    seen_daily_files.add(key)
                    daily_files.append(candidate)
            if not daily_files:
                raise FileNotFoundError(f"no daily partitions for {symbol}")
            # The daily-universe migration catalog is canonical provenance,
            # not an ad-hoc legacy fallback.  Its yearly physical partitions
            # provide the warmup history required by MA200/structure gates;
            # a recent incremental manifest row alone must not truncate the
            # feature window to the current year.
            if self.manifest_path == Path("data/manifests/storage_manifest.csv"):
                migration_path = self.manifest_path.with_name("daily_universe_migration.csv")
                migration = self._read_manifest(migration_path)
                migrated = migration[
                    migration.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(symbol)
                    & migration.get("status", pd.Series(dtype=str)).astype(str).eq("SUCCESS")
                ] if not migration.empty else pd.DataFrame()
                if not migrated.empty:
                    root = parquet_root / "daily" / f"symbol={symbol}"
                    migrated_files = sorted(p for p in root.glob("year=*/*.parquet")
                                            if "generations" not in p.parts)
                    known = {str(p.resolve()).casefold() for p in daily_files}
                    active_ids = rows.get("active_generation", pd.Series(dtype=str)).astype(str).str.strip()
                    active_rows = rows[~active_ids.str.lower().isin({"", "nan", "none", "null"})]
                    active_years = set(pd.to_numeric(active_rows.get("year", pd.Series(dtype=float)), errors="coerce").dropna().astype(int).tolist())
                    active_ranges = [(pd.Timestamp(r.min_date), pd.Timestamp(r.max_date)) for _, r in active_rows.iterrows() if str(r.get("min_date", "")) not in {"", "nan"} and str(r.get("max_date", "")) not in {"", "nan"}]
                    for candidate in migrated_files:
                        candidate_year = int([part for part in candidate.parts if str(part).startswith("year=")][-1].split("=", 1)[1])
                        candidate_frame = None
                        if candidate_year in active_years or any(pd.Timestamp(str(candidate_year)+"-01-01") <= hi and pd.Timestamp(str(candidate_year)+"-12-31") >= lo for lo, hi in active_ranges):
                            continue
                        if str(candidate.resolve()).casefold() not in known:
                            daily_files.append(candidate)
                    if migrated_files:
                        # ``read()`` uses SourceSpec.first_date when callers
                        # omit start_date. Expand that bound along with the
                        # physical warmup file set.
                        migrated_start = min(
                            (pd.Timestamp(year=int(p.parent.name.split("=", 1)[1]), month=1, day=1)
                             for p in migrated_files),
                            default=lo,
                        )
                        lo = min(lo, migrated_start)
            if start_date is not None or end_date is not None:
                lower_year = pd.Timestamp(start_date or lo).year
                upper_year = pd.Timestamp(end_date or hi).year
                def _daily_year(p):
                    year_parts = [part for part in p.parts if str(part).startswith("year=")]
                    return int(year_parts[-1].split("=", 1)[1]) if year_parts else None
                daily_files = [p for p in daily_files
                               if _daily_year(p) is not None and lower_year <= _daily_year(p) <= upper_year]
            path = Path(";".join(str(p) for p in sorted(daily_files)))
        else:
            path = parquet_root / resolved_dataset / f"symbol={symbol}" / "**" / "*.parquet"
        schema = rows.schema_version.iloc[0] if "schema_version" in rows else "1"
        return SourceSpec(resolved_dataset, symbol, "partitioned_parquet", str(path).replace("\\", "/"), str(lo.date()), str(hi.date()), int(rows.row_count.sum()), f"storage_manifest_v1:{manifest_path}", str(schema))

    def audit_manifest_physical_integrity(self, symbol: str, *, dataset: str = "options_v2",
                                          start_date=None, end_date=None) -> list[dict[str, Any]]:
        """Audit SUCCESS manifest rows against their declared physical files.

        A manifest row remains evidence even when its file has disappeared.
        Missing partitions before the executable boundary are warnings; a
        missing partition in the executable/requested window is blocking.
        """
        symbol = self._symbol(symbol)
        resolved_dataset, manifest_path, _ = self._resolve_route(dataset, symbol)
        manifest = self._read_manifest(manifest_path)
        if manifest.empty or "parquet_path" not in manifest.columns:
            return []
        rows = manifest[
            manifest.dataset.astype(str).eq(resolved_dataset)
            & manifest.symbol.astype(str).str.upper().eq(symbol)
            & manifest.status.astype(str).str.upper().eq("SUCCESS")
        ]
        boundary = resolve_executable_start_date(symbol, self.source_routes)
        requested_start = pd.Timestamp(start_date).date() if start_date is not None else None
        requested_end = pd.Timestamp(end_date).date() if end_date is not None else None
        findings = []
        declared_periods = set()
        for _, row in rows.iterrows():
            year, quarter = row.get("year"), row.get("quarter")
            if pd.isna(year) or pd.isna(quarter):
                continue
            period = pd.Period(f"{int(year)}Q{int(quarter)}", freq="Q")
            declared_periods.add(period)
            period_start, period_end = period.start_time.date(), period.end_time.date()
            if requested_start and period_end < requested_start or requested_end and period_start > requested_end:
                continue
            raw = str(row.get("parquet_path", ""))
            path = Path(raw) if Path(raw).is_absolute() else Path.cwd() / raw
            if not path.exists():
                in_executable = period_end >= boundary
                findings.append({
                    "symbol": symbol, "dataset": resolved_dataset,
                    "partition": f"{int(year)}Q{int(quarter)}",
                    "status": "MISSING_PHYSICAL_FILE",
                    "manifest_status": "SUCCESS",
                    "blocking": bool(in_executable),
                    "reason": "IN_EXECUTABLE_WINDOW" if in_executable else "OUTSIDE_EXECUTABLE_WINDOW",
                    "reason_code": "MANIFEST_PHYSICAL_MISMATCH",
                    "path": raw,
                })
        # A successful source advertises a date span; every quarter intersecting
        # the admitted span must have a canonical partition declaration.
        if len(rows):
            source_start = pd.Timestamp(rows.min_date.min()).date()
            source_end = pd.Timestamp(rows.max_date.max()).date()
            effective_start = max(source_start, boundary, requested_start or source_start)
            effective_end = min(source_end, requested_end or source_end)
            if effective_start <= effective_end:
                for period in pd.period_range(effective_start, effective_end, freq="Q"):
                    if period not in declared_periods:
                        blocking = period.end_time.date() >= boundary
                        findings.append({
                            "symbol": symbol, "dataset": resolved_dataset,
                            "partition": str(period), "status": "MISSING_MANIFEST_PARTITION",
                            "manifest_status": "MISSING", "blocking": blocking,
                            "reason": "IN_EXECUTABLE_WINDOW" if blocking else "OUTSIDE_EXECUTABLE_WINDOW",
                            "reason_code": "HISTORICAL_PARTITION_MISSING", "path": None,
                        })
        return findings

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
                # Every meaningful canonical payload field participates in
                # conflict detection. Restricting this to last/bid/ask would
                # allow duplicate identities differing in IV, Greeks, OI,
                # volume, or other source payload fields to pass through.
                quote = [c for c in out.columns if c not in key]
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
            elif spec.dataset.startswith("options_v2"):
                # DuckDB's Windows parameter binding treats a drive-qualified
                # single path as a glob in the scalar reader. Use the same
                # explicit-file path as multi-partition reads so canonical
                # v2 routes behave identically for one or many shards.
                parquet_input = [spec.path]
            if isinstance(parquet_input, list):
                # DuckDB's parameterized multi-file reader can duplicate rows
                # when active Parquet files were atomically replaced during a
                # long-lived process.  Read each authoritative file once and
                # UNION them explicitly; this preserves the validator's
                # strict duplicate/conflict semantics and avoids false rows.
                relations = " UNION ALL ".join(["SELECT * FROM read_parquet(?)"] * len(parquet_input))
                date_params = [pd.Timestamp(start_date or spec.first_date).date(), pd.Timestamp(end_date or spec.last_date).date()]
                # Forward-adjusted daily partitions are canonical files whose
                # symbol is encoded by the manifest/path rather than stored
                # as a column. Do not filter them out as if they were mixed
                # symbol option files.
                if spec.dataset == "daily":
                    out = con.execute(f"SELECT * FROM read_parquet(?) WHERE {column} BETWEEN ? AND ?",
                                      [parquet_input, *date_params]).fetchdf()
                    out["symbol"] = spec.symbol
                else:
                    params = list(parquet_input) + [spec.symbol, *date_params]
                    out = con.execute(f"SELECT * FROM ({relations}) AS canonical_rows WHERE symbol=? AND {column} BETWEEN ? AND ?", params).fetchdf()
            else:
                out = con.execute(f"SELECT * FROM read_parquet(?, hive_partitioning=true) WHERE symbol=? AND {column} BETWEEN ? AND ?", [parquet_input, spec.symbol, pd.Timestamp(start_date or spec.first_date).date(), pd.Timestamp(end_date or spec.last_date).date()]).fetchdf()
        finally:
            con.close()
        self.validate_coverage(out, spec.symbol, start_date, end_date, column)
        if spec.dataset == "daily":
            if "symbol" not in out.columns: out["symbol"] = spec.symbol
            out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
            if out[["symbol", "date"]].duplicated().any(): raise DataQualityError("DUPLICATE_CANONICAL_PRICE_KEY")
            if not out.date.is_monotonic_increasing: raise DataQualityError("CANONICAL_PRICE_ORDER_INVALID")
        if spec.dataset == "options" or spec.dataset.startswith("options"):
            out = self.validate_schema(out, spec.dataset)
            # The executable boundary is fail-closed even for legacy files
            # written before the quarantine layer existed.  Do not expose a
            # provably invalid row to selectors or lifecycle code.
            out, _, quality = audit_option_frame(
                out, source_version=spec.source_version,
                partition="read_boundary",
            )
            # Invalid legacy rows are excluded here; the audit/readiness path
            # reports their quarantine evidence separately.
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

    def audit_options_quality(self, symbol: str, start_date=None, end_date=None) -> dict[str, Any]:
        """Bounded canonical options audit without materializing full history.

        This is the readiness path for large option populations. It scans the
        active canonical files in DuckDB and returns only aggregate evidence;
        lifecycle selection still reads its bounded fixture window normally.
        """
        spec = self.resolve_source("options", symbol, start_date=start_date, end_date=end_date)
        files = spec.path.split(";") if ";" in spec.path else [spec.path]
        payload = ["symbol","last","bid","ask","bid_iv","ask_iv","open_interest","volume","delta","gamma","vega","theta","rho","year","quarter"]
        hash_expr = "md5(to_json(struct_pack(" + ",".join(f"{c}:={c}" for c in payload) + ")))"
        relations = " UNION ALL ".join(["SELECT * FROM read_parquet(?)"] * len(files))
        sql = f"""
        WITH raw AS ({relations}), grouped AS (
          SELECT symbol, trade_date, expiration_date, call_put, strike,
                 count(*) AS n, count(DISTINCT {hash_expr}) AS versions
          FROM raw WHERE symbol=? GROUP BY ALL
        ), q AS (
          SELECT *, date_diff('day', trade_date, expiration_date) AS dte
          FROM raw WHERE symbol=?
        )
        SELECT
          coalesce(sum(CASE WHEN n>1 THEN n ELSE 0 END),0) AS duplicate_rows,
          coalesce(count(CASE WHEN n>1 THEN 1 END),0) AS duplicate_keys,
          coalesce(count(CASE WHEN n>1 AND versions>1 THEN 1 END),0) AS conflicting_keys,
          coalesce(count(CASE WHEN n>1 AND versions=1 THEN 1 END),0) AS identical_keys,
          coalesce(count(CASE WHEN dte BETWEEN 30 AND 45 THEN 1 END),0) AS usable_rows,
          coalesce(count(CASE WHEN dte BETWEEN 30 AND 45 AND bid IS NOT NULL AND ask IS NOT NULL AND isfinite(bid) AND isfinite(ask) AND bid>=0 AND ask>=bid THEN 1 END),0) AS valid_quote_rows,
          coalesce(count(CASE WHEN dte BETWEEN 30 AND 45 AND expiration_date IS NOT NULL AND expiration_date>trade_date THEN 1 END),0) AS valid_expiration_rows,
          coalesce(count(CASE WHEN dte BETWEEN 30 AND 45 AND strike>0 THEN 1 END),0) AS valid_strike_rows
        FROM grouped CROSS JOIN q
        """
        # The aggregate is split into two independent queries to avoid a
        # grouped-key cross join; both remain bounded and scan-only.
        con = duckdb.connect()
        try:
            date_clause = (" AND trade_date >= ?" if start_date is not None else "") + (" AND trade_date <= ?" if end_date is not None else "")
            date_params = ([pd.Timestamp(start_date).date()] if start_date is not None else []) + ([pd.Timestamp(end_date).date()] if end_date is not None else [])
            params = files + [str(symbol).upper()] + date_params
            gsql = f"""WITH raw AS ({relations}) SELECT coalesce(sum(CASE WHEN n>1 THEN n ELSE 0 END),0) duplicate_rows, coalesce(count(CASE WHEN n>1 THEN 1 END),0) duplicate_keys, coalesce(count(CASE WHEN n>1 AND versions>1 THEN 1 END),0) conflicting_keys, coalesce(count(CASE WHEN n>1 AND versions=1 THEN 1 END),0) identical_keys FROM (SELECT symbol,trade_date,expiration_date,call_put,strike,count(*) n,count(DISTINCT {hash_expr}) versions FROM raw WHERE symbol=?{date_clause} GROUP BY ALL)"""
            qsql = f"""WITH raw AS ({relations}) SELECT count(*) canonical_rows,count(CASE WHEN symbol IS NULL OR trade_date IS NULL OR expiration_date IS NULL OR expiration_date<=trade_date OR call_put NOT IN ('p','c') OR strike IS NULL OR NOT isfinite(strike) OR strike<=0 OR bid IS NULL OR NOT isfinite(bid) OR bid<0 OR ask IS NULL OR NOT isfinite(ask) OR ask<0 OR ask<bid THEN 1 END) executable_invalid_rows,count(CASE WHEN date_diff('day',trade_date,expiration_date) BETWEEN 30 AND 45 THEN 1 END) usable_rows,count(CASE WHEN date_diff('day',trade_date,expiration_date) BETWEEN 30 AND 45 AND bid IS NOT NULL AND ask IS NOT NULL AND isfinite(bid) AND isfinite(ask) AND bid>=0 AND ask>=bid THEN 1 END) valid_quote_rows,count(CASE WHEN date_diff('day',trade_date,expiration_date) BETWEEN 30 AND 45 AND expiration_date IS NOT NULL AND expiration_date>trade_date THEN 1 END) valid_expiration_rows,count(CASE WHEN date_diff('day',trade_date,expiration_date) BETWEEN 30 AND 45 AND strike>0 THEN 1 END) valid_strike_rows,count(CASE WHEN symbol IS NULL OR trade_date IS NULL OR expiration_date IS NULL OR call_put IS NULL OR strike IS NULL THEN 1 END) null_identity_rows FROM raw WHERE symbol=?{date_clause}"""
            g = con.execute(gsql, params).fetchone()
            q = con.execute(qsql, params).fetchone()
        finally:
            con.close()
        quarantine_files = list((self.parquet_root / "quarantine" / spec.dataset / f"symbol={str(symbol).upper()}").rglob("*.parquet"))
        if start_date is not None or end_date is not None:
            q_start = pd.Timestamp(start_date or spec.first_date).to_period("Q")
            q_end = pd.Timestamp(end_date or spec.last_date).to_period("Q")
            quarantine_files = [
                path for path in quarantine_files
                if any(
                    part.startswith("year=") and pd.Period(
                        f"{part.split('=', 1)[1]}Q{quarter.split('=', 1)[1]}"
                    ) in set(pd.period_range(q_start, q_end, freq="Q"))
                    for part in [path.parent.parent.name]
                    for quarter in [path.parent.name]
                )
            ]
        quarantined_rows = 0; reason_breakdown = {}; affected_dates = set(); affected_partitions = set()
        for path in quarantine_files:
            try:
                qf = pd.read_parquet(path, columns=["reason_code", "trade_date", "partition"])
            except (OSError, PermissionError) as exc:
                # Quarantine evidence is diagnostic and must not make a
                # readable executable canonical population look unavailable.
                # Preserve the access failure as an audit reason instead.
                reason_breakdown["QUARANTINE_FILE_ACCESS_DENIED"] = int(
                    reason_breakdown.get("QUARANTINE_FILE_ACCESS_DENIED", 0) + 1
                )
                affected_partitions.add(str(path.parent.relative_to(self.parquet_root)))
                continue
            quarantined_rows += len(qf)
            reason_breakdown.update({k: int(reason_breakdown.get(k, 0) + v) for k, v in qf["reason_code"].value_counts().items()})
            affected_dates.update(str(x) for x in qf["trade_date"].dropna().unique())
            affected_partitions.update(str(x) for x in qf["partition"].dropna().unique())
        canonical_rows = int(q[0]); invalid_rows = int(q[1])
        return {"raw_rows": canonical_rows + quarantined_rows, "canonical_rows": canonical_rows, "quarantined_rows": quarantined_rows, "executable_rows": canonical_rows - invalid_rows, "executable_invalid_rows": invalid_rows, "reason_breakdown": reason_breakdown, "invalid_reason_breakdown": {}, "invalid_partition_breakdown": {}, "affected_dates": sorted(affected_dates), "affected_partitions": sorted(affected_partitions), "affected_percentage": (100.0 * quarantined_rows / (canonical_rows + quarantined_rows)) if canonical_rows + quarantined_rows else 0.0, "duplicate_option_rows": int(g[0]), "duplicate_option_keys": int(g[1]), "ambiguous_conflicting_option_keys": int(g[2]), "identical_duplicate_keys": int(g[3]), "usable_30_45_dte_rows": int(q[2]), "valid_30_45_dte_quote_rows": int(q[3]), "valid_30_45_dte_expiration_rows": int(q[4]), "valid_30_45_dte_strike_rows": int(q[5]), "null_identity_rows": int(q[6])}

    def read_quotes_for_windows(self, symbol: str, windows: list[tuple[object, object]], columns: list[str] | None = None) -> pd.DataFrame:
        """Read selected canonical quote windows with one bounded query."""
        if not windows:
            return pd.DataFrame(columns=columns or [])
        normalized = [(pd.Timestamp(a).date(), pd.Timestamp(b).date()) for a, b in windows]
        spec = self.resolve_source("options", symbol, min(a for a, _ in normalized), max(b for _, b in normalized))
        selected = columns or list(OPTION_FIELDS)
        # Reuse the active-manifest canonical reader.  Directly expanding
        # spec.path here can include both active and superseded generations;
        # that creates false same-key conflicts when windows overlap.
        out = self.read(spec.dataset, symbol, min(a for a, _ in normalized), max(b for _, b in normalized))
        out = out[[c for c in selected if c in out.columns]]
        trade_dates = pd.to_datetime(out["trade_date"]).dt.date
        mask = pd.Series(False, index=out.index)
        for a, b in normalized:
            mask |= trade_dates.between(a, b)
        out = out.loc[mask].reset_index(drop=True)
        self.validate_coverage(out, spec.symbol, min(a for a, _ in normalized), max(b for _, b in normalized), "trade_date")
        # OR-composed overlapping windows can return the same physical
        # canonical row more than once.  Remove only exact full-row
        # duplicates here; same-identity rows with different quote fields
        # remain visible to validate_schema/audit_option_frame and fail closed.
        out = out.drop_duplicates(keep="first").reset_index(drop=True)
        out = self.validate_schema(out, spec.dataset)
        out, _, quality = audit_option_frame(
            out, source_version=spec.source_version, partition="read_boundary"
        )
        return out

    def read_partition(self, dataset: str, symbol: str, partition: str, filename: str | None = None) -> pd.DataFrame:
        """Read one physical partition through the data-access boundary."""
        symbol = self._symbol(symbol)
        path = self.parquet_root / dataset / f"symbol={symbol}" / partition
        path = path / (filename or f"{symbol}_{partition.replace('=', '_').replace('/', '_')}.parquet")
        if not path.exists():
            return pd.DataFrame()
        out = pd.read_parquet(path)
        return self.validate_schema(out, dataset)

    def read_prices(self, symbol: str, start_date=None, end_date=None, *, verified_handle=None) -> pd.DataFrame:
        if verified_handle is None:
            return self.read("daily", symbol, start_date, end_date)
        return self.read_verified_dataset(verified_handle, start_date, end_date)

    def read_daily(self, symbol: str, start_date=None, end_date=None) -> pd.DataFrame:
        """Backward-compatible alias for the canonical daily read API.

        ``read_prices`` is the formal implementation.  Keeping this alias as
        a direct delegation preserves the same route, validation, date
        boundaries, price-basis handling, and provenance behavior.
        """
        return self.read_prices(symbol, start_date, end_date)

    def read_verified_dataset(self, handle, start_date=None, end_date=None, *, required_warmup_rows: int = 0) -> pd.DataFrame:
        """Read and validate every partition represented by a verified handle."""
        from .correctness_gate import validate_price_input, DataCorrectnessError
        if getattr(handle, "verification_status", None) != "VERIFIED":
            raise DataCorrectnessError("GENERATION_NOT_VERIFIED")
        if not getattr(handle, "dataset_fingerprint", "") or not getattr(handle, "checksum", ""):
            raise DataCorrectnessError("DATASET_FINGERPRINT_MISMATCH")
        generation_ids = str(handle.generation_id).split("|")
        if len(generation_ids) not in {1, len(handle.partitions)}:
            raise DataCorrectnessError("UNPINNED_INPUT")
        if len(generation_ids) == 1:
            generation_ids = generation_ids * len(handle.partitions)
        frames = [self.read_pinned_generation(handle.dataset, handle.ticker, partition, generation)
                  for partition, generation in zip(handle.partitions, generation_ids)]
        if not frames:
            raise DataCorrectnessError("UNPINNED_INPUT")
        frame = pd.concat(frames, ignore_index=True)
        if str(handle.dataset).lower() == "daily" and "symbol" not in frame.columns:
            frame.insert(0, "symbol", str(handle.ticker).upper())
        if len(frame) != int(handle.row_count):
            raise DataCorrectnessError("DATASET_ROW_COUNT_MISMATCH")
        actual_checksum = self.semantic_content_hash(frame)
        if str(actual_checksum) != str(handle.checksum):
            raise DataCorrectnessError("DATASET_CHECKSUM_MISMATCH")
        if str(handle.dataset).lower() == "daily":
            duplicate_key = ["date"] if "symbol" not in frame.columns else ["symbol", "date"]
            if frame[duplicate_key].duplicated().any():
                raise DataCorrectnessError("DUPLICATE_CANONICAL_PRICE_KEY")
        elif {"symbol", "trade_date", "expiration_date", "call_put", "strike"}.issubset(frame.columns):
            if frame[["symbol", "trade_date", "expiration_date", "call_put", "strike"]].duplicated().any():
                raise DataCorrectnessError("DUPLICATE_CANONICAL_OPTION_KEY")
        from .canonical_generations import canonical_snapshot_descriptor
        paths = [Path(str(p)) for p in handle.canonical_paths]
        if len(paths) != len(handle.partitions) or any(not p.exists() for p in paths):
            raise DataCorrectnessError("UNPINNED_INPUT")
        file_hash = hashlib.sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()
        byte_size = sum(p.stat().st_size for p in paths)
        descriptor = canonical_snapshot_descriptor(
            dataset=str(handle.dataset), symbol=str(handle.ticker), frame=frame,
            file_hash=file_hash, byte_size=byte_size,
            schema_version=str(handle.schema_version), price_basis=str(handle.price_basis),
            corporate_action_version=str(handle.corporate_action_version), partition_key="|".join(handle.partitions))
        if str(descriptor["dataset_fingerprint"]) != str(handle.dataset_fingerprint):
            raise DataCorrectnessError("DATASET_FINGERPRINT_MISMATCH")
        date_column = "date" if str(handle.dataset).lower() == "daily" else "trade_date"
        if start_date is not None:
            frame = frame[pd.to_datetime(frame[date_column], errors="coerce") >= pd.Timestamp(start_date)]
        if end_date is not None:
            frame = frame[pd.to_datetime(frame[date_column], errors="coerce") <= pd.Timestamp(end_date)]
        frame = frame.reset_index(drop=True)
        if frame.empty:
            raise DataAccessError("PINNED_GENERATION_COVERAGE_INSUFFICIENT")
        if str(handle.dataset).lower() == "daily":
            try:
                validate_price_input(frame, handle, handle.ticker, start_date, end_date,
                                     as_of=end_date, required_warmup_rows=required_warmup_rows)
            except DataCorrectnessError as exc:
                if exc.reason_code == "INSUFFICIENT_DATE_COVERAGE":
                    raise DataAccessError("PINNED_GENERATION_COVERAGE_INSUFFICIENT") from exc
                raise
        else:
            keys = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
            if not set(keys).issubset(frame.columns):
                raise DataAccessError("SCHEMA_MISMATCH")
            if frame[keys].duplicated().any():
                raise DataQualityError("DUPLICATE_CANONICAL_OPTION_KEY")
            dates = pd.to_datetime(frame["trade_date"], errors="coerce")
            if dates.isna().any():
                raise DataAccessError("PINNED_GENERATION_COVERAGE_INSUFFICIENT")
        sort_columns = ["symbol", "date"] if str(handle.dataset).lower() == "daily" else ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
        frame = frame.sort_values(sort_columns).reset_index(drop=True)
        return frame.reset_index(drop=True)

    def write(self, frame: pd.DataFrame, dataset: str, symbol: str, partition: str, *, source_version: str, allow_overwrite=False, update_manifest=True, filename=None, replace_manifest=False) -> Path:
        symbol = self._symbol(symbol)
        if dataset == "options" or dataset.startswith("options"):
            checked, quarantined, quality = audit_option_frame(frame, source_version=source_version, partition=partition)
            if len(quarantined):
                qdir = self.parquet_root / "quarantine" / dataset / f"symbol={symbol}" / partition
                qdir.mkdir(parents=True, exist_ok=True)
                qname = (filename or f"{symbol}_{partition.replace('=', '_').replace('/', '_')}.parquet").replace(".parquet", ".quarantine.parquet")
                quarantined.to_parquet(qdir / qname, index=False)
            if quality["partition_status"] == "PARTITION_REPAIR_REQUIRED":
                reasons = quality.get("reason_breakdown", {})
                detail = "ambiguous/conflicting option identity" if reasons.get("OPTION_CONFLICTING_IDENTITY") else "structural option quote corruption"
                raise DataQualityError(f"PARTITION_REPAIR_REQUIRED:{symbol}:{partition}:{quality['affected_percentage']:.4f}% invalid option rows ({detail})")
        else:
            checked = frame
        checked = self.validate_schema(checked, dataset)
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
        # Publish the partition only after the final NTFS object is readable
        # by the current PCS process identity.  A rename alone is not proof
        # that a private/protected ACL did not follow the file into canonical.
        try:
            self._assert_canonical_file_readable(path, dataset, symbol)
        except CanonicalFileAccessError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        if update_manifest:
            self.update_manifest(dataset, symbol, checked, path, source_version, partition, replace_existing=replace_manifest)
        return path

    @staticmethod
    def _assert_canonical_file_readable(path: Path, dataset: str, symbol: str) -> None:
        """Fail closed when the final canonical file cannot be opened."""
        try:
            with Path(path).open("rb"):
                pass
        except (OSError, PermissionError) as exc:
            raise CanonicalFileAccessError(str(dataset), str(symbol), [{
                "path": str(Path(path).resolve()),
                "operation": "open(rb)",
                "errno": str(getattr(exc, "errno", "")),
                "winerror": str(getattr(exc, "winerror", "")),
                "error": str(exc),
            }]) from exc

    def append(self, frame: pd.DataFrame, dataset: str, symbol: str, partition: str, *, source_version: str) -> Path:
        if (self.parquet_root / dataset / f"symbol={self._symbol(symbol)}" / partition).exists():
            raise FileExistsError("append requires a new partition; refusing trusted overwrite")
        return self.write(frame, dataset, symbol, partition, source_version=source_version)

    def upsert(self, *args, **kwargs):
        raise NotImplementedError("upsert is intentionally disabled for trusted canonical data; write a new version")

    def write_partition(self, frame, dataset, symbol, partition, *, source_version, allow_overwrite=False, update_manifest=True, filename=None, replace_manifest=False):
        return self.write(frame, dataset, symbol, partition, source_version=source_version, allow_overwrite=allow_overwrite, update_manifest=update_manifest, filename=filename, replace_manifest=replace_manifest)

    def promote_generation(self, frame, dataset, symbol, partition, *, source_version):
        """Publish an immutable generation for a mutable logical partition.

        Existing active data is merged by the canonical option identity. The
        old parquet object is never changed; the manifest pointer is switched
        only after the new object and its hash have been verified.
        """
        symbol = self._symbol(symbol)
        current = self._read_manifest(self.manifest_path)
        partition_parts = dict(x.split("=", 1) for x in str(partition).split("/"))
        target_year = int(partition_parts["year"])
        target_quarter = int(partition_parts.get("quarter", 0))
        rows = current[(current.get("dataset", pd.Series(dtype=str)).astype(str) == str(dataset)) &
                       (current.get("symbol", pd.Series(dtype=str)).astype(str).str.upper() == symbol) &
                       (pd.to_numeric(current.get("year", pd.Series(dtype=float)), errors="coerce") == target_year) &
                       (pd.to_numeric(current.get("quarter", pd.Series(dtype=float)), errors="coerce").fillna(0) == target_quarter)]
        merged = frame.copy()
        is_options = dataset == "options" or str(dataset).startswith("options")
        if len(rows) and is_options:
            old = pd.read_parquet(str(rows.iloc[-1].parquet_path))
            merged = pd.concat([old, merged], ignore_index=True)
        key = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
        if is_options and all(c in merged.columns for c in key):
            merged = merged.drop_duplicates(key, keep="last").reset_index(drop=True)
        if not is_options and "date" in merged.columns:
            merged = merged.drop_duplicates("date", keep="last").reset_index(drop=True)
        digest = self.semantic_content_hash(merged)
        generation = digest[:24]
        generation_partition = f"{partition}/generations"
        path = self.parquet_root / dataset / f"symbol={symbol}" / generation_partition / f"{generation}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(rows) and str(rows.iloc[-1].get("content_hash", "")) == digest:
            # No new promotion occurred; callers must not treat this as a
            # fresh PromotionReceipt.
            return Path(str(rows.iloc[-1].parquet_path))
        previous_generation = str(rows.iloc[-1].get("active_generation", "")) if len(rows) else ""
        previous_path = str(rows.iloc[-1].get("parquet_path", "")) if len(rows) else ""
        created_at=datetime.now(timezone.utc).isoformat()
        if not path.exists():
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            merged.to_parquet(tmp, index=False)
            if self.semantic_content_hash(pd.read_parquet(tmp)) != digest:
                tmp.unlink(missing_ok=True); raise DataQualityError("GENERATION_HASH_VERIFICATION_FAILED")
            os.replace(tmp, path)
        from .canonical_generations import canonical_snapshot_descriptor
        descriptor = canonical_snapshot_descriptor(
            dataset=str(dataset), symbol=symbol, frame=merged,
            file_hash=hashlib.sha256(path.read_bytes()).hexdigest(), byte_size=path.stat().st_size,
            schema_version="2", price_basis="canonical_adjusted",
            corporate_action_version="canonical_identity", partition_key=str(partition))
        dataset_fingerprint = str(descriptor["dataset_fingerprint"])
        manifest_before = self.manifest_path.read_bytes() if self.manifest_path.exists() else None
        try:
            self.update_manifest(dataset, symbol, merged, path, source_version, partition, replace_existing=True,
                             staging_generation_id=generation, promoted_generation_id=generation,
                             manifest_active_generation_id=generation, read_back_generation_id=generation,
                             active_generation=generation, previous_generation=previous_generation,
                             content_hash=digest, previous_path=previous_path,
                             dataset_fingerprint=dataset_fingerprint,
                             schema_fingerprint=str(descriptor["schema_fingerprint"]),
                             price_basis="canonical_adjusted",
                             corporate_action_version="canonical_identity",
                             partition_ids=str(partition), source_lineage=json.dumps({"source":source_version,"partition":str(partition)}, sort_keys=True),
                             created_at=created_at, lifecycle_status="ACTIVE")
            read_back = pd.read_parquet(path)
            read_back_checksum = self.semantic_content_hash(read_back)
            if read_back_checksum != digest or len(read_back) != len(merged):
                raise DataQualityError("READ_BACK_CHECKSUM_OR_ROW_COUNT_MISMATCH")
        except Exception:
            if manifest_before is None: self.manifest_path.unlink(missing_ok=True)
            else: self.manifest_path.write_bytes(manifest_before)
            raise
        read_back_generation = generation
        self.generation_cache.invalidate_partition(dataset, symbol, partition, except_generation=generation)
        promoted_at=datetime.now(timezone.utc).isoformat()
        return PromotionReceipt(dataset=str(dataset), ticker=symbol, generation_id=generation,
            promoted_partitions=(str(partition),), checksum=digest, row_count=len(merged),
            manifest_version="storage_manifest_v1", promotion_timestamp=promoted_at, path=str(path),
            read_back_generation_id=read_back_generation, read_back_checksum=read_back_checksum,
            read_back_row_count=len(read_back), staging_generation_id=generation,
            manifest_active_generation_id=generation,
            source_lineage=({"source": source_version, "partition": str(partition)},),
            created_at=created_at, partition_ids=(str(partition),),
            dataset_fingerprint=dataset_fingerprint, snapshot_descriptor=descriptor)

    def read_pinned_generation(self, dataset: str, symbol: str, partition: str,
                               generation_id: str) -> pd.DataFrame:
        """Read only the manifest-active immutable generation."""
        if not generation_id: raise DataAccessError("GENERATION_REQUIRED")
        manifest=self._read_manifest(self.manifest_path)
        parts=dict(x.split("=",1) for x in str(partition).split("/") if "=" in x)
        mask=(manifest.get("dataset",pd.Series(dtype=str)).astype(str).eq(str(dataset)) &
              manifest.get("symbol",pd.Series(dtype=str)).astype(str).str.upper().eq(self._symbol(symbol)) &
              pd.to_numeric(manifest.get("year",pd.Series(dtype=str)), errors="coerce").eq(pd.to_numeric(parts.get("year", ""), errors="coerce")))
        if "quarter" in parts:
            mask &= pd.to_numeric(manifest.get("quarter",pd.Series(dtype=str)), errors="coerce").eq(pd.to_numeric(parts["quarter"], errors="coerce"))
        if not mask.any(): raise DataAccessError("MANIFEST_GENERATION_MISSING")
        row=manifest.loc[mask].iloc[-1]
        if str(row.get("active_generation", "")) != str(generation_id): raise DataAccessError("GENERATION_MISMATCH")
        path=Path(str(row.parquet_path))
        if not path.exists(): raise DataAccessError("ACTIVE_GENERATION_PATH_MISSING")
        frame=pd.read_parquet(path)
        if len(frame) != int(row.row_count) or self.semantic_content_hash(frame) != str(row.content_hash): raise DataAccessError("READ_BACK_CHECKSUM_MISMATCH")
        return frame

    def active_generation_record(self, dataset: str, symbol: str, partition: str) -> dict[str, Any]:
        """Return the physically persisted active manifest record."""
        manifest=self._read_manifest(self.manifest_path)
        parts=dict(x.split("=",1) for x in str(partition).split("/") if "=" in x)
        mask=(manifest.get("dataset",pd.Series(dtype=str)).astype(str).eq(str(dataset)) &
              manifest.get("symbol",pd.Series(dtype=str)).astype(str).str.upper().eq(self._symbol(symbol)) &
              pd.to_numeric(manifest.get("year",pd.Series(dtype=float)), errors="coerce").eq(pd.to_numeric(parts.get("year", ""), errors="coerce")))
        if "quarter" in parts:
            mask &= pd.to_numeric(manifest.get("quarter",pd.Series(dtype=float)), errors="coerce").eq(pd.to_numeric(parts["quarter"], errors="coerce"))
        if not mask.any(): raise DataAccessError("ACTIVE_MANIFEST_RECORD_MISSING")
        return manifest.loc[mask].iloc[-1].to_dict()

    def update_manifest(self, dataset, symbol, frame, path, source_version, partition=None, replace_existing=False, **generation):
        """Atomically perform the complete manifest read/merge/replace transaction."""
        with self._file_lock(self.manifest_path):
            return self._update_manifest_locked(dataset, symbol, frame, path, source_version, partition, replace_existing, generation)

    def _update_manifest_locked(self, dataset, symbol, frame, path, source_version, partition=None, replace_existing=False, generation=None):
        generation = generation or {}
        fields = ["dataset","symbol","source_file","source_size","source_modified_time","row_count","min_date","max_date","year","quarter","parquet_path","schema_version","schema_fingerprint","dataset_fingerprint","price_basis","corporate_action_version","import_timestamp","status","lifecycle_status","superseded_by","active_generation","previous_generation","staging_generation_id","promoted_generation_id","manifest_active_generation_id","read_back_generation_id","content_hash","file_hash","created_at","promoted_at","source","source_lineage","partition_ids","provenance_id","previous_path"]
        now = datetime.now(timezone.utc).isoformat()
        file_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None
        row = {k: None for k in fields}; row.update(dataset=dataset, symbol=self._symbol(symbol), source_file=source_version, row_count=len(frame), parquet_path=str(path), schema_version="2" if generation else "1", import_timestamp=now, created_at=now, promoted_at=now, source=source_version, status="SUCCESS", file_hash=file_hash)
        row.update({k: v for k, v in generation.items() if k in fields})
        if partition:
            for part in str(partition).split('/'):
                if '=' in part:
                    key, value = part.split('=', 1)
                    if key in row: row[key] = value
        date_col = "trade_date" if dataset == "options" or dataset.startswith("options") else "date"
        if len(frame): row.update(min_date=str(pd.to_datetime(frame[date_col]).min().date()), max_date=str(pd.to_datetime(frame[date_col]).max().date()))
        current = self._read_manifest(self.manifest_path)
        for field in fields:
            if field not in current:
                current[field] = None
        if replace_existing:
            row_year = pd.to_numeric(pd.Series([row["year"]]), errors="coerce").iloc[0]
            row_quarter = pd.to_numeric(pd.Series([row["quarter"]]), errors="coerce").iloc[0]
            current_year = pd.to_numeric(current.year, errors="coerce")
            current_quarter = pd.to_numeric(current.quarter, errors="coerce")
            current = current[~((current.dataset == dataset)
                                & (current.symbol.astype(str).str.upper() == self._symbol(symbol))
                                & current_year.eq(row_year)
                                & ((current_quarter.eq(row_quarter)) | (current_quarter.isna() & pd.isna(row_quarter))))]
        updated = pd.concat([current[fields], pd.DataFrame([row], columns=fields)], ignore_index=True)
        key = lambda r: (str(r.get("dataset", "")), self._symbol(r.get("symbol", "")), str(r.get("year", "")), str(r.get("quarter", "")))
        matches = updated.apply(lambda r: key(r) == key(row), axis=1)
        if matches.sum() > 1:
            if any(self._semantic_record(r.to_dict()) != self._semantic_record(row) for _, r in updated[matches].iloc[:-1].iterrows()):
                raise DataQualityError(f"conflicting manifest partition: {key(row)}")
            updated = updated[~matches].copy()
            updated = pd.concat([updated, pd.DataFrame([row], columns=fields)], ignore_index=True)
        updated["_sort"] = updated.apply(key, axis=1)
        updated = updated.sort_values("_sort", kind="mergesort").drop(columns=["_sort"]).reset_index(drop=True)
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_name(f".{self.manifest_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            updated.to_csv(tmp, index=False)
            os.replace(tmp, self.manifest_path)
        finally:
            tmp.unlink(missing_ok=True)
            self._manifest = updated

    def rollback_generation(self, dataset: str, symbol: str, partition: str) -> dict[str, Any]:
        """Atomically switch a logical partition back to its previous generation."""
        with self._file_lock(self.manifest_path):
            current = self._read_manifest(self.manifest_path)
            parts = dict(x.split("=", 1) for x in str(partition).split("/"))
            mask = (current.dataset.astype(str) == str(dataset)) & current.symbol.astype(str).str.upper().eq(self._symbol(symbol)) & current.year.astype(str).eq(str(parts.get("year"))) & current.quarter.astype(str).eq(str(parts.get("quarter")))
            if not mask.any(): raise DataAccessError("CANONICAL_ROLLBACK_MANIFEST_MISSING")
            idx = current.index[mask][-1]; row = current.loc[idx]
            previous = str(row.get("previous_path", ""))
            previous_generation = str(row.get("previous_generation", ""))
            if not previous or not previous_generation or not Path(previous).exists(): raise DataAccessError("CANONICAL_ROLLBACK_PREVIOUS_GENERATION_UNAVAILABLE")
            old_generation, old_path = str(row.active_generation), str(row.parquet_path)
            current.loc[idx, "active_generation"] = previous_generation; current.loc[idx, "previous_generation"] = old_generation
            current.loc[idx, "parquet_path"] = previous; current.loc[idx, "previous_path"] = old_path; current.loc[idx, "content_hash"] = self.semantic_content_hash(pd.read_parquet(previous)); current.loc[idx, "promoted_at"] = datetime.now(timezone.utc).isoformat()
            tmp = self.manifest_path.with_name(f".{self.manifest_path.name}.{uuid.uuid4().hex}.tmp"); current.to_csv(tmp, index=False); os.replace(tmp, self.manifest_path); self._manifest = current
            return {"status": "ROLLED_BACK", "from_generation": old_generation, "to_generation": previous_generation}

    def record_provenance(self, record: dict[str, Any], path: str | Path | None = None) -> Path:
        """Atomically append one machine-readable source/provenance record."""
        target = Path(path) if path is not None else self.provenance_manifest_path
        record = dict(record); record["provenance_key"] = self._provenance_key(record)
        with self._file_lock(target):
            current = pd.read_csv(target) if target.exists() else pd.DataFrame()
            if "provenance_key" not in current:
                current["provenance_key"] = current.apply(lambda r: self._provenance_key(r.to_dict()), axis=1) if len(current) else None
            matches = current[current.provenance_key.astype(str) == record["provenance_key"]] if len(current) else pd.DataFrame()
            if len(matches):
                existing = self._semantic_record(matches.iloc[0].to_dict())
                incoming = self._semantic_record(record)
                if any(existing.get(k) != v for k, v in incoming.items()):
                    raise DataQualityError(f"conflicting provenance record: {record['provenance_key']}")
                return target
            updated = pd.concat([current, pd.DataFrame([record])], ignore_index=True).sort_values("provenance_key", kind="mergesort").reset_index(drop=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                updated.to_csv(tmp, index=False); os.replace(tmp, target)
            finally: tmp.unlink(missing_ok=True)
        return target

    def get_provenance(self, dataset: str, symbol: str) -> list[dict[str, Any]]:
        resolved_dataset, manifest_path, _ = self._resolve_route(dataset, symbol)
        manifest = self._read_manifest(manifest_path)
        if manifest.empty: return []
        rows = manifest[(manifest.dataset == resolved_dataset) & (manifest.symbol.astype(str).str.upper() == self._symbol(symbol))]
        return rows.to_dict("records")

    def write_artifact(self, frame: pd.DataFrame, namespace: str, name: str, root="data/parquet", metadata=None) -> Path:
        """Persist a derived/research frame under an explicit non-canonical namespace."""
        metadata = metadata or {}
        out = frame.copy()
        for key, value in metadata.items(): out[key] = value
        created_at = metadata.get("created_at", datetime.now(timezone.utc).isoformat())
        out["created_at"] = created_at
        target = Path(root) / namespace
        target.mkdir(parents=True, exist_ok=True)
        path = target / name
        if path.suffix != ".parquet": path = path.with_suffix(".parquet")
        semantic_hash = self.semantic_content_hash(out)
        sidecar = path.with_suffix(path.suffix + ".semantic.json")

        def write_sidecar(sidecar_created_at: str) -> None:
            side_tmp = sidecar.with_name(f".{sidecar.name}.{uuid.uuid4().hex}.tmp")
            try:
                side_tmp.write_text(json.dumps({
                    "semantic_hash": semantic_hash,
                    "created_at": sidecar_created_at,
                    "row_count": len(out),
                }, sort_keys=True), encoding="utf-8")
                os.replace(side_tmp, sidecar)
            finally:
                side_tmp.unlink(missing_ok=True)

        if path.exists():
            persisted = pd.read_parquet(path)
            if self.semantic_content_hash(persisted) == semantic_hash:
                # A prior interruption can leave the Parquet file without its
                # audit sidecar. Rebuild the derived metadata on an idempotent
                # retry instead of accepting an incomplete artifact set.
                persisted_created_at = (
                    str(persisted["created_at"].iloc[0])
                    if len(persisted) and "created_at" in persisted
                    else str(created_at)
                )
                expected = {
                    "semantic_hash": semantic_hash,
                    "created_at": persisted_created_at,
                    "row_count": len(persisted),
                }
                try:
                    current_sidecar = json.loads(sidecar.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    current_sidecar = None
                if current_sidecar != expected:
                    write_sidecar(persisted_created_at)
                return path
            raise DataQualityError(f"conflicting artifact content: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        out.to_parquet(tmp, index=False)
        if len(pd.read_parquet(tmp)) != len(out):
            tmp.unlink(missing_ok=True)
            raise DataQualityError("artifact row-count verification failed")
        os.replace(tmp, path)
        write_sidecar(str(created_at))
        return path

    def read_artifact(self, namespace: str, name: str, root="data/parquet", filters=None) -> pd.DataFrame:
        path = Path(root) / namespace / name
        if not path.exists(): return pd.DataFrame()
        out = pd.read_parquet(path)
        for key, value in (filters or {}).items():
            if key not in out: raise DataQualityError(f"artifact filter column missing: {key}")
            out = out[out[key] == value]
        return out.reset_index(drop=True)


def ensure_ready(symbol: str, dataset: str = "daily", as_of=None,
                 required_warmup_sessions: int = 0, *, access: PCSDataAccess | None = None,
                 start_date=None, end_date=None) -> DatasetReadinessResult:
    """Normal user-facing readiness entry point.

    Lifecycle decisions remain owned by the canonical control plane: an
    existing active generation is reused, a bounded gap may be incrementally
    promoted, and unavailable sources fail closed while preserving the active
    generation.  Callers do not provide paths, generation IDs, or fingerprints.
    """
    reader = access or PCSDataAccess.canonical()
    return reader.ensure_ready(dataset, symbol, start_date=start_date,
                               end_date=end_date, as_of=as_of,
                               required_warmup_sessions=required_warmup_sessions)


__all__ = ["PCSDataAccess", "SourceSpec", "DatasetReadinessResult", "DataAccessError", "DataQualityError", "ensure_ready"]
