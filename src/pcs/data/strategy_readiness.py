"""Single readiness boundary for every strategy data request.

This module is deliberately ticker-neutral.  Strategies describe their data
contract; the control plane owns discovery, repair, promotion and read-back.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from enum import StrEnum
from typing import Any, Callable
from types import SimpleNamespace
import pandas as pd
import hashlib
from pathlib import Path
from .access import PCSDataAccess
from .control_plane import ensure_market_data

class DataStatus(StrEnum):
    READY="READY"; REPAIRABLE="REPAIRABLE"; FETCH_REQUIRED="FETCH_REQUIRED"; SOURCE_UNAVAILABLE="SOURCE_UNAVAILABLE"; STALE="STALE"; CORRUPTED="CORRUPTED"; TARGET_WINDOW_MISSING="TARGET_WINDOW_MISSING"

@dataclass(frozen=True)
class StrategyDataRequirements:
    underlying_lookback: int = 200
    option_right: str = "PUT"
    target_dte_min: int = 30
    target_dte_max: int = 45
    strike_range_method: str = "spot_support_atr_strategy_boundary"
    quote_freshness: str = "same_completed_session"
    required_fields: tuple[str,...] = ("bid","ask","open_interest","volume","delta","iv")
    minimum_usable_rows: int = 2
    pit_required: bool = True
    price_basis: str = "canonical_adjusted"
    corporate_action_basis: str = "canonical_identity"
    strike_range: tuple[float,float] | None = None

@dataclass(frozen=True)
class CoverageReport:
    requested_window: dict[str,Any]; available_window: dict[str,Any]; expirations: tuple[dict[str,Any],...]
    strike_min: float|None; strike_max: float|None; total_rows: int; valid_rows: int; quarantined_rows: int
    last_quote_timestamp: str|None; source: tuple[dict[str,Any],...]; canonical_generation: str|None
    status: str; reason_codes: tuple[str,...]=()
    def to_dict(self): return asdict(self)

@dataclass(frozen=True)
class VerifiedDatasetHandle:
    dataset: str; ticker: str; generation_id: str; partitions: tuple[str, ...]
    checksum: str; row_count: int; canonical_paths: tuple[str, ...]
    coverage: dict[str, Any]
    source_lineage: tuple[dict[str, Any], ...] = ()
    dataset_fingerprint: str = ""
    schema_version: str = ""
    price_basis: str = ""
    corporate_action_version: str = ""
    min_date: str = ""
    max_date: str = ""
    partition_count: int = 0
    manifest_identity: str = ""
    verification_status: str = "VERIFIED"

    def __post_init__(self):
        if not self.dataset or not self.ticker or not self.generation_id or not self.partitions or not self.canonical_paths:
            raise ValueError("INCOMPLETE_VERIFIED_DATASET_HANDLE")
        if self.generation_id in {"UNKNOWN", "UNPINNED_GENERATION"}:
            raise ValueError("PLACEHOLDER_GENERATION_FORBIDDEN")
        required = (self.dataset_fingerprint, self.checksum, self.coverage,
                    self.row_count, self.min_date, self.max_date,
                    self.schema_version, self.price_basis,
                    self.corporate_action_version)
        if any(value is None or value == "" or value == {} or value == 0 for value in required):
            raise ValueError("INCOMPLETE_VERIFIED_DATASET_PROVENANCE")
        if self.verification_status != "VERIFIED": raise ValueError("UNVERIFIED_DATASET_HANDLE")

@dataclass(frozen=True, init=False)
class VerifiedDataHandle:
    ticker: str; strategy_type: str; as_of: str; mode: str
    underlying_handle: VerifiedDatasetHandle; options_handle: VerifiedDatasetHandle
    price_basis: str; corporate_action_basis: str; verified_at: str
    quote_date: str | None; quote_timestamp: str | None
    source_lineage: tuple[dict[str, Any], ...]
    benchmark_handles: dict[str, VerifiedDatasetHandle] = field(default_factory=dict)
    event_provenance: dict[str, Any] = field(default_factory=dict)
    input_summary: dict[str, Any] = field(default_factory=dict)
    refresh_policy: str = "REUSE_VERIFIED"
    def __init__(self, ticker, strategy_type, as_of, mode, underlying_handle, options_handle,
                 price_basis, corporate_action_basis, verified_at, quote_date, quote_timestamp,
                 source_lineage=(), *legacy, benchmark_handles=None,
                 event_provenance=None, input_summary=None,
                 refresh_policy="REUSE_VERIFIED"):
        # Accept the pre-split positional shape only to fail it deterministically;
        # no legacy data can be consumed or turned into a READY handle.
        if isinstance(underlying_handle, str) or isinstance(options_handle, str):
            raise ValueError("INCOMPLETE_VERIFIED_DATA_HANDLE")
        object.__setattr__(self, "ticker", ticker); object.__setattr__(self, "strategy_type", strategy_type)
        object.__setattr__(self, "as_of", as_of); object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "underlying_handle", underlying_handle); object.__setattr__(self, "options_handle", options_handle)
        object.__setattr__(self, "price_basis", price_basis); object.__setattr__(self, "corporate_action_basis", corporate_action_basis)
        object.__setattr__(self, "verified_at", verified_at); object.__setattr__(self, "quote_date", quote_date)
        object.__setattr__(self, "quote_timestamp", quote_timestamp); object.__setattr__(self, "source_lineage", source_lineage)
        object.__setattr__(self, "benchmark_handles", dict(benchmark_handles or {}))
        object.__setattr__(self, "event_provenance", dict(event_provenance or {}))
        object.__setattr__(self, "input_summary", dict(input_summary or {}))
        object.__setattr__(self, "refresh_policy", str(refresh_policy))
        self.__post_init__()
    def __post_init__(self):
        required=(self.ticker,self.strategy_type,self.as_of,self.mode,
                  self.verified_at,self.price_basis,self.corporate_action_basis)
        if any(not str(x).strip() for x in required):
            raise ValueError("INCOMPLETE_VERIFIED_DATA_HANDLE")
        if not self.underlying_handle or not self.options_handle:
            raise ValueError("INCOMPLETE_VERIFIED_DATA_HANDLE")
        if not isinstance(self.underlying_handle, VerifiedDatasetHandle) or not isinstance(self.options_handle, VerifiedDatasetHandle):
            raise ValueError("INVALID_VERIFIED_DATASET_HANDLE")
        if self.refresh_policy not in {"REUSE_VERIFIED", "INCREMENTAL_IF_NEEDED"}:
            raise ValueError("UNKNOWN_REFRESH_POLICY")
        if any(not isinstance(value, VerifiedDatasetHandle) or value.verification_status != "VERIFIED"
               for value in self.benchmark_handles.values()):
            raise ValueError("INVALID_VERIFIED_BENCHMARK_HANDLE")

    @property
    def underlying_generation_id(self): return self.underlying_handle.generation_id
    @property
    def options_generation_id(self): return self.options_handle.generation_id

@dataclass(frozen=True)
class ReadinessResult:
    ticker: str; strategy_type: str; as_of: str; data_status: str; action: str
    data_reason: str|None; coverage: CoverageReport|None; stages: dict[str,str]=field(default_factory=dict)
    verified_data_handle: VerifiedDataHandle|None = None
    attempts: int=1; details: dict[str,Any]=field(default_factory=dict)
    def to_dict(self):
        d=asdict(self); d["coverage"]=self.coverage.to_dict() if self.coverage else None; return d

def validate_generation_evidence(*, promoted_generation_id: str, manifest_active_generation_id: str,
                                 read_back_generation_id: str, expected_checksum: str,
                                 read_back_checksum: str, expected_row_count: int,
                                 read_back_row_count: int) -> str | None:
    """Return the precise blocking reason for incomplete promotion evidence."""
    if not promoted_generation_id or not manifest_active_generation_id or not read_back_generation_id:
        return "GENERATION_NOT_PINNED"
    if manifest_active_generation_id != promoted_generation_id:
        return "MANIFEST_GENERATION_MISMATCH"
    if read_back_generation_id != promoted_generation_id:
        return "READ_BACK_GENERATION_MISMATCH"
    if expected_checksum != read_back_checksum or expected_row_count != read_back_row_count:
        return "READ_BACK_CHECKSUM_MISMATCH"
    return None

def ensure_strategy_ready(ticker: str, strategy_type: str, as_of: str, mode: str,
                          strategy_requirements: StrategyDataRequirements, *, data_access=None,
                          strike_range_resolver: Callable[[pd.DataFrame],tuple[float,float]]|None=None,
                          max_attempts: int = 2, require_benchmarks: bool = False,
                          refresh_policy: str = "INCREMENTAL_IF_NEEDED") -> ReadinessResult:
    s=str(ticker).strip().upper(); access=data_access or PCSDataAccess(); day=pd.Timestamp(as_of).normalize()
    stages={"REQUEST":"COMPLETE","REQUIREMENTS":"COMPLETE","DISCOVER":"RUNNING"}
    req={"required_start":str((day-pd.Timedelta(days=max(30,strategy_requirements.underlying_lookback))).date()),"required_end":str(day.date()),"decision_as_of":str(day.date()),"datasets":("daily","options"),"option_type":strategy_requirements.option_right.lower(),"min_dte":strategy_requirements.target_dte_min,"max_dte":strategy_requirements.target_dte_max,"required_fields":strategy_requirements.required_fields,"required_history_rows":strategy_requirements.underlying_lookback,"consumer":"STRATEGY_READINESS"}
    last=None
    def receipts(value):
        if isinstance(value, dict):
            if value.get("dataset_type") and (value.get("promoted_generation_id") or value.get("generation_id")):
                yield value
            if isinstance(value.get("promotion_receipt"), dict): yield from receipts(value["promotion_receipt"])
            for child in value.values(): yield from receipts(child)
        elif isinstance(value, (list, tuple)):
            for child in value: yield from receipts(child)
    for attempt in range(1,max_attempts+1):
        try:
            if refresh_policy == "REUSE_VERIFIED":
                manifest = access._read_manifest(access.manifest_path)
                active = manifest[(manifest.dataset.astype(str).isin({"daily", "options", "options_v2"})) &
                                  manifest.symbol.astype(str).str.upper().eq(s) &
                                  manifest.active_generation.notna() &
                                  manifest.active_generation.astype(str).str.strip().ne("") &
                                  manifest.active_generation.astype(str).str.lower().ne("nan")]
                if active.empty:
                    return ReadinessResult(s, strategy_type, str(day.date()),
                        DataStatus.CORRUPTED.value, "DATA_BLOCKED", "DATASET_GENERATION_ID_MISSING",
                        None, stages, None, attempt, {"refresh_policy": refresh_policy})
                reuse_receipts = []
                for _, row in active.iterrows():
                    partition = "/".join([f"year={int(row.year)}"] +
                                         ([f"quarter={int(row.quarter)}"] if pd.notna(row.get("quarter")) else []))
                    reuse_receipts.append({"dataset_type": str(row.dataset), "promoted_generation_id": str(row.active_generation),
                        "manifest_active_generation_id": str(row.active_generation), "read_back_generation_id": str(row.active_generation),
                        "checksum": str(row.content_hash), "read_back_checksum": str(row.content_hash),
                        "row_count": int(row.row_count), "read_back_row_count": int(row.row_count),
                        "partition_ids": [partition], "path": str(row.parquet_path),
                        "dataset_fingerprint": str(row.get("dataset_fingerprint", "")), "schema_version": str(row.schema_version),
                        "min_date": str(row.min_date), "max_date": str(row.max_date),
                        "price_basis": strategy_requirements.price_basis,
                        "corporate_action_version": strategy_requirements.corporate_action_basis,
                        "source_lineage": [{"source": str(row.source), "partition": partition}]})
                last = SimpleNamespace(status="READY", to_dict=lambda: {"receipts": reuse_receipts})
            else:
                last=ensure_market_data(s,req,access=access)
            if getattr(last, "status", None) in {"BLOCKED", "SOURCE_UNAVAILABLE"}:
                stages["DISCOVER"]="COMPLETE"; stages["FETCH"]="FAILED"; stages["PROMOTE"]="NOT_RUN"
                return ReadinessResult(s,strategy_type,str(day.date()),DataStatus.SOURCE_UNAVAILABLE.value,"DATA_BLOCKED","SOURCE_UNAVAILABLE",None,stages,None,attempt,{"control_plane":last.to_dict() if hasattr(last,"to_dict") else last})
            stages["DISCOVER"]="COMPLETE"; stages["FETCH"]="COMPLETE"; stages["NORMALIZE"]="COMPLETE"; stages["REPAIR/QUARANTINE"]="COMPLETE"; stages["PROMOTE"]="COMPLETE"
            if refresh_policy == "REUSE_VERIFIED":
                target_handle = resolve_active_verified_daily_handle(s, str(day.date()), strategy_requirements.underlying_lookback, data_access=access)
                daily = access.read_verified_dataset(target_handle, end_date=day)
            else:
                target_handle = resolve_active_verified_daily_handle(s, str(day.date()), strategy_requirements.underlying_lookback, data_access=access)
                daily = access.read_verified_dataset(target_handle, end_date=day,
                                                     required_warmup_rows=strategy_requirements.underlying_lookback)
            daily=daily.sort_values("date")
            if daily["date"].duplicated().any():
                raise ValueError("DUPLICATE_CANONICAL_PRICE_KEY")
            if daily.empty: raise ValueError("DAILY_DATA_MISSING")
            if refresh_policy == "REUSE_VERIFIED":
                options_handle = resolve_active_verified_options_handle(s, str(day.date()), data_access=access)
                q = access.read_verified_dataset(options_handle, end_date=day)
            else:
                options_handle = resolve_active_verified_options_handle(s, str(day.date()), data_access=access)
                q = access.read_verified_dataset(options_handle, end_date=day)
            q=q[q.symbol.astype(str).str.upper().eq(s)]
            if str(mode).upper() == "LIVE":
                live_reasons = []
                if "quote_timestamp" not in q.columns:
                    live_reasons.append("LIVE_QUOTE_TIMESTAMP_MISSING")
                else:
                    timestamps = pd.to_datetime(q["quote_timestamp"], errors="coerce", utc=True)
                    if timestamps.isna().all():
                        live_reasons.append("LIVE_QUOTE_TIMESTAMP_MISSING")
                quote_days = pd.to_datetime(q.get("trade_date"), errors="coerce").dt.normalize()
                if not quote_days.empty and not quote_days.eq(day).all():
                    live_reasons.append("QUOTE_SESSION_MISMATCH")
                if live_reasons:
                    stages["TARGET-WINDOW COVERAGE"] = "FAILED"
                    return ReadinessResult(s,strategy_type,str(day.date()),DataStatus.STALE.value,"DATA_BLOCKED",live_reasons[0],None,stages,None,attempt,{"reason_codes":live_reasons})
            q["expiration_date"]=pd.to_datetime(q.expiration_date,errors="coerce"); q["dte"]=(q.expiration_date-pd.Timestamp(daily.date.max())).dt.days
            q=q[q.call_put.astype(str).str.lower().isin({strategy_requirements.option_right.lower(),strategy_requirements.option_right[:1].lower()})]
            lo,hi=strategy_requirements.strike_range or (float(pd.to_numeric(daily.close).iloc[-1])*0.5,float(pd.to_numeric(daily.close).iloc[-1])*1.05)
            target=q[q.dte.between(strategy_requirements.target_dte_min,strategy_requirements.target_dte_max)&q.strike.between(lo,hi)]
            ex=tuple({"expiration":str(e.date()),"dte":int((e-pd.Timestamp(daily.date.max())).days),"strike_min":float(g.strike.min()),"strike_max":float(g.strike.max()),"unique_strikes":int(g.strike.nunique()),"rows":len(g)} for e,g in target.groupby("expiration_date"))
            valid=target.replace([float("inf"),float("-inf")],pd.NA).dropna(subset=["strike","bid","ask","open_interest","volume"])
            report=CoverageReport({"as_of":str(day.date()),"dte":[strategy_requirements.target_dte_min,strategy_requirements.target_dte_max],"strike":[lo,hi]}, {"trade_date":str(pd.Timestamp(daily.date.max()).date())}, ex, float(target.strike.min()) if len(target) else None,float(target.strike.max()) if len(target) else None,len(target),len(valid),len(target)-len(valid),str(pd.Timestamp(daily.date.max()).date()),tuple(last.to_dict().get("source_inventory",())) if hasattr(last,"to_dict") else (),None,DataStatus.READY.value if len(valid)>=strategy_requirements.minimum_usable_rows else DataStatus.TARGET_WINDOW_MISSING.value,() if len(valid)>=strategy_requirements.minimum_usable_rows else ("TARGET_CHAIN_MISSING",))
            handle = None
            if report.status == DataStatus.READY.value:
                payload = last.to_dict() if hasattr(last, "to_dict") else {}
                receipts_found=list(receipts(payload))
                if not receipts_found:
                    # Reuse is a valid control-plane outcome.  In that case
                    # there is no new promotion receipt; recover the active,
                    # already-validated generation evidence from the manifest.
                    manifest = access._read_manifest(access.manifest_path)
                    if not manifest.empty:
                        active = manifest[(manifest.dataset.astype(str).isin({"daily", "options", "options_v2"})) &
                                          manifest.symbol.astype(str).str.upper().eq(s) &
                                          manifest.active_generation.astype(str).str.len().gt(0)]
                        receipts_found = [{
                            "dataset_type": str(r.dataset), "ticker": s,
                            "promoted_generation_id": str(r.active_generation),
                            "manifest_active_generation_id": str(r.active_generation),
                            "read_back_generation_id": str(r.active_generation),
                            "checksum": str(r.content_hash), "read_back_checksum": str(r.content_hash),
                            "row_count": int(r.row_count), "read_back_row_count": int(r.row_count),
                            # Older manifest rows may have an empty
                            # partition_ids field even though year/quarter
                            # are present.  Reconstruct the canonical
                            # identifier from those authoritative columns;
                            # never turn NaN into a literal partition name.
                            "partition_ids": ["/".join(
                                [f"year={int(r.year)}"] +
                                ([f"quarter={int(r.quarter)}"] if pd.notna(r.get("quarter")) else [])
                            )], "path": str(r.parquet_path),
                            "source_lineage": [{"source": str(r.source), "partition": str(r.partition_ids)}]
                        } for _, r in active.iterrows()]
                if not any(str(x.get("dataset_type", "")).lower() == "daily" for x in receipts_found):
                    raise ValueError("DATASET_GENERATION_ID_MISSING")
                rows = receipts_found or payload.get("promoted_partitions", ()) or payload.get("source_inventory", ())
                gens = [str(x.get("generation_id") or x.get("promoted_generation_id") or x.get("active_generation") or "") for x in rows if isinstance(x, dict)]
                gen = next((x for x in gens if x), "")
                if not gen:
                    return ReadinessResult(s,strategy_type,str(day.date()),DataStatus.CORRUPTED.value,"DATA_BLOCKED","GENERATION_NOT_PINNED",report,stages,None,attempt,{"control_plane":payload})
                receipt=receipts_found[0] if receipts_found else {}
                by_dataset={str(x.get("dataset_type") or x.get("dataset") or "").lower(): x for x in receipts_found if isinstance(x,dict)}
                daily_receipt=by_dataset.get("daily", receipt)
                options_receipt=by_dataset.get("options", by_dataset.get("options_v2", receipt))
                evidence_reason=validate_generation_evidence(
                    promoted_generation_id=str(options_receipt.get("promoted_generation_id", options_receipt.get("generation_id", gen))),
                    manifest_active_generation_id=str(options_receipt.get("manifest_active_generation_id", "")),
                    read_back_generation_id=str(options_receipt.get("read_back_generation_id", "")),
                    expected_checksum=str(options_receipt.get("manifest_content_hash", options_receipt.get("checksum", ""))), read_back_checksum=str(options_receipt.get("read_back_checksum", "")),
                    expected_row_count=int(options_receipt.get("manifest_row_count", options_receipt.get("row_count", 0)) or 0), read_back_row_count=int(options_receipt.get("read_back_row_count", 0) or 0))
                if evidence_reason:
                    return ReadinessResult(s,strategy_type,str(day.date()),DataStatus.CORRUPTED.value,"DATA_BLOCKED",evidence_reason,report,stages,None,attempt,{"control_plane":payload})
                paths=tuple(str(x.get("parquet_path") or x.get("path") or "") for x in rows if isinstance(x,dict))
                partitions=tuple(str(x.get("partition") or x.get("promoted_partitions") or "") for x in rows if isinstance(x,dict))
                lineage=tuple(x.get("source_lineage", {"source": x.get("source")}) for x in rows if isinstance(x,dict))
                checksum = str(options_receipt.get("checksum", options_receipt.get("manifest_content_hash", "")))
                # The two datasets are deliberately represented independently.
                # A provider may promote them in one request today, but runners
                # must never infer that they are the same physical generation.
                def _sequence(item):
                    """Normalize manifest fields without iterating strings by character."""
                    if item is None or item == "":
                        return ()
                    if isinstance(item, str):
                        # Manifest/catalog serializers may emit one partition
                        # as a scalar or several partitions joined by a
                        # delimiter.  A scalar must remain one identifier.
                        return tuple(x for x in item.split(";") if x)
                    return tuple(item)

                def _ids(item, fallback):
                    values = _sequence(item) or _sequence(fallback)
                    return tuple(str(x.get("partition") or x) if isinstance(x, dict) else str(x)
                                 for x in values)
                underlying = VerifiedDatasetHandle(
                    "daily", s, str(daily_receipt.get("promoted_generation_id", daily_receipt.get("generation_id", gen))),
                    _ids(daily_receipt.get("partition_ids", daily_receipt.get("promoted_partitions", partitions)), partitions),
                    str(daily_receipt.get("checksum", "")), int(daily_receipt.get("row_count", len(daily)) or len(daily)),
                    tuple(str(daily_receipt.get("path") or p) for p in _sequence(daily_receipt.get("paths")) or paths[:1]), report.available_window,
                    tuple(daily_receipt.get("source_lineage", lineage)),
                    dataset_fingerprint=str(daily_receipt.get("dataset_fingerprint", "")),
                    schema_version=str(daily_receipt.get("schema_version", "")),
                    price_basis=str(daily_receipt.get("price_basis", strategy_requirements.price_basis)),
                    corporate_action_version=str(daily_receipt.get("corporate_action_version", strategy_requirements.corporate_action_basis)),
                    min_date=str(daily_receipt.get("min_date", report.available_window.get("min_date", ""))),
                    max_date=str(daily_receipt.get("max_date", report.available_window.get("max_date", ""))),
                    partition_count=len(_ids(daily_receipt.get("partition_ids", daily_receipt.get("promoted_partitions", partitions)), partitions)))
                options = VerifiedDatasetHandle(
                    "options", s, str(options_receipt.get("promoted_generation_id", options_receipt.get("generation_id", gen))),
                    _ids(options_receipt.get("partition_ids", options_receipt.get("promoted_partitions", partitions)), partitions),
                    checksum, int(options_receipt.get("row_count", len(target)) or len(target)),
                    tuple(str(options_receipt.get("path") or p) for p in _sequence(options_receipt.get("paths")) or paths[:1]), report.available_window,
                    tuple(options_receipt.get("source_lineage", lineage)),
                    dataset_fingerprint=str(options_receipt.get("dataset_fingerprint", "")),
                    schema_version=str(options_receipt.get("schema_version", "")),
                    price_basis=str(options_receipt.get("price_basis", strategy_requirements.price_basis)),
                    corporate_action_version=str(options_receipt.get("corporate_action_version", strategy_requirements.corporate_action_basis)),
                    min_date=str(options_receipt.get("min_date", report.available_window.get("min_date", ""))),
                    max_date=str(options_receipt.get("max_date", report.available_window.get("max_date", ""))),
                    partition_count=len(_ids(options_receipt.get("partition_ids", options_receipt.get("promoted_partitions", partitions)), partitions)))
                benchmark_handles = {}
                if require_benchmarks:
                    for benchmark in ("QQQ", "SPY", "SOXX"):
                        try:
                            benchmark_handles[benchmark] = resolve_active_verified_daily_handle(
                                benchmark, str(day.date()), strategy_requirements.underlying_lookback,
                                data_access=access)
                        except ValueError as exc:
                            return ReadinessResult(s, strategy_type, str(day.date()),
                                DataStatus.CORRUPTED.value, "DATA_BLOCKED",
                                "BENCHMARK_GENERATION_MISSING" if str(exc) == "INSUFFICIENT_FEATURE_WARMUP" else str(exc),
                                report, stages, None, attempt,
                                {"benchmark": benchmark, "detail": str(exc)})
                handle = VerifiedDataHandle(
                    s, str(strategy_type), str(day.date()), str(mode).upper(), underlying, options,
                    strategy_requirements.price_basis, strategy_requirements.corporate_action_basis,
                    pd.Timestamp.now("UTC").isoformat(), str(pd.Timestamp(daily.date.max()).date()), None,
                    benchmark_handles=benchmark_handles, refresh_policy="REUSE_VERIFIED")
            if report.status == DataStatus.READY.value and handle is None:
                return ReadinessResult(s,strategy_type,str(day.date()),DataStatus.CORRUPTED.value,"DATA_BLOCKED","VERIFIED_DATA_HANDLE_MISSING",report,stages,None,attempt,{"control_plane":last.to_dict() if hasattr(last,"to_dict") else last})
            stages["READ-BACK VERIFY"]="COMPLETE"; stages["TARGET-WINDOW COVERAGE"]="COMPLETE" if report.status==DataStatus.READY.value else "FAILED"; stages["STRATEGY"]="ALLOWED" if report.status==DataStatus.READY.value else "BLOCKED"
            return ReadinessResult(s,strategy_type,str(day.date()),report.status,"DATA_READY" if report.status==DataStatus.READY.value else "DATA_BLOCKED",None if report.status==DataStatus.READY.value else report.reason_codes[0],report,stages,handle,attempt,{"control_plane":last.to_dict() if hasattr(last,"to_dict") else last})
        except Exception as exc:
            stages["READ-BACK VERIFY"]="FAILED"; last=exc
    return ReadinessResult(s,strategy_type,str(day.date()),DataStatus.SOURCE_UNAVAILABLE.value,"DATA_BLOCKED","SOURCE_UNAVAILABLE",None,stages,None,max_attempts,{"detail":str(last)})

def resolve_active_verified_daily_handle(symbol: str, as_of: str, required_warmup_sessions: int = 200, *, data_access=None) -> VerifiedDatasetHandle:
    """Resolve one complete active daily generation without refresh or promotion."""
    access = data_access or PCSDataAccess.canonical(); s = str(symbol).strip().upper(); day = pd.Timestamp(as_of).normalize()
    manifest = access._read_manifest(access.manifest_path)
    rows = manifest[(manifest.dataset.astype(str) == "daily") &
                    (manifest.symbol.astype(str).str.upper() == s) &
                    manifest.active_generation.notna() &
                    manifest.active_generation.astype(str).str.strip().ne("") &
                    manifest.active_generation.astype(str).str.lower().ne("nan")]
    if rows.empty:
        if manifest.empty or not ((manifest.dataset.astype(str) == "daily") &
                                  (manifest.symbol.astype(str).str.upper() == s)).any():
            raise ValueError("MANIFEST_ROUTE_MISSING")
        raise ValueError("ACTIVE_GENERATION_MISSING")
    active = rows.copy()
    active["_lo"] = pd.to_datetime(active.min_date, errors="coerce")
    active["_hi"] = pd.to_datetime(active.max_date, errors="coerce")
    for left_idx, left in active.iterrows():
        for right_idx, right in active.iterrows():
            if left_idx >= right_idx or pd.isna(left["_lo"]) or pd.isna(right["_lo"]):
                continue
            if left["_lo"] <= right["_hi"] and right["_lo"] <= left["_hi"]:
                left_gid, right_gid = str(left.active_generation), str(right.active_generation)
                raise ValueError("ACTIVE_GENERATION_OVERLAP_CONFLICT")
    candidates = rows[pd.to_datetime(rows.min_date, errors="coerce").le(day)].sort_values("min_date")
    if len(candidates) == 0 or not pd.to_datetime(candidates.max_date, errors="coerce").ge(day).any():
        raise ValueError("INSUFFICIENT_FEATURE_WARMUP")
    required = ["active_generation", "content_hash", "row_count", "min_date", "max_date", "schema_version", "parquet_path", "partition_ids"]
    if any(pd.isna(row.get(k)) or not str(row.get(k)).strip() for _, row in candidates.iterrows() for k in required):
        raise ValueError("DATASET_PROVENANCE_INCOMPLETE")
    partitions = tuple(str(row.partition_ids) for _, row in candidates.iterrows())
    generations = tuple(str(row.active_generation) for _, row in candidates.iterrows())
    paths = tuple(str(row.parquet_path) for _, row in candidates.iterrows())
    frame = pd.concat([access.read_pinned_generation("daily", s, p, g) for p, g in zip(partitions, generations)], ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    pit_rows = int(frame.loc[frame["date"] <= day, "date"].nunique())
    if len(frame) != int(candidates.row_count.sum()) or frame.date.max() < day or pit_rows < int(required_warmup_sessions):
        raise ValueError("INSUFFICIENT_FEATURE_WARMUP")
    duplicate_key = ["date"] if "symbol" not in frame.columns else ["symbol", "date"]
    if frame[duplicate_key].duplicated().any(): raise ValueError("DUPLICATE_CANONICAL_PRICE_KEY")
    checksum = access.semantic_content_hash(frame)
    descriptor = __import__("pcs.data.canonical_generations", fromlist=["canonical_snapshot_descriptor"]).canonical_snapshot_descriptor(
        dataset="daily", symbol=s, frame=frame,
        file_hash=hashlib.sha256(b"".join(Path(p).read_bytes() for p in paths)).hexdigest(),
        byte_size=sum(Path(p).stat().st_size for p in paths), partition_key="|".join(partitions))
    return VerifiedDatasetHandle("daily", s, "|".join(generations), partitions, str(checksum), len(frame), paths,
        {"min_date": str(candidates.min_date.iloc[0]), "max_date": str(candidates.max_date.max())},
        dataset_fingerprint=descriptor["dataset_fingerprint"], schema_version=str(candidates.schema_version.iloc[0]),
        price_basis="canonical_adjusted", corporate_action_version="canonical_identity",
        min_date=str(candidates.min_date.iloc[0]), max_date=str(candidates.max_date.max()), partition_count=len(partitions))

def resolve_active_verified_options_handle(symbol: str, as_of: str, *, data_access=None) -> VerifiedDatasetHandle:
    """Resolve and validate the active canonical options generation for a session."""
    access = data_access or PCSDataAccess.canonical(); s = str(symbol).strip().upper(); day = pd.Timestamp(as_of).normalize()
    manifest = access._read_manifest(access.manifest_path)
    rows = manifest[(manifest.dataset.astype(str).isin({"options", "options_v2", "options_v3"})) &
                    (manifest.symbol.astype(str).str.upper() == s) &
                    manifest.active_generation.notna() &
                    manifest.active_generation.astype(str).str.strip().ne("") &
                    manifest.active_generation.astype(str).str.lower().ne("nan")]
    rows = rows[pd.to_datetime(rows.max_date, errors="coerce").ge(day)].sort_values("max_date", ascending=False)
    if rows.empty: raise ValueError("OPTIONS_GENERATION_MISSING")
    row = rows.iloc[0]
    required = ["active_generation", "content_hash", "row_count", "min_date", "max_date", "schema_version", "parquet_path"]
    if any(pd.isna(row.get(k)) or not str(row.get(k)).strip() for k in required):
        raise ValueError("DATASET_PROVENANCE_INCOMPLETE")
    partition = "/".join([f"year={int(row.year)}"] + ([f"quarter={int(row.quarter)}"] if pd.notna(row.get("quarter")) else []))
    dataset = str(row.dataset)
    frame = access.read_pinned_generation(dataset, s, partition, str(row.active_generation))
    if len(frame) != int(row.row_count): raise ValueError("READ_BACK_ROW_COUNT_MISMATCH")
    if str(access.semantic_content_hash(frame)) != str(row.content_hash): raise ValueError("DATASET_CHECKSUM_MISMATCH")
    required_columns = {"symbol", "trade_date", "expiration_date", "call_put", "strike"}
    if not required_columns.issubset(frame.columns): raise ValueError("OPTIONS_SCHEMA_INCOMPLETE")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    if frame["trade_date"].isna().any() or frame["trade_date"].max() < day: raise ValueError("OPTIONS_DATE_COVERAGE_INSUFFICIENT")
    if frame[["symbol", "trade_date", "expiration_date", "call_put", "strike"]].duplicated().any():
        raise ValueError("DUPLICATE_CANONICAL_OPTION_KEY")
    return VerifiedDatasetHandle(dataset, s, str(row.active_generation), (partition,), str(row.content_hash), int(row.row_count), (str(row.parquet_path),),
        {"min_date": str(row.min_date), "max_date": str(row.max_date)},
        ({"source": str(row.source), "partition": partition},), dataset_fingerprint=str(row.get("dataset_fingerprint", "")),
        schema_version=str(row.schema_version), price_basis="canonical_adjusted", corporate_action_version="canonical_identity",
        min_date=str(row.min_date), max_date=str(row.max_date), partition_count=1)
