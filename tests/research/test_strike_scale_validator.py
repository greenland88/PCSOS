import pandas as pd

from pcs.research.strike_scale_validator import classify_reliable_range, validate_chain_date


def test_aligned_and_mismatch_scales():
    aligned = validate_chain_date("X", "2025-01-01", 100, 4, [90, 95, 98, 100, 102, 105, 108])
    mismatch = validate_chain_date("X", "2025-01-01", 100, 4, [5, 10, 20, 30])
    assert aligned["scale_status"] == "ALIGNED"
    assert mismatch["scale_status"] == "MISMATCH"


def test_sparse_chain_is_not_automatically_mismatch_and_no_mutation():
    strikes = [98.0]
    before = list(strikes)
    result = validate_chain_date("X", "2025-01-01", 100, 4, strikes)
    assert result["scale_status"] != "MISMATCH"
    assert strikes == before


def test_range_detection_ignores_single_bad_day():
    samples = [{"date": pd.Timestamp("2020-01-01"), "scale_status": "ALIGNED"},
               {"date": pd.Timestamp("2020-02-01"), "scale_status": "MISMATCH"},
               {"date": pd.Timestamp("2020-03-01"), "scale_status": "ALIGNED"}]
    assert classify_reliable_range(samples, "2020-01-01", "2020-03-01")["reliable_start_date"] == "2020-01-01"
