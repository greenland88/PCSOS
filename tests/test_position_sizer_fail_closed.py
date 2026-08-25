from pcs.engine.decision_engine import load_rules
from pcs.models.decision import SizeClass
from pcs.risk.position_sizing import PositionSizer
from pcs.models.trade import TradeCandidate


def candidate(**overrides):
    values = dict(ticker="QQQ", expiration="2026-09-18", short_strike=455,
                  long_strike=450, underlying_price=485, credit=0.85, dte=35,
                  short_delta=0.24, expected_move=20, support_level=462,
                  normal_daily_move=5, option_volume=2000, open_interest=10000,
                  bid_ask_pct=0.05, nearby_strikes=10, later_expirations=8,
                  business_quality=95, trend_score=90, support_score=90,
                  sector_alignment=85, price_confirmation=90,
                  correlation_bucket="nasdaq_mega")
    values.update(overrides)
    return TradeCandidate(**values)


def test_sizer_rejects_non_credit_spreads_even_without_gate():
    result = PositionSizer(load_rules()).size(candidate(credit=-0.1), SizeClass.ONE,
                                              {"planned_risk": 0, "bucket_risk": {}})
    assert result[:3] == (0, 0.0, 0.0)
    assert "INVALID_CREDIT_OR_SPREAD_WIDTH" in result[3]
