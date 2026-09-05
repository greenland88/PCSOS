"""Unified market-data import control plane over canonical PCSDataAccess."""
from __future__ import annotations
import uuid
import copy
import hashlib
import json
import shutil
import os
import subprocess
import stat
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
import pandas as pd
from .access import PCSDataAccess, PromotionReceipt, DataAccessError, CanonicalFileAccessError

class ImportStatus(StrEnum):
    READY = "READY"
    ALREADY_COMPLETE = "ALREADY_COMPLETE"
    BLOCKED = "BLOCKED"
    PARTIAL = "PARTIAL"

class PlanAction(StrEnum):
    REUSE_CANONICAL = "REUSE_CANONICAL"
    REPAIR_DAILY_FROM_GATEWAY = "REPAIR_DAILY_FROM_GATEWAY"
    SYNC_CURRENT_OPTIONS_FROM_CLICKHOUSE = "SYNC_CURRENT_OPTIONS_FROM_CLICKHOUSE"
    IMPORT_HISTORICAL_OPTIONS = "IMPORT_HISTORICAL_ZIP_WITH_CLICKHOUSE_OVERLAP"
    BLOCKED_NO_AUTHORIZED_SOURCE = "BLOCKED_NO_AUTHORIZED_SOURCE"
    REPAIR_EXACT_OPTIONS_GAP = "REPAIR_EXACT_OPTIONS_GAP"
    REPAIR_CANONICAL_FILE_ACCESS = "REPAIR_CANONICAL_FILE_ACCESS"
    IMPORT_PIT_EVENTS = "IMPORT_PIT_EVENTS"

@dataclass(frozen=True)
class MarketDataRequirements:
    symbol: str
    required_start: str | None = None
    required_end: str | None = None
    datasets: tuple[str, ...] = ("daily", "options")
    exact_contract_quote_keys: tuple[dict[str, Any], ...] = ()
    required_fields: tuple[str, ...] = ()
    decision_as_of: str | None = None
    option_type: str | None = None
    min_dte: int | None = None
    max_dte: int | None = None
    required_history_rows: int | None = None
    def __post_init__(self):
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if not self.symbol: raise ValueError("symbol must be non-empty")

    @classmethod
    def from_mapping(cls, symbol: str | None, value: dict[str, Any]):
        start = value.get("start", value.get("required_start"))
        end = value.get("end", value.get("required_end"))
        raw = value.get("datasets", ("daily", "options"))
        if isinstance(raw, dict):
            datasets = tuple(name for name, spec in raw.items() if not isinstance(spec, dict) or spec.get("required", True))
        else:
            datasets = tuple(raw)
        keys = value.get("exact_contract_quote_keys", ()) or ()
        if isinstance(keys, dict): keys = (keys,)
        fields = value.get("required_fields", ()) or ()
        return cls(symbol or value.get("symbol", ""), start, end, datasets, tuple(dict(x) for x in keys), tuple(str(x) for x in fields),
                   value.get("decision_as_of", value.get("as_of")), value.get("option_type"), value.get("min_dte"), value.get("max_dte"), value.get("required_history_rows"))

@dataclass(frozen=True)
class OptionChainRequirement:
    symbol: str
    as_of: str
    option_type: str = "put"
    min_dte: int = 7
    max_dte: int = 45
    required_fields: tuple[str, ...] = ()

@dataclass(frozen=True)
class CoveragePlan:
    requirements: MarketDataRequirements
    existing: dict[str, dict[str, Any]]
    actions: tuple[dict[str, Any], ...]
    required_option_periods: tuple[str, ...] = ()
    canonical_complete_periods: tuple[str, ...] = ()
    periods_to_import: tuple[str, ...] = ()
    periods_to_repair: tuple[str, ...] = ()
    periods_blocked: tuple[str, ...] = ()

@dataclass(frozen=True)
class MarketDataResult:
    module: str; version: str; symbol: str; as_of: str; status: str
    data_timestamp: str; calculation_version: str; run_id: str; request_id: str
    reason_codes: tuple[str, ...] = (); requirements: dict[str, Any] = field(default_factory=dict)
    coverage_plan: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    remaining_blockers: tuple[str, ...] = ()
    source_inventory: tuple[dict[str, Any], ...] = ()
    stages: dict[str, str] = field(default_factory=dict)
    reused_periods: tuple[str, ...] = ()
    imported_periods: tuple[str, ...] = ()
    repaired_periods: tuple[str, ...] = ()
    blocked_periods: tuple[str, ...] = ()
    initial_plan: dict[str, Any] = field(default_factory=dict)
    selected_source: tuple[str, ...] = ()
    provider_coverage: tuple[dict[str, Any], ...] = ()
    import_outcomes: tuple[dict[str, Any], ...] = ()
    promoted_partitions: tuple[dict[str, Any], ...] = ()
    blocked_partitions: tuple[dict[str, Any], ...] = ()
    final_canonical_status: str = ""
    def to_dict(self): return asdict(self)

class MarketDataSourceAdapter(Protocol):
    source_id: str
    def capabilities(self) -> dict[str, Any]: ...
    def health_check(self) -> str: ...
    def probe_coverage(self, symbol: str, dataset: str, start: str | None, end: str | None) -> dict[str, Any]: ...

class SourceResolver:
    def __init__(self, registry_path: str | Path | None = None):
        self.path = Path(registry_path or "config/market_data_source_registry.yaml")
        self.registry = self._load()
    def _load(self):
        if not self.path.exists(): return {}
        import yaml
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
    def resolve(self, dataset):
        rows = [dict(x) for x in self.registry.get("sources", {}).get(dataset, [])
                if x.get("authorized") is True and x.get("enabled") is True]
        return sorted(rows, key=lambda x: (int(x.get("priority", 9999)), str(x.get("source_id", ""))))

    def adapter_spec(self, dataset: str, source_id: str) -> dict[str, Any]:
        matches = [x for x in self.registry.get("sources", {}).get(dataset, []) if x.get("source_id") == source_id]
        if len(matches) != 1: raise DataAccessError(f"SOURCE_NOT_REGISTERED:{dataset}:{source_id}")
        spec = dict(matches[0])
        if not spec.get("enabled") or not spec.get("authorized"):
            raise DataAccessError(f"SOURCE_NOT_AUTHORIZED:{dataset}:{source_id}")
        return spec

    def load_adapter(self, dataset: str, source_id: str):
        spec = self.adapter_spec(dataset, source_id)
        import importlib
        module_name, separator, attr_name = str(spec["adapter"]).rpartition(".")
        if not separator: raise DataAccessError(f"ADAPTER_PATH_INVALID:{spec['adapter']}")
        try:
            return getattr(importlib.import_module(module_name), attr_name)
        except (ImportError, AttributeError) as exc:
            raise DataAccessError(f"ADAPTER_UNAVAILABLE:{spec['adapter']}") from exc

    def validate_registry(self) -> list[dict[str, Any]]:
        results = []
        for dataset in self.registry.get("sources", {}):
            for spec in self.resolve(dataset):
                adapter = self.load_adapter(dataset, spec["source_id"])
                has_capabilities = hasattr(adapter, "capabilities")
                results.append({"dataset": dataset, "source_id": spec["source_id"], "adapter": spec["adapter"], "status": "READY" if (has_capabilities or callable(adapter)) else "ADAPTER_CONTRACT_MISSING"})
        return results


