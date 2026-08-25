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
        metadata_path = self.manifest_path.with_name("price_basis_metadata.csv")
        if not metadata_path.exists() and self.manifest_path.parent == Path("data/manifests"):
            metadata_path = Path("data/manifests/price_basis_metadata.csv")
        self._price_basis_metadata = pd.read_csv(metadata_path) if metadata_path.exists() else pd.DataFrame()

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
        if self.manifest_path != Path("data/manifests/storage_manifest.csv"):
            if dataset == "options":
                manifest = self._read_manifest(self.manifest_path)
                if not manifest.empty and "dataset" in manifest and manifest.dataset.astype(str).str.startswith("options_").any():
                    dataset = str(manifest.loc[manifest.dataset.astype(str).str.startswith("options_"), "dataset"].iloc[0])
            return dataset, self.manifest_path, self.parquet_root
        # ``options`` is the compatibility logical name and resolves through
        # the configured per-ticker route.  An explicit ``options_v2`` request
        # is authoritative and must never be silently redirected to a legacy
        # dataset when its route is absent.
        route_dataset = dataset
        routes = self.source_routes.get(route_dataset, {}).get("by_symbol", {})
        if not routes and dataset.startswith("options_v2"):
            # The repository configuration historically stores ticker routes
            # under the logical ``options`` section.  This is route aliasing,
            # not a data fallback: the selected route must still resolve to
            # options_v2 and is validated below.
            route_dataset = "options"
            routes = self.source_routes.get(route_dataset, {}).get("by_symbol", {})
        route = routes.get(self._symbol(symbol), {})
        if dataset in {"options", "options_v2", "options_v3"} and not route:
            # Canonical v2 is manifest-driven by default.  Keep explicit
            # routes for true dataset/version exceptions (for example v3),
            # but do not require a hand-written ticker entry when the ticker
            # is already present in the active canonical v2 manifest.
            if dataset == "options_v2":
                candidates = [self.manifest_path]
                if self.manifest_path == Path("data/manifests/storage_manifest.csv"):
                    candidates.extend([
                        Path("data/manifests/storage_manifest_options_v2.csv"),
                        Path("data/manifests/storage_manifest_v2.csv"),
                    ])
                for candidate_manifest in candidates:
                    manifest = self._read_manifest(candidate_manifest)
                    if manifest.empty or "dataset" not in manifest.columns:
                        continue
                    found = manifest[
                        manifest.dataset.astype(str).eq("options_v2")
                        & manifest.symbol.astype(str).str.upper().eq(self._symbol(symbol))
                        & manifest.status.astype(str).str.upper().eq("SUCCESS")
                    ]
                    if not found.empty:
                        return "options_v2", candidate_manifest, self.parquet_root
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
        volatile = {"created_at", "import_timestamp", "run_id", "request_id", "timestamp", "updated_at"}
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
        manifest = self._manifest if manifest_path == self.manifest_path else self._read_manifest(manifest_path)
        if manifest.empty:
            if dataset.startswith("options_v2"):
                raise DataAccessError(
                    f"canonical route unavailable: requested_dataset={dataset} "
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
            if isinstance(parquet_input, list):
                # DuckDB's parameterized multi-file reader can duplicate rows
                # when active Parquet files were atomically replaced during a
                # long-lived process.  Read each authoritative file once and
                # UNION them explicitly; this preserves the validator's
                # strict duplicate/conflict semantics and avoids false rows.
                relations = " UNION ALL ".join(["SELECT * FROM read_parquet(?)"] * len(parquet_input))
                params = list(parquet_input) + [spec.symbol, pd.Timestamp(start_date or spec.first_date).date(), pd.Timestamp(end_date or spec.last_date).date()]
                out = con.execute(f"SELECT * FROM ({relations}) AS canonical_rows WHERE symbol=? AND {column} BETWEEN ? AND ?", params).fetchdf()
            else:
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

    def audit_options_quality(self, symbol: str, start_date=None) -> dict[str, Any]:
        """Bounded canonical options audit without materializing full history.

        This is the readiness path for large option populations. It scans the
        active canonical files in DuckDB and returns only aggregate evidence;
        lifecycle selection still reads its bounded fixture window normally.
        """
        spec = self.resolve_source("options_v2", symbol)
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
            date_clause = " AND trade_date >= ?" if start_date is not None else ""
            params = files + [str(symbol).upper()] + ([pd.Timestamp(start_date).date()] if start_date is not None else [])
            gsql = f"""WITH raw AS ({relations}) SELECT coalesce(sum(CASE WHEN n>1 THEN n ELSE 0 END),0) duplicate_rows, coalesce(count(CASE WHEN n>1 THEN 1 END),0) duplicate_keys, coalesce(count(CASE WHEN n>1 AND versions>1 THEN 1 END),0) conflicting_keys, coalesce(count(CASE WHEN n>1 AND versions=1 THEN 1 END),0) identical_keys FROM (SELECT symbol,trade_date,expiration_date,call_put,strike,count(*) n,count(DISTINCT {hash_expr}) versions FROM raw WHERE symbol=?{date_clause} GROUP BY ALL)"""
            qsql = f"""WITH raw AS ({relations}) SELECT count(CASE WHEN date_diff('day',trade_date,expiration_date) BETWEEN 30 AND 45 THEN 1 END) usable_rows,count(CASE WHEN date_diff('day',trade_date,expiration_date) BETWEEN 30 AND 45 AND bid IS NOT NULL AND ask IS NOT NULL AND isfinite(bid) AND isfinite(ask) AND bid>=0 AND ask>=bid THEN 1 END) valid_quote_rows,count(CASE WHEN date_diff('day',trade_date,expiration_date) BETWEEN 30 AND 45 AND expiration_date IS NOT NULL AND expiration_date>trade_date THEN 1 END) valid_expiration_rows,count(CASE WHEN date_diff('day',trade_date,expiration_date) BETWEEN 30 AND 45 AND strike>0 THEN 1 END) valid_strike_rows FROM raw WHERE symbol=?{date_clause}"""
            g = con.execute(gsql, params).fetchone()
            q = con.execute(qsql, params).fetchone()
        finally:
            con.close()
        return {"duplicate_option_rows": int(g[0]), "duplicate_option_keys": int(g[1]), "ambiguous_conflicting_option_keys": int(g[2]), "identical_duplicate_keys": int(g[3]), "usable_30_45_dte_rows": int(q[0]), "valid_30_45_dte_quote_rows": int(q[1]), "valid_30_45_dte_expiration_rows": int(q[2]), "valid_30_45_dte_strike_rows": int(q[3])}

    def read_quotes_for_windows(self, symbol: str, windows: list[tuple[object, object]], columns: list[str] | None = None) -> pd.DataFrame:
        """Read selected canonical quote windows with one bounded query."""
        if not windows:
            return pd.DataFrame(columns=columns or [])
        normalized = [(pd.Timestamp(a).date(), pd.Timestamp(b).date()) for a, b in windows]
        spec = self.resolve_source("options", symbol, min(a for a, _ in normalized), max(b for _, b in normalized))
        selected = columns or list(OPTION_FIELDS)
        predicates = " OR ".join(["(trade_date BETWEEN ? AND ?)"] * len(normalized))
        params: list[Any] = [spec.path.split(";") if ";" in spec.path else spec.path, spec.symbol]
        for a, b in normalized: params.extend([a, b])
        con = duckdb.connect()
        try:
            out = con.execute(f"SELECT {', '.join(selected)} FROM read_parquet(?, hive_partitioning=true) WHERE symbol=? AND ({predicates})", params).fetchdf()
        finally:
            con.close()
        self.validate_coverage(out, spec.symbol, min(a for a, _ in normalized), max(b for _, b in normalized), "trade_date")
        out = self.validate_schema(out, spec.dataset)
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
        """Atomically perform the complete manifest read/merge/replace transaction."""
        with self._file_lock(self.manifest_path):
            return self._update_manifest_locked(dataset, symbol, frame, path, source_version, partition, replace_existing)

    def _update_manifest_locked(self, dataset, symbol, frame, path, source_version, partition=None, replace_existing=False):
        fields = ["dataset","symbol","source_file","source_size","source_modified_time","row_count","min_date","max_date","year","quarter","parquet_path","schema_version","import_timestamp","status"]
        row = {k: None for k in fields}; row.update(dataset=dataset, symbol=self._symbol(symbol), source_file=source_version, row_count=len(frame), parquet_path=str(path), schema_version="1", import_timestamp=datetime.now(timezone.utc).isoformat(), status="SUCCESS")
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
            current = current[~((current.dataset == dataset)
                                & (current.symbol.astype(str).str.upper() == self._symbol(symbol))
                                & (current.year.astype(str) == str(row["year"]))
                                & (current.quarter.astype(str) == str(row["quarter"])))]
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
        manifest = self._manifest if manifest_path == self.manifest_path else self._read_manifest(manifest_path)
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


__all__ = ["PCSDataAccess", "SourceSpec", "DataAccessError", "DataQualityError"]
