from pcs.models.market import MarketState, Regime


class MarketRegimeEngine:
    def __init__(self, rules: dict):
        self.rules = rules["regime"]

    def classify(self, state: MarketState) -> tuple[Regime, float, list[str]]:
        flags = []
        # VIX is a required market-risk input for a trustworthy regime decision.
        # Treating an absent value as "no warning" would allow incomplete state
        # to reach the GREEN path.
        if state.vix is None:
            return Regime.RED, 0, ["VIX_UNAVAILABLE"]
        if state.sharp_selloff or state.recent_drawdown_pct >= self.rules["sharp_selloff_drawdown_pct"]:
            flags.append("sharp selloff")
        if state.vix >= self.rules["red_vix"]:
            flags.append("VIX red")
        if flags:
            return Regime.RED, 0, flags

        checks = [
            state.qqq_above_20dma, state.qqq_above_50dma, state.qqq_above_200dma,
            state.spy_above_50dma, state.soxx_above_50dma, state.breadth_positive,
        ]
        score = sum(1 for c in checks if c) / len(checks) * 100
        if state.vix >= self.rules["yellow_vix"]:
            score -= 15
            flags.append("elevated VIX")
        if score >= self.rules["green_min_score"]:
            return Regime.GREEN, score, flags
        if score >= self.rules["yellow_min_score"]:
            return Regime.YELLOW, score, flags
        return Regime.RED, score, flags

