from pcs.models.decision import ScoreBreakdown, SizeClass
from pcs.models.market import Regime


class OpportunityScorer:
    def __init__(self, rules: dict):
        self.rules = rules
        self.weights = rules["scoring"]["weights"]

    def score(self, breakdown: ScoreBreakdown, regime: Regime) -> tuple[float, SizeClass]:
        total = sum(getattr(breakdown, k) * w for k, w in self.weights.items())
        if regime == Regime.RED:
            return total, SizeClass.HALF
        if total >= self.rules["scoring"].get("top_quality_threshold", 92) and regime == Regime.GREEN:
            return total, SizeClass.TWO
        if total >= self.rules["scoring"]["highest_quality_threshold"]:
            return total, SizeClass.ONE_HALF
        if total >= self.rules["scoring"]["open_threshold"]:
            return total, SizeClass.ONE
        return total, SizeClass.HALF

