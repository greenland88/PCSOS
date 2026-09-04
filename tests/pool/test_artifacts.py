import json
from pathlib import Path
from dataclasses import asdict

import pandas as pd

from pcs.pool.artifacts import persist_pool_artifacts
from pcs.pool.models import (EligibilityStatus, PoolRunSnapshot, PoolScanResult,
                             OptionsStatus, TickerScanResult)
from pcs.pool.options import SpreadCandidate


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
    assert (root / "universe_snapshot.parquet").exists()
    assert (root / "options_shortlist.parquet").exists()
    assert (root / "final_decisions.parquet").exists()
    assert manifest["stage_status"]["OPTIONS_SHORTLIST"] == "NOT_RUN"
    assert not list(root.glob("*.tmp"))


def test_discovered_contracts_are_persisted_without_reconstruction(tmp_path: Path):
    snap = PoolRunSnapshot("run2", "2025-01-01", "EOD", "2024-12-31", "u1")
    candidate = SpreadCandidate(
        symbol="AAA", entry_date="2025-01-01", expiration="2025-02-01",
        short_strike=100, long_strike=95, width=5, short_distance_atr=2,
        bid_credit=1, mid_credit=1.1, credit_efficiency=.2,
        short_delta_diagnostic=-.2, open_interest=500, volume=100,
        bid_ask_spread=.1, quote_as_of="2025-01-01", dte=31,
        short_bid_ask_pct=.05, nearby_strike_count=4, later_expiration_count=4,
        reference_flags=("DTE_PREFERRED",))
    row = TickerScanResult("AAA", "run2", snap.as_of, EligibilityStatus.PCS_ELIGIBLE,
                           options_status=OptionsStatus.DISCOVERED,
                           discovered_contracts=(asdict(candidate),), spread_count=1)
    result = PoolScanResult(snap, (row,), {"raw_count": 1, "options_check_count": 1,
                                          "spread_count": 1},
                            discovered_contracts=(asdict(candidate),))
    root = persist_pool_artifacts(result, tmp_path)
    options = pd.read_parquet(root / "options_shortlist.parquet")
    manifest = json.loads((root / "run_manifest.json").read_text())
    assert len(options) == 1
    assert options.iloc[0]["entry_date"] == "2025-01-01"
    assert options.iloc[0]["reference_flags"] == ("DTE_PREFERRED",)
    assert len(options) == result.summary["spread_count"]
    assert manifest["stage_status"]["OPTIONS_SHORTLIST"] == "COMPLETE"
    report = (root / "human_report.md").read_text()
    assert "DISCOVERED_SPREAD_COUNT: **1**" in report
    assert "Options, event, and portfolio stages are not implemented" not in report


def test_options_evaluated_count_counts_tickers_not_boolean(tmp_path: Path):
    snap = PoolRunSnapshot("run3", "2025-01-01", "EOD", "2024-12-31", "u1")
    rows = (
        TickerScanResult("AAA", "run3", snap.as_of, EligibilityStatus.PCS_ELIGIBLE,
                         options_status=OptionsStatus.DISCOVERED),
        TickerScanResult("BBB", "run3", snap.as_of, EligibilityStatus.PCS_ELIGIBLE,
                         options_status=OptionsStatus.REJECT),
        TickerScanResult("CCC", "run3", snap.as_of, EligibilityStatus.PCS_ELIGIBLE),
    )
    root = persist_pool_artifacts(PoolScanResult(snap, rows, {}), tmp_path)
    report = (root / "human_report.md").read_text()
    manifest = json.loads((root / "run_manifest.json").read_text())
    assert "OPTIONS_EVALUATED_COUNT: **2**" in report
    assert manifest["stage_status"]["OPTIONS_SHORTLIST"] == "COMPLETE"
