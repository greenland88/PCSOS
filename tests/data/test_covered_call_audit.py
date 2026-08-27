from pcs.covered_call_research.audit import audit
import json
import pytest


def test_pltr_covered_call_audit_does_not_use_pcs_strategy_gates():
    result = audit("PLTR", earnings_path="research_outputs/pltr_covered_call_research_v1/earnings_events.csv")
    assert result.option_rows > 0
    assert result.duplicate_keys == 0
    assert result.unresolved_conflicts == 0
    assert "CORPORATE_ACTIONS_MISSING" in result.reason_codes
    assert "EARNINGS_DATES_MISSING" not in result.reason_codes


def test_train_baseline_persists_required_accounting_and_comparison_metrics():
    with open("research_outputs/pltr_covered_call_research_v1/train_baseline/baseline_v2.json", encoding="utf-8") as f:
        artifact = json.load(f)
    metrics = artifact["metrics"]
    assert artifact["split"] == "TRAIN"
    assert artifact["total_candidates"] == 43
    assert artifact["final_oos"] == "SEALED"
    assert artifact["accounting_closure"]["status"] == "PASS"
    assert {"yearly_results", "buy_and_hold_pnl_proxy", "max_drawdown",
            "source_gaps", "non_executable_closes_rolls", "assignment_exposures"}.issubset(metrics)
    expected = metrics["premium_received"] - metrics["buyback_cost"] - metrics["realized_pnl"] - metrics["expiration_assignment_settlement"]
    assert metrics["accounting_residual"] == pytest.approx(expected, abs=1e-8)
