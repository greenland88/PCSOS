from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import shutil
import subprocess
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .import_options import main as import_option_partition


DEFAULT_ARCHIVE_ROOT = r"K:\BaiduNetdiskDownload\USDailyOptions"
DEFAULT_RAW_ROOT = "data/raw/options"
DEFAULT_MANIFEST = "data/manifests/option_archive_import_manifest.csv"
ARCHIVE_PATTERN = re.compile(r"(?P<year>\d{4})_q(?P<quarter>[1-4])_option_chain_.*\.zip$", re.IGNORECASE)
OPTION_COLUMNS = [
    "Trade Date",
    "Strike",
    "Expiry Date",
    "Call/Put",
    "Last Trade Price",
    "Bid Price",
    "Ask Price",
    "Bid Implied Volatility",
    "Ask Implied Volatility",
    "Open Interest",
    "Volume",
    "Delta",
    "Gamma",
    "Vega",
    "Theta",
    "Rho",
]
MANIFEST_COLUMNS = [
    "archive_path",
    "archive_size",
    "archive_mtime_ns",
    "archive_sha256",
    "symbol",
    "year",
    "quarter",
    "member_name",
    "raw_path",
    "raw_size",
    "rows_written",
    "parquet_synced",
    "status",
    "processed_at",
]


@dataclass(frozen=True)
class OptionArchiveImportResult:
    status: str
    archives_seen: int
    archives_processed: int
    symbols_requested: int
    files_written: int
    files_skipped: int
    rows_written: int
    parquet_partitions_synced: int
    missing_members: int
    manifest: str

    def to_dict(self) -> dict:
        return asdict(self)


def _now_utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {
        "archive_path": str(path),
        "archive_size": stat.st_size,
        "archive_mtime_ns": stat.st_mtime_ns,
        "archive_sha256": _sha256(path),
    }


def discover_archives(archive_root: str | Path = DEFAULT_ARCHIVE_ROOT) -> list[tuple[Path, int, int]]:
    archives = []
    for path in Path(archive_root).glob("*.zip"):
        match = ARCHIVE_PATTERN.match(path.name)
        if match:
            archives.append((path, int(match.group("year")), int(match.group("quarter"))))
    return sorted(archives, key=lambda item: (item[1], item[2], item[0].name))


def discover_symbols(raw_root: str | Path = DEFAULT_RAW_ROOT) -> list[str]:
    symbols = []
    for path in Path(raw_root).iterdir():
        if path.is_dir() and path.name.lower() != "daily":
            symbols.append(path.name.upper())
    return sorted(symbols)


def _read_manifest(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=MANIFEST_COLUMNS)
    manifest = pd.read_csv(path)
    for col in MANIFEST_COLUMNS:
        if col not in manifest:
            manifest[col] = None
    return manifest[MANIFEST_COLUMNS]


def _already_done(manifest: pd.DataFrame, fingerprint: dict, symbol: str, year: int, quarter: int, parquet: bool) -> bool:
    if manifest.empty:
        return False
    rows = manifest[
        (manifest["archive_path"].astype(str) == fingerprint["archive_path"])
        & (manifest["archive_size"].astype("Int64") == fingerprint["archive_size"])
        & (manifest["archive_mtime_ns"].astype("Int64") == fingerprint["archive_mtime_ns"])
        & (manifest["archive_sha256"].astype(str) == fingerprint["archive_sha256"])
        & (manifest["symbol"].astype(str) == symbol)
        & (manifest["year"].astype("Int64") == year)
        & (manifest["quarter"].astype("Int64") == quarter)
        & (manifest["status"].astype(str) == "SUCCESS")
    ]
    if parquet:
        rows = rows[rows["parquet_synced"].astype(str).str.lower().isin(["true", "1"])]
    return not rows.empty


