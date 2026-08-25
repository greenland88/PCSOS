import pytest
from pcs.research.backend import resolve_option_backend
from pcs.research.compatibility import compatibility, enforce_reliable_range

def test_default_backend_is_duckdb(): assert resolve_option_backend() == "duckdb"
def test_explicit_csv_reference(): assert resolve_option_backend("csv") == "csv"
def test_scale_compatibility_separates_availability():
    c=compatibility("NVDA","2020-01-01"); assert c["data_available"] and not c["pcs_research_compatible"] and c["reason_code"]=="SCALE_INCOMPATIBLE"
def test_reliable_range_rejects_incompatible_dates():
    with pytest.raises(ValueError,match="PCS_RESEARCH_SCALE_INCOMPATIBLE"): enforce_reliable_range("AMZN","2020-01-01","2020-02-01")
