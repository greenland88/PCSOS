import yaml
from pathlib import Path
from pcs.management.profit_engine import ProfitEngine
from pcs.management.roll_engine import RollEngine
from pcs.models.decision import Action, Decision, ScoreBreakdown, SizeClass
from pcs.models.market import Regime
from pcs.regime.market_regime import MarketRegimeEngine
from pcs.risk.position_sizing import PositionSizer
from pcs.risk.portfolio_risk import PortfolioRiskAggregator, PortfolioRiskSnapshot
from pcs.entry.gates import HardGatePipeline, GateStatus, build_production_entry_context
from pcs.scoring.liquidity_score import LiquidityScorer
from pcs.scoring.opportunity_score import OpportunityScorer
from pcs.scoring.portfolio_capacity import PortfolioCapacityScorer
from pcs.scoring.strike_score import StrikeScorer
from pcs.scoring.support_score import score_support
from pcs.scoring.trend_score import score_trend
from pcs.scoring.underlying_quality import score_underlying_quality

_REPO_ROOT = Path(__file__).resolve().parents[3]

def load_rules(path: str | Path = "config/pcs_rules.yaml") -> dict:
    if str(path).replace("\\", "/") == "config/pcs_rules.yaml":
        path = _REPO_ROOT / path
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class DecisionEngine:
    def __init__(self, rules: dict, *, trading_sessions=None):
        self.rules = rules
        self.regime = MarketRegimeEngine(rules)
        self.liquidity = LiquidityScorer(rules)
        self.strike = StrikeScorer(rules)
        self.capacity = PortfolioCapacityScorer(rules)
        self.opportunity = OpportunityScorer(rules)
        self.sizer = PositionSizer(rules)
        self.gates = HardGatePipeline(rules, trading_sessions=trading_sessions)
        self.risk = PortfolioRiskAggregator()
        self.rolls = RollEngine(rules)
        self.profits = ProfitEngine(rules)

    def evaluate_candidate(self, c, market_state, portfolio, event_calendar=None, entry_context=None) -> Decision:
        regime, regime_score, regime_flags = self.regime.classify(market_state)
        risk_snapshot = portfolio if isinstance(portfolio, PortfolioRiskSnapshot) else self.risk.from_portfolio(portfolio)
        built_context = entry_context if entry_context is not None else build_production_entry_context(c)
        if built_context is not None and not hasattr(built_context, "entry_context_state"):
            raise ValueError("INVALID_ENTRY_CONTEXT")
        gate_results = self.gates.evaluate(c, risk_snapshot, regime=regime, event_calendar=event_calendar, entry_context=built_context)
        gate_codes = [code for result in gate_results if result.status == GateStatus.FAIL for code in result.reason_codes]
        if gate_codes:
            return Decision(ticker=c.ticker, expiration=c.expiration, short_strike=c.short_strike, long_strike=c.long_strike,
                underlying_price=c.underlying_price, market_regime=regime.value, scores=ScoreBreakdown(market_regime=regime_score, underlying_quality=0, trend=0, support=0, liquidity=0, rollability=0, strike_buffer=0, iv_premium=0, portfolio_capacity=0, news_risk=0),
                total_score=0, classification=SizeClass.HALF, action=Action.WAIT, reason="hard eligibility gate failed",
                reason_codes=gate_codes, delta_diagnostics={"short_delta": c.short_delta, "is_hard_gate": False})
        liq, rollability, liq_flags = self.liquidity.score(c)
        strike, strike_flags = self.strike.score(c)
        cap, cap_flags = self.capacity.score(c, {"planned_risk": risk_snapshot.planned_loss, "bucket_risk": risk_snapshot.bucket_planned_loss})
        quality = score_underlying_quality(c)
        news_score = max(0, 100 - c.event_risk * 25)
        breakdown = ScoreBreakdown(
            market_regime=regime_score, underlying_quality=quality, trend=score_trend(c),
            support=score_support(c), liquidity=liq, rollability=rollability,
            strike_buffer=strike, iv_premium=min(100, c.credit / max(c.short_strike - c.long_strike, 1) * 500),
            portfolio_capacity=cap, news_risk=news_score,
        )
        total, size_class = self.opportunity.score(breakdown, regime)
        flags = regime_flags + liq_flags + strike_flags + cap_flags
        action = Action.OPEN
        reason = "valid PCS opportunity"
        if regime == Regime.RED:
            action, reason = Action.WAIT, "RED market blocks new PCS"
        elif liq < self.rules["liquidity"]["reject_below"]:
            action, reason = Action.WAIT, "liquidity/rollability below hard threshold"
        elif strike == 0:
            action, reason = Action.WAIT, "no strike provides enough 3-5 day buffer"
        elif cap == 0:
            action, reason = Action.WAIT, "portfolio capacity exceeded"
        elif total < self.rules["scoring"]["open_threshold"]:
            action, reason = Action.WAIT, "opportunity score below open threshold"
        contracts, planned, theoretical, size_flags = self.sizer.size(c, size_class, risk_snapshot)
        flags += size_flags
        if contracts == 0 and action == Action.OPEN:
            action, reason = Action.WAIT, "sizing rules allow no new contracts"
        return Decision(
            ticker=c.ticker, expiration=c.expiration, short_strike=c.short_strike, long_strike=c.long_strike,
            underlying_price=c.underlying_price, market_regime=regime.value, scores=breakdown,
            total_score=round(total, 2), classification=size_class, action=action, reason=reason,
            recommended_contracts=contracts if action == Action.OPEN else 0, estimated_credit=c.credit,
            planned_risk=planned if action == Action.OPEN else 0, theoretical_max_loss=theoretical if action == Action.OPEN else 0,
            planned_loss=planned if action == Action.OPEN else 0,
            reason_codes=gate_codes,
            delta_diagnostics={"short_delta": c.short_delta, "preferred_range": [self.rules["entry"]["preferred_delta_min"], self.rules["entry"]["preferred_delta_max"]], "is_hard_gate": False},
            flags=flags,
        )

    def evaluate_position(self, p, market_state) -> Decision:
        regime, regime_score, flags = self.regime.classify(market_state)
        action, reason, roll = self.rolls.evaluate(p)
        if action == Action.HOLD and p.profit_capture_pct > 0:
            action, reason = self.profits.evaluate(p)
        scores = ScoreBreakdown(market_regime=regime_score, underlying_quality=80, trend=80 if p.structure_valid else 20,
                                support=80 if p.structure_valid else 20, liquidity=p.liquidity_score,
                                rollability=p.rollability_score, strike_buffer=80, iv_premium=60,
                                portfolio_capacity=80, news_risk=100 if p.thesis_valid else 0)
        return Decision(ticker=p.ticker, expiration=p.expiration, short_strike=p.short_strike, long_strike=p.long_strike,
                        underlying_price=p.underlying_price, market_regime=regime.value, scores=scores,
                        total_score=0, classification="1x", action=action, reason=reason,
                        planned_risk=p.planned_risk, theoretical_max_loss=p.theoretical_max_loss,
                        flags=flags, roll_candidate=roll)
