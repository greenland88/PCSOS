"""Unified market-data import control plane over canonical PCSDataAccess."""
from __future__ import annotations
import uuid
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
import pandas as pd
from .access import PCSDataAccess, DataAccessError

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

@dataclass(frozen=True)
class MarketDataRequirements:
    symbol: str
    required_start: str | None = None
    required_end: str | None = None
    datasets: tuple[str, ...] = ("daily", "options")
    exact_contract_quote_keys: tuple[dict[str, Any], ...] = ()
    required_fields: tuple[str, ...] = ()
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
        return cls(symbol or value.get("symbol", ""), start, end, datasets, tuple(dict(x) for x in keys), tuple(str(x) for x in fields))

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

    def stage(self, frame: pd.DataFrame, *, symbol: str, dataset: str, partition: str, source_id: str, request_id: str | None = None):
        rid = request_id or uuid.uuid4().hex
        target = self.staging_root / rid / symbol.upper() / dataset / partition
        target.mkdir(parents=True, exist_ok=True)
        path = target / "payload.parquet"
        frame.to_parquet(path, index=False)
        verify = pd.read_parquet(path)
        if len(verify) != len(frame): raise DataAccessError("STAGE_ROW_COUNT_MISMATCH")
        return {"status": "STAGED", "path": str(path), "symbol": symbol.upper(), "dataset": dataset, "partition": partition, "source_id": source_id, "request_id": rid, "row_count": len(frame)}

    def promote(self, staged: dict[str, Any], *, source_version: str):
        path = Path(staged["path"]); frame = pd.read_parquet(path)
        written = None
        try:
            written = self.access.write_partition(frame, staged["dataset"], staged["symbol"], staged["partition"], source_version=source_version)
            self.access.record_provenance({"dataset": staged["dataset"], "symbol": staged["symbol"], "partition": staged["partition"], "source_id": staged["source_id"], "source_version": source_version, "request_id": staged["request_id"], "row_count": len(frame), "status": "PROMOTED"})
            self.catalog.rebuild(self.access)
            self.ledger.record(request_id=staged["request_id"], source_id=staged["source_id"], symbol=staged["symbol"], dataset=staged["dataset"], shard=staged["partition"], physical_row_count=len(frame), status="API_COMPLETE", completed_at=datetime.now(timezone.utc).isoformat())
            return {"status": "IMPORTED", "path": str(written), "request_id": staged["request_id"]}
        except Exception as exc:
            # write_partition refuses overwrite by default; removing only the
            # path created in this transaction preserves any prior canonical
            # partition when metadata finalization fails.
            if written is not None:
                try: Path(written).unlink(missing_ok=True)
                except OSError: pass
            quarantine = self.staging_root / "quarantine" / staged["request_id"]
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            if path.exists(): path.replace(quarantine.with_suffix(".parquet"))
            return {"status": "QUARANTINED", "reason_codes": [type(exc).__name__], "detail": str(exc), "request_id": staged["request_id"]}


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
        result = update_ticker(symbol, daily_frame=row, parquet_root=access.parquet_root if access else "data/parquet", manifest_path=access.manifest_path if access else "data/manifests/storage_manifest.csv", source_version=source_version)
        return {"status": "AUTO_REPAIRED", "target_date": str(target), "result": result}
    except Exception as exc:
        return {"status": "BLOCKED", "reason_codes": [type(exc).__name__], "detail": str(exc), "target_date": str(target)}


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
            handler = self.handlers.get(action["dataset"]) or self.handlers.get(action["action"])
            if handler is None:
                outcomes.append({"dataset": action["dataset"], "status": "BLOCKED", "reason_codes": ["IMPORT_HANDLER_NOT_REGISTERED"]}); continue
            try:
                value = handler(plan)
                outcomes.append({"dataset": action["dataset"], "status": "IMPORTED", "result": value})
            except Exception as exc:
                outcomes.append({"dataset": action["dataset"], "status": "BLOCKED", "reason_codes": [type(exc).__name__], "detail": str(exc)})
        final = self.control_plane.get_market_data_status(plan.requirements)
        payload = final.to_dict()
        payload["imported_periods"] = tuple(x["dataset"] for x in outcomes if x["status"] == "IMPORTED")
        payload["blocked_periods"] = tuple(x["dataset"] for x in outcomes if x["status"] == "BLOCKED")
        payload["stages"] = {**payload.get("stages", {}), **{x["dataset"]: x["status"] for x in outcomes}}
        return {"status": final.status, "result": payload, "outcomes": outcomes}


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
    from .import_option_archives import import_option_archives
    access = kwargs.get("access") or PCSDataAccess.canonical()

    def daily(plan):
        req = plan.requirements if hasattr(plan, "requirements") else plan
        gateway = kwargs.get("massive_client")
        if gateway is not None and req.required_start and req.required_end:
            from .incremental_update import update_ticker
            frame = gateway.fetch_daily_range(req.symbol, req.required_start, req.required_end)
            return update_ticker(req.symbol, daily_frame=frame,
                parquet_root=kwargs.get("parquet_root", "data/parquet"),
                manifest_path=kwargs.get("manifest_path", "data/manifests/storage_manifest.csv"),
                source_version=f"massive_daily:{req.required_start}:{req.required_end}")
        return import_daily_snapshot(source_path=daily_snapshot_path,
                                     historical_root=historical_root,
                                     sync_parquet=True,
                                     run_id=kwargs.get("run_id"), request_id=kwargs.get("request_id")).__dict__

    def options(plan):
        req = plan.requirements if hasattr(plan, "requirements") else plan
        current_client = kwargs.get("clickhouse_client")
        if current_client is not None and req.required_start and req.required_end:
            from .incremental_update import update_ticker
            start = req.required_start
            try:
                existing = access.read("options", req.symbol)
                if len(existing):
                    latest = pd.Timestamp(existing["trade_date"].max()).date()
                    start = max(pd.Timestamp(start).date(), latest + pd.Timedelta(days=1).to_pytimedelta()).isoformat()
            except Exception:
                pass
            if str(start) > str(plan.requirements.required_end):
                return {"status": "REUSED", "reason_codes": ["CURRENT_OPTIONS_ALREADY_COVERED"], "requested_start": start}
            frame = current_client.fetch_options_range(req.symbol, str(start), req.required_end)
            # ClickHouse may repeat an identical quote row in the current
            # source.  Exact full-row duplicates are safe to remove under the
            # canonical policy; different payloads for one contract identity
            # remain fail-closed in PCSDataAccess.validate_schema().
            frame = frame.drop_duplicates(keep="last").reset_index(drop=True)
            return update_ticker(req.symbol, options_frame=frame,
                parquet_root=kwargs.get("parquet_root", "data/parquet"),
                options_manifest_path=options_manifest_path,
                source_version=f"clickhouse_incremental:{req.required_start}:{req.required_end}")
        loader = kwargs.get("clickhouse_loader")
        periods = kwargs.get("periods")
        if loader is not None and periods:
            from .onboarding import HistoricalTxtZipAdapter, onboard_ticker_incremental
            return onboard_ticker_incremental(req.symbol, periods, loader,
                adapter=HistoricalTxtZipAdapter(archive_root or r"K:\BaiduNetdiskDownload\USDailyOptions"),
                access=kwargs.get("access") or PCSDataAccess.canonical(),
                workers=kwargs.get("workers", 4), resume=True).__dict__
        return {"status": "BLOCKED", "reason_codes": ["CLICKHOUSE_LOADER_REQUIRED", "LEGACY_IMPORT_FALLBACK_DISABLED"], "detail": "Control plane cannot bypass overlap, staging, manifest, provenance, and readiness validation."}

    return {"daily": daily, "options": options,
            PlanAction.REPAIR_DAILY_FROM_GATEWAY.value: daily,
            PlanAction.IMPORT_HISTORICAL_OPTIONS.value: options,
            PlanAction.SYNC_CURRENT_OPTIONS_FROM_CLICKHOUSE.value: options}