def _append_manifest(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow({column: record.get(column) for column in MANIFEST_COLUMNS})


def _extract_member(zip_file: zipfile.ZipFile, member_name: str, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    rows = 0
    try:
        source = zip_file.open(member_name)
        process = None
    except NotImplementedError:
        seven_zip = shutil.which("7z") or shutil.which("7zz") or r"C:\Program Files\NVIDIA Corporation\NVIDIA GeForce Experience\7z.exe"
        process = subprocess.Popen(
            [seven_zip, "e", "-so", str(zip_file.filename), member_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        source = process.stdout
    try:
        with source, tmp.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(OPTION_COLUMNS)
            reader = csv.reader((line.decode("utf-8-sig", errors="replace") for line in source))
            for row in reader:
                if not row:
                    continue
                writer.writerow(row)
                rows += 1
    finally:
        if process is not None:
            stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
            if process.wait() != 0:
                raise RuntimeError(f"tar failed for {member_name}: {stderr.strip()}")
    os.replace(tmp, target)
    return rows


def import_option_archives(
    archive_root: str | Path = DEFAULT_ARCHIVE_ROOT,
    raw_root: str | Path = DEFAULT_RAW_ROOT,
    output_root: str | Path = "data/parquet/options",
    manifest_path: str | Path = DEFAULT_MANIFEST,
    symbols: list[str] | None = None,
    sync_parquet: bool = True,
    reprocess: bool = False,
    start_year: int | None = None,
) -> OptionArchiveImportResult:
    raw_root = Path(raw_root)
    manifest_path = Path(manifest_path)
    symbols = [symbol.upper() for symbol in (symbols or discover_symbols(raw_root))]
    manifest = _read_manifest(manifest_path)
    archives = [
        item for item in discover_archives(archive_root)
        if start_year is None or item[1] >= start_year
    ]
    files_written = files_skipped = rows_written = parquet_synced = missing_members = archives_processed = 0

    for archive_path, year, quarter in archives:
        fingerprint = _archive_fingerprint(archive_path)
        wrote_archive = False
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            for symbol in symbols:
                member_name = f"{symbol}_{year}_q{quarter}_option_chain.txt"
                if member_name not in names:
                    missing_members += 1
                    continue
                if not reprocess and _already_done(manifest, fingerprint, symbol, year, quarter, sync_parquet):
                    files_skipped += 1
                    continue
                raw_path = raw_root / symbol / f"{symbol}_{year}_q{quarter}_option_chain.csv"
                rows = _extract_member(archive, member_name, raw_path)
                files_written += 1
                rows_written += rows
                wrote_archive = True
                if sync_parquet:
                    import_option_partition(
                        [
                            "--symbol",
                            symbol,
                            "--year",
                            str(year),
                            "--quarter",
                            str(quarter),
                            "--raw-root",
                            str(raw_root),
                            "--output-root",
                            str(output_root),
                        ]
                    )
                    parquet_synced += 1
                record = {
                    **fingerprint,
                    "symbol": symbol,
                    "year": year,
                    "quarter": quarter,
                    "member_name": member_name,
                    "raw_path": str(raw_path),
                    "raw_size": raw_path.stat().st_size,
                    "rows_written": rows,
                    "parquet_synced": sync_parquet,
                    "status": "SUCCESS",
                    "processed_at": _now_utc(),
                }
                _append_manifest(manifest_path, record)
                manifest = pd.concat([manifest, pd.DataFrame([record])], ignore_index=True)
        archives_processed += int(wrote_archive)

    return OptionArchiveImportResult(
        status="SUCCESS",
        archives_seen=len(archives),
        archives_processed=archives_processed,
        symbols_requested=len(symbols),
        files_written=files_written,
        files_skipped=files_skipped,
        rows_written=rows_written,
        parquet_partitions_synced=parquet_synced,
        missing_members=missing_members,
        manifest=str(manifest_path),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract selected symbols from quarterly option archive zips.")
    parser.add_argument("--archive-root", default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--raw-root", default=DEFAULT_RAW_ROOT)
    parser.add_argument("--output-root", default="data/parquet/options")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--symbols", help="Comma-separated symbols. Defaults to existing data/raw/options symbol folders.")
    parser.add_argument("--no-sync-parquet", action="store_true")
    parser.add_argument("--reprocess", action="store_true")
    parser.add_argument("--start-year", type=int, help="Only process archives from this year onward.")
    args = parser.parse_args(argv)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()] if args.symbols else None
    result = import_option_archives(
        archive_root=args.archive_root,
        raw_root=args.raw_root,
        output_root=args.output_root,
        manifest_path=args.manifest,
        symbols=symbols,
        sync_parquet=not args.no_sync_parquet,
        reprocess=args.reprocess,
        start_year=args.start_year,
    )
    print(result.to_dict())
    return 0


if __name__ == "__main__":
    from .import_boundary import reject_legacy_import_entrypoint
    reject_legacy_import_entrypoint()
