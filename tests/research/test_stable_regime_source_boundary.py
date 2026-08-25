import pandas as pd
import pytest

from pcs.research.stable_regime_detector import load_symbol_csv


def test_noncanonical_regime_source_requires_explicit_test_opt_in(tmp_path):
    pd.DataFrame({"date": ["2025-01-02"], "close": [1.0]}).to_csv(
        tmp_path / "ZZZ_daily_qfq.csv", index=False
    )
    with pytest.raises(ValueError, match="NONCANONICAL_DAILY_SOURCE"):
        load_symbol_csv("ZZZ", tmp_path)
    frame = load_symbol_csv("ZZZ", tmp_path, allow_test_source=True)
    assert frame.loc[0, "close"] == 1.0
