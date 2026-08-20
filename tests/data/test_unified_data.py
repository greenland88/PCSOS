import pytest
from pcs.data.unified import UnifiedDataAccess


def test_ticker_specific_manifest_resolution():
    a = UnifiedDataAccess()
    tsla = a.resolve_source("options", "TSLA", "2020-01-02", "2020-01-03")
    mu = a.resolve_source("options", "MU", "2020-01-02", "2020-01-03")
    assert "symbol=TSLA" in tsla.path and "symbol=MU" in mu.path
    assert tsla.row_count > 12_000_000 and mu.row_count > 3_000_000


def test_out_of_coverage_and_wrong_ticker_fail():
    a = UnifiedDataAccess()
    with pytest.raises(ValueError): a.resolve_source("options", "MU", "2010-01-01", "2010-01-02")
    with pytest.raises(FileNotFoundError): a.resolve_source("options", "NOT_A_TICKER", "2020-01-01", "2020-01-02")


def test_no_qqq_substitution():
    a = UnifiedDataAccess()
    rows = a.load_option_quotes("MU", "2020-01-02", "2020-01-02")
    assert rows.empty or set(rows.symbol) == {"MU"}
