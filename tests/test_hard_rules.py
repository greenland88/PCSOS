from pcs.engine.decision_engine import DecisionEngine, load_rules
from pcs.models.market import MarketState
from pcs.models.position import PCSPosition
from pcs.models.trade import TradeCandidate
from pcs.models.decision import Action


def candidate(**overrides):
    data = dict(ticker="QQQ", expiration="2026-09-18", short_strike=455, long_strike=450,
                underlying_price=485, credit=0.85, dte=35, short_delta=0.24, expected_move=20,
                support_level=462, normal_daily_move=5, option_volume=2000, open_interest=10000,
                bid_ask_pct=0.05, nearby_strikes=10, later_expirations=8, business_quality=95,
                trend_score=90, support_score=90, sector_alignment=85, price_confirmation=90,
                correlation_bucket="nasdaq_mega")
    data.update(overrides)
    return TradeCandidate(**data)


def engine():
    return DecisionEngine(load_rules())


def test_red_market_blocks_new_pcs():
    d = engine().evaluate_candidate(candidate(), MarketState(vix=35), {"planned_risk": 0, "bucket_risk": {}})
    assert d.action == Action.NO_TRADE
    assert "REGIME_RED" in d.reason_codes


def test_no_three_day_buffer_waits():
    d = engine().evaluate_candidate(candidate(short_strike=475), MarketState(vix=18), {"planned_risk": 0, "bucket_risk": {}})
    assert d.action == Action.NO_TRADE
    assert d.reason_codes


def test_poor_liquidity_waits():
    d = engine().evaluate_candidate(candidate(option_volume=10, open_interest=10, bid_ask_pct=0.4, nearby_strikes=1, later_expirations=1), MarketState(vix=18), {"planned_risk": 0, "bucket_risk": {}})
    assert d.action == Action.NO_TRADE
    assert d.reason_codes


def test_capacity_exceeded_restricts_new_positions():
    d = engine().evaluate_candidate(candidate(), MarketState(vix=18), {"planned_risk": 11000, "bucket_risk": {}})
    assert d.action == Action.NO_TRADE
    assert "PORTFOLIO_PLANNED_LOSS_LIMIT" in d.reason_codes


def test_broken_thesis_closes_not_rolls():
    p = PCSPosition(ticker="NVDA", expiration="2026-08-28", short_strike=160, long_strike=155,
                    underlying_price=158, credit_opened=1, current_mark=2, contracts=1, dte=10,
                    planned_risk=500, theoretical_max_loss=400, support_level=160, structure_valid=False,
                    thesis_valid=False, liquidity_score=90, rollability_score=90, decline_temporary=True,
                    candidate_roll={"expiration": "2026-09-25"})
    d = engine().evaluate_position(p, MarketState(vix=18))
    assert d.action == Action.CLOSE


def test_temporary_decline_with_intact_structure_rolls():
    p = PCSPosition(ticker="NVDA", expiration="2026-08-28", short_strike=160, long_strike=155,
                    underlying_price=162, credit_opened=1, current_mark=2, contracts=1, dte=10,
                    planned_risk=500, theoretical_max_loss=400, support_level=160, structure_valid=True,
                    thesis_valid=True, liquidity_score=90, rollability_score=90, decline_temporary=True,
                    candidate_roll={"expiration": "2026-09-25", "net_credit": 0.2})
    d = engine().evaluate_position(p, MarketState(vix=18))
    assert d.action == Action.ROLL


def test_early_profitable_healthy_position_holds():
    p = PCSPosition(ticker="QQQ", expiration="2026-08-28", short_strike=450, long_strike=445,
                    underlying_price=484, credit_opened=1, current_mark=0.6, contracts=1, dte=20,
                    planned_risk=500, theoretical_max_loss=400, support_level=462, structure_valid=True,
                    thesis_valid=True, liquidity_score=95, rollability_score=95)
    d = engine().evaluate_position(p, MarketState(vix=18))
    assert d.action == Action.HOLD
    assert "early-profit patience" in d.reason


def test_reserve_cash_does_not_increase_sizing():
    rules = load_rules()
    d = DecisionEngine(rules).evaluate_candidate(candidate(), MarketState(vix=18), {"planned_risk": 0, "bucket_risk": {}})
    assert d.planned_risk <= rules["capital"]["single_ticker"]["conviction_ceiling"]
    assert rules["capital"]["reserve_cash"] == 20000
