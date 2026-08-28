from pcs.models.decision import SizeClass
from pcs.models.trade import TradeCandidate


class PositionSizer:
    def __init__(self, rules: dict):
        self.rules = rules

    def size(self, c: TradeCandidate, size_class: SizeClass, portfolio) -> tuple[int, float, float, list[str]]:
        flags = []
        multiplier = {SizeClass.HALF: 0.5, SizeClass.ONE: 1, SizeClass.ONE_HALF: 1.5, SizeClass.TWO: 2}[size_class]
        planned_target = min(1500 * multiplier, self.rules["capital"]["single_ticker"]["conviction_ceiling"])
        current_planned = getattr(portfolio, "planned_loss", portfolio.get("planned_risk", 0) if isinstance(portfolio, dict) else 0)
        portfolio_limit = float(self.rules["portfolio"]["max_planned_risk"])
        remaining_capacity = max(0.0, portfolio_limit - float(current_planned))
        if current_planned >= self.rules["capital"]["high_exposure_threshold"]:
            planned_target = 0
            flags.append("HIGH EXPOSURE restricts new positions")
        width = c.short_strike - c.long_strike
        theoretical_per_contract = max(0, (width - c.credit) * 100)
        multiple = self.rules["entry"].get("planned_loss_multiple")
        planned_per_contract = min(theoretical_per_contract, c.credit * float(multiple) * 100) if multiple is not None else theoretical_per_contract
        # Capacity is a hard upper bound, not merely a score.  This preserves
        # the existing sizing policy while preventing a new order from
        # crossing the portfolio planned-loss limit.
        planned_target = min(planned_target, remaining_capacity)
        if remaining_capacity <= 0 and planned_target:
            flags.append("PORTFOLIO CAPACITY exhausted")
        contracts = int(planned_target // planned_per_contract) if planned_per_contract else 0
        planned_risk = contracts * planned_per_contract
        theoretical = contracts * theoretical_per_contract
        if planned_risk > self.rules["capital"]["single_ticker"]["conviction_ceiling"]:
            flags.append("single ticker caution")
        return contracts, planned_risk, theoretical, flags
