from pcs.models.decision import Action
from pcs.models.position import PCSPosition


class RollEngine:
    def __init__(self, rules: dict):
        self.rules = rules["management"]

    def evaluate(self, p: PCSPosition) -> tuple[Action, str, dict | None]:
        if not p.thesis_valid or not p.structure_valid:
            return Action.CLOSE, "thesis or structure is broken", None
        if p.dte <= self.rules["min_dte_to_roll"]:
            return Action.CLOSE, "too little time remains to roll safely", None
        under_pressure = p.underlying_price <= p.short_strike * (1 + self.rules["roll_watch_buffer_pct"] / 100)
        roll = p.candidate_roll
        if under_pressure and p.decline_temporary and roll and self._valid_roll(p, roll):
            return Action.ROLL, "temporary decline with intact structure and viable roll", roll
        return Action.HOLD, "structure remains valid", None

    @staticmethod
    def _valid_roll(p: PCSPosition, roll: dict) -> bool:
        try:
            if float(roll["net_credit"]) < 0:
                return False
            if "short_strike" in roll and "long_strike" in roll and float(roll["short_strike"]) <= float(roll["long_strike"]):
                return False
            if "dte" in roll and int(roll["dte"]) <= 0:
                return False
            if "max_risk" in roll and float(roll["max_risk"]) < 0:
                return False
            if "liquidity_score" in roll and float(roll["liquidity_score"]) < 45:
                return False
            return True
        except (KeyError, TypeError, ValueError):
            return False

