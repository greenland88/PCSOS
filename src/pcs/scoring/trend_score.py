from pcs.models.trade import TradeCandidate


def score_trend(c: TradeCandidate) -> float:
    return max(0, min(100, (c.trend_score * 0.7) + (c.price_confirmation * 0.3)))

