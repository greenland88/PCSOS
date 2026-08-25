import pandas as pd

from pcs.research.entry_candidate_universe import _atr14


def test_nvda_v2_atr_contract_uses_canonical_wilder_atr():
    dates = pd.date_range("2020-01-01", periods=220, freq="D")
    daily = pd.DataFrame({
        "date": dates,
        "open": range(100, 320),
        "high": [x + 3 for x in range(100, 320)],
        "low": [x - 2 for x in range(100, 320)],
        "close": range(100, 320),
        "volume": [1000] * 220,
    })
    atr = _atr14(daily)
    assert atr.iloc[:14].isna().all()
    assert atr.iloc[14:].notna().all()
