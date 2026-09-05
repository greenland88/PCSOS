from pathlib import Path

import pandas as pd
import pytest

from pcs.data.access import DataQualityError
from pcs.data.incremental_update import update_ticker


def daily(rows):
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


def options(symbol="SPY", day="2026-08-21"):
    return pd.DataFrame([{
        "symbol": symbol, "trade_date": day, "expiration_date": "2026-09-18",
        "strike": 500.0, "call_put": "p", "last": 1.0, "bid": .9, "ask": 1.1,
        "open_interest": 10, "volume": 2,
    }])


def test_new_daily_date_changes_only_current_year(tmp_path):
    result = update_ticker("SPY", daily_frame=daily([["2026-08-20", 1, 2, 1, 1.5, 10], ["2026-08-21", 1.5, 2, 1, 1.8, 11]]), parquet_root=tmp_path / "parquet", manifest_path=tmp_path / "manifest.csv")
    assert result["daily_update"] == "UPDATED"
    assert result["frozen_generations_touched"] == 0
    assert result["affected_partitions"] == ["daily/symbol=SPY/year=2026"]


def test_daily_only_update_does_not_run_options_research_preflight(tmp_path, monkeypatch):
    import pcs.research.ticker_readiness as readiness
    monkeypatch.chdir(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("daily preparation entered options/research readiness")
    monkeypatch.setattr(readiness, "preflight_ticker", forbidden)
    result = update_ticker("SPY", daily_frame=daily([["2026-08-21", 1, 2, 1, 1.5, 10]]),
                           refresh_research_readiness=False)
    assert result["daily_update"] == "UPDATED"
    assert result["readiness_refresh_status"] == "SKIPPED_DAILY_ONLY"
    assert result["current_derived_artifacts_invalidated"]


def test_daily_update_rejects_foreign_ticker_rows(tmp_path):
    incoming = daily([["2026-08-20", 1, 2, 1, 1.5, 10]]).assign(symbol="QQQ")

    with pytest.raises(DataQualityError, match="ticker isolation"):
        update_ticker(
            "SPY",
            daily_frame=incoming,
            parquet_root=tmp_path / "parquet",
            manifest_path=tmp_path / "manifest.csv",
        )

    assert not (tmp_path / "parquet").exists()


def test_daily_overlap_conflict_preserves_active_generation(tmp_path):
    from pcs.data.access import PCSDataAccess
    kwargs = dict(parquet_root=tmp_path / 'parquet', manifest_path=tmp_path / 'manifest.csv', refresh_research_readiness=False)
    update_ticker('SPY', daily_frame=daily([['2026-09-02', 10, 12, 9, 11, 100]]), **kwargs)
    access = PCSDataAccess.isolated(manifest_path=kwargs['manifest_path'], parquet_root=kwargs['parquet_root'])
    before = access.active_generation_record('daily', 'SPY', 'year=2026')['active_generation']
    with pytest.raises(DataQualityError, match='DAILY_SOURCE_OVERLAP_CONFLICT'):
        update_ticker('SPY', daily_frame=daily([['2026-09-02', 10, 12, 9, 10, 100]]), **kwargs)
    assert access.active_generation_record('daily', 'SPY', 'year=2026')['active_generation'] == before


def test_options_same_partition_is_idempotent(tmp_path):
    kwargs = dict(parquet_root=tmp_path / "parquet", options_manifest_path=tmp_path / "options_manifest.csv")
    first = update_ticker("SPY", options_frame=options(), **kwargs)
    target = next((tmp_path / "parquet" / "options_v2" / "symbol=SPY" / "year=2026" / "quarter=3").glob("*.parquet"))
    before = target.read_bytes()
    second = update_ticker("SPY", options_frame=options(), **kwargs)
    assert first["options_update"] == "UPDATED"
    assert second["options_update"] == "NO_OP"
    assert target.read_bytes() == before
