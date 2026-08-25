import pytest
from pcs.data.unified import UnifiedDataAccess
from pcs.data.access import DataAccessError


def test_ticker_specific_manifest_resolution():
    a = UnifiedDataAccess()
    tsla = a.resolve_source("options", "TSLA", "2020-01-02", "2020-01-03")
    assert "symbol=TSLA" in tsla.path
    assert tsla.row_count > 0
    mu = a.resolve_source("options", "MU", "2020-01-02", "2020-01-03")
    assert "symbol=MU" in mu.path
    assert mu.row_count > 0


def test_out_of_coverage_and_wrong_ticker_fail():
    a = UnifiedDataAccess()
    with pytest.raises(ValueError, match="outside"):
        a.resolve_source("options", "MU", "2010-01-01", "2010-01-02")
    with pytest.raises(DataAccessError, match="canonical route unavailable"):
        a.resolve_source("options", "NOT_A_TICKER", "2020-01-01", "2020-01-02")


def test_no_qqq_substitution():
    a = UnifiedDataAccess()
    quotes = a.load_option_quotes("MU", "2020-01-02", "2020-01-02")
    assert not quotes.empty
    assert set(quotes["symbol"].astype(str).str.upper()) == {"MU"}
