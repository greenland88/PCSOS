import pandas as pd
import pytest

from pcs.data.market_confirmation import (
    ConfirmationStatus,
    build_market_confirmation,
    lookup_for_decision,
)


class FakeAccess:
    def __init__(self, spy, qqq):
        self.frames = {"SPY": spy, "QQQ": qqq}

    def read_partition(self, dataset, symbol, partition, filename=None):
        year = int(partition.split("=", 1)[1])
        return self.frames[symbol][self.frames[symbol].date.dt.year.eq(year)].copy()


def _daily(values):
    dates = pd.date_range("2020-01-01", periods=len(values), freq="B")
    return pd.DataFrame({"symbol": "X", "date": dates, "open": values, "high": values, "low": values, "close": values, "volume": 1})


def test_all_four_confirmation_combinations():
    # 50-day SMA is 10 for SPY and QQQ; each case exercises the explicit AND.
    spy = _daily([10] * 50 + [11, 9, 9, 11])
    qqq = _daily([10] * 50 + [11, 11, 9, 9])
    requested = spy.date.iloc[50:54]
    frame, _ = build_market_confirmation(FakeAccess(spy, qqq), requested.iloc[0], requested.iloc[-1], required_dates=requested)
    # Date alignment: (True,True), (False,True), (False,False), (True,False)
    assert frame.breadth_positive.dropna().tolist() == [True, False, False, False]


def test_warmup_is_explicit_and_not_filled():
    spy, qqq = _daily([10] * 3), _daily([10] * 3)
    frame, report = build_market_confirmation(FakeAccess(spy, qqq), "2020-01-01", "2020-01-03", required_dates=pd.date_range("2020-01-01", "2020-01-03", freq="B"))
    assert frame.breadth_positive.isna().all()
    assert set(frame.status) == {ConfirmationStatus.INSUFFICIENT_SMA50_HISTORY.value}
    assert report["warmup_rows"] == 3


def test_pre_close_lookup_never_reads_same_session_close():
    spy = _daily([10] * 50 + [11, 9])
    qqq = _daily([10] * 50 + [11, 11])
    requested = spy.date.iloc[50:52]
    frame, _ = build_market_confirmation(FakeAccess(spy, qqq), requested.iloc[0], requested.iloc[-1], required_dates=requested)
    after_day = requested.iloc[1].strftime("%Y-%m-%d")
    before_day = requested.iloc[0].strftime("%Y-%m-%d")
    after = lookup_for_decision(frame, f"{after_day} 23:59:59-04:00", after_close=True)
    before = lookup_for_decision(frame, f"{after_day} 15:00:00-04:00", after_close=False)
    assert pd.Timestamp(after.date) == pd.Timestamp(after_day)
    assert pd.Timestamp(before.date) == pd.Timestamp(before_day)
