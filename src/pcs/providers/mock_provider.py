from pcs.models.market import MarketState
from pcs.models.position import PCSPosition
from pcs.models.trade import TradeCandidate
import pandas as pd
from .base import BaseBrokerProvider


class MockProvider(BaseBrokerProvider):
    def get_event_calendar(self):
        return pd.DataFrame([{"symbol": "QQQ", "event_type": "EARNINGS", "event_date": "2027-01-01"}])
    def get_accounts(self):
        return {"pcs_pool": 20000, "reserve_cash": 20000}

    def get_portfolio(self):
        return {"planned_risk": 4600, "theoretical_max_loss": 8400}

    def get_market_state(self):
        return MarketState(qqq_above_20dma=True, qqq_above_50dma=True,
                           qqq_above_200dma=True, spy_above_50dma=True,
                           soxx_above_50dma=True, breadth_positive=True,
                           vix=18, recent_drawdown_pct=1.2)

    def get_candidates(self):
        return [
            TradeCandidate(ticker="QQQ", expiration="2026-09-18", short_strike=455, long_strike=450, underlying_price=485, credit=0.82, dte=35, short_delta=0.24, expected_move=20, support_level=462, normal_daily_move=5.0, option_volume=2400, open_interest=22000, bid_ask_pct=0.05, nearby_strikes=12, later_expirations=8, business_quality=96, trend_score=86, support_score=88, sector_alignment=82, price_confirmation=85, correlation_bucket="nasdaq_mega"),
            TradeCandidate(ticker="NVDA", expiration="2026-09-18", short_strike=155, long_strike=150, underlying_price=177, credit=1.05, dte=35, short_delta=0.28, expected_move=16, support_level=160, normal_daily_move=4.5, option_volume=900, open_interest=8500, bid_ask_pct=0.08, nearby_strikes=10, later_expirations=7, business_quality=92, trend_score=80, support_score=78, sector_alignment=86, price_confirmation=78, correlation_bucket="semiconductor"),
            TradeCandidate(ticker="MSFT", expiration="2026-09-18", short_strike=470, long_strike=465, underlying_price=508, credit=0.70, dte=35, short_delta=0.21, expected_move=22, support_level=482, normal_daily_move=4.0, option_volume=450, open_interest=3200, bid_ask_pct=0.10, nearby_strikes=8, later_expirations=6, business_quality=95, trend_score=82, support_score=80, sector_alignment=78, price_confirmation=80, correlation_bucket="nasdaq_mega"),
            TradeCandidate(ticker="AMZN", expiration="2026-09-18", short_strike=195, long_strike=190, underlying_price=216, credit=0.62, dte=35, short_delta=0.23, expected_move=12, support_level=202, normal_daily_move=3.5, option_volume=80, open_interest=260, bid_ask_pct=0.22, nearby_strikes=3, later_expirations=3, business_quality=90, trend_score=76, support_score=70, sector_alignment=75, price_confirmation=72, correlation_bucket="nasdaq_mega"),
        ]

    def get_positions(self):
        return [
            PCSPosition(ticker="QQQ", expiration="2026-08-28", short_strike=450, long_strike=445, underlying_price=484, credit_opened=0.90, current_mark=0.34, contracts=3, dte=14, planned_risk=900, theoretical_max_loss=1230, support_level=462, structure_valid=True, thesis_valid=True, liquidity_score=95, rollability_score=98),
            PCSPosition(ticker="NVDA", expiration="2026-08-28", short_strike=160, long_strike=155, underlying_price=162, credit_opened=1.10, current_mark=1.85, contracts=2, dte=14, planned_risk=900, theoretical_max_loss=780, support_level=160, structure_valid=True, thesis_valid=True, liquidity_score=86, rollability_score=90, decline_temporary=True, candidate_roll={"expiration": "2026-09-25", "short_strike": 150, "long_strike": 145, "net_credit": 0.20}),
        ]

    def get_equity_quote(self, symbol: str):
        return {"symbol": symbol, "price": 100}

    def get_option_chain(self, symbol: str):
        return []

    def get_option_quotes(self, ids: list[str]):
        return []
