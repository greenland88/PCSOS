import numpy as np
import pandas as pd

from pcs.strategies.adaptive_profiles import measure_characteristics, resolve_strategy_config


def _daily(n=260):
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(np.full(n, 0.001)))
    return pd.DataFrame({"date": dates, "open": close, "high": close * 1.01,
                         "low": close * .99, "close": close, "volume": np.full(n, 1000)})


def test_characteristics_are_pit_bounded_and_do_not_require_outcomes():
    d = _daily()
    early = measure_characteristics(d, as_of="2020-06-01")
    changed_later = d.copy()
    changed_later.loc[changed_later.date > pd.Timestamp("2020-06-01"), "close"] *= 5
    assert early == measure_characteristics(changed_later, as_of="2020-06-01")
    assert early.option_quote_coverage is None


def test_resolver_preserves_frozen_execution_constants_and_is_ticker_generic():
    d = _daily()
    nvda = resolve_strategy_config("PCS_TREND_CONTINUATION_V1", "NVDA", d)
    meta = resolve_strategy_config("PCS_TREND_CONTINUATION_V1", "META", d)
    assert nvda.dte_min == meta.dte_min == 30
    assert nvda.dte_max == meta.dte_max == 45
    assert nvda.safe_strike_atr == meta.safe_strike_atr == 2.3
    assert nvda.min_credit_width == meta.min_credit_width == 0.10
    assert "NO_PNL_INPUT" in nvda.reason_codes
    assert nvda.module == "pcs.strategies.adaptive_profiles"
    assert nvda.calculation_version == "pit-behavior-v1"


def test_option_coverage_is_date_coverage_not_quote_row_count():
    d = _daily()
    options = pd.DataFrame({"trade_date": d.date.iloc[::2]})
    c = measure_characteristics(d, options=options)
    assert 0.49 < c.option_quote_coverage < 0.51
