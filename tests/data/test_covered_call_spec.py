from pathlib import Path

from pcs.covered_call_research.spec import load_spec


def test_pltr_covered_call_spec_is_frozen_and_seals_final_oos():
    spec = load_spec(Path("config/covered_call/pltr_covered_call_research.yaml"))
    assert spec.symbol == "PLTR"
    assert spec.status == "FROZEN"
    assert spec.final_oos_access is False
    assert spec.execution["entry_price"] == "bid"
    assert spec.execution["buyback_price"] == "ask"
    assert spec.execution["year_end_forced_close"] is False
    assert len(spec.spec_hash) == 64
