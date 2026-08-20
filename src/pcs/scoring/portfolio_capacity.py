from pcs.models.trade import TradeCandidate


class PortfolioCapacityScorer:
    def __init__(self, rules: dict):
        self.rules = rules["portfolio"]

    def score(self, c: TradeCandidate, portfolio: dict) -> tuple[float, list[str]]:
        flags = []
        planned = portfolio.get("planned_risk", 0)
        bucket_risk = portfolio.get("bucket_risk", {}).get(c.correlation_bucket, 0)
        score = 100.0
        if planned >= self.rules["max_planned_risk"]:
            return 0, ["HIGH EXPOSURE: planned PCS risk exceeds limit"]
        if planned >= self.rules["max_planned_risk"] * 0.8:
            score -= 30; flags.append("high total planned risk")
        if bucket_risk >= self.rules["max_bucket_risk"]:
            score -= 40; flags.append("correlation bucket capacity tight")
        return max(0, score), flags

