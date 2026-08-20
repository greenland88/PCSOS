from pcs.models.trade import TradeCandidate


def score_support(c: TradeCandidate) -> float:
    distance = c.underlying_price - c.support_level
    penalty = 10 if distance < 0 else 0
    return max(0, min(100, c.support_score - penalty))

