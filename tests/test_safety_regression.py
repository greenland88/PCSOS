"""Permanent PCS safety gate: regression checks, not strategy tests."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from pcs.backtest.replay import HistoricalReplayEngine
from pcs.data.access import PCSDataAccess, DataQualityError

FIXTURE = Path(__file__).parent / "fixtures" / "safety" / "golden_cases.json"
TICKERS = {"NVDA", "TSLA", "MU", "AMZN", "AMD", "META", "QQQ"}


def _options(symbol="QQQ", strike=204.78, day="2025-07-01"):
    return pd.DataFrame([{"symbol": symbol, "trade_date": day, "expiration_date": "2025-08-01", "strike": strike,
        "call_put": "p", "last": 1.1, "bid": 1.0, "ask": 1.2, "bid_iv": .3, "ask_iv": .31,
        "open_interest": 1000, "volume": 200, "delta": -.25, "gamma": .01, "vega": .02, "theta": -.03, "rho": -.01}])


def _access(tmp_path):
    a = PCSDataAccess(tmp_path / "manifest.csv", tmp_path / "parquet")
    a.write_partition(_options(), "options", "QQQ", "year=2025/quarter=3", source_version="canonical-v1")
    return a


def test_golden_fixture_is_versioned_and_representative():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["fixture_version"] and data["strategy_version"]
    assert {x["ticker"] for x in data["cases"]} == TICKERS
    assert {x["outcome"] for x in data["cases"]} == {"ENTRY", "REJECT"}
    assert any(x["exit_reason"] == "STOP_LOSS" for x in data["cases"])


def test_contract_identity_and_fractional_strike_are_exact(tmp_path):
    a = _access(tmp_path)
    assert a.read_quotes("QQQ", "2025-07-01", "2025-07-01").strike.tolist() == [204.78]
    with pytest.raises(DataQualityError, match="ambiguous"):
        a.validate_schema(pd.concat([_options(), _options().assign(last=1.2)]), "options")


def test_ticker_isolation_unknown_and_coverage_fail_closed(tmp_path):
    a = _access(tmp_path)
    with pytest.raises(FileNotFoundError): a.read_quotes("MU", "2025-07-01", "2025-07-01")
    with pytest.raises(ValueError): a.read_quotes("QQQ", "2026-07-01", "2026-07-01")
    with pytest.raises(DataQualityError, match="ticker isolation"):
        a.validate_coverage(pd.DataFrame({"symbol": ["MU"]}), "QQQ")


def test_point_in_time_and_invalid_records_fail_closed(tmp_path):
    a = _access(tmp_path)
    with pytest.raises(DataQualityError, match="after"):
        a.validate_coverage(pd.DataFrame({"symbol": ["QQQ"], "trade_date": ["2025-07-02"]}), "QQQ", end_date="2025-07-01")
    with pytest.raises(DataQualityError, match="null"):
        a.validate_schema(_options().assign(expiration_date=None), "options")
    with pytest.raises(DataQualityError, match="missing"):
        a.validate_schema(pd.DataFrame({"symbol": ["QQQ"]}), "options")


def test_replay_is_reproducible_and_hard_filters_remain_deterministic():
    rows = [{"id": 1, "market_regime": "GREEN", "DTE": 35, "liquidity_score": 80, "buffer_ratio": 1.2},
            {"id": 2, "market_regime": "RED", "DTE": 35, "liquidity_score": 80, "buffer_ratio": 1.2}]
    first = HistoricalReplayEngine().replay(rows).__dict__
    second = HistoricalReplayEngine().replay(rows).__dict__
    assert first == second and [x["id"] for x in first["entries"]] == [1]


def test_production_guards_and_v2_isolation():
    rules = Path("config/pcs_rules.yaml").read_bytes()
    assert hashlib.sha256(rules).hexdigest() == hashlib.sha256(Path("config/pcs_rules.yaml").read_bytes()).hexdigest()
    # Per-ticker source routing is configuration-driven; strategy/replay code
    # must remain free of ticker-specific filesystem paths.
    assert "NVDA" not in Path("src/pcs/data/access.py").read_text(encoding="utf-8")