class MarketDataControlPlane:
    MODULE = "pcs.data.control_plane"; VERSION = "1.0"
    def __init__(self, access=None, resolver=None):
        self.access = access or PCSDataAccess.canonical(); self.resolver = resolver or SourceResolver()
    def _existing(self, req):
        out = {}
        for dataset in req.datasets:
            try:
                spec = self.access.resolve_source(dataset, req.symbol, req.required_start, req.required_end)
                out[dataset] = {"present": True, **spec.to_dict()}
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
                except (DataAccessError, FileNotFoundError, ValueError):
                    out[dataset] = {"present": False, "reason_code": "DATASET_UNAVAILABLE", "detail": str(exc)}
        return out

    def validate_content(self, req: MarketDataRequirements) -> dict[str, Any]:
        """Perform deterministic content checks on already-resolved canonical data."""
        report = {}
        for dataset in req.datasets:
            item = {"status": "UNKNOWN", "reason_codes": []}
            try:
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
                else:
                    required = {"symbol", "trade_date", "expiration_date", "call_put", "strike", "bid", "ask"}
                    missing = sorted(required - set(frame.columns))
                    duplicate = int(frame.duplicated(["symbol", "trade_date", "expiration_date", "call_put", "strike"]).sum()) if not missing else 0
                    sides = set(frame.call_put.astype(str).str.lower()) if "call_put" in frame else set()
                    item.update({"row_count": len(frame), "duplicate_keys": duplicate, "call_side": "c" in sides, "put_side": "p" in sides})
                    if missing: item["reason_codes"].append("OPTION_REQUIRED_FIELD_MISSING")
                    if duplicate: item["reason_codes"].append("OPTION_EXACT_DUPLICATES")
                    if sides and "c" not in sides: item["reason_codes"].append("OPTION_CALL_SIDE_MISSING")
                    if sides and "p" not in sides: item["reason_codes"].append("OPTION_PUT_SIDE_MISSING")
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
            if existing[dataset]["present"] and "coverage_gap" not in existing[dataset]:
                actions.append({"dataset": dataset, "action": PlanAction.REUSE_CANONICAL.value, "reason": "validated canonical source exists"})
            else:
                sources = self.resolver.resolve(dataset)
                if dataset == "daily": action = PlanAction.REPAIR_DAILY_FROM_GATEWAY if sources else PlanAction.BLOCKED_NO_AUTHORIZED_SOURCE
                elif sources and req.required_start:
                    # Historical quarters require the approved ZIP + ClickHouse
                    # overlap path; dates in the current calendar year use the
                    # bounded ClickHouse incremental path.  Do not classify a
                    # current gap as historical merely because the request also
                    # includes older dates.
                    current_year = pd.Timestamp.now(tz="UTC").year
                    existing_last = existing.get("options", {}).get("last_date")
                    current_gap = bool(existing_last and pd.Timestamp(existing_last).year == current_year)
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
            client = PCSClickHouseClient(os.getenv("CLICKHOUSE_URL", "http://db.base32.cn:8123/"), os.getenv("CLICKHOUSE_USER", ""), os.getenv("CLICKHOUSE_PASSWORD", ""))
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
        result = self.get_market_data_status(requirements, symbol)
        if result.status == ImportStatus.PARTIAL:
            if importer:
                importer(self.plan(requirements, symbol))
            else:
                # Default execution is still routed through the registered
                # repository importers; no provider is guessed by callers.
                coordinator = ImportCoordinator(self, handlers=default_import_handlers(access=self.access))
                coordinator.run(requirements, symbol)
            result = self.get_market_data_status(requirements, symbol)
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
        checked["call_put"] = checked["call_put"].astype(str).str.lower().replace({"call": "c"})
        checked = checked[checked["call_put"] == "c"]
        checked = checked.drop_duplicates(keep="last")
        if expected_keys:
            actual = {(str(r.trade_date.date()), str(r.expiration_date.date()), float(r.strike), str(r.call_put)) for r in checked.itertuples()}
            missing = [k for k in expected_keys if k not in actual]
            if missing: raise DataAccessError(f"EXACT_OPTIONS_EXPECTED_KEYS_MISSING:{missing}")
        from .incremental_update import update_ticker
        return update_ticker(symbol, options_frame=checked, parquet_root=str(self.access.parquet_root),
                             options_manifest_path=str(self.access.manifest_path), source_version=source_version)

def get_market_data_status(symbol_or_requirements, requirements=None, *, access=None): return MarketDataControlPlane(access).get_market_data_status(requirements or symbol_or_requirements, None if requirements is None else symbol_or_requirements)
def ensure_market_data(symbol=None, requirements=None, *, access=None, importer=None): return MarketDataControlPlane(access).ensure_market_data(requirements or {}, importer, symbol)
def require_market_data(symbol, requirements, *, access=None): return MarketDataControlPlane(access).require_market_data(symbol, requirements)