class RequestLedger:
    """Append-only request audit with deterministic reuse lookup."""
    def __init__(self, path: str | Path = "data/manifests/data_request_ledger.jsonl"):
        self.path = Path(path)
    def find_completed(self, source_id, symbol, dataset, start, end, query_version="1"):
        if not self.path.exists(): return None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try: row = json.loads(line)
            except json.JSONDecodeError: continue
            if (row.get("source_id"), row.get("symbol"), row.get("dataset"), row.get("requested_start"), row.get("requested_end"), row.get("query_version")) == (source_id, symbol.upper(), dataset, start, end, query_version) and row.get("status") == "API_COMPLETE": return row
        return None
    def record(self, **fields):
        row = {"request_id": fields.pop("request_id", uuid.uuid4().hex), "recorded_at": datetime.now(timezone.utc).isoformat(), **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle: handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        return row


class CanonicalDataCatalog:
    """Rebuildable catalog derived from the active manifest, never hand-authored."""
    def __init__(self, path: str | Path = "data/manifests/canonical_data_catalog.parquet"):
        self.path = Path(path)
    def rebuild(self, access: PCSDataAccess):
        manifest = access._read_manifest(access.manifest_path)
        previous = {}
        if self.path.exists():
            try:
                old = pd.read_parquet(self.path)
                previous = {str(r.physical_path): r.to_dict() for _, r in old.iterrows()}
            except Exception:
                previous = {}
        rows = []
        for row in manifest.to_dict("records") if not manifest.empty else []:
            if str(row.get("status", "")).upper() != "SUCCESS": continue
            physical = Path(str(row.get("parquet_path", "")))
            if not physical.is_absolute(): physical = Path.cwd() / physical
            old = previous.get(str(row.get("parquet_path", "")))
            try:
                stat = physical.stat()
            except (FileNotFoundError, PermissionError, OSError):
                stat = None
            reusable = bool(old and stat and old.get("physical_size") == stat.st_size and old.get("physical_mtime_ns") == stat.st_mtime_ns)
            if reusable:
                physical_hash = old.get("physical_checksum")
            elif stat is not None:
                try:
                    physical_hash = hashlib.sha256(physical.read_bytes()).hexdigest()
                except (FileNotFoundError, PermissionError, OSError):
                    physical_hash = None
            else:
                physical_hash = None
            symbol = str(row.get("symbol", "")).upper(); dataset = str(row.get("dataset", ""))
            basis = access.get_price_basis(dataset, symbol) if hasattr(access, "get_price_basis") else {}
            try:
                provenance_rows = access.get_provenance(dataset, symbol)
            except Exception:
                provenance_rows = []
            provenance = [p for p in provenance_rows if str(p.get("partition", "")) in {str(row.get("partition", "")), f"year={row.get('year')}/quarter={row.get('quarter')}"}]
            rows.append({"symbol": symbol, "dataset": dataset, "partition": f"year={row.get('year')}/quarter={row.get('quarter')}" if row.get("year") else "", "coverage_start": row.get("min_date"), "coverage_end": row.get("max_date"), "row_count": row.get("row_count"), "source_id": (provenance[0].get("source_id") if provenance else None), "source_authority": (provenance[0].get("authority") if provenance else None), "source_version": row.get("source_file", row.get("source_version")), "price_basis": basis.get("price_basis"), "schema_version": row.get("schema_version"), "physical_path": row.get("parquet_path"), "physical_checksum": physical_hash, "physical_size": stat.st_size if stat else None, "physical_mtime_ns": stat.st_mtime_ns if stat else None, "validation_status": "SUCCESS" if stat is not None else "PHYSICAL_UNAVAILABLE", "manifest_identity": hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest(), "provenance_identity": (provenance[0].get("provenance_key") if provenance else None)})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        pd.DataFrame(rows).to_parquet(tmp, index=False)
        tmp.replace(self.path)
        return self.path


class ImportEngine:
    """Two-phase frame importer used by adapters and onboarding orchestration.

    Stage files are outside the canonical root. Promotion delegates schema,
    quality, atomic Parquet replacement, and manifest locking to PCSDataAccess.
    """
    def __init__(self, access=None, staging_root="data/staging/imports", catalog=None, ledger=None):
        self.access = access or PCSDataAccess.canonical()
        self.staging_root = Path(staging_root)
        self.catalog = catalog or CanonicalDataCatalog()
        self.ledger = ledger or RequestLedger()

    def _bound_access(self, staged: dict[str, Any]) -> PCSDataAccess:
        bound = copy.copy(self.access)
        bound.routing_mode = "isolated"
        bound.manifest_path = Path(staged["manifest_path"])
        bound.provenance_manifest_path = bound.manifest_path.with_name("data_provenance_manifest.csv")
        bound.parquet_root = Path(staged["parquet_root"])
        bound._manifest = bound._read_manifest(bound.manifest_path)
        return bound

    def stage(self, frame: pd.DataFrame, *, symbol: str, dataset: str, partition: str, source_id: str, request_id: str | None = None):
        rid = request_id or uuid.uuid4().hex
        symbol = str(symbol).strip().upper()
        if frame is None or frame.empty:
            raise DataAccessError("STAGE_EMPTY_PAYLOAD")
        logical_dataset = str(dataset)
        route_access, physical_dataset, manifest_path, parquet_root = self.access.route_bound_access(logical_dataset, symbol, for_import=True)
        checked = frame.copy()
        if "symbol" not in checked.columns or set(checked["symbol"].astype(str).str.upper()) != {symbol}:
            raise DataAccessError("TICKER_ISOLATION_FAILED")
        checked = self.access.validate_schema(checked, physical_dataset)
        target = self.staging_root / rid / symbol.upper() / physical_dataset / partition
        target.mkdir(parents=True, exist_ok=True)
        path = target / "payload.parquet"
        checked.to_parquet(path, index=False)
        verify = pd.read_parquet(path)
        if len(verify) != len(checked): raise DataAccessError("STAGE_ROW_COUNT_MISMATCH")
        return {"status": "STAGED", "path": str(path), "symbol": symbol,
                "dataset": physical_dataset, "logical_dataset": logical_dataset,
                "physical_dataset": physical_dataset,
                "manifest_path": str(manifest_path), "parquet_root": str(parquet_root),
                "source_routes": self.access.source_routes,
                "partition": partition, "source_id": source_id,
                "request_id": rid, "row_count": len(checked)}

    def promote(self, staged: dict[str, Any], *, source_version: str):
        path = Path(staged["path"]); frame = pd.read_parquet(path)
        route_access = self._bound_access(staged)
        written = None
        created_path = None
        canonical_symbol_root = (Path(staged["parquet_root"]) / staged["physical_dataset"] /
                                  f"symbol={staged['symbol']}")
        preexisting_canonical_paths = set(canonical_symbol_root.rglob("*.parquet")) if canonical_symbol_root.exists() else set()
        metadata_files = [Path(route_access.manifest_path), Path(route_access.provenance_manifest_path), Path(self.catalog.path), Path(self.ledger.path)]
        snapshots = {}
        for target in metadata_files:
            snapshots[target] = target.read_bytes() if target.exists() else None
        try:
            written = route_access.promote_generation(frame, staged["physical_dataset"], staged["symbol"], staged["partition"], source_version=source_version, logical_dataset=staged["logical_dataset"])
            if isinstance(written, PromotionReceipt):
                candidate_path = Path(written.path)
                if candidate_path not in preexisting_canonical_paths:
                    created_path = candidate_path
                self._assert_route_identity(staged, written, route_access)
            else:
                if not isinstance(written, Path):
                    raise DataAccessError("UNRECOGNIZED_PROMOTION_RESULT")
                self._verify_idempotent_noop(staged, written, route_access)
                return {"status": "ALREADY_COMPLETE", "path": str(written),
                        "request_id": staged["request_id"],
                        "promotion_receipt": None,
                        "promoted_generation_id": str(route_access.active_generation_record(
                            staged["physical_dataset"], staged["symbol"], staged["partition"],
                            manifest_identity=route_access.normalized_path_identity(staged["manifest_path"])
                        ).get("active_generation") or ""),
                        "reason_codes": ["IDEMPOTENT_NO_OP"]}
            route_access.record_provenance({"dataset": staged["physical_dataset"], "symbol": staged["symbol"], "partition": staged["partition"], "source_id": staged["source_id"], "source_version": source_version, "request_id": staged["request_id"], "row_count": len(frame), "status": "PROMOTED"})
            self.catalog.rebuild(route_access)
            self.ledger.record(request_id=staged["request_id"], source_id=staged["source_id"], symbol=staged["symbol"], dataset=staged["dataset"], shard=staged["partition"], physical_row_count=len(frame), status="API_COMPLETE", completed_at=datetime.now(timezone.utc).isoformat())
            receipt = written
            receipt_payload = receipt.__dict__.copy() if hasattr(receipt, "__dict__") else None
            if receipt_payload is not None:
                active = route_access.active_generation_record(staged["physical_dataset"], staged["symbol"], staged["partition"], manifest_identity=route_access.normalized_path_identity(staged["manifest_path"]))
                receipt_payload["manifest_active_generation_id"] = str(active.get("active_generation") or "")
                receipt_payload["manifest_content_hash"] = str(active.get("content_hash") or "")
                receipt_payload["manifest_row_count"] = int(active.get("row_count") or 0)
            return {"status": "IMPORTED", "path": str(receipt), "request_id": staged["request_id"],
                    "promotion_receipt": receipt_payload,
                    "promoted_generation_id": getattr(receipt, "generation_id", None),
                    "read_back_generation_id": getattr(receipt, "read_back_generation_id", None),
                    "checksum": getattr(receipt, "checksum", None),
                    "row_count": getattr(receipt, "row_count", None)}
        except Exception as exc:
            # A prior interrupted/legacy import may have left the exact
            # canonical payload in place without its metadata. Reconcile it
            # only when semantic content is byte-independent and identical;
            # never overwrite an existing trusted partition.
            if isinstance(exc, FileExistsError):
                target = route_access.parquet_root / staged["physical_dataset"] / f"symbol={staged['symbol']}" / staged["partition"] / f"{staged['symbol']}_{staged['partition'].replace('=', '_').replace('/', '_')}.parquet"
                try:
                    existing = pd.read_parquet(target)
                    if route_access.semantic_content_hash(existing) == route_access.semantic_content_hash(frame):
                        route_access.update_manifest(staged["physical_dataset"], staged["symbol"], existing, target, source_version, staged["partition"], replace_existing=True)
                        route_access.record_provenance({"dataset": staged["physical_dataset"], "symbol": staged["symbol"], "partition": staged["partition"], "source_id": staged["source_id"], "source_version": source_version, "request_id": staged["request_id"], "row_count": len(existing), "status": "PROMOTED"})
                        self.catalog.rebuild(route_access)
                        self.ledger.record(request_id=staged["request_id"], source_id=staged["source_id"], symbol=staged["symbol"], dataset=staged["dataset"], shard=staged["partition"], physical_row_count=len(existing), status="API_COMPLETE", completed_at=datetime.now(timezone.utc).isoformat())
                        return {"status": "IMPORTED", "path": str(target), "request_id": staged["request_id"], "reason_codes": ["EXACT_CANONICAL_PARTITION_METADATA_RECONCILED"]}
                except Exception:
                    pass
            # write_partition refuses overwrite by default; removing only the
            # path created in this transaction preserves any prior canonical
            # partition when metadata finalization fails.
            if created_path is not None:
                try: created_path.unlink(missing_ok=True)
                except OSError: pass
            for target, payload in snapshots.items():
                try:
                    if payload is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(payload)
                except OSError:
                    pass
            quarantine = self.staging_root / "quarantine" / staged["request_id"]
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            if path.exists(): path.replace(quarantine.with_suffix(".parquet"))
            return {"status": "QUARANTINED", "reason_codes": [type(exc).__name__], "detail": str(exc), "request_id": staged["request_id"]}

    @staticmethod
    def _verify_idempotent_noop(staged, existing, route_access):
        expected_root = (Path(staged["parquet_root"]) / staged["physical_dataset"] /
                         f"symbol={staged['symbol']}").resolve()
        existing = Path(existing).resolve()
        try:
            existing.relative_to(expected_root)
        except ValueError as exc:
            raise DataAccessError("CANONICAL_ROUTE_IDENTITY_MISMATCH") from exc
        active = route_access.active_generation_record(
            staged["physical_dataset"], staged["symbol"], staged["partition"],
            manifest_identity=route_access.normalized_path_identity(staged["manifest_path"]),
        )
        active_path = Path(str(active.get("parquet_path", ""))).resolve()
        if active_path != existing:
            raise DataAccessError("IDEMPOTENT_CANONICAL_STATE_INVALID")
        if not existing.exists():
            raise DataAccessError("IDEMPOTENT_CANONICAL_STATE_INVALID")
        read_back = pd.read_parquet(existing)
        if (len(read_back) != int(active.get("row_count") or 0) or
                route_access.semantic_content_hash(read_back) != str(active.get("content_hash", ""))):
            raise DataAccessError("IDEMPOTENT_CANONICAL_STATE_INVALID")

    @staticmethod
    def _assert_route_identity(staged, receipt, route_access):
        expected_root = (Path(staged["parquet_root"]) / staged["physical_dataset"] / f"symbol={staged['symbol']}").resolve()
        try:
            Path(receipt.path).resolve().relative_to(expected_root)
        except ValueError as exc:
            raise DataAccessError("CANONICAL_ROUTE_IDENTITY_MISMATCH") from exc
        active = route_access.active_generation_record(
            staged["physical_dataset"], staged["symbol"], staged["partition"],
            manifest_identity=route_access.normalized_path_identity(staged["manifest_path"]),
        )
        if (receipt.dataset != staged["physical_dataset"] or
                receipt.logical_dataset != staged["logical_dataset"] or
                receipt.manifest_identity != route_access.normalized_path_identity(staged["manifest_path"]) or
                receipt.parquet_root_identity != route_access.normalized_path_identity(staged["parquet_root"]) or
                str(active.get("dataset")) != staged["physical_dataset"] or
                route_access.normalized_path_identity(staged["manifest_path"]) != route_access.normalized_path_identity(route_access.manifest_path)):
            raise DataAccessError("CANONICAL_ROUTE_IDENTITY_MISMATCH")

    def promote_batch(self, staged: list[dict[str, Any]], *, source_version: str):
        """Promote a set of partitions as one recoverable transaction."""
        if not staged:
            return []
        symbol = str(staged[0]["symbol"]).upper()
        dataset = str(staged[0]["physical_dataset"])
        identity = tuple(staged[0].get(key) for key in ("logical_dataset", "physical_dataset", "manifest_path", "parquet_root"))
        if any((str(item["symbol"]).upper() != symbol or
                tuple(item.get(key) for key in ("logical_dataset", "physical_dataset", "manifest_path", "parquet_root")) != identity)
               for item in staged):
            return [{"status": "QUARANTINED", "reason_codes": ["BATCH_TICKER_OR_DATASET_MISMATCH"]}]
        route_access = self._bound_access(staged[0])
        canonical_root = Path(staged[0]["parquet_root"]) / dataset / f"symbol={symbol}"
        files = [route_access.manifest_path, route_access.provenance_manifest_path,
                 self.catalog.path, self.ledger.path]
        transaction_root = self.staging_root / ".transactions"
        transaction_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pcs_import_txn_", dir=transaction_root) as backup:
            backup_root = Path(backup)
            file_state = []
            for index, path in enumerate(files):
                path = Path(path); copy = backup_root / f"file_{index}"
                if path.exists(): shutil.copy2(path, copy)
                file_state.append((path, copy, path.exists()))
            tree_backup = backup_root / "canonical"
            tree_exists = canonical_root.exists()
            if tree_exists: shutil.copytree(canonical_root, tree_backup)
            results = [self.promote(item, source_version=source_version) for item in staged]
            if all(item.get("status") == "IMPORTED" for item in results):
                return results
            if canonical_root.exists(): shutil.rmtree(canonical_root)
            if tree_exists: shutil.copytree(tree_backup, canonical_root)
            for path, copy, existed in file_state:
                if existed: shutil.copy2(copy, path)
                elif path.exists(): path.unlink()
            return [{"status": "QUARANTINED", "reason_codes": ["BATCH_PROMOTION_ROLLED_BACK"],
                     "partitions": results}]


def repair_daily_session(symbol: str, target_date: str, window: pd.DataFrame, *, access=None,
                         source_version="daily_safety_window") -> dict[str, Any]:
    """Validate one missing daily session from a bounded safety window."""
    from .daily_provider import normalize_daily_frame
    from .incremental_update import update_ticker
    symbol = str(symbol).strip().upper(); target = pd.Timestamp(target_date).date()
    try:
        frame = normalize_daily_frame(window.copy())
        if "symbol" not in frame: frame["symbol"] = symbol
        frame["symbol"] = frame.symbol.astype(str).str.upper()
        rows = frame[(frame.symbol == symbol) & (pd.to_datetime(frame.date).dt.date == target)].copy()
        if len(rows) != 1: return {"status": "BLOCKED", "reason_codes": ["DAILY_TARGET_SESSION_NOT_UNIQUE"], "target_date": str(target), "rows": len(rows)}
        row = rows.iloc[0:1]
        if not (row.high.iloc[0] >= max(row.open.iloc[0], row.close.iloc[0], row.low.iloc[0]) and row.low.iloc[0] <= min(row.open.iloc[0], row.close.iloc[0], row.high.iloc[0])):
            return {"status": "BLOCKED", "reason_codes": ["DAILY_INVALID_OHLCV"], "target_date": str(target)}
        result = update_ticker(symbol, daily_frame=row, parquet_root=access.parquet_root if access else "data/parquet", manifest_path=access.manifest_path if access else "data/manifests/storage_manifest.csv", source_version=source_version, refresh_research_readiness=False)
        return {"status": "AUTO_REPAIRED", "target_date": str(target), "result": result}
    except Exception as exc:
        return {"status": "BLOCKED", "reason_codes": [type(exc).__name__], "detail": str(exc), "target_date": str(target)}


def repair_canonical_file_access(paths: list[str] | tuple[str, ...], *, canonical_root: str | Path) -> dict[str, Any]:
    """Repair access to exact canonical files without contacting a provider.

    Targets must already exist beneath the configured canonical root. On
    Windows, ownership/ACL repair is attempted only after the ordinary chmod
    path fails. A privilege failure remains a permission blocker and must not
    be reclassified as a data gap.
    """
    root = Path(canonical_root).resolve()
    repaired: list[str] = []
    blocked: list[dict[str, Any]] = []
    for raw in paths:
        target = Path(raw)
        if not target.is_absolute():
            target = Path.cwd() / target
        target = target.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            blocked.append({"path": str(target), "reason_code": "CANONICAL_PERMISSION_TARGET_OUTSIDE_ROOT"})
            continue
        if not target.exists():
            blocked.append({"path": str(target), "reason_code": "CANONICAL_FILE_DISAPPEARED"})
            continue
        commands: list[dict[str, Any]] = []
        try:
            target.chmod(target.stat().st_mode | stat.S_IREAD | stat.S_IWRITE)
        except OSError as exc:
            commands.append({"command": "chmod", "returncode": None, "detail": str(exc)})
        try:
            with target.open("rb"):
                pass
            repaired.append(str(target))
            continue
        except OSError:
            pass
        if os.name == "nt":
            username = os.environ.get("USERNAME", "")
            attempts = (["takeown", "/f", str(target)],
                        ["icacls", str(target), "/inheritance:e"],
                        ["icacls", str(target), "/grant:r", f"{username}:(R)"])
            for command in attempts:
                completed = subprocess.run(command, capture_output=True, text=True, shell=False)
                commands.append({"command": command[0], "returncode": completed.returncode,
                                 "detail": (completed.stdout + completed.stderr).strip()})
        try:
            with target.open("rb"):
                pass
            repaired.append(str(target))
        except OSError as exc:
            blocked.append({"path": str(target),
                            "reason_code": "CANONICAL_PERMISSION_REPAIR_REQUIRES_OWNER",
                            "detail": str(exc), "attempts": commands})
    return {"status": "AUTO_REPAIRED" if not blocked else "BLOCKED",
            "reason_codes": [] if not blocked else ["CANONICAL_PERMISSION_REPAIR_REQUIRES_OWNER"],
            "repaired_paths": repaired, "blocked_paths": blocked,
            "provider_download_attempted": False}


class ImportCoordinator:
    """Execute a plan through injected, already-authorized import handlers."""
    def __init__(self, control_plane=None, engine=None, handlers=None):
        self.control_plane = control_plane or MarketDataControlPlane()
        self.engine = engine or ImportEngine(access=self.control_plane.access)
        self.handlers = handlers or {}

    def run(self, requirements, symbol=None):
        plan = self.control_plane.plan(requirements, symbol)
        outcomes = []
        for action in plan.actions:
            if action["action"] == PlanAction.REUSE_CANONICAL.value: outcomes.append({"dataset": action["dataset"], "status": "REUSED"}); continue
            # Action-specific remediation must win over a generic dataset
            # importer. In particular, unreadable existing files may never be
            # routed to the options download handler.
            handler = self.handlers.get(action["action"]) or self.handlers.get(action["dataset"])
            if handler is None:
                outcomes.append({"dataset": action["dataset"], "status": "BLOCKED", "reason_codes": ["IMPORT_HANDLER_NOT_REGISTERED"]}); continue
            try:
                value = handler(plan)
                status = "IMPORTED" if not isinstance(value, dict) or value.get("status") in {"IMPORTED", "READY", "ALREADY_COMPLETE", "SUCCESS", "REUSED"} else "BLOCKED"
                outcomes.append({"dataset": action["dataset"], "status": status, "result": value})
            except Exception as exc:
                outcomes.append({"dataset": action["dataset"], "status": "BLOCKED", "reason_codes": [type(exc).__name__], "detail": str(exc)})
        try:
            final = self.control_plane.get_market_data_status(plan.requirements)
            payload = final.to_dict()
        except Exception as exc:
            payload = {"status": "BLOCKED", "symbol": plan.requirements.symbol,
                       "requirements": asdict(plan.requirements),
                       "reason_codes": ["FINAL_CANONICAL_STATUS_UNAVAILABLE"],
                       "detail": str(exc)}
        payload["imported_periods"] = tuple(x["dataset"] for x in outcomes if x["status"] == "IMPORTED")
        payload["blocked_periods"] = tuple(x["dataset"] for x in outcomes if x["status"] == "BLOCKED")
        payload["stages"] = {**payload.get("stages", {}), **{x["dataset"]: x["status"] for x in outcomes}}
        payload["initial_plan"] = asdict(plan)
        payload["import_outcomes"] = outcomes
        payload["selected_source"] = tuple(sorted({
            str(x.get("result", {}).get("selected_source"))
            for x in outcomes if isinstance(x.get("result"), dict)
            and x.get("result", {}).get("selected_source")
        }))
        payload["provider_coverage"] = tuple(
            x.get("result", {}).get("provider_coverage")
            for x in outcomes if isinstance(x.get("result"), dict)
            and x.get("result", {}).get("provider_coverage") is not None
        )
        payload["promoted_partitions"] = tuple(
            partition for x in outcomes if isinstance(x.get("result"), dict)
            for partition in x.get("result", {}).get("promoted_partitions", ())
            if isinstance(partition, dict) and partition.get("status") == "IMPORTED"
        )
        payload["blocked_partitions"] = tuple(
            partition for x in outcomes if isinstance(x.get("result"), dict)
            for partition in x.get("result", {}).get("promoted_partitions", ())
            if isinstance(partition, dict) and partition.get("status") != "IMPORTED"
        )
        payload["final_canonical_status"] = payload.get("status", "BLOCKED")
        return {"status": payload.get("status", "BLOCKED"), "result": payload, "outcomes": outcomes}


def default_import_handlers(*, daily_snapshot_path=None, archive_root=None,
                             historical_root="data/raw/daily_forward_adjusted",
                             options_raw_root="data/raw/options",
                             options_output_root="data/parquet/options",
                             options_manifest_path="data/manifests/storage_manifest.csv",
                             **kwargs):
    """Return handlers backed by the repository's canonical importers.

    The handlers are intentionally explicit and lazy: no provider is contacted
    merely by asking for a plan, and a missing credential/source remains a
    classified failure in the coordinator.
    """
    from .import_daily_snapshot import import_daily_snapshot
    access = kwargs.get("access") or PCSDataAccess.canonical()

    def daily(plan):
        req = plan.requirements if hasattr(plan, "requirements") else plan
        gateway = kwargs.get("massive_client")
        if gateway is None:
            try:
                from .massive_client import GatewayConfig, MassiveCompatibleClient
                gateway = MassiveCompatibleClient(GatewayConfig.from_environment())
            except Exception:
                gateway = None
        if gateway is not None and req.required_start and req.required_end:
            from .incremental_update import update_ticker
            frame = gateway.fetch_daily_range(req.symbol, req.required_start, req.required_end)
            return update_ticker(req.symbol, daily_frame=frame,
                parquet_root=kwargs.get("parquet_root", "data/parquet"),
                manifest_path=kwargs.get("manifest_path", "data/manifests/storage_manifest.csv"),
                source_version=f"massive_daily:{req.required_start}:{req.required_end}",
                refresh_research_readiness=False)
        if req.required_end and daily_snapshot_path is None:
            from .import_daily_snapshot import find_latest_daily_snapshot
            try:
                latest = find_latest_daily_snapshot()
                if pd.Timestamp(latest.stem.split("daily_", 1)[-1]) < pd.Timestamp(req.required_end):
                    return {"status": "BLOCKED", "reason_codes": ["DAILY_SOURCE_COVERAGE_UNAVAILABLE"],
                            "source_path": str(latest), "required_end": req.required_end}
            except Exception:
                return {"status": "BLOCKED", "reason_codes": ["DAILY_SOURCE_COVERAGE_UNAVAILABLE"],
                        "required_end": req.required_end}
        return import_daily_snapshot(source_path=daily_snapshot_path,
                                     historical_root=historical_root,
                                     # Invalid source rows are quarantined with
                                     # machine-readable evidence; they must not
                                     # block valid symbols in an all-market
                                     # snapshot.
                                     skip_invalid_rows=True,
                                     sync_parquet=True,
                                     run_id=kwargs.get("run_id"), request_id=kwargs.get("request_id")).__dict__

    def options(plan):
        req = plan.requirements if hasattr(plan, "requirements") else plan
        resolver = kwargs.get("resolver") or SourceResolver(kwargs.get("source_registry_path"))
        approved = {str(item.get("source_id")) for item in resolver.resolve("options")}
        if "clickhouse_options" not in approved and kwargs.get("clickhouse_client") is None:
            return {"status": "BLOCKED", "reason_codes": ["SOURCE_NOT_AUTHORIZED"], "selected_source": "clickhouse_options"}
        current_client = kwargs.get("clickhouse_client")
        if current_client is None:
            import os
            from .massive_client import load_project_environment
            load_project_environment()
            password = os.getenv("CLICKHOUSE_PASSWORD")
            if not password:
                return {"status": "BLOCKED", "reason_codes": [
                    "CONFIGURATION_NOT_LOADED" if os.getenv("PCS_ENV_FILE") and not Path(os.environ["PCS_ENV_FILE"]).is_file()
                    else "CLICKHOUSE_CREDENTIALS_MISSING"], "selected_source": "clickhouse_options"}
            from .clickhouse import PCSClickHouseClient
            current_client = PCSClickHouseClient(os.getenv("CLICKHOUSE_URL", "http://db.base32.cn:8123/"), os.getenv("CLICKHOUSE_USER", "hisdata230"), password)
        if current_client is not None and req.required_start and req.required_end:
            from .incremental_update import update_ticker
            from .control_plane import ImportEngine
            start = req.required_start
            # Respect the instrument's own canonical listing boundary.  The
            # provider is never queried for pre-listing dates.
            try:
                daily = access.read_prices(req.symbol)
                if not daily.empty:
                    first_daily = pd.Timestamp(daily["date"].min()).date()
                    start = max(pd.Timestamp(start).date(), first_daily).isoformat()
            except (DataAccessError, FileNotFoundError, ValueError):
                pass
            # Do not advance past the latest quote: an earlier requested
            # session may be an interior gap. The plan reuses complete data;
            # promotion merges this bounded response into the active partition.
            coverage = current_client.fetch_options_coverage(req.symbol, str(start), req.required_end)
            if coverage.get("status") != "READY":
                return {**coverage, "selected_source": "clickhouse_options"}
            frame = current_client.fetch_options_range(req.symbol, str(start), req.required_end)
            if frame.empty:
                return {**coverage, "status": "BLOCKED", "reason_codes": ["AUTHORIZED_SOURCE_NO_ROWS"], "selected_source": "clickhouse_options"}
            # ClickHouse may repeat an identical quote row in the current
            # source.  Exact full-row duplicates are safe to remove under the
            # canonical policy; different payloads for one contract identity
            # remain fail-closed in PCSDataAccess.validate_schema().
            frame = frame.drop_duplicates(keep="last").reset_index(drop=True)
            frame["symbol"] = frame["symbol"].astype(str).str.upper()
            if set(frame["symbol"].unique()) != {req.symbol}:
                return {"status": "BLOCKED", "reason_codes": ["TICKER_ISOLATION_FAILED"], "selected_source": "clickhouse_options"}
            # Stage every partition first. Nothing is promoted until the full
            # provider response has passed identity and quote validation.
            engine = ImportEngine(access=access)
            staged = []
            for (year, quarter), part in frame.groupby([pd.to_datetime(frame.trade_date).dt.year,
                                                          ((pd.to_datetime(frame.trade_date).dt.quarter))]):
                partition = f"year={int(year)}/quarter={int(quarter)}"
                staged.append(engine.stage(part.reset_index(drop=True), symbol=req.symbol,
                    dataset="options", partition=partition, source_id="clickhouse_options"))
            promotions = engine.promote_batch(staged, source_version=f"clickhouse:{coverage.get('source_table')}:{req.required_start}:{req.required_end}")
            blocked = [x for x in promotions if x.get("status") != "IMPORTED"]
            return {"status": "BLOCKED" if blocked else "IMPORTED", "provider_coverage": coverage,
                    "selected_source": "clickhouse_options", "promoted_partitions": promotions,
                    "reason_codes": ["PROMOTION_FAILED"] if blocked else []}
        return {"status": "BLOCKED", "reason_codes": ["OPTIONS_REQUEST_WINDOW_REQUIRED"], "selected_source": "clickhouse_options"}

    def events(plan):
        req = plan.requirements if hasattr(plan, "requirements") else plan
        resolver = kwargs.get("resolver") or SourceResolver(kwargs.get("source_registry_path"))
        approved = {str(item.get("source_id")) for item in resolver.resolve("events")}
        if "palantir_ir_earnings" not in approved:
            return {"status": "BLOCKED", "reason_codes": ["SOURCE_NOT_AUTHORIZED"],
                    "selected_source": "palantir_ir_earnings"}
        # Event import is provider-neutral.  A registered event adapter must
        # accept (symbol, start, end) and return PIT event rows.  The former
        # symbol-specific Palantir branch made recovery silently a no-op for
        # every other ticker and violated the control-plane contract.
        event_fetcher = kwargs.get("event_fetcher")
        if not callable(event_fetcher):
            return {"status": "BLOCKED", "reason_codes": ["EVENT_PIT_SOURCE_UNAVAILABLE"],
                    "selected_source": "palantir_ir_earnings"}
        frame = event_fetcher(req.symbol, req.required_start, req.required_end)
        if frame is None:
            return {"status": "BLOCKED", "reason_codes": ["EVENT_PIT_SOURCE_NO_ROWS"],
                    "selected_source": "palantir_ir_earnings"}
        frame = frame.copy()
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        frame = frame[frame["symbol"] == req.symbol]
        frame = frame[(frame["event_date"] >= pd.Timestamp(req.required_start or frame.event_date.min())) &
                      (frame["event_date"] <= pd.Timestamp(req.required_end or frame.event_date.max()))]
        if frame.empty:
            return {"status": "BLOCKED", "reason_codes": ["EVENT_PIT_SOURCE_NO_ROWS"],
                    "selected_source": "palantir_ir_earnings"}
        invalid = frame[pd.to_datetime(frame.event_asof, utc=True).dt.tz_localize(None) >=
                        pd.to_datetime(frame.event_date)]
        if not invalid.empty:
            return {"status": "BLOCKED", "reason_codes": ["EVENT_PIT_TIMESTAMP_INVALID"],
                    "selected_source": "palantir_ir_earnings"}
        engine = ImportEngine(access=access)
        staged = []
        for year, part in frame.groupby(pd.to_datetime(frame.event_date).dt.year):
            staged.append(engine.stage(part.reset_index(drop=True), symbol=req.symbol,
                                       dataset="events", partition=f"year={int(year)}",
                                       source_id="palantir_ir_earnings"))
        promotions = engine.promote_batch(staged, source_version="palantir_ir_archive_pit_v1")
        blocked = [x for x in promotions if x.get("status") != "IMPORTED"]
        return {"status": "BLOCKED" if blocked else "IMPORTED",
                "reason_codes": ["PROMOTION_FAILED"] if blocked else [],
                "selected_source": "palantir_ir_earnings",
                "promoted_partitions": promotions}

    def canonical_file_access(plan):
        failures = []
        for item in plan.existing.values():
            failures.extend(item.get("access_failures", ()))
        return repair_canonical_file_access(
            [str(item["path"]) for item in failures if item.get("path")],
            canonical_root=access.parquet_root,
        )

    return {"daily": daily, "options": options, "events": events,
            PlanAction.REPAIR_DAILY_FROM_GATEWAY.value: daily,
            PlanAction.IMPORT_HISTORICAL_OPTIONS.value: options,
            PlanAction.SYNC_CURRENT_OPTIONS_FROM_CLICKHOUSE.value: options,
            PlanAction.IMPORT_PIT_EVENTS.value: events,
            PlanAction.REPAIR_CANONICAL_FILE_ACCESS.value: canonical_file_access}

class MarketDataControlPlane:
    MODULE = "pcs.data.control_plane"; VERSION = "1.0"
    def __init__(self, access=None, resolver=None):
        self.access = access or PCSDataAccess.canonical(); self.resolver = resolver or SourceResolver()
    def _existing(self, req):
        out = {}
        for dataset in req.datasets:
            try:
                # Event rows are sparse occurrences, not continuous market
                # sessions. Their first/last event dates must not be treated
                # as a coverage gap around an otherwise valid research range.
                spec = self.access.resolve_source(dataset, req.symbol) if dataset == "events" else self.access.resolve_source(dataset, req.symbol, req.required_start, req.required_end)
                payload = {"present": True, **spec.to_dict()}
                if dataset == "daily":
                    # Planning must use the verified active generation, not a
                    # newer legacy fixed-target file that is outside the
                    # manifest generation boundary.
                    manifest = self.access._read_manifest(self.access.manifest_path)
                    rows = manifest[(manifest.dataset.astype(str) == "daily") &
                                    manifest.symbol.astype(str).str.upper().eq(str(req.symbol).upper())]
                    active = rows[rows.active_generation.notna() &
                                  rows.active_generation.astype(str).str.strip().ne("") &
                                  rows.active_generation.astype(str).str.lower().ne("nan")]
                    if len(active):
                        payload["last_date"] = str(pd.to_datetime(active.max_date, errors="coerce").max().date())
                        payload["row_count"] = int(pd.to_numeric(active.row_count, errors="coerce").fillna(0).sum())
                        if req.required_start and req.required_end:
                            # Coverage endpoints cannot prove interior sessions.
                            # Inspect pinned active objects, never legacy files
                            # which may contain unadmitted or conflicting rows.
                            import exchange_calendars as xc
                            start = max(pd.Timestamp(req.required_start),
                                        pd.to_datetime(active.min_date).min())
                            end = pd.Timestamp(req.required_end)
                            expected = set(xc.get_calendar("XNYS").sessions_in_range(start, end).date) if start <= end else set()
                            actual = set()
                            intersecting = active[pd.to_datetime(active.min_date).le(end) &
                                                  pd.to_datetime(active.max_date).ge(start)]
                            for row in intersecting.itertuples():
                                pinned = self.access.read_pinned_generation("daily", req.symbol,
                                    str(row.partition_ids), str(row.active_generation))
                                actual.update(pd.to_datetime(pinned.date).dt.date)
                            missing = sorted(str(day) for day in expected - actual)
                            if missing:
                                payload["missing_sessions"] = missing
                                payload["coverage_gap"] = "DAILY_SESSION_MISSING"
                                payload.setdefault("reason_codes", []).append("DAILY_SESSION_MISSING")
                if dataset == "options" and req.required_start == req.required_end and req.required_end:
                    from .strategy_readiness import resolve_active_verified_options_handle
                    try:
                        handle = resolve_active_verified_options_handle(req.symbol, req.required_end, data_access=self.access)
                        quotes = self.access.read_verified_dataset(handle, start_date=req.required_start, end_date=req.required_end)
                        if quotes.empty:
                            raise ValueError("OPTIONS_SESSION_MISSING")
                    except (DataAccessError, FileNotFoundError, ValueError):
                        payload["coverage_gap"] = "OPTIONS_SESSION_MISSING"
                out[dataset] = payload
            except CanonicalFileAccessError as exc:
                out[dataset] = {"present": True, "readable": False,
                                "reason_code": exc.reason_code,
                                "reason_codes": [exc.reason_code],
                                "access_failures": list(exc.failures),
                                "detail": str(exc)}
            except (DataAccessError, FileNotFoundError, ValueError) as exc:
                # A source may exist while the requested window extends beyond
                # its actual/listing boundary. Resolve identity without the
                # window so the planner can report a precise coverage gap.
                try:
                    spec = self.access.resolve_source(dataset, req.symbol)
                    reasons = []
                    if req.required_start and pd.Timestamp(req.required_start) < pd.Timestamp(spec.first_date):
                        reasons.append("PRE_LISTING_NOT_REQUIRED")
                    if req.required_end and pd.Timestamp(req.required_end) > pd.Timestamp(spec.last_date):
                        reasons.append("OPTION_STALE" if dataset == "options" else "DAILY_STALE")
                    payload = {"present": True, "reason_codes": reasons, **spec.to_dict()}
                    if not (reasons and set(reasons) <= {"PRE_LISTING_NOT_REQUIRED"}):
                        payload["coverage_gap"] = str(exc)
                    out[dataset] = payload
                except CanonicalFileAccessError as access_exc:
                    out[dataset] = {"present": True, "readable": False,
                                    "reason_code": access_exc.reason_code,
                                    "reason_codes": [access_exc.reason_code],
                                    "access_failures": list(access_exc.failures),
                                    "detail": str(access_exc)}
                except (DataAccessError, FileNotFoundError, ValueError):
                    out[dataset] = {"present": False, "reason_code": "DATASET_UNAVAILABLE", "detail": str(exc)}
        return out

    def validate_content(self, req: MarketDataRequirements) -> dict[str, Any]:
        """Perform deterministic content checks on already-resolved canonical data."""
        report = {}
        for dataset in req.datasets:
            item = {"status": "UNKNOWN", "reason_codes": []}
            try:
                if dataset == "events":
                    frame = self.access.read(dataset, req.symbol)
                    event_dates = pd.to_datetime(frame["event_date"])
                    if req.required_start:
                        frame = frame[event_dates >= pd.Timestamp(req.required_start)]
                        event_dates = pd.to_datetime(frame["event_date"])
                    if req.required_end:
                        frame = frame[event_dates <= pd.Timestamp(req.required_end)]
                else:
                    frame = self.access.read(dataset, req.symbol, req.required_start, req.required_end)
                if dataset == "daily":
                    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
                    missing = sorted(required - set(frame.columns))
                    duplicate = int(frame.duplicated(["symbol", "date"]).sum()) if not missing else 0
                    item.update({"row_count": len(frame), "duplicate_dates": duplicate})
                    if missing: item["reason_codes"].append("DAILY_REQUIRED_FIELD_MISSING")
                    if duplicate: item["reason_codes"].append("DAILY_DUPLICATE_DATE")
                    # The repository's existing readiness contract uses the
                    # canonical SPY daily route as the exchange-session
                    # authority. Never synthesize sessions with weekdays.
                    try:
                        first_ticker_date = pd.to_datetime(frame["date"]).min()
                        session_start = max(pd.Timestamp(req.required_start or first_ticker_date), first_ticker_date)
                        session = self.access.read_prices("SPY", session_start, req.required_end)
                        expected = set(pd.to_datetime(session.date).dt.date)
                        actual = set(pd.to_datetime(frame.date).dt.date) if "date" in frame else set()
                        missing_sessions = sorted(str(x) for x in expected - actual)
                        item["missing_sessions"] = missing_sessions
                        if missing_sessions: item["reason_codes"].append("DAILY_SESSION_MISSING")
                    except Exception:
                        item["session_authority"] = "UNAVAILABLE"
                elif dataset == "options":
                    required = {"symbol", "trade_date", "expiration_date", "call_put", "strike", "bid", "ask"}
                    missing = sorted(required - set(frame.columns))
                    duplicate = int(frame.duplicated(["symbol", "trade_date", "expiration_date", "call_put", "strike"]).sum()) if not missing else 0
                    sides = set(frame.call_put.astype(str).str.lower()) if "call_put" in frame else set()
                    item.update({"row_count": len(frame), "duplicate_keys": duplicate, "call_side": "c" in sides, "put_side": "p" in sides})
                    if missing: item["reason_codes"].append("OPTION_REQUIRED_FIELD_MISSING")
                    if duplicate: item["reason_codes"].append("OPTION_EXACT_DUPLICATES")
                    if sides and "c" not in sides: item["reason_codes"].append("OPTION_CALL_SIDE_MISSING")
                    if sides and "p" not in sides: item["reason_codes"].append("OPTION_PUT_SIDE_MISSING")
                elif dataset == "events":
                    required = {"symbol", "event_type", "event_date", "event_asof", "source", "source_id"}
                    missing = sorted(required - set(frame.columns))
                    item.update({"row_count": len(frame), "missing_fields": missing})
                    if missing:
                        item["reason_codes"].append("EVENT_PIT_REQUIRED_FIELD_MISSING")
                    elif frame[list(required)].isna().any().any():
                        item["reason_codes"].append("EVENT_PIT_NULL_FIELD")
                    elif (pd.to_datetime(frame.event_asof, utc=True).dt.tz_localize(None) >=
                          pd.to_datetime(frame.event_date)).any():
                        item["reason_codes"].append("EVENT_PIT_TIMESTAMP_INVALID")
                else:
                    item["reason_codes"].append("DATASET_VALIDATOR_NOT_REGISTERED")
                item["status"] = "READY" if not item["reason_codes"] else "BLOCKED"
            except Exception as exc: item.update(status="BLOCKED", reason_codes=["DATASET_UNAVAILABLE"], detail=str(exc))
            report[dataset] = item
        return report
    def plan(self, requirements, symbol=None):
        req = requirements if isinstance(requirements, MarketDataRequirements) else MarketDataRequirements.from_mapping(symbol, requirements)
        existing = self._existing(req); actions = []
        for dataset in req.datasets:
            if dataset == "options" and req.exact_contract_quote_keys:
                actions.append({"dataset": dataset, "action": PlanAction.REPAIR_EXACT_OPTIONS_GAP.value,
                                "reason": "exact execution quote keys require source-level probe"})
                continue
            if existing[dataset].get("reason_code") == "CANONICAL_FILE_ACCESS_DENIED":
                actions.append({"dataset": dataset,
                                "action": PlanAction.REPAIR_CANONICAL_FILE_ACCESS.value,
                                "selected_sources": [],
                                "reason": "registered canonical files exist but are unreadable"})
                continue
            current_stale = bool(req.decision_as_of and existing[dataset].get("last_date") and
                                 pd.Timestamp(existing[dataset]["last_date"]) < pd.Timestamp(req.decision_as_of))
            if current_stale:
                existing[dataset].setdefault("reason_codes", []).append("CANONICAL_OPTIONS_STALE" if dataset == "options" else "CANONICAL_DAILY_STALE")
                existing[dataset]["coverage_gap"] = "CURRENT_DECISION_FRESHNESS_GAP"
            if dataset == "daily" and req.required_history_rows and int(existing[dataset].get("row_count", 0)) < int(req.required_history_rows):
                existing[dataset].setdefault("reason_codes", []).append("DAILY_HISTORY_WARMUP_INSUFFICIENT")
                existing[dataset]["coverage_gap"] = "FEATURE_WARMUP_GAP"
            if existing[dataset]["present"] and "coverage_gap" not in existing[dataset]:
                actions.append({"dataset": dataset, "action": PlanAction.REUSE_CANONICAL.value, "reason": "validated canonical source exists"})
            else:
                sources = self.resolver.resolve(dataset)
                if dataset == "daily": action = PlanAction.REPAIR_DAILY_FROM_GATEWAY if sources else PlanAction.BLOCKED_NO_AUTHORIZED_SOURCE
                elif dataset == "events": action = PlanAction.IMPORT_PIT_EVENTS if sources else PlanAction.BLOCKED_NO_AUTHORIZED_SOURCE
                elif sources and req.required_start:
                    # Historical quarters require the approved ZIP + ClickHouse
                    # overlap path; dates in the current calendar year use the
                    # bounded ClickHouse incremental path.  Do not classify a
                    # current gap as historical merely because the request also
                    # includes older dates.
                    current_year = pd.Timestamp.now(tz="UTC").year
                    existing_last = existing.get("options", {}).get("last_date")
                    current_gap = (pd.Timestamp(req.required_start).year == current_year or
                                   bool(existing_last and pd.Timestamp(existing_last).year == current_year))
                    action = PlanAction.SYNC_CURRENT_OPTIONS_FROM_CLICKHOUSE if current_gap else PlanAction.IMPORT_HISTORICAL_OPTIONS
                else: action = PlanAction.SYNC_CURRENT_OPTIONS_FROM_CLICKHOUSE if sources else PlanAction.BLOCKED_NO_AUTHORIZED_SOURCE
                selected = sources
                if action == PlanAction.SYNC_CURRENT_OPTIONS_FROM_CLICKHOUSE:
                    selected = [source for source in sources if source.get("source_id") == "clickhouse_options"]
                actions.append({"dataset": dataset, "action": action.value, "selected_sources": selected, "reason": "canonical gap"})
        required_periods = []
        if "options" in req.datasets and req.required_start and req.required_end:
            effective_start = req.required_start
            physical_start = existing.get("options", {}).get("first_date")
            if physical_start and pd.Timestamp(physical_start) > pd.Timestamp(effective_start):
                effective_start = physical_start
            start = pd.Timestamp(effective_start).to_period("Q")
            end = pd.Timestamp(req.required_end).to_period("Q")
            required_periods = [str(p) for p in pd.period_range(start, end, freq="Q")]
        canonical_periods = []
        option = existing.get("options", {})
        if option.get("present") and option.get("first_date") and option.get("last_date"):
            first_period = pd.Timestamp(option["first_date"]).to_period("Q")
            last_date = pd.Timestamp(option["last_date"])
            last_period = last_date.to_period("Q")
            # A quarter containing the latest observed date is incomplete
            # unless the source reaches that quarter's final calendar day.
            if last_date.date() < last_period.end_time.date():
                last_period = last_period - 1
            canonical_periods = [str(p) for p in pd.period_range(first_period, last_period, freq="Q")] if last_period >= first_period else []
        missing_periods = tuple(p for p in required_periods if p not in canonical_periods)
        blocked_periods = tuple(p for p in missing_periods if any(a["dataset"] == "options" and a["action"] == PlanAction.BLOCKED_NO_AUTHORIZED_SOURCE.value for a in actions))
        repair_periods = tuple(p for p in missing_periods if p not in blocked_periods)
        return CoveragePlan(req, existing, tuple(actions), tuple(required_periods), tuple(canonical_periods), repair_periods, (), blocked_periods)
    def get_market_data_status(self, requirements, symbol=None):
        plan = self.plan(requirements, symbol); missing = tuple(a["dataset"] for a in plan.actions if a["action"] != PlanAction.REUSE_CANONICAL.value)
        blockers = tuple(f"{a['dataset']}:{a['action']}" for a in plan.actions if a["action"] == PlanAction.BLOCKED_NO_AUTHORIZED_SOURCE.value)
        coverage_reasons = tuple(code for item in plan.existing.values() for code in item.get("reason_codes", ()))
        now = datetime.now(timezone.utc).isoformat()
        sources = tuple(source for dataset in plan.requirements.datasets for source in self.resolver.resolve(dataset))
        stages = {a["dataset"]: ("REUSED" if a["action"] == PlanAction.REUSE_CANONICAL.value else "BLOCKED" if a["action"] == PlanAction.BLOCKED_NO_AUTHORIZED_SOURCE.value else "NOT_RUN") for a in plan.actions}
        validation = self.validate_content(plan.requirements) if not missing else {"canonical": False}
        validation_reasons = tuple(
            f"{dataset}:{code}"
            for dataset, item in validation.items()
            for code in (item.get("reason_codes", ()) if isinstance(item, dict) else ())
        )
        status = (ImportStatus.BLOCKED.value if blockers or validation_reasons
                  else ImportStatus.ALREADY_COMPLETE.value if not missing
                  else ImportStatus.PARTIAL.value)
        plan_payload = asdict(plan)
        plan_payload["requirements"] = asdict(plan.requirements)
        reasons = blockers or validation_reasons or coverage_reasons or (("DATASET_GAP",) if missing else ())
        return MarketDataResult(self.MODULE, self.VERSION, plan.requirements.symbol, plan.requirements.required_end or now, status, now, self.VERSION, uuid.uuid4().hex, uuid.uuid4().hex, reasons, asdict(plan.requirements), plan_payload, validation, blockers or validation_reasons, sources, stages, tuple(a["dataset"] for a in plan.actions if a["action"] == PlanAction.REUSE_CANONICAL.value), (), (), tuple(a["dataset"] for a in plan.actions if a["action"] == PlanAction.BLOCKED_NO_AUTHORIZED_SOURCE.value))
    def ensure_market_data(self, requirements, importer=None, symbol=None):
        req = requirements if isinstance(requirements, MarketDataRequirements) else MarketDataRequirements.from_mapping(symbol, requirements)
        if req.exact_contract_quote_keys and "options" in req.datasets:
            # Exact-gap repair is deliberately bounded to the requested keys.
            # It never uses adjacent dates/strikes or a legacy fallback.
            from .clickhouse import PCSClickHouseClient
            import os
            from .massive_client import load_project_environment
            load_project_environment()
            client = PCSClickHouseClient(os.getenv("CLICKHOUSE_URL", "http://db.base32.cn:8123/"), os.getenv("CLICKHOUSE_USER", "hisdata230"), os.getenv("CLICKHOUSE_PASSWORD", ""))
            dates = sorted({str(x.get("quote_date", x.get("trade_date")))[:10] for x in req.exact_contract_quote_keys})
            source = client.fetch_options_dates(req.symbol, dates)
            source["trade_date"] = pd.to_datetime(source["trade_date"]).dt.normalize()
            source["expiration_date"] = pd.to_datetime(source["expiration_date"]).dt.normalize()
            source["call_put"] = source["call_put"].astype(str).str.lower().replace({"call": "c"})
            wanted = {(str(x.get("quote_date", x.get("trade_date")))[:10], str(x["expiration_date"])[:10], float(x["strike"]), str(x.get("call_put", "c")).lower().replace("call", "c")) for x in req.exact_contract_quote_keys}
            selected = source[source.apply(lambda r: (str(r.trade_date.date()), str(r.expiration_date.date()), float(r.strike), str(r.call_put)) in wanted, axis=1)].drop_duplicates(keep="last")
            actual = {(str(r.trade_date.date()), str(r.expiration_date.date()), float(r.strike), str(r.call_put)) for r in selected.itertuples()}
            if actual != wanted:
                return {"status": ImportStatus.BLOCKED.value, "reason_codes": ["AUTHORIZED_SOURCE_QUOTE_GAPS"], "source_status": "AUTHORIZED_SOURCE_QUOTE_GAP", "missing_keys": sorted(wanted - actual)}
            repair = self.repair_exact_option_quotes(req.symbol, selected, source_version="clickhouse_exact_key_auto_repair", expected_keys=list(wanted))
            return {"status": ImportStatus.READY.value, "source_status": "CANONICAL_INGESTION_GAP_AUTO_REPAIRABLE", "repair": repair, "repaired_keys": sorted(actual)}
        result = self.get_market_data_status(req)
        # A canonical validation gap is recoverable even when the status
        # envelope is BLOCKED.  Previously only PARTIAL entered the
        # coordinator, so a missing daily session (or equivalent repairable
        # validation issue) stopped before provider fetch/promotion.  Keep
        # source/permission blockers fail-closed; only validation gaps with
        # an authorized repair action may proceed.
        validation_gap_codes = {
            "DAILY_SESSION_MISSING", "DATASET_GAP", "CANONICAL_GAP",
            "OPTIONS_SESSION_MISSING", "OPTION_CHAIN_REFRESH_REQUIRED",
        }
        result_reasons = tuple(getattr(result, "reason_codes", ()) or ())
        has_repairable_validation_gap = any(
            any(code == candidate or code.endswith(f":{candidate}")
                for candidate in validation_gap_codes)
            for code in result_reasons
        ) and not any(str(code).endswith("BLOCKED_NO_AUTHORIZED_SOURCE") or
                      "PERMISSION" in str(code) for code in result_reasons)
        if result.status == ImportStatus.PARTIAL or has_repairable_validation_gap:
            if importer:
                importer(self.plan(req))
            else:
                # Default execution is still routed through the registered
                # repository importers; no provider is guessed by callers.
                coordinator = ImportCoordinator(self, handlers=default_import_handlers(access=self.access))
                envelope = coordinator.run(req)
                result = self.get_market_data_status(req)
                payload = envelope.get("result", {})
                outcome_reasons = tuple(dict.fromkeys(
                    code
                    for outcome in envelope.get("outcomes", ())
                    if outcome.get("status") == "BLOCKED"
                    for code in (outcome.get("reason_codes", ()) or
                                 outcome.get("result", {}).get("reason_codes", ()))
                ))
                return replace(result,
                    status=ImportStatus.BLOCKED.value if outcome_reasons else result.status,
                    reason_codes=tuple(dict.fromkeys((*outcome_reasons, *result.reason_codes))),
                    remaining_blockers=tuple(dict.fromkeys((*result.remaining_blockers, *outcome_reasons))),
                    initial_plan=payload.get("initial_plan", {}),
                    selected_source=tuple(payload.get("selected_source", ())),
                    provider_coverage=tuple(payload.get("provider_coverage", ())),
                    import_outcomes=tuple(payload.get("import_outcomes", envelope.get("outcomes", ()))),
                    promoted_partitions=tuple(payload.get("promoted_partitions", ())),
                    blocked_partitions=tuple(payload.get("blocked_partitions", ())),
                    final_canonical_status=payload.get("final_canonical_status", result.status))
            result = self.get_market_data_status(req)
        return result
    def require_market_data(self, symbol, requirements):
        req = requirements if isinstance(requirements, MarketDataRequirements) else MarketDataRequirements.from_mapping(symbol, requirements)
        result = self.get_market_data_status(req)
        if result.status not in {ImportStatus.READY.value, ImportStatus.ALREADY_COMPLETE.value}: raise DataAccessError(f"DATA_NOT_READY:{req.symbol}:{','.join(result.reason_codes)}")
        return result

    def repair_exact_option_quotes(self, symbol: str, frame: pd.DataFrame, *, source_version: str,
                                   expected_keys: list[tuple[str, str, float, str]] | None = None) -> dict[str, Any]:
        """Promote a bounded, source-probed options gap through the canonical upsert.

        This is intentionally narrower than an import: callers must provide
        exact execution rows already obtained from an authorized source. The
        existing options duplicate/conflict validation and partition upsert
        remain authoritative.
        """
        symbol = str(symbol).strip().upper()
        if frame is None or frame.empty:
            raise DataAccessError("EXACT_OPTIONS_GAP_EMPTY")
        checked = frame.copy()
        checked["symbol"] = checked.get("symbol", symbol).astype(str).str.upper()
        checked = checked[checked["symbol"] == symbol]
        checked["trade_date"] = pd.to_datetime(checked["trade_date"]).dt.normalize()
        checked["expiration_date"] = pd.to_datetime(checked["expiration_date"]).dt.normalize()
        checked["call_put"] = (checked["call_put"].astype(str).str.lower()
                                .replace({"call": "c", "put": "p"}))
        if not checked["call_put"].isin({"c", "p"}).all():
            raise DataAccessError("EXACT_OPTIONS_INVALID_CALL_PUT")
        checked = checked.drop_duplicates(keep="last")
        if expected_keys:
            actual = {(str(r.trade_date.date()), str(r.expiration_date.date()), float(r.strike), str(r.call_put)) for r in checked.itertuples()}
            missing = [k for k in expected_keys if k not in actual]
            if missing: raise DataAccessError(f"EXACT_OPTIONS_EXPECTED_KEYS_MISSING:{missing}")
        from .incremental_update import update_ticker
        physical_dataset, routed_manifest, routed_root = self.access._resolve_route("options", symbol)
        return update_ticker(symbol, options_frame=checked, parquet_root=str(routed_root),
                             options_manifest_path=str(routed_manifest), source_version=source_version,
                             physical_dataset=physical_dataset)

def get_market_data_status(symbol_or_requirements, requirements=None, *, access=None): return MarketDataControlPlane(access).get_market_data_status(requirements or symbol_or_requirements, None if requirements is None else symbol_or_requirements)
def ensure_market_data(symbol=None, requirements=None, *, access=None, importer=None): return MarketDataControlPlane(access).ensure_market_data(requirements or {}, importer, symbol)
def require_market_data(symbol, requirements, *, access=None): return MarketDataControlPlane(access).require_market_data(symbol, requirements)
