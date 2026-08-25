import pytest
import pandas as pd
from pcs.data.duckdb_store import connect, refresh_views
from pcs.research.backend import resolve_option_backend
from pcs.research.compatibility import compatibility, enforce_reliable_range

def test_default_backend_is_duckdb(): assert resolve_option_backend() == "duckdb"
def test_explicit_csv_reference(): assert resolve_option_backend("csv") == "csv"
def test_scale_compatibility_separates_availability():
    c=compatibility("NVDA","2020-01-01"); assert c["data_available"] and not c["pcs_research_compatible"] and c["reason_code"]=="SCALE_INCOMPATIBLE"
def test_reliable_range_rejects_incompatible_dates():
    with pytest.raises(ValueError,match="PCS_RESEARCH_SCALE_INCOMPATIBLE"): enforce_reliable_range("AMZN","2020-01-01","2020-02-01")


def test_duckdb_research_view_uses_canonical_writer_location(tmp_path):
    target = tmp_path / "parquet" / "research" / "pcs_backtest_trades" / "run_id=run-1"
    target.mkdir(parents=True)
    pd.DataFrame({"run_id": ["run-1"], "pnl": [12.5]}).to_parquet(target / "trades.parquet", index=False)

    con = connect(":memory:")
    refresh_views(con, parquet_root=tmp_path / "parquet")

    rows = con.execute("select run_id, pnl from pcs_backtest_trades").fetchall()
    assert rows == [("run-1", 12.5)]
