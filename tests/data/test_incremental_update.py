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


def test_options_same_partition_is_idempotent(tmp_path):
    kwargs = dict(parquet_root=tmp_path / "parquet", options_manifest_path=tmp_path / "options_manifest.csv")
    first = update_ticker("SPY", options_frame=options(), **kwargs)
    target = next((tmp_path / "parquet" / "options_v2" / "symbol=SPY" / "year=2026" / "quarter=3").glob("*.parquet"))
    before = target.read_bytes()
    second = update_ticker("SPY", options_frame=options(), **kwargs)
    assert first["options_update"] == "UPDATED"
    assert second["options_update"] == "NO_OP"
    assert target.read_bytes() == before
