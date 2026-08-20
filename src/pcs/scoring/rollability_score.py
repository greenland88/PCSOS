from pcs.data.schemas import RollCandidate


class RollabilityScorer:
    def __init__(self, min_liquidity: float = 50):
        self.min_liquidity = min_liquidity

    def score_candidates(self, current_short: float, current_long: float, candidates: list[dict]) -> list[RollCandidate]:
        width = current_short - current_long
        scored = []
        for c in candidates:
            same_width = abs((c["short_strike"] - c["long_strike"]) - width) < 0.01
            strike_improvement = current_short - c["short_strike"]
            liquidity = float(c.get("liquidity_score", 0))
            rollability = liquidity
            if same_width:
                rollability += 10
            if strike_improvement > 0:
                rollability += min(20, strike_improvement * 2)
            if c.get("net_credit_estimate", 0) is not None and c.get("net_credit_estimate", 0) >= 0:
                rollability += 10
            scored.append(RollCandidate(
                current_spread=f"{current_short}/{current_long}",
                candidate_spread=f"{c['short_strike']}/{c['long_strike']}",
                expiration=c["expiration"],
                net_credit_estimate=c.get("net_credit_estimate"),
                days_added=int(c.get("days_added", 0)),
                strike_improvement=strike_improvement,
                buffer_improvement=c.get("buffer_improvement"),
                liquidity_score=liquidity,
                rollability_score=max(0, min(100, rollability)),
            ))
        return sorted(scored, key=lambda r: r.rollability_score, reverse=True)
