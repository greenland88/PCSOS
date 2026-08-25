import pandas as pd
from pcs.research.daily_data_diagnosis import diagnose_daily_file


def write(tmp_path, rows):
    p = tmp_path / "daily.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_valid_ohlc_passes(tmp_path):
    _, issues = diagnose_daily_file(write(tmp_path, [{"date":"2025-01-01","open":10,"high":12,"low":9,"close":11,"volume":1}]))
    assert issues.empty


def test_all_relationships_and_signs_detected(tmp_path):
    _, issues = diagnose_daily_file(write(tmp_path, [{"date":"2025-01-01","open":10,"high":9,"low":11,"close":10,"volume":1}, {"date":"2025-01-01","open":0,"high":0,"low":0,"close":0,"volume":-1}]))
    kinds=set(issues.issue_type)
    assert {"HIGH_BELOW_OPEN","HIGH_BELOW_CLOSE","LOW_ABOVE_OPEN","LOW_ABOVE_CLOSE","NEGATIVE_OR_ZERO_PRICE","DUPLICATE_DATE","NEGATIVE_VOLUME"} <= kinds


def test_small_violation_and_source_unchanged(tmp_path):
    rows=[{"date":"2025-01-01","open":10.0001,"high":10.0,"low":9.0,"close":9.5,"volume":1}]
    p=write(tmp_path,rows); before=p.read_bytes(); _, issues=diagnose_daily_file(p)
    assert "HIGH_BELOW_OPEN" in set(issues.issue_type)
    assert p.read_bytes()==before
