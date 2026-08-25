import duckdb
import pandas as pd

from pcs.data.storage_schema import OPTIONS_REQUIRED_FIELDS, OPTIONS_OPTIONAL_FIELDS, OPTIONS_SCHEMA_VERSION


def test_schema_registry_is_backward_compatible():
    assert OPTIONS_SCHEMA_VERSION == 1
    assert "strike" in OPTIONS_REQUIRED_FIELDS
    assert "underlying_price" in OPTIONS_OPTIONAL_FIELDS


def test_old_new_parquet_union_returns_null_for_old_rows(tmp_path):
    old = tmp_path / "old.parquet"; new = tmp_path / "new.parquet"
    pd.DataFrame({"symbol":["QQQ"],"trade_date":[pd.Timestamp("2026-01-01").date()],"expiration_date":[pd.Timestamp("2026-02-01").date()],"strike":[100.0],"call_put":["p"],"bid":[1.0]}).to_parquet(old)
    pd.DataFrame({"symbol":["QQQ"],"trade_date":[pd.Timestamp("2026-01-02").date()],"expiration_date":[pd.Timestamp("2026-02-01").date()],"strike":[101.0],"call_put":["p"],"bid":[1.1],"underlying_price":[102.0],"quote_time":["10:00"]}).to_parquet(new)
    con=duckdb.connect(); result=con.execute("select symbol, strike, bid, underlying_price from read_parquet(?, union_by_name=true) order by trade_date", [str(tmp_path/'*.parquet')]).fetchdf()
    assert len(result)==2 and pd.isna(result.iloc[0].underlying_price) and result.iloc[1].underlying_price==102.0
