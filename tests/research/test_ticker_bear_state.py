import pandas as pd
from pcs.research.ticker_bear_state import calculate_ticker_bear_states


def bars(n=260, close=100.0, high=125.0):
    dates=pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"date":dates,"open":close,"high":high,"low":close-1,"close":close,"volume":100})

def set_close(x, start, end, value):
    x.loc[start:end,"close"]=value; x.loc[start:end,"open"]=value; x.loc[start:end,"low"]=value-1


def test_normal_when_fewer_than_two_conditions():
    x=bars(); out=calculate_ticker_bear_states(x,"XYZ")
    assert out.iloc[-1].ticker_bear_state == "NORMAL"


def test_weak_bear_when_exactly_two_conditions():
    x=bars(); set_close(x,259,259,90)
    out=calculate_ticker_bear_states(x,"XYZ")
    assert out.iloc[-1].ticker_bear_state == "WEAK_BEAR"


def test_bear_confirmed_on_exactly_five_full_days():
    x=bars()
    # Make SMA50 below SMA200 only for the final five observations.
    set_close(x,205,254,70); set_close(x,255,259,75)
    out=calculate_ticker_bear_states(x,"XYZ")
    first=out[out.ticker_bear_state.eq("BEAR_CONFIRMED")].iloc[0]
    prior=out.iloc[first.name-1]
    assert first.consecutive_full_bear_days == 5
    assert prior.consecutive_full_bear_days == 4
    assert prior.ticker_bear_state == "WEAK_BEAR"


def test_drawdown_boundary_is_inclusive():
    x=bars(); set_close(x,252,259,100)
    out=calculate_ticker_bear_states(x,"XYZ")
    assert bool(out.iloc[-1].drawdown_ge_20pct) is True


def test_reset_after_full_bear_breaks():
    x=bars(); set_close(x,205,254,70); set_close(x,255,258,75); set_close(x,259,259,110)
    out=calculate_ticker_bear_states(x,"XYZ")
    assert out.iloc[-1].consecutive_full_bear_days == 0
    assert out.iloc[-1].ticker_bear_state != "BEAR_CONFIRMED"


def test_insufficient_history_fails_closed():
    out=calculate_ticker_bear_states(bars(251),"XYZ")
    assert out.iloc[-1].ticker_bear_state == "INSUFFICIENT_HISTORY"


def test_future_bars_do_not_change_prior_state():
    x=bars(); before=calculate_ticker_bear_states(x,"XYZ")
    changed=x.copy(); changed.loc[259,"close"]=1; changed.loc[259,"low"]=1
    after=calculate_ticker_bear_states(changed,"XYZ")
    pd.testing.assert_series_equal(before.ticker_bear_state.iloc[:259],after.ticker_bear_state.iloc[:259],check_names=False)
