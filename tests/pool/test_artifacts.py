import json
from pathlib import Path
from dataclasses import asdict

import pandas as pd

from pcs.pool.artifacts import persist_pool_artifacts
from pcs.pool.models import (EligibilityStatus, PoolRunSnapshot, PoolScanResult,
                             OptionsStatus, TickerScanResult, TimingStatus, FinalAction)
from pcs.pool.options import SpreadCandidate
from pcs.pool.ai_evidence import read_ai_evidence, upgrade_current_pool_artifacts


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
    assert (root / "preparation_recovery.json").exists()
    assert manifest["stage_status"]["OPTIONS_SHORTLIST"] == "NOT_RUN"
    assert not list(root.glob("*.tmp"))


def test_ai_evidence_has_compact_pool_view_and_on_demand_detail(tmp_path: Path):
    snap = PoolRunSnapshot("ai-run", "2025-01-01", "EOD", "2024-12-31", "u1")
    selected = TickerScanResult(
        "AAA", "ai-run", snap.as_of, EligibilityStatus.PCS_ELIGIBLE,
        timing_status=TimingStatus.TIMING_ENTRY_READY, options_status=OptionsStatus.PASS,
        final_action=FinalAction.PCS_TRADE_READY, reason_codes=("timing_pass",),
        candidate_state={"close": 101, "atr": 2, "price_indicator_series":
                         [{"date": "2025-01-01", "close": 101, "atr14": 2}],
                         "daily_identity": ["daily", "AAA"],
                         "code_identity": "code-v1", "rules_identity": "rules-v1"})
    rejected = TickerScanResult(
        "BBB", "ai-run", snap.as_of, EligibilityStatus.PCS_ELIGIBLE,
        timing_status=TimingStatus.WAIT, final_action=FinalAction.REJECTED,
        reason_codes=("UNDERLYING_STRUCTURAL_REJECT",),
        candidate_state={"timing_reason_codes": ["UNDERLYING_STRUCTURAL_REJECT"]})
    blocked = TickerScanResult(
        "CCC", "ai-run", snap.as_of, EligibilityStatus.DATA_BLOCKED,
        final_action=FinalAction.DATA_FAILED, reason_codes=("DATASET_CHECKSUM_MISMATCH",))
    root = persist_pool_artifacts(PoolScanResult(snap, (selected, rejected, blocked), {}), tmp_path)
    manifest = json.loads((root / "run_manifest.json").read_text())
    summary = json.loads((root / "full_pool_summary.json").read_text())
    assert [row["symbol"] for row in summary] == ["AAA", "BBB", "CCC"]
    assert manifest["ai_evidence"]["index"] == "ai_evidence_index.json"
    assert read_ai_evidence(root, "AAA")["system_verdict"]["final_action"] == "PCS_TRADE_READY"
    assert read_ai_evidence(root, "AAA")["price_and_indicators"]["sequence"]["status"] == "PROVIDED"
    assert read_ai_evidence(root, "BBB")["outcome_class"] == "STRATEGY_REJECTED"
    assert read_ai_evidence(root, "CCC")["outcome_class"] == "DATA_BLOCKED"
    assert read_ai_evidence(root, "CCC")["price_and_indicators"]["sequence"]["status"] == "UNKNOWN"


def test_ai_evidence_upgrade_reuses_hash_valid_legacy_artifact(tmp_path: Path):
    snap = PoolRunSnapshot("legacy", "2025-01-01", "EOD", "2024-12-31", "u1")
    row = TickerScanResult("AAA", "legacy", snap.as_of, EligibilityStatus.PCS_ELIGIBLE)
    root = persist_pool_artifacts(PoolScanResult(snap, (row,), {}), tmp_path)
    for name in ("full_pool_summary.json", "focus_index.json", "ai_evidence_index.json", "ai_evidence_packets.jsonl"):
        (root / name).unlink()
    manifest = json.loads((root / "run_manifest.json").read_text())
    for name in list(manifest["artifact_hashes"]):
        if name.startswith("ai_evidence") or name in {"full_pool_summary.json", "focus_index.json"}:
            manifest["artifact_hashes"].pop(name)
    manifest.pop("ai_evidence", None)
    root.joinpath("run_manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
    upgrade_current_pool_artifacts(root)
    assert read_ai_evidence(root, "AAA")["price_and_indicators"]["sequence"]["status"] == "UNKNOWN"


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


def test_artifacts_generate_identity_checked_reconciliation(tmp_path: Path):
    first_snap = PoolRunSnapshot("run-first", "2025-01-01", "EOD", "2024-12-31", "u1",
                                 effective_daily_session="2024-12-31")
    first_row = TickerScanResult("AAA", "run-first", first_snap.as_of,
                                 EligibilityStatus.DATA_BLOCKED,
                                 initial_daily_readiness="PREP_REQUIRED")
    persist_pool_artifacts(PoolScanResult(first_snap, (first_row,), {}), tmp_path)

    second_snap = PoolRunSnapshot("run-second", "2025-01-02", "EOD", "2025-01-02", "u1",
                                  effective_daily_session="2025-01-02")
    second_row = TickerScanResult("AAA", "run-second", second_snap.as_of,
                                  EligibilityStatus.PCS_ELIGIBLE,
                                  initial_daily_readiness="READY")
    root = persist_pool_artifacts(PoolScanResult(second_snap, (second_row,), {}), tmp_path)
    reconciliation = json.loads((root / "reconciliation.json").read_text())
    assert reconciliation["status"] == "COMPARED"
    assert reconciliation["previous_run_id"] == "run-first"
    assert reconciliation["comparable"] is False
    assert reconciliation["daily_ready_added"] == ["AAA"]


def test_artifacts_ignore_tampered_history_and_accept_explicit_baseline(tmp_path: Path):
    snap = PoolRunSnapshot("run-first", "2025-01-01", "EOD", "2024-12-31", "u1",
                           effective_daily_session="2024-12-31")
    row = TickerScanResult("AAA", "run-first", snap.as_of, EligibilityStatus.DATA_BLOCKED)
    root = persist_pool_artifacts(PoolScanResult(snap, (row,), {}), tmp_path)
    (root / "daily_timing.json").write_text("[]", encoding="utf-8")
    snap2 = PoolRunSnapshot("run-second", "2025-01-02", "EOD", "2025-01-02", "u1",
                            effective_daily_session="2025-01-02")
    row2 = TickerScanResult("AAA", "run-second", snap2.as_of, EligibilityStatus.PCS_ELIGIBLE)
    out = persist_pool_artifacts(PoolScanResult(snap2, (row2,), {}), tmp_path,
                                 baseline_run_id="run-first")
    assert json.loads((out / "reconciliation.json").read_text())["status"] == "BASELINE_NOT_FOUND"
