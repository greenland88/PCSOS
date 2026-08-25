import pandas as pd

from pcs.research.benchmark_selection import common_return_stats


def test_common_date_alignment_and_correlation():
    dates = pd.date_range("2025-01-01", periods=80, freq="B")
    q = pd.DataFrame({"date": dates, "close": range(100, 180)})
    c = pd.DataFrame({"date": dates[2:], "close": range(50, 128)})
    result = common_return_stats(q, c)
    assert result["common_days"] == 78
    assert result["common_start"] == dates[2]


def test_source_frames_are_not_mutated():
    dates = pd.date_range("2025-01-01", periods=80, freq="B")
    q = pd.DataFrame({"date": dates, "close": range(100, 180)})
    c = pd.DataFrame({"date": dates, "close": range(50, 130)})
    q0, c0 = q.copy(deep=True), c.copy(deep=True)
    common_return_stats(q, c)
    pd.testing.assert_frame_equal(q, q0)
    pd.testing.assert_frame_equal(c, c0)
