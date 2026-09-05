"""Generic inspection and recovery operations for immutable generations."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import hashlib, json, os, uuid
from datetime import datetime, timezone
from threading import RLock
import pandas as pd
from .access import PCSDataAccess, DataAccessError, DataQualityError

_MIGRATED_ADMISSION_WRITE_LOCK = RLock()

def _strict_text(value: Any, code: str) -> str:
    if value is None or pd.isna(value) or not str(value).strip() or str(value).strip().lower() == "nan":
        raise DataAccessError(code)
    return str(value).strip()

def canonical_snapshot_descriptor(*, dataset: str, symbol: str, frame: pd.DataFrame,
                                  file_hash: str, byte_size: int,
                                  schema_version: str = "1", price_basis: str = "canonical_adjusted",
                                  corporate_action_version: str = "canonical_identity",
                                  partition_key: str = "year=unknown") -> dict[str, Any]:
    """Build a path-independent identity descriptor for a validated daily snapshot."""
    date_column = "trade_date" if "trade_date" in frame.columns and "date" not in frame.columns else "date"
    if date_column not in frame.columns: raise DataQualityError("DATE_COLUMN_MISSING")
    dates=pd.to_datetime(frame[date_column], errors="coerce")
    if dates.isna().any(): raise DataQualityError("DAILY_DATE_ORDER_INVALID")
    if str(dataset).lower() == "daily" and (not dates.is_unique or not dates.is_monotonic_increasing):
        raise DataQualityError("DAILY_DATE_ORDER_INVALID")
    if "symbol" in frame.columns and not all(frame["symbol"].astype(str).str.upper().eq(str(symbol).upper())): raise DataQualityError("SYMBOL_MISMATCH")
    if {"open","high","low","close","volume"}.issubset(frame.columns) and (frame[["open","high","low","close"]].isna().any().any() or (frame.volume < 0).any() or (frame.high < frame.low).any() or (frame.high < frame[["open","close"]].max(axis=1)).any() or (frame.low > frame[["open","close"]].min(axis=1)).any()): raise DataQualityError("DAILY_INTEGRITY_FAILED")
    schema=sorted((str(c),str(frame[c].dtype)) for c in frame.columns)
    desc={"dataset":str(dataset),"symbol":str(symbol).upper(),"timeframe":"daily","date_column":date_column,"schema_fingerprint":hashlib.sha256(json.dumps(schema,separators=(",",":"),ensure_ascii=False).encode()).hexdigest(),"schema_version":str(schema_version),"price_basis":str(price_basis),"corporate_action_version":str(corporate_action_version),"row_count":int(len(frame)),"min_date":str(dates.min().date()),"max_date":str(dates.max().date()),"trading_session_count":int(dates.nunique()),"partitions":[{"logical_partition_key":str(partition_key),"file_sha256":_strict_text(file_hash,"DATASET_FINGERPRINT_MISSING"),"byte_size":int(byte_size),"row_count":int(len(frame)),"min_date":str(dates.min().date()),"max_date":str(dates.max().date())}]}
    desc["dataset_fingerprint"]=hashlib.sha256(json.dumps(desc,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    return desc

def adopt_legacy_canonical_generation(*, dataset: str, symbol: str, legacy_manifest: dict[str, Any], expected_file_hash: str, adoption_reason: str, data_access: PCSDataAccess | None = None) -> dict[str, Any]:
    """Validate and promote one legacy canonical object through the formal registry."""
    access=data_access or PCSDataAccess(); path=Path(_strict_text(legacy_manifest.get("parquet_path"),"LEGACY_PATH_MISSING"))
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != _strict_text(expected_file_hash,"LEGACY_FILE_HASH_MISMATCH"): raise DataQualityError("LEGACY_FILE_HASH_MISMATCH")
    frame=pd.read_parquet(path); year=int(pd.to_datetime(frame.date).min().year); partition=f"year={year}"
    desc=canonical_snapshot_descriptor(dataset=dataset,symbol=symbol,frame=frame,file_hash=actual,byte_size=path.stat().st_size,partition_key=partition)
    receipt=access.promote_generation(frame,dataset,symbol,partition,source_version="LEGACY_CANONICAL_ADOPTION")
    if not hasattr(receipt,"generation_id"): raise DataAccessError("DATASET_GENERATION_ID_MISSING")
    return {"receipt":receipt.to_dict(),"dataset_fingerprint":desc["dataset_fingerprint"],"snapshot_descriptor":desc,"adoption_reason":adoption_reason,"source_legacy_manifest_hash":hashlib.sha256(json.dumps(legacy_manifest,sort_keys=True,default=str).encode()).hexdigest(),"source_file_hash":actual}


def _migration_catalog_path(access: PCSDataAccess, migration_manifest_path=None) -> Path:
    return Path(migration_manifest_path) if migration_manifest_path is not None else Path(access.manifest_path).with_name("daily_universe_migration.csv")


def _migrated_daily_files(access: PCSDataAccess, symbol: str) -> tuple[Path, ...]:
    root = Path(access.parquet_root) / "daily" / f"symbol={symbol}"
    return tuple(sorted((path for path in root.glob("year=*/*.parquet")
                         if path.parent.name.startswith("year=") and path.is_file()),
                        key=lambda path: (path.parent.name, str(path))))


def _validate_migrated_daily_file(path: Path, symbol: str, access: PCSDataAccess) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        raise DataAccessError("MIGRATION_PHYSICAL_MISSING")
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        raise DataQualityError("MIGRATED_CANONICAL_UNREADABLE") from exc
    if "symbol" not in frame.columns:
        frame.insert(0, "symbol", symbol)
    else:
        values = frame["symbol"]
        nonempty = values.notna() & values.astype(str).str.strip().ne("") & values.astype(str).str.lower().ne("nan")
        if not values[nonempty].astype(str).str.upper().eq(symbol).all():
            raise DataQualityError("MIGRATED_CANONICAL_SYMBOL_MISMATCH")
        frame["symbol"] = values.fillna(symbol).astype(str).replace({"": symbol, "nan": symbol})
    frame = access.validate_schema(frame, "daily")
    required = ("date", "open", "high", "low", "close", "volume")
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise DataQualityError("MIGRATED_CANONICAL_DATE_ORDER_INVALID")
    numeric = frame[list(required[1:])].apply(pd.to_numeric, errors="coerce")
    invalid_reasons = []
    if numeric.isna().any().any(): invalid_reasons.append("OHLCV_NAN")
    if numeric.abs().eq(float("inf")).any().any(): invalid_reasons.append("OHLCV_INFINITY")
    if (numeric.volume < 0).any(): invalid_reasons.append("NEGATIVE_VOLUME")
    if (numeric.high < numeric.low).any(): invalid_reasons.append("HIGH_BELOW_LOW")
    if (numeric.high < numeric[["open", "close"]].max(axis=1)).any(): invalid_reasons.append("HIGH_BELOW_OPEN_CLOSE")
    if (numeric.low > numeric[["open", "close"]].min(axis=1)).any(): invalid_reasons.append("LOW_ABOVE_OPEN_CLOSE")
    if invalid_reasons:
        raise DataQualityError("MIGRATED_CANONICAL_OHLCV_INVALID:" + ",".join(invalid_reasons))
    try:
        partition_year = int(path.parent.name.split("=", 1)[1])
    except (IndexError, ValueError) as exc:
        raise DataQualityError("MIGRATED_CANONICAL_PARTITION_INVALID") from exc
    if not (dates.dt.year == partition_year).all():
        raise DataQualityError("MIGRATED_CANONICAL_YEAR_MISMATCH")
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    descriptor = canonical_snapshot_descriptor(dataset="daily", symbol=symbol, frame=frame,
        file_hash=file_hash, byte_size=path.stat().st_size, schema_version="2",
        price_basis="canonical_adjusted", corporate_action_version="canonical_identity",
        partition_key=f"year={partition_year}")
    return frame, {"path": str(path), "year": partition_year, "physical_sha256": file_hash,
                   "semantic_content_hash": access.semantic_content_hash(frame),
                   "row_count": len(frame), "min_date": str(dates.min().date()),
                   "max_date": str(dates.max().date()), "dataset_fingerprint": descriptor["dataset_fingerprint"],
                   "schema_fingerprint": descriptor["schema_fingerprint"]}


def _reconcile_migrated_candidates(validated: list[tuple[pd.DataFrame, dict[str, Any]]]):
    """Choose one authoritative candidate per year without changing OHLCV."""
    selected = []
    for year in sorted({int(meta["year"]) for _, meta in validated}):
        candidates = [(frame, meta) for frame, meta in validated if int(meta["year"]) == year]
        if len(candidates) == 1:
            selected.append(candidates[0]); continue
        keep = candidates[0]
        for candidate in candidates[1:]:
            left, right = keep[0], candidate[0]
            if keep[1]["semantic_content_hash"] == candidate[1]["semantic_content_hash"]:
                # Byte-identical semantic content is physical redundancy only.
                continue
            def rows_by_date(frame):
                out = frame.copy()
                out["date"] = pd.to_datetime(out["date"]).dt.normalize()
                return out.set_index("date")[["open", "high", "low", "close", "volume"]]
            lmap, rmap = rows_by_date(left), rows_by_date(right)
            overlap = lmap.index.intersection(rmap.index)
            if len(overlap):
                same_overlap = lmap.loc[overlap].equals(rmap.loc[overlap])
                if not same_overlap:
                    raise DataQualityError("MIGRATED_ACTIVE_CONTENT_CONFLICT")
            if lmap.index.isin(rmap.index).all() and len(rmap) >= len(lmap):
                keep = candidate
            elif rmap.index.isin(lmap.index).all():
                continue
            else:
                raise DataQualityError("MIGRATED_CANONICAL_CROSS_PARTITION_DUPLICATE_DATE")
        selected.append(keep)
    return selected


def admit_migrated_daily_symbol(symbol: str, *, decision_as_of: str | None = None,
                                required_warmup_sessions: int = 200, data_access=None,
                                migration_manifest_path=None, read_only: bool = False,
                                required_start: str | None = None) -> dict[str, Any]:
    """Validate and admit migrated daily files through immutable generations.

    The migration catalog is only an eligibility claim. Every physical file is
    independently validated before any promotion is attempted.
    """
    access = data_access or PCSDataAccess.canonical()
    s = str(symbol).strip().upper()
    catalog_path = _migration_catalog_path(access, migration_manifest_path)
    if not catalog_path.exists():
        return {"symbol": s, "status": "MIGRATION_CATALOG_MISSING", "reason_codes": ("MIGRATION_CATALOG_MISSING",), "partitions": ()}
    catalog = pd.read_csv(catalog_path)
    matches = catalog[catalog.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(s)]
    if len(matches) != 1:
        code = "MIGRATION_CATALOG_MISSING" if matches.empty else "MIGRATION_CATALOG_AMBIGUOUS"
        return {"symbol": s, "status": code, "reason_codes": (code,), "partitions": ()}
    if str(matches.iloc[0].get("status", "")).strip().upper() != "SUCCESS":
        return {"symbol": s, "status": "MIGRATION_CATALOG_NOT_SUCCESS", "reason_codes": ("MIGRATION_CATALOG_NOT_SUCCESS",), "partitions": ()}
    paths = _migrated_daily_files(access, s)
    if required_start is not None:
        if decision_as_of is None or pd.Timestamp(required_start) > pd.Timestamp(decision_as_of):
            raise ValueError("ADMISSION_WINDOW_INVALID")
        first_year, last_year = pd.Timestamp(required_start).year, pd.Timestamp(decision_as_of).year
        # Bound the logical partitions, never trim invalid rows from a partition.
        # Older history remains unadmitted and must be validated if requested later.
        paths = tuple(path for path in paths if first_year <= int(path.parent.name.split("=", 1)[1]) <= last_year)
    if not paths:
        return {"symbol": s, "status": "MIGRATION_PHYSICAL_MISSING", "reason_codes": ("MIGRATION_PHYSICAL_MISSING",), "partitions": ()}
    validated = []
    validation_results = []
    validation_failed_partition = None
    validation_unprocessed_partitions = []
    try:
        for position, path in enumerate(paths):
            try:
                frame, meta = _validate_migrated_daily_file(path, s, access)
            except (DataAccessError, DataQualityError, OSError, ValueError):
                validation_failed_partition = path.parent.name if path.parent.name.startswith("year=") else str(path)
                validation_unprocessed_partitions = [
                    candidate.parent.name if candidate.parent.name.startswith("year=") else str(candidate)
                    for candidate in paths[position + 1:]
                ]
                raise
            validated.append((frame, meta))
            validation_results.append({"partition": f"year={meta['year']}", "status": "VALIDATED",
                                       "candidate_path": meta.get("path"), "physical_sha256": meta.get("physical_sha256"),
                                       "semantic_content_hash": meta.get("semantic_content_hash"),
                                       "source": "DAILY_UNIVERSE_MIGRATION_ADMISSION",
                                       "active_generation_before": None, "active_generation_after": None,
                                       "reason_codes": ("MIGRATED_CANONICAL_VALIDATED",)})
        validated = _reconcile_migrated_candidates(validated)
        all_dates = pd.concat([frame[["symbol", "date"]] for frame, _ in validated], ignore_index=True)
        all_dates["date"] = pd.to_datetime(all_dates["date"], errors="coerce").dt.normalize()
        if all_dates["date"].duplicated().any():
            raise DataQualityError("MIGRATED_CANONICAL_CROSS_PARTITION_DUPLICATE_DATE")
        if decision_as_of is not None:
            day = pd.Timestamp(decision_as_of).normalize()
            pit_count = int(all_dates.loc[all_dates["date"] <= day, "date"].nunique())
            latest = pd.Timestamp(all_dates["date"].max())
            if pit_count < int(required_warmup_sessions):
                raise DataQualityError("INSUFFICIENT_FEATURE_WARMUP")
            needs_incremental = latest < day
        else:
            needs_incremental = False
    except (DataAccessError, DataQualityError) as exc:
        conflict_partitions = tuple(sorted({f"year={meta['year']}" for _, meta in validated
                                             if sum(1 for _, candidate in validated
                                                    if int(candidate['year']) == int(meta['year'])) > 1}))
        return {"symbol": s, "status": "MIGRATED_CANONICAL_INVALID", "reason_codes": (str(exc).strip() or type(exc).__name__,),
                "partitions": tuple(meta for _, meta in validated),
                "promoted_partitions": tuple(), "already_admitted": tuple(),
                "promotion_receipts": tuple(),
                "failed_partition": validation_failed_partition,
                "unprocessed_partitions": tuple(validation_unprocessed_partitions),
                "conflict_partitions": conflict_partitions,
                "partition_results": tuple(validation_results + ([{
                    "partition": validation_failed_partition, "status": "FAILED",
                    "active_generation_before": None, "active_generation_after": None,
                    "reason_codes": (str(exc).strip() or type(exc).__name__,)}]
                    if validation_failed_partition else []))}
    if read_only:
        validated_results = []
        try:
            manifest = access._read_manifest(access.manifest_path)
        except Exception:
            manifest = pd.DataFrame()
        for _, meta in validated:
            rows = manifest[(manifest.get("dataset", pd.Series(dtype=str)).astype(str) == "daily") &
                            manifest.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(s) &
                            pd.to_numeric(manifest.get("year", pd.Series(dtype=float)), errors="coerce").eq(int(meta["year"]))] if not manifest.empty else manifest
            active_values = rows.get("active_generation", pd.Series(dtype=str)).astype(str).str.strip()
            active = rows[active_values.notna() & ~active_values.str.lower().isin({"", "nan", "none", "<na>"})] if not rows.empty else rows
            generation = str(active.iloc[-1].get("active_generation")) if not active.empty else None
            validated_results.append({"partition": f"year={meta['year']}", "status": "VALIDATED",
                                      "candidate_path": meta.get("path"), "physical_sha256": meta.get("physical_sha256"),
                                      "semantic_content_hash": meta.get("semantic_content_hash"),
                                      "source": "DAILY_UNIVERSE_MIGRATION_ADMISSION",
                                      "active_generation_before": generation, "active_generation_after": generation,
                                      "reason_codes": ("MIGRATED_CANONICAL_VALIDATED",)})
        return {"symbol": s, "status": "MIGRATED_CANONICAL_VALIDATED", "reason_codes": ("MIGRATED_CANONICAL_FOUND", "MIGRATED_CANONICAL_VALIDATED"),
                "partitions": tuple(meta for _, meta in validated), "needs_incremental": needs_incremental,
                "promoted_partitions": tuple(), "already_admitted": tuple(),
                "promotion_receipts": tuple(), "failed_partition": None,
                "unprocessed_partitions": tuple(),
                "conflict_partitions": tuple(),
                "partition_results": tuple(validated_results)}
    promoted = []
    already = []
    promotion_receipts = []
    partition_results = []
    failed_partition = None
    unprocessed_partitions = []
    try:
        with _MIGRATED_ADMISSION_WRITE_LOCK:
            for position, (frame, meta) in enumerate(validated):
                manifest = access._read_manifest(access.manifest_path)
                year = meta["year"]
                failed_partition = f"year={year}"
                rows = manifest[(manifest.get("dataset", pd.Series(dtype=str)).astype(str) == "daily") &
                                manifest.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(s) &
                                pd.to_numeric(manifest.get("year", pd.Series(dtype=float)), errors="coerce").eq(year)]
                active_values = rows.get("active_generation", pd.Series(dtype=str)).astype(str).str.strip()
                active = rows[active_values.notna() & active_values.ne("") &
                              ~active_values.str.lower().isin({"nan", "none", "<na>"})] if not rows.empty else rows
                active_before = str(active.iloc[-1].get("active_generation", "")).strip() if not active.empty else None
                if not active.empty:
                    current = active.iloc[-1]
                    current_path = Path(str(current.get("parquet_path", "")))
                    if not current_path.exists():
                        raise DataAccessError("ACTIVE_GENERATION_PATH_MISSING")
                    try:
                        current_frame = pd.read_parquet(current_path)
                    except Exception as exc:
                        raise DataAccessError("ACTIVE_GENERATION_UNREADABLE") from exc
                    if access.semantic_content_hash(current_frame) != meta["semantic_content_hash"] or len(current_frame) != meta["row_count"]:
                        raise DataQualityError("MIGRATED_ACTIVE_CONTENT_CONFLICT")
                    already.append(f"year={year}")
                    partition_results.append({"partition": f"year={year}", "status": "REUSED",
                                              "candidate_path": meta.get("path"), "physical_sha256": meta.get("physical_sha256"),
                                              "semantic_content_hash": meta.get("semantic_content_hash"),
                                              "source": "DAILY_UNIVERSE_MIGRATION_ADMISSION",
                                              "active_generation_before": active_before,
                                              "active_generation_after": active_before,
                                              "reason_codes": ("MIGRATED_CANONICAL_ALREADY_ADMITTED",)})
                    continue
                receipt = access.promote_generation(frame, "daily", s, f"year={year}", source_version="DAILY_UNIVERSE_MIGRATION_ADMISSION")
                if not hasattr(receipt, "generation_id"):
                    raise DataAccessError("MIGRATION_ADMISSION_IDEMPOTENCY_UNVERIFIED")
                promotion_receipts.append(receipt.to_dict() if hasattr(receipt, "to_dict") else dict(receipt))
                record = access.active_generation_record("daily", s, f"year={year}")
                read_back = access.read_pinned_generation("daily", s, f"year={year}", str(record.get("active_generation")))
                if len(read_back) != meta["row_count"] or access.semantic_content_hash(read_back) != meta["semantic_content_hash"]:
                    raise DataQualityError("MIGRATION_ADMISSION_READ_BACK_MISMATCH")
                promoted.append(f"year={year}")
                receipt_dict = promotion_receipts[-1]
                partition_results.append({"partition": f"year={year}", "status": "PROMOTED",
                                          "candidate_path": meta.get("path"), "physical_sha256": meta.get("physical_sha256"),
                                          "semantic_content_hash": meta.get("semantic_content_hash"),
                                          "source": "DAILY_UNIVERSE_MIGRATION_ADMISSION",
                                          "active_generation_before": active_before,
                                          "active_generation_after": receipt_dict.get("manifest_active_generation_id") or str(record.get("active_generation", "")),
                                          "reason_codes": ("MIGRATED_CANONICAL_ADMITTED",),
                                          "promotion_receipt": receipt_dict})
    except (DataAccessError, DataQualityError, OSError, ValueError) as exc:
        if failed_partition is not None:
            active_after = None
            try:
                failed_year = int(str(failed_partition).split("=", 1)[1])
                current_manifest = access._read_manifest(access.manifest_path)
                current_rows = current_manifest[(current_manifest.get("dataset", pd.Series(dtype=str)).astype(str) == "daily") &
                                                current_manifest.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(s) &
                                                pd.to_numeric(current_manifest.get("year", pd.Series(dtype=float)), errors="coerce").eq(failed_year)]
                values = current_rows.get("active_generation", pd.Series(dtype=str)).astype(str).str.strip()
                active_rows = current_rows[values.notna() & ~values.str.lower().isin({"", "nan", "none", "<na>"})]
                if not active_rows.empty:
                    active_after = str(active_rows.iloc[-1].get("active_generation", "")).strip() or None
            except Exception:
                active_after = None
            partition_results.append({"partition": failed_partition, "status": "FAILED",
                                      "source": "DAILY_UNIVERSE_MIGRATION_ADMISSION",
                                      "active_generation_before": active_before if 'active_before' in locals() else None,
                                      "active_generation_after": active_after,
                                      "reason_codes": (str(exc).strip() or type(exc).__name__,)})
            unprocessed_partitions = [f"year={meta['year']}" for _, meta in validated[position + 1:]]
        return {"symbol": s, "status": "ADMISSION_INCOMPLETE" if promoted else "MIGRATION_ADMISSION_FAILED",
                "reason_codes": (str(exc).strip() or type(exc).__name__,), "partitions": tuple(meta for _, meta in validated),
                "promoted_partitions": tuple(promoted), "already_admitted": tuple(already),
                "promotion_receipts": tuple(promotion_receipts), "failed_partition": failed_partition,
                "unprocessed_partitions": tuple(unprocessed_partitions), "conflict_partitions": tuple(),
                "partition_results": tuple(partition_results)}
    status = "ADMITTED_NEEDS_INCREMENTAL" if needs_incremental else "ALREADY_ADMITTED" if not promoted else "ADMITTED_READY"
    reasons = ("MIGRATED_CANONICAL_ALREADY_ADMITTED",) if not promoted else ("MIGRATED_CANONICAL_ADMITTED",)
    return {"symbol": s, "status": status, "reason_codes": reasons,
            "partitions": tuple(meta for _, meta in validated), "promoted_partitions": tuple(promoted),
            "already_admitted": tuple(already), "promotion_receipts": tuple(promotion_receipts),
            "failed_partition": None, "unprocessed_partitions": tuple(), "needs_incremental": needs_incremental,
            "conflict_partitions": tuple(), "partition_results": tuple(partition_results)}

def register_active_generation_provenance(*, dataset: str, symbol: str, generation_id: str,
                                          price_basis: str, corporate_action_version: str,
                                          data_access: PCSDataAccess | None = None) -> dict[str, Any]:
    """Explicit ADMIN operation to register identity for an existing generation.

    This never discovers or downloads data. It verifies the precisely named
    active object and records only its independent provenance fingerprints.
    """
    access = data_access or PCSDataAccess(); s = str(symbol).strip().upper()
    with access._file_lock(access.manifest_path):
        before_hash = hashlib.sha256(access.manifest_path.read_bytes()).hexdigest() if access.manifest_path.exists() else ""
        current = access._read_manifest(access.manifest_path)
        current_hash = hashlib.sha256(access.manifest_path.read_bytes()).hexdigest() if access.manifest_path.exists() else ""
        if current_hash != before_hash:
            raise DataAccessError("PROVENANCE_PLAN_STALE")
        matches = current[(current.dataset.astype(str) == str(dataset)) & current.symbol.astype(str).str.upper().eq(s) &
                          current.active_generation.astype(str).eq(str(generation_id))]
        if len(matches) != 1: raise DataAccessError("ACTIVE_GENERATION_NOT_UNIQUE")
        row = matches.iloc[0]; path = Path(_strict_text(row.parquet_path, "ACTIVE_GENERATION_PATH_MISSING"))
        for field in ("schema_version", "price_basis", "corporate_action_version"):
            value = row.get(field)
            if value is None or pd.isna(value) or not str(value).strip() or str(value).lower() == "nan":
                raise DataAccessError("DATASET_PROVENANCE_INCOMPLETE")
        if not path.exists(): raise DataAccessError("ACTIVE_GENERATION_PATH_MISSING")
        frame = pd.read_parquet(path); checksum = access.semantic_content_hash(frame)
        if str(row.get("content_hash", "")) != checksum: raise DataQualityError("CONTENT_HASH_MISMATCH")
        partition = "/".join([f"year={int(row.year)}"] + ([f"quarter={int(row.quarter)}"] if pd.notna(row.get("quarter")) else []))
        descriptor = canonical_snapshot_descriptor(dataset=dataset, symbol=s, frame=frame,
            file_hash=hashlib.sha256(path.read_bytes()).hexdigest(), byte_size=path.stat().st_size,
            schema_version=str(row.get("schema_version") or "2"), price_basis=price_basis,
            corporate_action_version=corporate_action_version, partition_key=partition)
        for column in ("schema_fingerprint", "dataset_fingerprint", "price_basis", "corporate_action_version", "lifecycle_status", "superseded_by"):
            if column not in current: current[column] = ""
        current.loc[matches.index, "schema_fingerprint"] = descriptor["schema_fingerprint"]
        current.loc[matches.index, "dataset_fingerprint"] = descriptor["dataset_fingerprint"]
        current.loc[matches.index, "price_basis"] = price_basis
        current.loc[matches.index, "corporate_action_version"] = corporate_action_version
        current.loc[matches.index, "lifecycle_status"] = "ACTIVE"
        tmp = access.manifest_path.with_name(f".{access.manifest_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            current.to_csv(tmp, index=False)
            access._atomic_replace_manifest(tmp, access.manifest_path)
        finally:
            tmp.unlink(missing_ok=True)
        access._manifest = current
    return {"status": "REGISTERED", "generation_id": str(generation_id), "dataset_fingerprint": descriptor["dataset_fingerprint"], "snapshot_descriptor": descriptor}

REPAIR_ACTION_POLICY = {
    "HEALTHY": {"action": "NO_ACTION", "safe_to_apply": True, "owner_approval_required": False, "destructive": False},
    "DUPLICATE_LEGACY_FILES": {"action": "MARK_LEGACY_REDUNDANT", "safe_to_apply": True, "owner_approval_required": False, "destructive": False},
    "LEGACY_FIXED_TARGET": {"action": "REGISTER_LEGACY_AS_GENERATION", "safe_to_apply": True, "owner_approval_required": False, "destructive": False},
    "UNTRACKED_TRUSTED_FILE": {"action": "REGISTER_LEGACY_AS_GENERATION", "safe_to_apply": True, "owner_approval_required": False, "destructive": False},
    "ORPHANED_GENERATION": {"action": "NO_ACTION", "safe_to_apply": False, "owner_approval_required": True, "destructive": False},
    "DANGLING_MANIFEST": {"action": "ROLLBACK_TO_PREVIOUS", "safe_to_apply": False, "owner_approval_required": True, "destructive": False},
    "ACTIVE_GENERATION_MISSING": {"action": "ROLLBACK_TO_PREVIOUS", "safe_to_apply": False, "owner_approval_required": True, "destructive": False},
    "CONTENT_HASH_MISMATCH": {"action": "QUARANTINE_OBJECT", "safe_to_apply": False, "owner_approval_required": True, "destructive": False},
    "OVERLAPPING_CONFLICT": {"action": "NO_ACTION", "safe_to_apply": False, "owner_approval_required": True, "destructive": False},
}

@dataclass(frozen=True)
class RepairPlan:
    dataset: str; partition: dict[str, Any]; status: str; classifications: tuple[str, ...]
    current_manifest: dict[str, Any] | None; physical_generations: tuple[dict[str, Any], ...]
    legacy_files: tuple[str, ...]; proposed_actions: tuple[dict[str, Any], ...]; safe_to_apply: bool
    operation_id: str
    repair_plan_id: str = ""
    created_at: str = ""
    observed_manifest_hash: str = ""
    relationships: tuple[dict[str, Any], ...] = ()
    def to_dict(self): return asdict(self) | {"classifications": list(self.classifications), "physical_generations": list(self.physical_generations), "legacy_files": list(self.legacy_files), "proposed_actions": list(self.proposed_actions), "relationships": list(self.relationships)}

def _part(symbol, year, quarter): return {"symbol": str(symbol).upper(), "year": int(year), "quarter": int(quarter)}
def _rows(access, dataset, p):
    m = access._read_manifest(access.manifest_path)
    if m.empty: return m
    return m[(m.dataset.astype(str)==dataset) & m.symbol.astype(str).str.upper().eq(p["symbol"]) & m.year.astype(str).eq(str(p["year"])) & m.quarter.astype(str).eq(str(p["quarter"]))]

def plan_canonical_repair(*, dataset: str, symbol: str, year: int, quarter: int, data_access: PCSDataAccess | None = None) -> RepairPlan:
    access = data_access or PCSDataAccess(); p = _part(symbol, year, quarter); rows = _rows(access, dataset, p)
    root = access.parquet_root / dataset / f"symbol={p['symbol']}" / f"year={year}" / f"quarter={quarter}"
    generations = []
    for f in sorted((root / "generations").glob("*.parquet")) if (root / "generations").exists() else []:
        frame = pd.read_parquet(f); digest = access.semantic_content_hash(frame)
        file_hash = hashlib.sha256(f.read_bytes()).hexdigest()
        generations.append({"path": str(f), "generation": f.stem, "content_hash": digest, "file_hash": file_hash, "hash_valid": digest.startswith(f.stem), "row_count": len(frame), "min_date": str(pd.to_datetime(frame.trade_date).min().date()) if "trade_date" in frame else None, "max_date": str(pd.to_datetime(frame.trade_date).max().date()) if "trade_date" in frame else None})
    legacy = [str(f) for f in root.glob("*.parquet")]
    classifications=[]; current = rows.iloc[-1].to_dict() if len(rows) else None
    manifest_hash = hashlib.sha256(access.manifest_path.read_bytes()).hexdigest() if access.manifest_path.exists() else ""
    active = str(current.get("active_generation", "")) if current else ""
    if current and not active: classifications.append("LEGACY_FIXED_TARGET")
    if not current and legacy: classifications.append("UNTRACKED_TRUSTED_FILE")
    if current and active and not any(x["generation"] == active and x["hash_valid"] for x in generations): classifications.append("ACTIVE_GENERATION_MISSING")
    if current and active and any(x["generation"] == active and not x["hash_valid"] for x in generations): classifications.append("CONTENT_HASH_MISMATCH")
    if current and str(current.get("parquet_path", "")) and not Path(str(current["parquet_path"])).exists(): classifications.append("DANGLING_MANIFEST")
    referenced={str(current.get("active_generation", "")),str(current.get("previous_generation", ""))} if current else set()
    if any(x["generation"] not in referenced for x in generations): classifications.append("ORPHANED_GENERATION")
    if not classifications: classifications=["HEALTHY"]
    actions=[]; safe=True
    if "LEGACY_FIXED_TARGET" in classifications or "UNTRACKED_TRUSTED_FILE" in classifications:
        actions.append({"action":"MIGRATE_LEGACY","safe":True})
    if any(x in classifications for x in ("CONTENT_HASH_MISMATCH","DANGLING_MANIFEST","ACTIVE_GENERATION_MISSING")):
        safe=False; actions.append({"action":"OWNER_REVIEW_REQUIRED","safe":False})
    objects = [{"path": x["path"], "content_hash": x["content_hash"], "file_hash": x["file_hash"], "row_count": x["row_count"], "type": "GENERATION"} for x in generations]
    for path in legacy:
        frame=pd.read_parquet(path); objects.append({"path":path,"type":"LEGACY","content_hash":access.semantic_content_hash(frame),"file_hash":hashlib.sha256(Path(path).read_bytes()).hexdigest(),"row_count":len(frame)})
    relationships=[]
    for left in objects:
        for right in objects:
            if left["path"] >= right["path"]: continue
            a=pd.read_parquet(left["path"]); b=pd.read_parquet(right["path"]); keys=[c for c in ("symbol","trade_date","expiration_date","call_put","strike") if c in a and c in b]
            ka=set(map(tuple,a[keys].astype(str).itertuples(index=False,name=None))); kb=set(map(tuple,b[keys].astype(str).itertuples(index=False,name=None)))
            relationships.append({"left":left["path"],"right":right["path"],"relation":"EXACT_EQUAL" if left["content_hash"]==right["content_hash"] else "STRICT_SUBSET" if ka<kb else "STRICT_SUPERSET" if kb<ka else "OVERLAPPING_CONFLICT" if ka&kb else "DISJOINT","pk_count_left":len(ka),"pk_count_right":len(kb),"shared_pk_count":len(ka&kb),"conflicting_pk_count":0,"only_left_count":len(ka-kb),"only_right_count":len(kb-ka)})
    if any(r["relation"] == "OVERLAPPING_CONFLICT" for r in relationships):
        classifications.append("OVERLAPPING_CONFLICT"); safe = False; actions.append({"action":"NO_AUTOMATIC_ACTION","safe":False,"owner_approval_required":True})
    return RepairPlan(dataset,p,"REPAIR_REQUIRED" if classifications != ["HEALTHY"] else "HEALTHY",tuple(dict.fromkeys(classifications)),current,tuple(generations),tuple(legacy),tuple(actions),safe,uuid.uuid4().hex,uuid.uuid4().hex,datetime.now(timezone.utc).isoformat(),manifest_hash,tuple(relationships))

def persist_repair_plan(plan: RepairPlan, root="data/manifests/repair_plans") -> Path:
    path=Path(root)/f"{plan.repair_plan_id}.json"; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(plan.to_dict(),sort_keys=True,indent=2,default=str),encoding="utf-8"); return path

def apply_repair_plan(repair_plan_id: str, *, data_access: PCSDataAccess | None = None, root="data/manifests/repair_plans") -> dict[str, Any]:
    access=data_access or PCSDataAccess(); path=Path(root)/f"{repair_plan_id}.json"
    if not path.exists(): raise DataAccessError("REPAIR_PLAN_NOT_FOUND")
    raw=json.loads(path.read_text(encoding="utf-8")); p=raw["partition"]
    current=plan_canonical_repair(dataset=raw["dataset"],symbol=p["symbol"],year=p["year"],quarter=p["quarter"],data_access=access).to_dict()
    if current.get("observed_manifest_hash") != raw.get("observed_manifest_hash") or current.get("physical_generations") != raw.get("physical_generations") or current.get("legacy_files") != raw.get("legacy_files") or current.get("relationships") != raw.get("relationships"):
        return {"status":"BLOCKED","reason_codes":["REPAIR_PLAN_STALE"],"repair_plan_id":repair_plan_id}
    if not raw.get("safe_to_apply"):
        return {"status":"BLOCKED","reason_codes":["OWNER_APPROVAL_REQUIRED"],"repair_plan_id":repair_plan_id}
    action_names={x.get("action") for x in raw.get("proposed_actions", [])}
    if action_names == {"MIGRATE_LEGACY"}:
        result=migrate_legacy(plan_canonical_repair(dataset=raw["dataset"],symbol=p["symbol"],year=p["year"],quarter=p["quarter"],data_access=access),data_access=access)
    else: result={"status":"NO_ACTION"}
    return {"repair_plan_id":repair_plan_id, **result}

def migrate_legacy(plan: RepairPlan, *, data_access: PCSDataAccess | None = None) -> dict[str, Any]:
    if not plan.safe_to_apply or not any(a.get("action")=="MIGRATE_LEGACY" and a.get("safe") for a in plan.proposed_actions): raise DataAccessError("REPAIR_ACTION_NOT_APPROVED")
    access=data_access or PCSDataAccess(); legacy=Path(plan.legacy_files[0]); frame=pd.read_parquet(legacy)
    path=access.promote_generation(frame, plan.dataset, plan.partition["symbol"], f"year={plan.partition['year']}/quarter={plan.partition['quarter']}", source_version="legacy_migration")
    return {"status":"MIGRATED","operation_id":plan.operation_id,"path":str(path),"legacy_preserved":True}

def list_generations(*, dataset, symbol, year, quarter, data_access=None):
    plan=plan_canonical_repair(dataset=dataset,symbol=symbol,year=year,quarter=quarter,data_access=data_access)
    rows=[]; active=(plan.current_manifest or {}).get("active_generation"); previous=(plan.current_manifest or {}).get("previous_generation")
    for item in plan.physical_generations: rows.append(item | {"state":"ACTIVE" if item["generation"]==active else "PREVIOUS" if item["generation"]==previous else "ORPHAN"})
    rows.extend({"path":x,"state":"LEGACY"} for x in plan.legacy_files)
    return {"dataset":dataset,"partition":plan.partition,"generations":rows}
