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
        if under_pressure and p.decline_temporary and p.candidate_roll:
            return Action.ROLL, "temporary decline with intact structure and viable roll", p.candidate_roll
        return Action.HOLD, "structure remains valid", None

