import json
from pathlib import Path

from pcs.pool.artifacts import persist_pool_artifacts
from pcs.pool.models import (EligibilityStatus, PoolRunSnapshot, PoolScanResult,
                             TickerScanResult)


def test_artifacts_are_manifested_and_atomic(tmp_path: Path):
    snap = PoolRunSnapshot("run1", "2025-01-01", "EOD", "2024-12-31", "u1")
    row = TickerScanResult("AAA", "run1", snap.as_of, EligibilityStatus.PCS_ELIGIBLE)
    root = persist_pool_artifacts(PoolScanResult(snap, (row,), {"raw_count": 1}), tmp_path)
    manifest = json.loads((root / "run_manifest.json").read_text())
    assert manifest["current"] is True
    assert manifest["artifact_hashes"]["daily_timing.json"]
    assert len((root / "state_transitions.jsonl").read_text().splitlines()) == 1
    assert (root / "human_report.md").exists()
    assert (root / "static_eligibility.parquet").exists()
    assert (root / "daily_timing.parquet").exists()
    assert (root / "options_shortlist.parquet").exists()
    assert (root / "final_decisions.parquet").exists()
    assert manifest["stage_status"]["OPTIONS_SHORTLIST"] == "NOT_RUN"
    assert not list(root.glob("*.tmp"))
