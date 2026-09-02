"""Generic inspection and recovery operations for immutable generations."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import hashlib, json, os, uuid
from datetime import datetime, timezone
import pandas as pd
from .access import PCSDataAccess, DataAccessError, DataQualityError

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

def register_active_generation_provenance(*, dataset: str, symbol: str, generation_id: str,
                                          price_basis: str, corporate_action_version: str,
                                          data_access: PCSDataAccess | None = None) -> dict[str, Any]:
    """Explicit ADMIN operation to register identity for an existing generation.

    This never discovers or downloads data. It verifies the precisely named
    active object and records only its independent provenance fingerprints.
    """
    access = data_access or PCSDataAccess(); s = str(symbol).strip().upper()
    manifest_before = access.manifest_path.read_bytes() if access.manifest_path.exists() else b""
    before_hash = hashlib.sha256(manifest_before).hexdigest()
    with access._file_lock(access.manifest_path):
        current = access._read_manifest(access.manifest_path)
        if hashlib.sha256(access.manifest_path.read_bytes()).hexdigest() != before_hash:
            raise DataAccessError("PROVENANCE_PLAN_STALE")
        matches = current[(current.dataset.astype(str) == str(dataset)) & current.symbol.astype(str).str.upper().eq(s) &
                          current.active_generation.astype(str).eq(str(generation_id))]
        if len(matches) != 1: raise DataAccessError("ACTIVE_GENERATION_NOT_UNIQUE")
        row = matches.iloc[0]; path = Path(_strict_text(row.parquet_path, "ACTIVE_GENERATION_PATH_MISSING"))
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
        current.to_csv(tmp, index=False)
        os.replace(tmp, access.manifest_path)
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
