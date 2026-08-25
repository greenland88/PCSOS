import pandas as pd

from pcs.research.entry_candidate_universe import _atr14


def test_nvda_v2_atr_contract_uses_canonical_wilder_atr():
    dates = pd.date_range("2020-01-01", periods=30, freq="D")
    daily = pd.DataFrame({
        "date": dates,
        "open": range(100, 130),
        "high": [x + 3 for x in range(100, 130)],
        "low": [x - 2 for x in range(100, 130)],
        "close": range(100, 130),
        "volume": [1000] * 30,
    })
    atr = _atr14(daily)
    assert atr.iloc[:13].isna().all()
    assert atr.iloc[13:].notna().all()
