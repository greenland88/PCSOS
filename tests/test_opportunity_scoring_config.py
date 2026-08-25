from pcs.engine.decision_engine import load_rules
from pcs.models.decision import ScoreBreakdown, SizeClass
from pcs.models.market import Regime
from pcs.scoring.opportunity_score import OpportunityScorer


def test_top_quality_threshold_is_configured_and_used():
    rules = load_rules()
    assert rules["scoring"]["top_quality_threshold"] == 92
    breakdown = ScoreBreakdown(**{field: 100 for field in ScoreBreakdown.model_fields})
    assert OpportunityScorer(rules).score(breakdown, Regime.GREEN)[1] == SizeClass.TWO
