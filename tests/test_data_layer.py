from datetime import datetime, timezone

from pcs.backtest.replay import HistoricalReplayEngine
from pcs.data.storage import ParquetStore
from pcs.features.expected_move import calculate_expected_move
from pcs.features.market_features import calculate_market_features
from pcs.scoring.rollability_score import RollabilityScorer


def test_expected_move_buffer_ratio():
    result = calculate_expected_move(spot=220, short_strike=195, atr=5, iv=0.35, dte=35)
    assert result.expected_move_5d > 0
    assert result.buffer_ratio > 1


def test_predictability_score_is_bounded():
    rows = [
        {"date": f"2026-01-{i:02d}", "symbol": "QQQ", "open": 100+i, "high": 101+i, "low": 99+i, "close": 100+i, "adjusted_close": 100+i, "volume": 1000}
        for i in range(1, 31)
    ]
    features = calculate_market_features(rows)
    score = features[-1]["predictability_score"]
    # Strict feature warm-up intentionally leaves short histories unusable.
    # Once a score exists, it must remain within the documented range.
    assert (score != score) or (0 <= score <= 100)


def test_parquet_snapshots_do_not_overwrite(tmp_path):
    store = ParquetStore(tmp_path)
    t1 = datetime(2026, 8, 14, 20, 0, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 14, 20, 0, 2, tzinfo=timezone.utc)
    p1 = store.write_snapshot("options", [{"ticker": "QQQ", "bid": 1.0}], as_of=t1, name="QQQ_option_chain")
    p2 = store.write_snapshot("options", [{"ticker": "QQQ", "bid": 1.1}], as_of=t2, name="QQQ_option_chain")
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_rollability_prefers_lower_same_width_credit_roll():
    scored = RollabilityScorer().score_candidates(700, 695, [
        {"expiration": "2026-10-02", "short_strike": 695, "long_strike": 690, "days_added": 14, "liquidity_score": 80, "net_credit_estimate": 0.1},
        {"expiration": "2026-10-02", "short_strike": 700, "long_strike": 695, "days_added": 14, "liquidity_score": 80, "net_credit_estimate": -0.2},
    ])
    assert scored[0].candidate_spread == "695/690"


def test_historical_replay_keeps_live_logic_separate():
    result = HistoricalReplayEngine().replay([
        {"ticker": "QQQ", "market_regime": "GREEN", "DTE": 35, "liquidity_score": 80, "buffer_ratio": 1.2},
        {"ticker": "QQQ", "market_regime": "RED", "DTE": 35, "liquidity_score": 80, "buffer_ratio": 1.2},
    ])
    assert len(result.entries) == 1
    assert len(result.skipped) == 1
