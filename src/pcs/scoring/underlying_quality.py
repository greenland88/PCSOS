from pcs.models.trade import TradeCandidate


def score_underlying_quality(c: TradeCandidate) -> float:
    event_penalty = c.event_risk * 12
    return max(0, min(100, c.business_quality - event_penalty))

