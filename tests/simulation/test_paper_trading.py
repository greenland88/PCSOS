import json
import sqlite3

from pcs.engine.decision_engine import load_rules
from pcs.models.decision import Action
from pcs.providers.mock_provider import MockProvider
from pcs.simulation.paper_trading import PaperTradingStatus, run_daily_paper_trading
from pcs.models.decision import Action, Decision, ScoreBreakdown, SizeClass
from pcs.models.market import MarketState
from pcs.models.trade import TradeCandidate


def test_daily_paper_trading_returns_agent_ready_envelope():
    result = run_daily_paper_trading(
        MockProvider(),
        load_rules(),
        as_of="2026-08-18",
        run_id="run_test",
        request_id="req_test",
    )

    assert result.module == "paper_trading_daily"
    assert result.version == "1.0"
    assert result.symbol == "PORTFOLIO"
    assert result.as_of == "2026-08-18"
    assert result.status == PaperTradingStatus.READY
    assert result.run_id == "run_test"
    assert result.request_id == "req_test"
    assert result.candidate_count == 4
    assert result.position_count == 2
    assert set(result.action_counts) == {action.value for action in Action}
    assert result.action_counts["OPEN"] == 0
    assert result.action_counts["WAIT"] == 4
    assert result.action_counts["HOLD"] == 1
    assert result.action_counts["ROLL"] == 1
    assert result.planned_risk_open == 0
    assert result.theoretical_max_loss_open == 0
    assert all(snapshot.action in Action for snapshot in result.snapshots)
    assert all(snapshot.reason_codes for snapshot in result.snapshots)
    hold = next(snapshot for snapshot in result.snapshots if snapshot.action == Action.HOLD)
    assert hold.reason_codes == ["POSITION_HELD"]


def test_daily_paper_trading_persists_outputs(tmp_path):
    db_path = tmp_path / "pcs.db"
    out_dir = tmp_path / "paper"

    result = run_daily_paper_trading(
        MockProvider(),
        load_rules(),
        as_of="2026-08-18",
        run_id="run_test",
        request_id="req_test",
        output_dir=out_dir,
        sqlite_path=db_path,
    )

    json_path = out_dir / "2026-08-18" / "paper_trading_snapshot.json"
    summary_path = out_dir / "2026-08-18" / "paper_trading_summary.csv"
    snapshots_path = out_dir / "2026-08-18" / "paper_trading_snapshots.csv"
    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == result.run_id
    assert summary_path.exists()
    assert snapshots_path.exists()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT as_of, run_id, status FROM paper_trading_runs").fetchone()
    assert row == ("2026-08-18", "run_test", "READY")


def test_sequential_open_candidates_reserve_capacity(monkeypatch):
    class Provider(MockProvider):
        def get_portfolio(self):
            return {"planned_risk": 9000, "bucket_risk": {}, "ticker_risk": {}}
        def get_candidates(self):
            base = dict(ticker="QQQ", expiration="2026-09-18", short_strike=455, long_strike=450,
                        underlying_price=485, credit=.85, dte=35, short_delta=.24, expected_move=20,
                        support_level=462, normal_daily_move=5, option_volume=2000, open_interest=10000,
                        bid_ask_pct=.05, nearby_strikes=10, later_expirations=8, business_quality=95,
                        trend_score=90, support_score=90, sector_alignment=85, price_confirmation=90,
                        correlation_bucket="nasdaq_mega")
            return [TradeCandidate(**base), TradeCandidate(**{**base, "long_strike": 449})]
        def get_positions(self): return []

    seen = []
    class CapacityEngine:
        def __init__(self, rules, **kwargs): pass
        def evaluate_candidate(self, c, market, portfolio, **kwargs):
            seen.append(float(portfolio["planned_loss"]))
            action = Action.OPEN if portfolio["planned_loss"] < 10000 else Action.WAIT
            return Decision(ticker=c.ticker, expiration=c.expiration, short_strike=c.short_strike,
                long_strike=c.long_strike, underlying_price=c.underlying_price, market_regime="GREEN",
                scores=ScoreBreakdown(market_regime=1, underlying_quality=1, trend=1, support=1,
                    liquidity=1, rollability=1, strike_buffer=1, iv_premium=1, portfolio_capacity=1, news_risk=1),
                total_score=80, classification=SizeClass.ONE, action=action, reason="test",
                recommended_contracts=1 if action == Action.OPEN else 0, estimated_credit=c.credit,
                planned_risk=1000 if action == Action.OPEN else 0, theoretical_max_loss=1000 if action == Action.OPEN else 0)
        def evaluate_position(self, p, market): raise AssertionError("no positions expected")

    import pcs.simulation.paper_trading as module
    monkeypatch.setattr(module, "DecisionEngine", CapacityEngine)
    result = run_daily_paper_trading(Provider(), {}, as_of="2026-08-18")
    assert seen == [9000.0, 10000.0]
    assert result.action_counts[Action.OPEN.value] == 1
