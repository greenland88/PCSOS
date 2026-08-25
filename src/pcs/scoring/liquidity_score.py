from pcs.models.trade import TradeCandidate


class LiquidityScorer:
    def __init__(self, rules: dict):
        self.rules = rules["liquidity"]

    def score(self, c: TradeCandidate) -> tuple[float, float, list[str]]:
        score = 100.0
        flags = []
        if c.option_volume < self.rules["min_option_volume"]:
            score -= 25; flags.append("low option volume")
        if c.open_interest < self.rules["min_open_interest"]:
            score -= 25; flags.append("low open interest")
        if c.bid_ask_pct > self.rules["max_bid_ask_pct"]:
            score -= 25; flags.append("wide bid/ask")
        if c.nearby_strikes < self.rules["min_nearby_strikes"]:
            score -= 15; flags.append("sparse nearby strikes")
        if c.later_expirations < self.rules["min_later_expirations"]:
            score -= 15; flags.append("limited later expirations")
        rollability = max(0, min(100, score + min(c.later_expirations, 8) * 2))
        return max(0, score), rollability, flags
