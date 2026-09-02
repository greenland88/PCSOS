"""Atomic, auditable U1 pool artifacts."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import json

from .models import PoolScanResult


def _write_atomic(path: Path, payload: str) -> str:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    digest = sha256(payload.encode("utf-8")).hexdigest()
    temporary.replace(path)
    return digest


def persist_pool_artifacts(result: PoolScanResult, output_directory: str | Path) -> Path:
    """Write the U1 snapshot/results and manifest; later stages are explicit."""
    root = Path(output_directory) / result.snapshot.run_id
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    snapshot = json.dumps(asdict(result.snapshot), default=str, sort_keys=True, indent=2)
    files["universe_snapshot.json"] = _write_atomic(root / "universe_snapshot.json", snapshot)
    rows = json.dumps([asdict(row) for row in result.ticker_results], default=str, sort_keys=True, indent=2)
    files["static_eligibility.json"] = _write_atomic(root / "static_eligibility.json", rows)
    files["daily_timing.json"] = _write_atomic(root / "daily_timing.json", rows)
    summary = json.dumps(dict(result.summary), sort_keys=True, indent=2)
    files["aggregate_summary.json"] = _write_atomic(root / "aggregate_summary.json", summary)
    manifest = {
        "current": True, "run_id": result.snapshot.run_id, "as_of": result.snapshot.as_of,
        "mode": result.snapshot.mode, "universe_snapshot_id": result.snapshot.universe_snapshot_id,
        "stage_status": {"RAW_UNIVERSE": "COMPLETE", "STATIC_ELIGIBILITY": "COMPLETE",
                          "DAILY_TIMING": "COMPLETE", "OPTIONS_SHORTLIST": "NOT_IMPLEMENTED",
                          "EVENT_GATE": "NOT_IMPLEMENTED", "PORTFOLIO_GATE": "NOT_IMPLEMENTED"},
        "input_symbol_count": len(result.ticker_results), "summary": dict(result.summary),
        "counters": dict(result.counters), "artifact_hashes": files,
    }
    _write_atomic(root / "run_manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
    return root
