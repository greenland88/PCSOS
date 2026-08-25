"""Generic staged historical correction workflow for canonical partitions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import hashlib, os, shutil, tempfile, uuid
from typing import Any

import pandas as pd

from .access import PCSDataAccess, DataQualityError


@dataclass
class CorrectionResult:
    module: str = "pcs.data.historical_correction"
    version: str = "1.0"
    symbol: str = ""
    status: str = "FAILED"
    correction_reason: str = ""
    source_version: str = ""
    affected_range: dict[str, str] = field(default_factory=dict)
    EXPECTED_CHANGED_PARTITIONS: list[str] = field(default_factory=list)
    ACTUAL_CHANGED_PARTITIONS: list[str] = field(default_factory=list)
    UNEXPECTED_CHANGED_PARTITIONS: list[str] = field(default_factory=list)
    POST_CORRECTION_READY: str = "NO"
    ROLLBACK_REQUIRED: str = "NO"
    rollback_verified: bool = False
    derived_invalidations: list[str] = field(default_factory=list)
    post_readiness_status: str = "NOT_RUN"
    snapshot_hashes: dict[str, str] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def correct_partitions(symbol: str, dataset: str, replacement: pd.DataFrame,
                       *, affected_partitions: list[str], source_version: str,
                       correction_reason: str, access: PCSDataAccess | None = None) -> CorrectionResult:
    """Atomically replace only declared canonical partitions.

    The caller must declare partition identities. No unlisted partition is
    touched; validation failure restores both files and the manifest.
    """
    s = str(symbol).upper(); result = CorrectionResult(symbol=s, correction_reason=correction_reason, source_version=source_version, EXPECTED_CHANGED_PARTITIONS=sorted(set(affected_partitions)))
    if not correction_reason.strip() or not source_version.strip():
        result.reason_codes.append("CORRECTION_METADATA_INCOMPLETE"); return result
    access = access or PCSDataAccess()
    if dataset not in {"daily", "options_v2", "options_v3"}:
        result.reason_codes.append("CORRECTION_DATASET_NOT_CANONICAL"); return result
    try:
        resolved_dataset, manifest_path, parquet_root = access._resolve_route(dataset, s)
        incoming = replacement.copy()
        if "symbol" not in incoming:
            incoming["symbol"] = s
        checked = access.validate_schema(incoming, resolved_dataset)
        access.validate_coverage(checked, s, date_column="trade_date" if resolved_dataset.startswith("options") else "date")
        key = ["symbol", "trade_date", "expiration_date", "call_put", "strike"] if resolved_dataset.startswith("options") else ["symbol", "date"]
        if checked.duplicated(key, keep=False).any():
            raise DataQualityError("CORRECTION_DUPLICATE_CANONICAL_KEYS")
        manifest = pd.read_csv(manifest_path) if Path(manifest_path).exists() else pd.DataFrame()
        if manifest.empty: raise DataQualityError("CORRECTION_MANIFEST_MISSING")
        date_col = "trade_date" if resolved_dataset.startswith("options") else "date"
        checked[date_col] = pd.to_datetime(checked[date_col]).dt.normalize()
        result.affected_range = {"start": str(checked[date_col].min().date()), "end": str(checked[date_col].max().date())}
        targets: dict[str, Path] = {}
        for part in result.EXPECTED_CHANGED_PARTITIONS:
            rows = manifest[(manifest.dataset == resolved_dataset) & manifest.symbol.astype(str).str.upper().eq(s)]
            for component in part.split("/"):
                if "=" in component:
                    k, v = component.split("=", 1); rows = rows[rows.get(k, pd.Series(index=rows.index, dtype=object)).astype(str).eq(v)]
            if rows.empty:
                targets[part] = parquet_root / resolved_dataset / f"symbol={s}" / part / f"{s}_{part.replace('=', '_').replace('/', '_')}.parquet"
            else:
                raw = str(rows.iloc[0].get("parquet_path", "")); targets[part] = Path(raw) if Path(raw).is_absolute() else Path(raw)
        # Stage on the same volume as the canonical target so the final
        # os.replace remains atomic on Windows (cross-volume replace fails).
        stage_parent = next(iter(targets.values())).parent
        stage_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".pcs-correction-{s}-", dir=str(stage_parent)) as temp_name:
            temp = Path(temp_name); backups: dict[Path, bytes | None] = {}; manifest_bytes = Path(manifest_path).read_bytes(); staged: dict[str, Path] = {}
            for part, target in targets.items():
                target.parent.mkdir(parents=True, exist_ok=True); backups[target] = target.read_bytes() if target.exists() else None
                subset = checked.copy()
                if "year=" in part:
                    year = int(part.split("year=", 1)[1].split("/", 1)[0]); subset = subset[subset[date_col].dt.year.eq(year)]
                if "quarter=" in part:
                    quarter = int(part.split("quarter=", 1)[1].split("/", 1)[0]); subset = subset[subset[date_col].dt.quarter.eq(quarter)]
                if subset.empty: raise DataQualityError(f"CORRECTION_PARTITION_EMPTY:{part}")
                stage = temp / target.name; subset.to_parquet(stage, index=False); staged[part] = stage
                result.snapshot_hashes[part] = _sha(target) if target.exists() else "MISSING"
            try:
                for part, target in targets.items():
                    os.replace(staged[part], target); result.ACTUAL_CHANGED_PARTITIONS.append(part)
                    frame = pd.read_parquet(target); access.update_manifest(resolved_dataset, s, frame, target, source_version, part, replace_existing=True)
                unexpected = sorted(set(result.ACTUAL_CHANGED_PARTITIONS) - set(result.EXPECTED_CHANGED_PARTITIONS)); result.UNEXPECTED_CHANGED_PARTITIONS = unexpected
                if unexpected: raise DataQualityError("CORRECTION_UNEXPECTED_PARTITION_MUTATION")
                from .incremental_update import invalidate_current_derived
                result.derived_invalidations = invalidate_current_derived(s, result.EXPECTED_CHANGED_PARTITIONS)
                result.POST_CORRECTION_READY = "YES"; result.post_readiness_status = "REFRESH_REQUIRED"; result.status = "COMPLETED"; result.reason_codes.append("CORRECTION_ATOMIC_COMMIT")
            except Exception as exc:
                result.ROLLBACK_REQUIRED = "YES"
                for target, old in backups.items():
                    if old is None: target.unlink(missing_ok=True)
                    else: target.write_bytes(old)
                Path(manifest_path).write_bytes(manifest_bytes)
                result.rollback_verified = all((not p.exists() if old is None else _sha(p) == hashlib.sha256(old).hexdigest()) for p, old in backups.items()) and Path(manifest_path).read_bytes() == manifest_bytes
                result.reason_codes.append(type(exc).__name__ + ":" + str(exc))
                result.reason_codes.append("CORRECTION_ROLLED_BACK")
    except Exception as exc:
        result.reason_codes.append(type(exc).__name__ + ":" + str(exc))
    return result


__all__ = ["CorrectionResult", "correct_partitions"]
