from pcs.models.trade import TradeCandidate


class StrikeScorer:
    def __init__(self, rules: dict):
        self.rules = rules["entry"]

    def score(self, c: TradeCandidate) -> tuple[float, list[str]]:
        flags = []
        buffer = c.underlying_price - c.short_strike
        move = c.expected_move_1d if c.expected_move_1d is not None else c.normal_daily_move
        if move is None or move <= 0:
            return 0, ["expected 1-day move unavailable"]
        required = self.rules["min_buffer_days"] * move
        target = self.rules["target_buffer_days"] * move
        if buffer < required:
            return 0, ["insufficient 3-5 day strike buffer"]
        buffer_score = min(100, 60 + (buffer - required) / max(target - required, 1) * 40)
        if c.short_strike > c.support_level:
            buffer_score -= 20
            flags.append("short strike above support")
        if not (self.rules["preferred_dte_min"] <= c.dte <= self.rules["preferred_dte_max"]):
            buffer_score -= 10
            flags.append("DTE outside preferred range")
        return max(0, min(100, buffer_score)), flags

