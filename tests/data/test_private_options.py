from datetime import datetime, timezone

import pandas as pd
import pytest

from pcs.data.massive_client import GatewayConfig
from pcs.data.private_options import PrivateGatewayOptionsReader, PrivateOptionQuoteError
from pcs.pool.modes import resolve_option_quote_window


def _ns(value: str) -> int:
    return pd.Timestamp(value).value


class Gateway:
    config = GatewayConfig("secret")

    def __init__(self, quote):
        self.quote = quote
        self.snapshot_calls = []
        self.quote_calls = []

    def option_chain_snapshot(self, symbol, **kwargs):
        self.snapshot_calls.append((symbol, kwargs))
        return [{
            "details": {"ticker": f"O:{symbol}261009P00150000", "contract_type": "put",
                        "expiration_date": "2026-10-09", "strike_price": 150},
            "last_quote": {"bid": 99, "ask": 100},
            "last_trade": {"price": 2.25},
            "implied_volatility": 0.42,
            "greeks": {"delta": -0.2, "gamma": 0.01, "theta": -0.03, "vega": 0.08},
            "day": {"volume": 25}, "open_interest": 400,
        }]

    def option_quotes(self, ticker, **kwargs):
        self.quote_calls.append((ticker, kwargs))
        return [self.quote]


def _reader(gateway, *, mode, decision, max_age=300):
    return PrivateGatewayOptionsReader(
        gateway, mode=mode, decision_time=decision, min_dte=30, max_dte=45,
        max_quote_age_seconds=max_age, max_contracts=2,
        clock=lambda: datetime(2026, 9, 7, 18, tzinfo=timezone.utc))


def test_market_closed_uses_recent_completed_session_eod_and_preserves_source_time():
    gateway = Gateway({"bid_price": 1.10, "ask_price": 1.25,
                       "sip_timestamp": _ns("2026-09-04T19:59:00Z")})
    reader = _reader(gateway, mode="EOD", decision="2026-09-07T12:00:00-04:00")

    frame = reader("NVDA", "2026-09-04")

    assert frame.loc[0, "trade_date"] == "2026-09-04"
    assert frame.loc[0, "quote_as_of"] == pd.Timestamp("2026-09-04T19:59:00Z")
    assert frame.loc[0, "quote_timestamp_raw"] == _ns("2026-09-04T19:59:00Z")
    assert frame.loc[0, "quote_timestamp_unit"] == "ns"
    assert frame.loc[0, "request_as_of"] != frame.loc[0, "quote_as_of"]
    assert (frame.loc[0, "bid"], frame.loc[0, "ask"]) == (1.10, 1.25)
    assert frame.loc[0, "snapshot_implied_volatility"] == 0.42
    assert gateway.quote_calls[0][1]["timestamp_gte"] == "2026-09-04T13:30:00+00:00"


def test_open_intraday_accepts_fresh_same_record_quote():
    gateway = Gateway({"bid_price": 1.15, "ask_price": 1.30,
                       "sip_timestamp": _ns("2026-09-08T14:59:30Z")})
    reader = _reader(gateway, mode="INTRADAY", decision="2026-09-08T15:00:00Z")

    frame = reader("NVDA", "2026-09-08")

    assert frame.loc[0, "trade_date"] == "2026-09-08"
    assert frame.loc[0, "quote_as_of"] == pd.Timestamp("2026-09-08T14:59:30Z")
    assert reader.last_diagnostics["quote_time_status"] == "VERIFIED"


def test_same_day_but_stale_intraday_quote_is_blocked():
    gateway = Gateway({"bid_price": 1.15, "ask_price": 1.30,
                       "sip_timestamp": _ns("2026-09-08T14:50:00Z")})
    reader = _reader(gateway, mode="INTRADAY", decision="2026-09-08T15:00:00Z")

    with pytest.raises(PrivateOptionQuoteError, match="OPTION_QUOTE_STALE"):
        reader("NVDA", "2026-09-08")


def test_missing_source_quote_timestamp_is_blocked_without_using_request_time():
    gateway = Gateway({"bid_price": 1.15, "ask_price": 1.30})
    reader = _reader(gateway, mode="EOD", decision="2026-09-07T12:00:00-04:00")

    with pytest.raises(PrivateOptionQuoteError, match="OPTION_QUOTE_TIMESTAMP_MISSING"):
        reader("NVDA", "2026-09-04")
    assert reader.last_diagnostics["quote_detail_accessible"] is True
    assert reader.last_diagnostics["bid_ask_readable"] is True
    assert reader.last_diagnostics["quote_time_verified"] is False


def test_exchange_timezone_controls_utc_midnight_and_holiday_boundary():
    window = resolve_option_quote_window("2026-09-08T00:30:00Z", "EOD", "XNYS")

    assert window.session == "2026-09-04"
    assert window.market_state == "CLOSED_RECENT_EOD"


def test_explicit_intraday_request_while_market_closed_reports_closed_without_io():
    gateway = Gateway({"bid_price": 1.15, "ask_price": 1.30,
                       "sip_timestamp": _ns("2026-09-04T19:59:00Z")})
    reader = _reader(gateway, mode="INTRADAY", decision="2026-09-07T12:00:00-04:00")

    with pytest.raises(PrivateOptionQuoteError, match="MARKET_CLOSED_INTRADAY_REQUEST"):
        reader("NVDA", "2026-09-04")
    assert gateway.snapshot_calls == []
    assert gateway.quote_calls == []
