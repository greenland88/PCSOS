from pcs.engine.decision_engine import load_rules
from pcs.models.decision import SizeClass
from pcs.risk.position_sizing import PositionSizer
from tests.test_hard_rules import candidate


def test_sizer_rejects_non_credit_spreads_even_without_gate():
    result = PositionSizer(load_rules()).size(candidate(credit=-0.1), SizeClass.ONE,
                                              {"planned_risk": 0, "bucket_risk": {}})
    assert result[:3] == (0, 0.0, 0.0)
    assert "INVALID_CREDIT_OR_SPREAD_WIDTH" in result[3]
