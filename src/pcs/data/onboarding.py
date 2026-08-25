"""Standardized, non-strategy ticker onboarding for historical option data.

The pipeline is intentionally blocked unless the historical TXT/ZIP source has
an exact-key overlap with ClickHouse.  All canonical writes go through
``PCSDataAccess``.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yaml

from .access import PCSDataAccess, DataQualityError
from .import_option_archives import OPTION_COLUMNS
from .storage_schema import OPTION_FIELDS, audit_option_frame

KEY = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
COMPARE = ["last", "bid", "ask", "bid_iv", "ask_iv", "open_interest", "volume"]
ARCHIVE_RE = re.compile(r"(?P<year>\d{4})_q(?P<quarter>[1-4])_option_chain_.*\.zip$", re.I)


@dataclass(frozen=True)
class OverlapResult:
    status: str
    overlap_rows: int
    mismatched_rows: int
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class OnboardingResult:
    symbol: str
    status: str
    periods: int
    rows_written: int
    provenance_records: int
    reason_codes: list[str] = field(default_factory=list)
    explanation: str = ""
    duplicate_count: int = 0
    conflict_count: int = 0

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class GenericOnboardingResult:
    symbol: str
    onboarding_status: str
    readiness_status: str
    onboarding: dict[str, Any]
    readiness: dict[str, Any]
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def replay_onboarded_partition(access: PCSDataAccess, dataset: str, symbol: str, year: int, quarter: int, expected_rows: int) -> bool:
    """Replay the canonical partition through PCSDataAccess and verify identity."""
    frame = access.read_partition(dataset, symbol, f"year={year}/quarter={quarter}", f"{symbol}_{year}_q{quarter}.parquet")
    return len(frame) == expected_rows and not frame.duplicated(KEY).any()


@dataclass(frozen=True)
class ConflictPolicyResult:
    frame: pd.DataFrame
    exact_duplicates_removed: int
    conflicts_resolved: int
    conflicts_blocked: int


def apply_conflict_policy(txt: pd.DataFrame, clickhouse: pd.DataFrame) -> ConflictPolicyResult:
    """Apply the canonical fail-closed identity policy.

    Conflicting identities are not resolved by source order.  They are
    excluded from the executable population and counted for quarantine/audit.
    """
    frame = txt[OPTION_FIELDS].copy()
    for data in (frame, clickhouse.copy() if clickhouse is not None else None):
        if data is None or data.empty:
            continue
        data["symbol"] = data["symbol"].astype(str).str.upper()
        for col in ("trade_date", "expiration_date"):
            data[col] = pd.to_datetime(data[col], errors="coerce").dt.date
        data["call_put"] = data["call_put"].astype(str).str.lower()
        data["strike"] = pd.to_numeric(data["strike"], errors="coerce")
    checked, _, quality = audit_option_frame(frame, source="onboarding")
    exact = checked.drop_duplicates(subset=OPTION_FIELDS, keep="last")
    removed = len(frame) - len(exact)
    groups = exact.groupby(KEY, sort=False, dropna=False)
    if all(len(group) == 1 for _, group in groups):
        blocked = int(quality["reason_breakdown"].get("OPTION_DUPLICATE_IDENTITY", 0))
        blocked += int(quality["reason_breakdown"].get("OPTION_CONFLICTING_IDENTITY", 0))
        return ConflictPolicyResult(exact.reset_index(drop=True), removed, 0, blocked)
    selected = []
    blocked = 0
    for _, group in groups:
        if len(group) == 1:
            selected.append(group.iloc[0]); continue
        blocked += 1
    return ConflictPolicyResult(pd.DataFrame(selected, columns=OPTION_FIELDS).reset_index(drop=True), removed, 0, blocked)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_rows(raw: bytes, symbol: str) -> pd.DataFrame:
    # Archive members are historical TXT rows without a reliable header.
    frame = pd.read_csv(io.BytesIO(raw), header=None, names=OPTION_COLUMNS, encoding="utf-8", on_bad_lines="error")
    if len(frame) and str(frame.iloc[0, 0]).strip().lower() in {"trade date", "tradedate"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    frame = frame.rename(columns={
        "Trade Date": "trade_date", "Expiry Date": "expiration_date", "Strike": "strike",
        "Call/Put": "call_put", "Last Trade Price": "last", "Bid Price": "bid", "Ask Price": "ask",
        "Bid Implied Volatility": "bid_iv", "Ask Implied Volatility": "ask_iv", "Open Interest": "open_interest",
        "Volume": "volume", "Delta": "delta", "Gamma": "gamma", "Vega": "vega", "Theta": "theta", "Rho": "rho",
    })
    frame["symbol"] = str(symbol).strip().upper()
    for col in ("trade_date", "expiration_date"):
        frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.date
    for col in OPTION_FIELDS:
        if col not in frame:
            frame[col] = None
    for col in OPTION_FIELDS:
        if col not in KEY + ["symbol"]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame[OPTION_FIELDS]


class HistoricalTxtZipAdapter:
    """Read one ticker/quarter from the approved historical TXT/ZIP root."""

    def __init__(self, root: str | Path = r"K:\BaiduNetdiskDownload\USDailyOptions"):
        self.root = Path(root)
        self._archive_hashes: dict[Path, str] = {}

    def _archive(self, year: int, quarter: int) -> Path:
        matches = [p for p in self.root.glob(f"{year}_q{quarter}_option_chain_*.zip") if ARCHIVE_RE.match(p.name)]
        if not matches:
            raise FileNotFoundError(f"historical archive unavailable for {year} Q{quarter}")
        return sorted(matches)[0]

    def read_period(self, symbol: str, year: int, quarter: int) -> tuple[pd.DataFrame, dict]:
        symbol = str(symbol).strip().upper()
        archive = self._archive(year, quarter)
        member = f"{symbol}_{year}_q{quarter}_option_chain.txt"
        extraction_method = "native_zipfile"
        try:
            with zipfile.ZipFile(archive) as zf:
                if member not in zf.namelist():
                    raise FileNotFoundError(f"historical member unavailable: {member}")
                raw = zf.read(member)
        except NotImplementedError:
            raw, extraction_method = self._read_deflate64(archive, member)
        frame = _normalize_rows(raw, symbol)
        # The member hash is the authoritative identity used for overlap and
        # resume.  Hashing a multi-GB container for every partition can block a
        # long onboarding run; retain stable container metadata for provenance
        # and compute the full archive hash only when a caller explicitly does
        # so outside this hot path.
        archive_hash = self._archive_hashes.get(archive)
        if archive_hash is None:
            stat = archive.stat()
            archive_hash = f"archive-stat:{stat.st_size}:{stat.st_mtime_ns}"
            self._archive_hashes[archive] = archive_hash
        return frame, {"source": "historical_txt_zip", "source_path": str(archive), "source_member": member,
                       "source_sha256": _sha256_bytes(raw), "archive_sha256": archive_hash,
                       "extraction_method": extraction_method, "extracted_file_sha256": _sha256_bytes(raw),
                       "authoritative_source": "original_purchased_zip_member",
                       "year": year, "quarter": quarter, "rows": len(frame)}

    @staticmethod
    def _read_deflate64(archive: Path, member: str) -> tuple[bytes, str]:
        """Read method-9 members through an installed 7-Zip-compatible tool.

        The temporary stream is only a decoding aid; provenance remains the
        original archive/member. No extracted path is treated as authoritative.
        """
        candidates = [shutil.which(name) for name in ("7z", "7za", "7zz")]
        candidates += [
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
            r"C:\ProgramData\chocolatey\tools\7z.exe",
        ]
        executable = next((x for x in candidates if x and Path(x).exists()), None)
        if executable is None:
            raise RuntimeError("VENDOR_ARCHIVE_UNREADABLE_DEFLATE64: no Deflate64-capable reader installed")
        with tempfile.TemporaryDirectory(prefix="pcs_vendor_extract_") as temp:
            target = Path(temp) / Path(member).name
            command = [executable, "e", "-y", f"-o{temp}", str(archive), member]
            result = subprocess.run(command, capture_output=True, check=False)
            if result.returncode != 0 or not target.exists():
                raise RuntimeError("VENDOR_ARCHIVE_UNREADABLE_DEFLATE64: external extraction failed")
            raw = target.read_bytes()
        return raw, f"external_7zip:{Path(executable).name}"


def validate_txt_clickhouse_overlap(txt: pd.DataFrame, clickhouse: pd.DataFrame) -> OverlapResult:
    """Mandatory exact-contract overlap check; quote differences are audited."""
    if clickhouse is None or clickhouse.empty:
        return OverlapResult("BLOCKED", 0, 0, ["CLICKHOUSE_OVERLAP_MISSING"])
    left = txt.copy(); right = clickhouse.copy()
    left = left[OPTION_FIELDS]; right = right[OPTION_FIELDS]
    for frame in (left, right):
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        for col in ("trade_date", "expiration_date"):
            frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.date
        frame["call_put"] = frame["call_put"].astype(str).str.lower()
        frame["strike"] = pd.to_numeric(frame["strike"], errors="coerce")
    joined = left.merge(right, on=KEY, how="inner", suffixes=("_txt", "_ch"))
    if joined.empty:
        return OverlapResult("BLOCKED", 0, 0, ["CLICKHOUSE_OVERLAP_EMPTY"])
    mismatch = pd.Series(False, index=joined.index)
    for col in COMPARE:
        mismatch |= ~(joined[f"{col}_txt"].fillna(float("nan")).eq(joined[f"{col}_ch"].fillna(float("nan"))) | (joined[f"{col}_txt"].isna() & joined[f"{col}_ch"].isna()))
    count = int(mismatch.sum())
    return OverlapResult("READY", len(joined), count, [] if count == 0 else ["CLICKHOUSE_QUOTE_DIFFERENCE_AUDIT"])


def onboard_ticker(symbol: str, periods: list[tuple[int, int]], clickhouse_loader: Callable[[str, int, int], pd.DataFrame], *, adapter: HistoricalTxtZipAdapter, access: PCSDataAccess, dataset: str = "options") -> OnboardingResult:
    """Onboard a new ticker, ending only in READY or BLOCKED."""
    symbol = str(symbol).strip().upper()
    frames = []
    metadata = []
    for year, quarter in periods:
        try:
            frame, meta = adapter.read_period(symbol, year, quarter)
            clickhouse = clickhouse_loader(symbol, year, quarter)
            policy = apply_conflict_policy(frame, clickhouse)
            frame = policy.frame
            meta.update({"exact_duplicates_removed": policy.exact_duplicates_removed, "conflicts_resolved": policy.conflicts_resolved})
            overlap = validate_txt_clickhouse_overlap(frame, clickhouse)
        except (FileNotFoundError, DataQualityError, ValueError) as exc:
            return OnboardingResult(symbol, "BLOCKED", len(frames), 0, 0, ["HISTORICAL_SOURCE_INVALID"], str(exc))
        if overlap.status != "READY":
            return OnboardingResult(symbol, "BLOCKED", len(frames), 0, 0, overlap.reason_codes, "TXT/ClickHouse overlap validation failed")
        frames.append(frame); metadata.append(meta)
    written = records = 0
    for frame, meta in zip(frames, metadata):
        year = meta["year"]; quarter = meta["quarter"]
        source_version = f"historical_txt:{meta['source_member']}:sha256:{meta['source_sha256']}"
        access.write_partition(frame, dataset, symbol, f"year={year}/quarter={quarter}", source_version=source_version,
                               filename=f"{symbol}_{year}_q{quarter}.parquet")
        access.record_provenance({**meta, "dataset": dataset, "symbol": symbol, "source_table": "historical_txt",
                                  "source_version": source_version, "status": "READY", "written_rows": len(frame),
                                  "synced_at": datetime.now(timezone.utc).isoformat()})
        written += len(frame); records += 1
    if not all(replay_onboarded_partition(access, dataset, symbol, meta["year"], meta["quarter"], len(frame)) for frame, meta in zip(frames, metadata)):
        return OnboardingResult(symbol, "BLOCKED", len(frames), written, records, ["CANONICAL_REPLAY_MISMATCH"], "post-append replay validation failed")
    return OnboardingResult(symbol, "READY", len(frames), written, records, [], "append and canonical replay verified")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checkpoint_path(root: Path, symbol: str) -> Path:
    return root / "onboarding_checkpoints" / f"{str(symbol).upper()}_options.json"


def activate_authoritative_route(symbol: str, *, dataset: str, manifest_path: str, parquet_root: str, routes_path: str | Path = "config/data_source_routes.yaml") -> None:
    """Atomically activate a fully finalized generic ticker route."""
    manifest = Path(manifest_path)
    if not manifest.exists():
        raise DataQualityError(f"CANONICAL_MANIFEST_MISSING:{manifest}")
    rows = pd.read_csv(manifest)
    required = {"dataset", "symbol", "status"}
    if not required.issubset(rows.columns):
        raise DataQualityError("CANONICAL_MANIFEST_SCHEMA_INVALID")
    selected = rows[(rows.dataset.astype(str) == str(dataset)) & (rows.symbol.astype(str).str.upper() == str(symbol).upper())]
    if selected.empty or not selected.status.astype(str).str.upper().eq("SUCCESS").all():
        raise DataQualityError(f"CANONICAL_MANIFEST_NOT_VALIDATED:{str(symbol).upper()}")
    path = Path(routes_path); config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    config.setdefault("options", {}).setdefault("by_symbol", {})[str(symbol).upper()] = {"dataset": dataset, "manifest_path": manifest_path, "parquet_root": parquet_root}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp"); tmp.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8"); os.replace(tmp, path)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def onboard_ticker_incremental(symbol: str, periods: list[tuple[int, int]], clickhouse_loader: Callable[[str, int, int], pd.DataFrame], *, adapter: HistoricalTxtZipAdapter, access: PCSDataAccess, dataset: str = "options", workers: int = 4, resume: bool = True, checkpoint_root: str | Path | None = None, routes_path: str | Path = "config/data_source_routes.yaml", activate_route: bool = True) -> OnboardingResult:
    """Generic partition-checkpointed onboarding with fail-closed activation.

    Workers only validate/build physical partitions. Manifest/provenance and route
    activation remain single-writer finalization steps after every partition passes.
    """
    symbol = str(symbol).strip().upper(); root = Path(checkpoint_root or access.parquet_root)
    cp = _checkpoint_path(root, symbol)
    state = json.loads(cp.read_text(encoding="utf-8")) if resume and cp.exists() else {"symbol": symbol, "dataset": dataset, "pipeline_version": "onboarding.v2", "partitions": {}}
    state.setdefault("partitions", {})
    required = {f"{y}Q{q}": (y, q) for y, q in periods}
    for part in required:
        state["partitions"].setdefault(part, {"status": "PENDING"})
    _atomic_json(cp, state)

    def work(item):
        part, (year, quarter) = item
        prior = state["partitions"].get(part, {})
        target = access.parquet_root / dataset / f"symbol={symbol}" / f"year={year}" / f"quarter={quarter}" / f"{symbol}_{year}_q{quarter}.parquet"
        if prior.get("status") == "COMMITTED" and target.exists() and prior.get("output_checksum") == _sha256_file(target):
            return part, {**prior, "status": "COMMITTED", "skipped": True}
        try:
            frame, meta = adapter.read_period(symbol, year, quarter)
            clickhouse = clickhouse_loader(symbol, year, quarter)
            policy = apply_conflict_policy(frame, clickhouse); frame = policy.frame
            overlap = validate_txt_clickhouse_overlap(frame, clickhouse)
            if overlap.status != "READY":
                raise DataQualityError(";".join(overlap.reason_codes))
            checked = access.validate_schema(frame, dataset)
            access.validate_coverage(checked, symbol)
            source_version = f"historical_txt:{meta['source_member']}:sha256:{meta['source_sha256']}"
            path = access.write_partition(checked, dataset, symbol, f"year={year}/quarter={quarter}", source_version=source_version, filename=target.name, update_manifest=False, allow_overwrite=True)
            checksum = _sha256_file(path)
            return part, {"ticker": symbol, "partition_id": part, "source_type": meta["source"], "source_identity": meta["source_member"], "source_path": meta["source_path"], "source_hash": meta["source_sha256"], "pipeline_version": "onboarding.v2", "schema_version": "1", "status": "COMMITTED", "row_count": len(checked), "output_path": str(path), "output_checksum": checksum, "validation_timestamp": datetime.now(timezone.utc).isoformat(), "committed_timestamp": datetime.now(timezone.utc).isoformat(), "failure_code": None, "failure_reason": None, "duplicate_count": policy.exact_duplicates_removed, "conflict_count": policy.conflicts_blocked, "provenance": {**meta, "dataset": dataset, "symbol": symbol, "source_version": source_version, "status": "READY", "written_rows": len(checked)}}
        except Exception as exc:
            return part, {"ticker": symbol, "partition_id": part, "status": "FAILED", "failure_code": type(exc).__name__, "failure_reason": str(exc)}

    pending = [(part, period) for part, period in required.items() if state["partitions"].get(part, {}).get("status") != "COMMITTED"]
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = [pool.submit(work, item) for item in pending]
        for future in as_completed(futures):
            part, result = future.result(); state["partitions"][part] = result; _atomic_json(cp, state)
    failed = [p for p in required if state["partitions"].get(p, {}).get("status") != "COMMITTED"]
    if failed:
        failures = {p: state["partitions"].get(p, {}).get("failure_reason") for p in failed}
        return OnboardingResult(symbol, "BLOCKED", len(required) - len(failed), 0, 0, ["PARTITIONS_INCOMPLETE"], f"checkpoint={cp}; failed_or_pending={failed[:5]}; failures={failures}")
    # Single-writer finalization: manifest and provenance only become active after full inventory.
    rows_written = records = 0
    for part in sorted(required):
        rec = state["partitions"][part]; frame = access.read_partition(dataset, symbol, f"year={required[part][0]}/quarter={required[part][1]}", Path(rec["output_path"]).name)
        access.update_manifest(dataset, symbol, frame, Path(rec["output_path"]), f"historical_txt:{rec['source_identity']}:sha256:{rec['source_hash']}", f"year={required[part][0]}/quarter={required[part][1]}", replace_existing=True)
        access.record_provenance(rec["provenance"]); rows_written += len(frame); records += 1
    if activate_route:
        activate_authoritative_route(symbol, dataset=dataset, manifest_path=str(access.manifest_path), parquet_root=str(access.parquet_root), routes_path=routes_path)
    message = f"incremental checkpoint finalized and {'route activated' if activate_route else 'route pending final activation'}: {cp}"
    return OnboardingResult(symbol, "READY", len(required), rows_written, records, [], message,
                            duplicate_count=sum(int(state["partitions"][p].get("duplicate_count", 0)) for p in required),
                            conflict_count=sum(int(state["partitions"][p].get("conflict_count", 0)) for p in required))


def onboard_ticker_to_readiness(symbol: str, periods: list[tuple[int, int]], clickhouse_loader: Callable[[str, int, int], pd.DataFrame], *, adapter: HistoricalTxtZipAdapter, access: PCSDataAccess, dataset: str = "options", workers: int = 4, resume: bool = True, checkpoint_root: str | Path | None = None, daily_frame: pd.DataFrame | None = None, routes_path: str | Path = "config/data_source_routes.yaml") -> GenericOnboardingResult:
    """Run generic canonical onboarding through the universal readiness gate."""
    if daily_frame is not None:
        from .incremental_update import update_ticker
        update_ticker(symbol, daily_frame=daily_frame, parquet_root=access.parquet_root, manifest_path=access.manifest_path, options_manifest_path=access.manifest_path, source_version="generic-daily")
    onboarding = onboard_ticker_incremental(symbol, periods, clickhouse_loader, adapter=adapter, access=access, dataset=dataset, workers=workers, resume=resume, checkpoint_root=checkpoint_root, routes_path=routes_path)
    if onboarding.status != "READY":
        return GenericOnboardingResult(str(symbol).upper(), onboarding.status, "NO", onboarding.to_dict(), {}, onboarding.reason_codes)
    from pcs.research.ticker_readiness import preflight_ticker
    fresh_access = PCSDataAccess(manifest_path=access.manifest_path, parquet_root=access.parquet_root)
    readiness = preflight_ticker(symbol, access=fresh_access)
    return GenericOnboardingResult(str(symbol).upper(), onboarding.status, readiness.PCS_RESEARCH_READY, onboarding.to_dict(), readiness.to_dict(), readiness.reason_codes)


def discover_source_periods(adapter: HistoricalTxtZipAdapter) -> list[tuple[int, int]]:
    """Discover all available year/quarter source shards without ticker logic."""
    periods = []
    for path in adapter.root.glob("*_q*_option_chain_*.zip"):
        match = ARCHIVE_RE.match(path.name)
        if match:
            periods.append((int(match.group("year")), int(match.group("quarter"))))
    return sorted(set(periods))


def run_system_onboarding(symbol: str, *, adapter: HistoricalTxtZipAdapter, access: PCSDataAccess,
                          clickhouse_loader: Callable[[str, int, int], pd.DataFrame], periods: list[tuple[int, int]] | None = None,
                          workers: int = 4, state_root: str | Path = "data/onboarding",
                          routes_path: str | Path = "config/data_source_routes.yaml", daily_frame: pd.DataFrame | None = None) -> dict[str, Any]:
    """Run the complete system-owned onboarding workflow for one symbol.

    The normal caller supplies only a symbol and source services.  Periods are
    discovered from the adapter; the optional argument exists for isolated
    fixtures and deterministic tests.
    """
    from .onboarding_engine import FailureType, OnboardingEngine, StageResult
    from .incremental_update import update_ticker

    symbol = str(symbol).strip().upper()
    engine = OnboardingEngine(symbol, state_root)
    discovered: list[tuple[int, int]] = []

    def source(state):
        nonlocal discovered
        discovered = sorted(set(periods if periods is not None else discover_source_periods(adapter)))
        if not discovered:
            return StageResult("FAIL", FailureType.NON_RECOVERABLE_EXTERNAL, "no supported source periods discovered", reason_codes=["SOURCE_PERIODS_MISSING"])
        state.shards_total = len(discovered)
        state.source_version = "historical_txt_zip"
        return StageResult("PASS", metrics={"periods": [f"{y}Q{q}" for y, q in discovered]})

    def daily(state):
        if daily_frame is not None:
            result = update_ticker(symbol, daily_frame=daily_frame, parquet_root=access.parquet_root,
                                   manifest_path=access.manifest_path, options_manifest_path=access.manifest_path,
                                   source_version="onboarding-daily")
            return StageResult("PASS", metrics=result)
        try:
            daily = access.read_prices(symbol)
        except Exception as exc:
            return StageResult("FAIL", FailureType.NON_RECOVERABLE_EXTERNAL,
                               f"canonical daily source unavailable: {exc}", reason_codes=["DAILY_SOURCE_UNAVAILABLE"])
        if daily.empty:
            return StageResult("FAIL", FailureType.DATA_QUALITY_FAILURE,
                               "canonical daily source is empty", reason_codes=["DAILY_EMPTY"])
        dates = pd.to_datetime(daily["date"], errors="coerce")
        return StageResult("PASS", metrics={"status": "REUSED_CANONICAL_DAILY", "rows": len(daily),
                                              "min_date": str(dates.min().date()), "max_date": str(dates.max().date())})

    def options(state):
        result = onboard_ticker_incremental(symbol, discovered, clickhouse_loader, adapter=adapter, access=access,
                                             workers=workers, resume=True, checkpoint_root=state_root,
                                             routes_path=routes_path, activate_route=False)
        if result.status != "READY":
            return StageResult("FAIL", FailureType.DATA_QUALITY_FAILURE, result.explanation, reason_codes=result.reason_codes)
        state.shards_complete = result.periods
        state.rows_written = result.rows_written
        state.rows_processed = result.rows_written
        state.duplicate_count = result.duplicate_count
        state.conflict_count = result.conflict_count
        return StageResult("PASS", metrics=result.to_dict())

    def validate(state):
        return StageResult("PASS", metrics={"validated_shards": state.shards_complete})

    def manifest(state):
        return StageResult("PASS", metrics={"manifest": str(access.manifest_path)})

    def route(state):
        activate_authoritative_route(symbol, dataset="options", manifest_path=str(access.manifest_path),
                                     parquet_root=str(access.parquet_root), routes_path=routes_path)
        return StageResult("PASS", metrics={"route": symbol})

    def readiness(state):
        from pcs.research.ticker_readiness import preflight_ticker
        result = preflight_ticker(symbol, access=PCSDataAccess(manifest_path=access.manifest_path, parquet_root=access.parquet_root))
        if str(result.PCS_RESEARCH_READY).upper() not in {"YES", "TRUE", "PASS"}:
            return StageResult("FAIL", FailureType.DATA_QUALITY_FAILURE, "canonical ticker readiness failed", result.to_dict(), result.reason_codes)
        case_payload = result.checks.get("contract_selection", {}).get("case")
        if case_payload:
            state.metrics["_readiness_smoke_case"] = case_payload
        return StageResult("PASS", metrics=result.to_dict(), reason_codes=result.reason_codes)

    smoke_case: dict[str, Any] = {}

    def contract_smoke(state):
        from .readiness import LifecycleSmokeCase
        payload = state.metrics.get("_readiness_smoke_case")
        if not payload:
            return StageResult("FAIL", FailureType.DATA_QUALITY_FAILURE, "contract selection smoke failed",
                               reason_codes=["CONTRACT_SELECTION_SMOKE_FAILED"])
        fields = {key: payload[key] for key in LifecycleSmokeCase.__dataclass_fields__ if key in payload}
        case = LifecycleSmokeCase(**fields)
        smoke_case["case"] = case
        return StageResult("PASS", metrics={"case": case.to_dict(), "evidence": "REUSED_CANONICAL_READINESS"})

    def lifecycle_smoke(state):
        from .readiness import execute_lifecycle_smoke
        case = smoke_case.get("case")
        if case is None:
            return StageResult("FAIL", FailureType.INVARIANT_VIOLATION, "lifecycle smoke reached without contract case")
        evidence = execute_lifecycle_smoke(access, case)
        if evidence.get("exit_date") is None or evidence.get("realized_pnl") is None:
            return StageResult("FAIL", FailureType.DATA_QUALITY_FAILURE, "lifecycle smoke did not complete",
                               metrics=evidence, reason_codes=["LIFECYCLE_SMOKE_INCOMPLETE"])
        return StageResult("PASS", metrics=evidence)

    handlers = {"SOURCE_DISCOVERY": source, "DAILY_READY": daily, "OPTIONS_INGESTION": options,
                "OPTIONS_VALIDATION": validate, "MANIFEST_UPDATE": manifest, "ROUTE_ACTIVATION": route,
                "CANONICAL_READINESS": readiness, "CONTRACT_SMOKE": contract_smoke,
                "LIFECYCLE_SMOKE": lifecycle_smoke,
                "RESEARCH_READY": lambda s: StageResult("PASS", metrics={"boundary": "RESEARCH_READY"})}
    return engine.run(handlers).__dict__


__all__ = ["HistoricalTxtZipAdapter", "ConflictPolicyResult", "OverlapResult", "OnboardingResult", "GenericOnboardingResult", "apply_conflict_policy", "validate_txt_clickhouse_overlap", "replay_onboarded_partition", "onboard_ticker", "onboard_ticker_incremental", "onboard_ticker_to_readiness", "discover_source_periods", "run_system_onboarding", "activate_authoritative_route"]
