"""Versioned persistence for reusable derived features and research results."""
from datetime import datetime, timezone
from pathlib import Path
import os
import uuid
import json
import pandas as pd
from .access import PCSDataAccess

DERIVED_SCHEMAS = {
    "daily_indicators": {"key": ["symbol", "date"], "version_fields": ["calculation_version", "source_data_version", "created_at"]},
    "trend_history": {"key": ["symbol", "date", "benchmark_symbol"], "version_fields": ["trend_engine_version", "config_version", "source_data_version", "created_at"]},
    "option_features": {"key": ["symbol", "trade_date", "expiration_date", "strike", "call_put"], "version_fields": ["calculation_version", "source_data_version", "created_at"]},
    "market_features": {"key": ["symbol", "date"], "version_fields": ["calculation_version", "source_data_version", "created_at"]},
}

def _access(root):
    root=Path(root)
    return PCSDataAccess(manifest_path=root / ".storage_manifest.csv", parquet_root=root.parent)

def _stamp(df, metadata):
    out=df.copy()
    for key,value in metadata.items(): out[key]=value
    out["created_at"]=metadata.get("created_at", datetime.now(timezone.utc).isoformat())
    return out

def write_derived(df, dataset, root="data/parquet/derived", metadata=None):
    if dataset not in DERIVED_SCHEMAS: raise ValueError(f"unknown derived dataset: {dataset}")
    metadata=metadata or {}; out=_stamp(df,metadata); name=f"{dataset}_{metadata.get('symbol','multi')}_{metadata.get('calculation_version',metadata.get('trend_engine_version','v1'))}.parquet"
    return _access(root).write_artifact(out, dataset, name, root=Path(root))

def read_derived(dataset, root="data/parquet/derived", filters=None):
    paths=sorted((Path(root)/dataset).glob("*.parquet"));
    if not paths:return pd.DataFrame()
    access=_access(root)
    out=pd.concat([access.read_artifact(dataset,p.name,root=Path(root)) for p in paths],ignore_index=True)
    for key,value in (filters or {}).items(): out=out[out[key]==value]
    return out.reset_index(drop=True)

def cache_matches(dataset, filters, versions, root="data/parquet/derived"):
    out=read_derived(dataset,root,filters)
    if out.empty:
        return False
    for key, value in versions.items():
        if key not in out.columns:
            return False
        if not out[key].eq(value).all():
            return False
    return True

def write_research_run(record, path="data/manifests/research_runs.csv"):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    run_id = str(record["run_id"])
    with PCSDataAccess._file_lock(target):
        current = pd.read_csv(target) if target.exists() else pd.DataFrame()
        incoming = pd.DataFrame([record])
        if "run_id" in current.columns:
            current = current[current.run_id.astype(str) != run_id]
        columns = list(dict.fromkeys([*current.columns.tolist(), *incoming.columns.tolist()]))
        updated = pd.concat([current.reindex(columns=columns), incoming.reindex(columns=columns)], ignore_index=True)
        csv_tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            updated.to_csv(csv_tmp, index=False)
            os.replace(csv_tmp, target)
        finally:
            csv_tmp.unlink(missing_ok=True)
        json_dir = target.parent / "research_runs"
        json_dir.mkdir(parents=True, exist_ok=True)
        json_target = json_dir / f"{run_id}.json"
        json_tmp = json_target.with_name(f".{json_target.name}.{uuid.uuid4().hex}.tmp")
        try:
            json_tmp.write_text(json.dumps(record, default=str, sort_keys=True), encoding="utf-8")
            os.replace(json_tmp, json_target)
        finally:
            json_tmp.unlink(missing_ok=True)
    return run_id

def write_backtest_trades(trades, run_id, root="data/parquet/research"):
    rows=[]
    for trade in trades:
        row={k:v for k,v in trade.items() if k!="events"}; row["run_id"]=run_id
        events=trade.get("events",{}); row.update({f"{k}_date":v for k,v in events.items()}); rows.append(row)
    return _access(root).write_artifact(pd.DataFrame(rows), "pcs_backtest_trades", f"run_id={run_id}/trades.parquet", root=Path(root))

def read_backtest_trades(run_id, root="data/parquet/research"):
    path=Path(root)/"pcs_backtest_trades"/f"run_id={run_id}"/"trades.parquet"
    if not path.exists(): return pd.DataFrame()
    return _access(root).read_artifact("pcs_backtest_trades", f"run_id={run_id}/trades.parquet", root=Path(root))
