from __future__ import annotations

import argparse
import csv
import hashlib
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import pandas as pd

from .daily_provider import DailyDataError, normalize_daily_frame
from .parquet_store import read_daily_source
from .storage_schema import DAILY_FIELDS


MODULE = "daily_snapshot_import"
VERSION = "1.0"
CALCULATION_VERSION = "daily-snapshot-import-v1"
CHINESE_REQUIRED = ("日期", "代码", "开盘价", "最高价", "最低价", "收盘价", "成交量")
DAILY_FILE_GLOB = "daily_*.csv"
DEFAULT_MANIFEST_PATH = "data/manifests/daily_snapshot_import_manifest.csv"
DEFAULT_PROVENANCE_PATH = "data/manifests/daily_snapshot_provenance_manifest.csv"
PROVENANCE_COLUMNS = ["source", "source_table", "source_path", "query_start", "query_end", "rows_written", "sha256", "run_id", "request_id", "status", "manifest_path", "sync_timestamp"]


class DailySnapshotImportStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class DailySnapshotImportReason(str, Enum):
    EMPTY_SOURCE = "EMPTY_SOURCE"
    MISSING_COLUMNS = "MISSING_COLUMNS"
    INVALID_DAILY_DATA = "INVALID_DAILY_DATA"
    SKIPPED_INVALID_ROWS = "SKIPPED_INVALID_ROWS"


