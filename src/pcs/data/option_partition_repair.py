"""Explicit repair workflow for already-written canonical option partitions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import tempfile
import uuid
import pandas as pd

from .access import PCSDataAccess, DataQualityError
from .storage_schema import audit_option_frame


def repair_option_partitions(symbol: str, dataset: str, *, access: PCSDataAccess | None = None,
                             start_date=None, end_date=None) -> dict:
    """Quarantine invalid rows and atomically replace declared canonical partitions.

    Valid rows are written unchanged in value (only canonical date/string
    normalization from ``audit_option_frame`` is applied). Manifest and
    provenance are replaced only after every staged partition validates.
    """
    access = access or PCSDataAccess.canonical()
    symbol = str(symbol).upper()
    if dataset not in {"options_v2", "options_v3"}:
        raise DataQualityError("OPTION_REPAIR_REQUIRES_PHYSICAL_OPTIONS_DATASET")
    resolved, manifest_path, parquet_root = access._resolve_route(dataset, symbol)
    if resolved != dataset:
        raise DataQualityError(f"OPTION_REPAIR_ROUTE_MISMATCH:{resolved}")
    manifest = pd.read_csv(manifest_path)
    rows = manifest[(manifest.dataset == dataset) & manifest.symbol.astype(str).str.upper().eq(symbol)].copy()
    if start_date is not None:
        rows = rows[pd.to_datetime(rows.max_date) >= pd.Timestamp(start_date)]
    if end_date is not None:
        rows = rows[pd.to_datetime(rows.min_date) <= pd.Timestamp(end_date)]
    if rows.empty:
        raise FileNotFoundError(f"NO_CANONICAL_PARTITIONS:{dataset}:{symbol}")
    run_id = uuid.uuid4().hex
    staged = []
    quarantine_staged = []
    summary = {"module": "pcs.data.option_partition_repair", "version": "1.0", "symbol": symbol,
               "dataset": dataset, "run_id": run_id, "partitions": [], "status": "FAILED"}
    try:
        with tempfile.TemporaryDirectory(prefix=f".pcs-option-repair-{symbol}-", dir=str(parquet_root)) as temp_root:
            temp_root = Path(temp_root)
            updated = manifest.copy()
            prov_path = Path(manifest_path).with_name("data_provenance_manifest.csv")
            provenance = pd.read_csv(prov_path) if prov_path.exists() else pd.DataFrame()
            updated_prov = provenance.copy()
            for column in ("repair_run_id", "quarantined_rows", "repair_reason_breakdown"):
                if column not in updated_prov:
                    updated_prov[column] = pd.Series(index=updated_prov.index, dtype="object")
                else:
                    updated_prov[column] = updated_prov[column].astype("object")
            for _, row in rows.sort_values(["year", "quarter"]).iterrows():
                part = f"year={int(row.year)}/quarter={int(row.quarter)}"
                target = Path(str(row.parquet_path))
                if not target.is_absolute(): target = Path.cwd() / target
                frame = pd.read_parquet(target)
                valid, invalid, quality = audit_option_frame(
                    frame, source="canonical_repair", source_file=row.get("source_file"),
                    source_version=row.get("source_file"), partition=part,
                )
                stage = temp_root / f"{target.name}.valid"
                valid.to_parquet(stage, index=False)
                staged.append((stage, target))
                if len(invalid):
                    qdir = parquet_root / "quarantine" / dataset / f"symbol={symbol}" / part
                    qstage = temp_root / f"{target.name}.quarantine"
                    invalid.to_parquet(qstage, index=False)
                    quarantine_staged.append((qstage, qdir / f"{symbol}_{part.replace('=', '_').replace('/', '_')}.quarantine.parquet"))
                mask = (updated.dataset == dataset) & updated.symbol.astype(str).str.upper().eq(symbol) & updated.year.astype(int).eq(int(row.year)) & updated.quarter.astype(int).eq(int(row.quarter))
                updated.loc[mask, "row_count"] = len(valid)
                updated.loc[mask, "status"] = "SUCCESS"
                summary["partitions"].append({"partition": part, "path": str(target), "source_version": row.get("source_file"), **quality})
                if not updated_prov.empty:
                    years = pd.to_numeric(updated_prov.get("year", pd.Series(index=updated_prov.index, dtype=float)), errors="coerce")
                    quarters = pd.to_numeric(updated_prov.get("quarter", pd.Series(index=updated_prov.index, dtype=float)), errors="coerce")
                    pmask = (updated_prov.get("dataset", pd.Series(dtype=str)).astype(str).eq(dataset) & updated_prov.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(symbol) & years.eq(int(row.year)) & quarters.eq(int(row.quarter)))
                    updated_prov.loc[pmask, "row_count"] = len(valid)
                    updated_prov.loc[pmask, "status"] = "REBUILT_VALIDATED"
                    updated_prov.loc[pmask, "repair_run_id"] = run_id
                    existing_q = list((parquet_root / "quarantine" / dataset / f"symbol={symbol}" / part).glob("*.quarantine.parquet"))
                    prior_q = sum(len(pd.read_parquet(path, columns=["reason_code"])) for path in existing_q) if existing_q else 0
                    updated_prov.loc[pmask, "quarantined_rows"] = max(len(invalid), prior_q)
                    updated_prov.loc[pmask, "repair_reason_breakdown"] = json.dumps(quality["reason_breakdown"], sort_keys=True)
            # Stage metadata on the same volume, then commit data, quarantine,
            # manifest, and provenance under one lock with rollback bytes.
            manifest_bytes = Path(manifest_path).read_bytes(); prov_bytes = prov_path.read_bytes() if prov_path.exists() else None
            backups = {target: target.read_bytes() for _, target in staged}
            try:
                for stage, target in staged: os.replace(stage, target)
                for stage, target in quarantine_staged:
                    target.parent.mkdir(parents=True, exist_ok=True); os.replace(stage, target)
                mtmp = Path(manifest_path).with_suffix(f".{run_id}.tmp"); updated.to_csv(mtmp, index=False); os.replace(mtmp, manifest_path)
                if not updated_prov.empty:
                    ptmp = prov_path.with_suffix(f".{run_id}.tmp"); updated_prov.to_csv(ptmp, index=False); os.replace(ptmp, prov_path)
            except Exception:
                for target, old in backups.items(): target.write_bytes(old)
                Path(manifest_path).write_bytes(manifest_bytes)
                if prov_bytes is None: prov_path.unlink(missing_ok=True)
                else: prov_path.write_bytes(prov_bytes)
                raise
        summary["status"] = "COMPLETED"
        summary["committed_at"] = datetime.now(timezone.utc).isoformat()
        return summary
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}:{exc}"
        raise


__all__ = ["repair_option_partitions"]
