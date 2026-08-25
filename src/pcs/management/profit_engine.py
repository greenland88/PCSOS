from pcs.models.decision import Action
from pcs.models.position import PCSPosition


class ProfitEngine:
    def __init__(self, rules: dict):
        self.rules = rules["management"]

    def evaluate(self, p: PCSPosition) -> tuple[Action, str]:
        capture = p.profit_capture_pct
        if capture >= self.rules["early_profit_capture_pct"] and p.structure_valid and p.thesis_valid and p.rollability_score >= 70:
            return Action.HOLD, "early-profit patience: healthy structure, buffer, and rollability"
        if capture >= self.rules["high_profit_capture_pct"] and p.dte < 14:
            return Action.CLOSE, "remaining reward is small relative to tail risk"
        return Action.HOLD, "theta can continue to work"