@dataclass(frozen=True)
class DailySnapshotImportResult:
    module: str
    version: str
    symbol: str
    as_of: str
    status: str
    data_timestamp: str
    calculation_version: str
    run_id: str
    request_id: str
    reason_codes: list[str] = field(default_factory=list)
    source_path: str = ""
    historical_root: str = ""
    rows_read: int = 0
    symbols_read: int = 0
    symbols_written: int = 0
    rows_written: int = 0
    skipped_invalid_rows: int = 0
    skipped_invalid_symbols: list[str] = field(default_factory=list)
    files_created: int = 0
    files_updated: int = 0
    parquet_synced: bool = False
    parquet_symbols_synced: int = 0
    parquet_partitions_written: int = 0
    parquet_rows_written: int = 0
    explanation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _now_utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def find_latest_daily_snapshot(daily_root: str | Path = "data/raw/daily") -> Path:
    daily_root = Path(daily_root)
    files = sorted(daily_root.glob(DAILY_FILE_GLOB), key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    if not files:
        raise FileNotFoundError(f"no daily snapshot files found in {daily_root} matching {DAILY_FILE_GLOB}")
    return files[0]


def find_daily_snapshots(daily_root: str | Path = "data/raw/daily") -> list[Path]:
    daily_root = Path(daily_root)
    files = sorted(daily_root.glob(DAILY_FILE_GLOB), key=lambda path: path.name)
    if not files:
        raise FileNotFoundError(f"no daily snapshot files found in {daily_root} matching {DAILY_FILE_GLOB}")
    return files


def _file_fingerprint(path: Path) -> dict:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"source_path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}


def _read_manifest(path: Path) -> pd.DataFrame:
    columns = ["source_path", "size", "mtime_ns", "sha256", "status", "as_of", "processed_at"]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    manifest = pd.read_csv(path)
    for col in columns:
        if col not in manifest:
            manifest[col] = None
    return manifest[columns]


def _manifest_has_success(manifest: pd.DataFrame, fingerprint: dict) -> bool:
    if manifest.empty:
        return False
    matches = manifest[
        (manifest["source_path"].astype(str) == fingerprint["source_path"])
        & (manifest["size"].astype("Int64") == fingerprint["size"])
        & (manifest["mtime_ns"].astype("Int64") == fingerprint["mtime_ns"])
        & (manifest["sha256"].astype(str) == fingerprint["sha256"])
        & (manifest["status"].astype(str) == DailySnapshotImportStatus.SUCCESS.value)
    ]
    return not matches.empty


def _update_manifest(path: Path, manifest: pd.DataFrame, fingerprint: dict, result: DailySnapshotImportResult) -> pd.DataFrame:
    row = {
        **fingerprint,
        "status": result.status,
        "as_of": result.as_of,
        "processed_at": _now_utc(),
    }
    kept = manifest[manifest["source_path"].astype(str) != fingerprint["source_path"]] if not manifest.empty else manifest
    out = pd.concat([kept, pd.DataFrame([row])], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    out.sort_values("source_path").to_csv(tmp, index=False)
    os.replace(tmp, path)
    return out


def _append_provenance(path: Path, fingerprint: dict, result: DailySnapshotImportResult, manifest_path: Path) -> None:
    """Record every processed daily snapshot as an auditable source event."""
    row = {"source": "daily_snapshot_csv", "source_table": "daily_snapshot", "source_path": fingerprint["source_path"],
           "query_start": result.data_timestamp[:10] if result.status == DailySnapshotImportStatus.SUCCESS.value else None,
           "query_end": result.as_of if result.status == DailySnapshotImportStatus.SUCCESS.value else None,
           "rows_written": result.rows_written, "sha256": fingerprint["sha256"], "run_id": result.run_id,
           "request_id": result.request_id, "status": result.status, "manifest_path": str(manifest_path), "sync_timestamp": _now_utc()}
    current = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=PROVENANCE_COLUMNS)
    for column in PROVENANCE_COLUMNS:
        if column not in current:
            current[column] = None
    updated = pd.concat([current[PROVENANCE_COLUMNS], pd.DataFrame([row], columns=PROVENANCE_COLUMNS)], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        updated.to_csv(tmp, index=False)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _failed_result(
    source_path: Path,
    historical_root: Path,
    reason: DailySnapshotImportReason,
    explanation: str,
    *,
    run_id: str,
    request_id: str,
) -> DailySnapshotImportResult:
    now = _now_utc()
    return DailySnapshotImportResult(
        module=MODULE,
        version=VERSION,
        symbol="*",
        as_of=now,
        status=DailySnapshotImportStatus.FAILED.value,
        data_timestamp=now,
        calculation_version=CALCULATION_VERSION,
        run_id=run_id,
        request_id=request_id,
        reason_codes=[reason.value],
        source_path=str(source_path),
        historical_root=str(historical_root),
        explanation=explanation,
    )


def _invalid_row_mask(source: pd.DataFrame) -> pd.Series:
    dates = pd.to_datetime(source["日期"], errors="coerce")
    numeric = source[list(CHINESE_REQUIRED[2:])].apply(pd.to_numeric, errors="coerce")
    ohlc = numeric[["开盘价", "最高价", "最低价", "收盘价"]]
    return (
        dates.isna()
        | source["代码"].isna()
        | source["代码"].astype(str).str.strip().eq("")
        | numeric.isna().any(axis=1)
        | (numeric["最高价"] < ohlc.max(axis=1))
        | (numeric["最低价"] > ohlc.min(axis=1))
        | (numeric["成交量"] < 0)
    )


def _read_snapshot(source_path: Path, *, skip_invalid_rows: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = pd.read_csv(source_path)
    if source.empty:
        raise DailyDataError(DailySnapshotImportReason.EMPTY_SOURCE.value)
    missing = [col for col in CHINESE_REQUIRED if col not in source.columns]
    if missing:
        raise DailyDataError(f"{DailySnapshotImportReason.MISSING_COLUMNS.value}: {', '.join(missing)}")

    source = source.copy()
    source["代码"] = source["代码"].astype(str).str.strip().str.upper()
    source = source[source["代码"] != ""]
    if source.empty:
        raise DailyDataError(DailySnapshotImportReason.EMPTY_SOURCE.value)

    # Validate the full source before writing any symbol file.
    invalid = _invalid_row_mask(source)
    skipped = source[invalid].copy()
    if invalid.any():
        if not skip_invalid_rows:
            normalize_daily_frame(source)
        source = source[~invalid].copy()
    if source.empty:
        raise DailyDataError(DailySnapshotImportReason.EMPTY_SOURCE.value)
    normalize_daily_frame(source)
    source["日期"] = pd.to_datetime(source["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    source = source.sort_values(["代码", "日期"]).drop_duplicates(["代码", "日期"], keep="last")
    return source, skipped


def _last_csv_row(path: Path) -> list[str] | None:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        if position == 0:
            return None
        buffer = b""
        while position > 0:
            step = min(4096, position)
            position -= step
            handle.seek(position)
            buffer = handle.read(step) + buffer
            lines = [line for line in buffer.splitlines() if line.strip()]
            if len(lines) >= 2 or position == 0:
                last_line = lines[-1] if lines else b""
                decoded = last_line.decode("utf-8-sig")
                return next(csv.reader([decoded])) if decoded else None
    return None


def _values_equal(left, right) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    try:
        left_num = float(left)
        right_num = float(right)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()
    return abs(left_num - right_num) <= 1e-9


def _last_row_matches(target_columns: list[str], last_row: list[str], symbol_rows: pd.DataFrame) -> bool:
    if len(symbol_rows) != 1:
        return False
    incoming = symbol_rows.iloc[0]
    for column in symbol_rows.columns:
        if column not in target_columns:
            return False
        if not _values_equal(last_row[target_columns.index(column)], incoming[column]):
            return False
    return True


def _write_symbol_file(target: Path, symbol_rows: pd.DataFrame) -> tuple[int, bool, bool]:
    existed = target.exists()
    symbol_rows = symbol_rows.copy()
    symbol_rows["日期"] = pd.to_datetime(symbol_rows["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    symbol = symbol_rows["代码"].iloc[0]
    symbol_rows["代码"] = symbol_rows["代码"].fillna(symbol).astype(str).str.strip().str.upper()

    if not existed:
        target.parent.mkdir(parents=True, exist_ok=True)
        symbol_rows.to_csv(target, index=False)
        return len(symbol_rows), existed, True

    target_columns = pd.read_csv(target, nrows=0).columns.tolist()
    source_columns = symbol_rows.columns.tolist()
    last_row = _last_csv_row(target)
    can_append = (
        last_row is not None
        and "日期" in target_columns
        and len(last_row) == len(target_columns)
        and set(source_columns).issubset(set(target_columns))
    )
    if can_append:
        last_date = pd.Timestamp(last_row[target_columns.index("日期")])
        first_new_date = pd.to_datetime(symbol_rows["日期"]).min()
        can_append = bool(last_date < first_new_date)
        if not can_append and last_date == first_new_date and _last_row_matches(target_columns, last_row, symbol_rows):
            return 0, existed, False

    if can_append:
        symbol_rows.reindex(columns=target_columns).to_csv(target, mode="a", header=False, index=False)
        return len(symbol_rows), existed, True

    current = pd.read_csv(target)
    combined = pd.concat([current, symbol_rows], ignore_index=True, sort=False)
    combined["日期"] = pd.to_datetime(combined["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "代码" not in combined:
        combined["代码"] = symbol
    else:
        combined["代码"] = combined["代码"].fillna(symbol)
    combined["代码"] = combined["代码"].astype(str).str.strip().str.upper()
    combined = combined.sort_values("日期").drop_duplicates("日期", keep="last").reset_index(drop=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    combined.to_csv(tmp, index=False)
    os.replace(tmp, target)
    return len(symbol_rows), existed, True


def _snapshot_rows_to_daily_fields(symbol_rows: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = symbol_rows.rename(
        columns={"日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low", "收盘价": "close", "成交量": "volume"}
    ).copy()
    df["symbol"] = symbol.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    for col in DAILY_FIELDS:
        if col not in df:
            df[col] = None
    return df[DAILY_FIELDS]


def _write_daily_partitions_for_years(
    source_path: Path,
    symbol: str,
    symbol_rows: pd.DataFrame,
    output_root: Path,
    years: set[int],
) -> list[tuple[Path, int]]:
    incoming = _snapshot_rows_to_daily_fields(symbol_rows, symbol)
    paths = []
    for year in sorted(years):
        target = output_root / f"symbol={symbol.upper()}" / f"year={year}"
        path = target / f"{symbol.upper()}_{year}.parquet"
        incoming_year = incoming[pd.to_datetime(incoming.date).dt.year == year]
        if incoming_year.empty:
            continue
        if path.exists():
            base = pd.read_parquet(path)
            base["date"] = pd.to_datetime(base["date"], errors="coerce").dt.date
            group = pd.concat([base, incoming_year], ignore_index=True, sort=False)
        else:
            source = read_daily_source(source_path, symbol)
            group = source[pd.to_datetime(source.date).dt.year == year]
        group = group[DAILY_FIELDS].sort_values("date").drop_duplicates(["symbol", "date"], keep="last").reset_index(drop=True)
        target.mkdir(parents=True, exist_ok=True)
        group.to_parquet(path, index=False)
        paths.append((path, len(group)))
    return paths


def import_daily_snapshot(
    source_path: str | Path | None = None,
    historical_root: str | Path = "data/raw/daily_forward_adjusted",
    *,
    daily_root: str | Path = "data/raw/daily",
    skip_invalid_rows: bool = False,
    sync_parquet: bool = False,
    force_sync_parquet: bool = False,
    parquet_root: str | Path = "data/parquet/daily",
    run_id: str | None = None,
    request_id: str | None = None,
) -> DailySnapshotImportResult:
    """Import one all-market daily CSV into per-symbol QFQ history files."""

    run_id = run_id or f"run_{uuid.uuid4().hex}"
    request_id = request_id or f"req_{uuid.uuid4().hex}"
    source_path = Path(source_path) if source_path is not None else find_latest_daily_snapshot(daily_root)
    historical_root = Path(historical_root)
    parquet_root = Path(parquet_root)

    try:
        snapshot, skipped = _read_snapshot(source_path, skip_invalid_rows=skip_invalid_rows)
    except DailyDataError as exc:
        message = str(exc)
        if message.startswith(DailySnapshotImportReason.MISSING_COLUMNS.value):
            reason = DailySnapshotImportReason.MISSING_COLUMNS
        elif message == DailySnapshotImportReason.EMPTY_SOURCE.value:
            reason = DailySnapshotImportReason.EMPTY_SOURCE
        else:
            reason = DailySnapshotImportReason.INVALID_DAILY_DATA
        return _failed_result(source_path, historical_root, reason, message, run_id=run_id, request_id=request_id)

    reason_codes = []
    skipped_symbols = []
    if not skipped.empty:
        reason_codes.append(DailySnapshotImportReason.SKIPPED_INVALID_ROWS.value)
        skipped_symbols = sorted(skipped["代码"].astype(str).str.upper().unique().tolist())

    symbols_written = rows_written = files_created = files_updated = 0
    changed_symbols = []
    for symbol, rows in snapshot.groupby("代码", sort=True):
        target = historical_root / f"{symbol}_daily_qfq.csv"
        imported_rows, existed, changed = _write_symbol_file(target, rows)
        symbols_written += 1
        rows_written += imported_rows
        files_updated += int(existed and changed)
        files_created += int((not existed) and changed)
        if changed or force_sync_parquet:
            changed_symbols.append(symbol)

    parquet_symbols_synced = parquet_partitions_written = parquet_rows_written = 0
    snapshot_years = set(pd.to_datetime(snapshot["日期"]).dt.year.astype(int).tolist())
    if sync_parquet:
        for symbol, rows in snapshot[snapshot["代码"].isin(changed_symbols)].groupby("代码", sort=True):
            paths = _write_daily_partitions_for_years(historical_root / f"{symbol}_daily_qfq.csv", symbol, rows, parquet_root, snapshot_years)
            parquet_symbols_synced += 1
            parquet_partitions_written += len(paths)
            parquet_rows_written += sum(row_count for _, row_count in paths)

    max_date = pd.to_datetime(snapshot["日期"]).max().date().isoformat()
    return DailySnapshotImportResult(
        module=MODULE,
        version=VERSION,
        symbol="*",
        as_of=max_date,
        status=DailySnapshotImportStatus.SUCCESS.value,
        data_timestamp=f"{max_date}T00:00:00Z",
        calculation_version=CALCULATION_VERSION,
        run_id=run_id,
        request_id=request_id,
        reason_codes=reason_codes,
        source_path=str(source_path),
        historical_root=str(historical_root),
        rows_read=len(snapshot),
        symbols_read=int(snapshot["代码"].nunique()),
        symbols_written=symbols_written,
        rows_written=rows_written,
        skipped_invalid_rows=len(skipped),
        skipped_invalid_symbols=skipped_symbols,
        files_created=files_created,
        files_updated=files_updated,
        parquet_synced=sync_parquet,
        parquet_symbols_synced=parquet_symbols_synced,
        parquet_partitions_written=parquet_partitions_written,
        parquet_rows_written=parquet_rows_written,
        explanation="Imported daily snapshot into per-symbol historical files.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import an all-market daily CSV into per-symbol daily history files.")
    parser.add_argument("--file", help="Import one specific daily CSV. Defaults to all daily_*.csv files in --daily-root.")
    parser.add_argument("--daily-root", default="data/raw/daily")
    parser.add_argument("--historical-root", default="data/raw/daily_forward_adjusted")
    parser.add_argument("--strict", action="store_true", help="Fail instead of skipping invalid downloaded rows.")
    parser.add_argument("--no-sync-parquet", action="store_true", help="Only update per-symbol CSV history; do not sync Parquet.")
    parser.add_argument("--force-sync-parquet", action="store_true", help="With --sync-parquet, sync all symbols in the snapshot even if CSV files were unchanged.")
    parser.add_argument("--parquet-root", default="data/parquet/daily")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE_PATH)
    parser.add_argument("--reprocess", action="store_true", help="Ignore manifest state and process matching files again.")
    args = parser.parse_args(argv)
    files = [Path(args.file)] if args.file else find_daily_snapshots(args.daily_root)
    manifest_path = Path(args.manifest)
    manifest = _read_manifest(manifest_path)
    results = []
    skipped_files = []
    for file in files:
        fingerprint = _file_fingerprint(file)
        if not args.reprocess and _manifest_has_success(manifest, fingerprint):
            skipped_files.append(str(file))
            continue
        result = import_daily_snapshot(
            file,
            args.historical_root,
            daily_root=args.daily_root,
            skip_invalid_rows=not args.strict,
            sync_parquet=not args.no_sync_parquet,
            force_sync_parquet=args.force_sync_parquet,
            parquet_root=args.parquet_root,
        )
        results.append(result)
        manifest = _update_manifest(manifest_path, manifest, fingerprint, result)
        _append_provenance(Path(args.provenance), fingerprint, result, manifest_path)
    failed = [result for result in results if result.status != DailySnapshotImportStatus.SUCCESS.value]
    summary = {
        "status": "FAILED" if failed else "SUCCESS",
        "files_seen": len(files),
        "files_processed": len(results),
        "files_skipped": len(skipped_files),
        "latest_as_of": max((result.as_of for result in results), default=None),
        "rows_written": sum(result.rows_written for result in results),
        "files_created": sum(result.files_created for result in results),
        "files_updated": sum(result.files_updated for result in results),
        "skipped_invalid_rows": sum(result.skipped_invalid_rows for result in results),
        "skipped_invalid_symbols": sorted({symbol for result in results for symbol in result.skipped_invalid_symbols}),
        "parquet_symbols_synced": sum(result.parquet_symbols_synced for result in results),
        "parquet_partitions_written": sum(result.parquet_partitions_written for result in results),
        "failed_files": [result.source_path for result in failed],
        "manifest": str(manifest_path),
        "provenance": str(args.provenance),
    }
    print(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
