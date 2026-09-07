"""Timestamp-preserving options reader over the existing private gateway client."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from .massive_client import (REST_BASE_URL, GatewayConfig, MassiveCompatibleClient,
                             MarketGatewayError)
from pcs.pool.modes import OptionQuoteWindow, resolve_option_quote_window


class PrivateOptionQuoteError(RuntimeError):
    """Fail-closed quote error with credential-free diagnostic evidence."""

    def __init__(self, reason_code: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.diagnostics = diagnostics or {}


def decode_quote_timestamp(value: Any) -> tuple[pd.Timestamp, str]:
    """Decode a source timestamp while retaining its original unit separately."""
    if value is None or isinstance(value, bool):
        raise PrivateOptionQuoteError("OPTION_QUOTE_TIMESTAMP_MISSING")
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if isinstance(value, (int, float)):
        magnitude = abs(float(value))
        unit = ("ns" if magnitude >= 1e17 else "us" if magnitude >= 1e14
                else "ms" if magnitude >= 1e11 else "s")
        stamp = pd.to_datetime(value, unit=unit, utc=True, errors="coerce")
    else:
        unit = "iso8601"
        stamp = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(stamp):
        raise PrivateOptionQuoteError("OPTION_QUOTE_TIMESTAMP_INVALID")
    return pd.Timestamp(stamp), unit


class PrivateGatewayOptionsReader:
    """Pool ``options_reader`` adapter; all I/O remains owned by one existing client."""

    def __init__(self, client: MassiveCompatibleClient, *, mode: str, decision_time: Any,
                 exchange_calendar: str = "XNYS", min_dte: int = 30, max_dte: int = 45,
                 max_quote_age_seconds: float = 300.0, snapshot_page_limit: int = 250,
                 snapshot_max_pages: int = 2, max_contracts: int = 24,
                 clock: Callable[[], datetime] | None = None):
        if client.config.rest_base_url.rstrip("/") != REST_BASE_URL:
            raise ValueError("PRIVATE_GATEWAY_ENDPOINT_REQUIRED")
        if min_dte < 0 or max_dte < min_dte:
            raise ValueError("OPTION_DTE_WINDOW_INVALID")
        if max_quote_age_seconds <= 0 or max_contracts < 1:
            raise ValueError("OPTION_READER_BOUND_INVALID")
        self.client = client
        self.mode = str(mode).upper()
        self.decision_time = decision_time
        self.exchange_calendar = exchange_calendar
        self.min_dte, self.max_dte = int(min_dte), int(max_dte)
        self.max_quote_age_seconds = float(max_quote_age_seconds)
        self.snapshot_page_limit = int(snapshot_page_limit)
        self.snapshot_max_pages = int(snapshot_max_pages)
        self.max_contracts = int(max_contracts)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.last_diagnostics: dict[str, Any] = {}

    def quote_window(self) -> OptionQuoteWindow:
        return resolve_option_quote_window(
            self.decision_time, self.mode, self.exchange_calendar)

    def _validate_quote_time(self, raw_timestamp: Any, window: OptionQuoteWindow) -> tuple[pd.Timestamp, str]:
        stamp, unit = decode_quote_timestamp(raw_timestamp)
        start, end = pd.Timestamp(window.request_start_utc), pd.Timestamp(window.request_end_utc)
        if stamp < start or stamp > end:
            raise PrivateOptionQuoteError("OPTION_QUOTE_OUTSIDE_REQUEST_WINDOW", {
                "quote_as_of": stamp.isoformat(), "window": [start.isoformat(), end.isoformat()]})
        if window.market_state == "OPEN_INTRADAY":
            age = (pd.Timestamp(window.decision_time_utc) - stamp).total_seconds()
            if age < 0:
                raise PrivateOptionQuoteError("OPTION_QUOTE_FROM_FUTURE")
            if age > self.max_quote_age_seconds:
                raise PrivateOptionQuoteError("OPTION_QUOTE_STALE", {
                    "quote_as_of": stamp.isoformat(), "age_seconds": age,
                    "max_age_seconds": self.max_quote_age_seconds})
        return stamp, unit

    def __call__(self, symbol: str, trade_date: Any) -> pd.DataFrame:
        symbol = str(symbol).strip().upper()
        window = self.quote_window()
        self.last_diagnostics = {"symbol": symbol, "quote_window": asdict(window),
                                 "endpoint": REST_BASE_URL, "status": "STARTED",
                                 "snapshot_accessible": False,
                                 "quote_detail_accessible": False,
                                 "bid_ask_readable": False,
                                 "quote_time_verified": False,
                                 "evaluation_status": "NOT_RUN_BY_READER"}
        if window.market_state == "MARKET_CLOSED_INTRADAY_REQUEST":
            self.last_diagnostics["status"] = "BLOCKED"
            self.last_diagnostics["reason_code"] = "MARKET_CLOSED_INTRADAY_REQUEST"
            raise PrivateOptionQuoteError("MARKET_CLOSED_INTRADAY_REQUEST", self.last_diagnostics)
        if str(pd.Timestamp(trade_date).date()) != window.session:
            raise PrivateOptionQuoteError("OPTIONS_QUOTE_SESSION_MISMATCH", self.last_diagnostics)

        session = pd.Timestamp(window.session)
        expiry_start = str((session + pd.Timedelta(days=self.min_dte)).date())
        expiry_end = str((session + pd.Timedelta(days=self.max_dte)).date())
        try:
            snapshots = self.client.option_chain_snapshot(
                symbol, limit=self.snapshot_page_limit, max_pages=self.snapshot_max_pages,
                require_complete=False, contract_type="put",
                expiration_date_gte=expiry_start, expiration_date_lte=expiry_end)
        except MarketGatewayError as exc:
            self.last_diagnostics.update(status="BLOCKED",
                                         reason_code="PRIVATE_GATEWAY_OPTIONS_UNAVAILABLE")
            raise PrivateOptionQuoteError(
                "PRIVATE_GATEWAY_OPTIONS_UNAVAILABLE", self.last_diagnostics) from exc
        self.last_diagnostics["snapshot_accessible"] = True
        eligible = []
        for snapshot in snapshots:
            details = snapshot.get("details") or {}
            expiration = pd.to_datetime(details.get("expiration_date"), errors="coerce")
            if (str(details.get("contract_type", "")).lower() != "put" or
                    pd.isna(expiration) or not expiry_start <= str(expiration.date()) <= expiry_end or
                    not details.get("ticker")):
                continue
            eligible.append(snapshot)
            if len(eligible) >= self.max_contracts:
                break
        if not eligible:
            self.last_diagnostics.update(status="BLOCKED",
                                         reason_code="OPTIONS_CONTRACTS_NOT_FOUND")
            raise PrivateOptionQuoteError("OPTIONS_CONTRACTS_NOT_FOUND", self.last_diagnostics)

        output = []
        for snapshot in eligible:
            details = snapshot.get("details") or {}
            contract_ticker = str(details["ticker"]).upper()
            try:
                quotes = self.client.option_quotes(
                    contract_ticker, timestamp_gte=window.request_start_utc,
                    timestamp_lte=window.request_end_utc, limit=1)
            except MarketGatewayError as exc:
                self.last_diagnostics.update(
                    status="BLOCKED", reason_code="PRIVATE_GATEWAY_OPTIONS_UNAVAILABLE",
                    contract_ticker=contract_ticker)
                raise PrivateOptionQuoteError(
                    "PRIVATE_GATEWAY_OPTIONS_UNAVAILABLE", self.last_diagnostics) from exc
            self.last_diagnostics["quote_detail_accessible"] = True
            if not quotes:
                self.last_diagnostics.update(
                    status="BLOCKED", reason_code="OPTION_QUOTE_NOT_FOUND",
                    contract_ticker=contract_ticker)
                raise PrivateOptionQuoteError("OPTION_QUOTE_NOT_FOUND", {
                    **self.last_diagnostics, "contract_ticker": contract_ticker})
            quote = quotes[0]
            bid, ask = quote.get("bid_price"), quote.get("ask_price")
            try:
                bid, ask = float(bid), float(ask)
            except (TypeError, ValueError):
                self.last_diagnostics.update(
                    status="BLOCKED", reason_code="OPTION_QUOTE_PRICES_MISSING",
                    contract_ticker=contract_ticker)
                raise PrivateOptionQuoteError("OPTION_QUOTE_PRICES_MISSING", {
                    **self.last_diagnostics, "contract_ticker": contract_ticker})
            if bid < 0 or ask < bid:
                self.last_diagnostics.update(
                    status="BLOCKED", reason_code="OPTION_QUOTE_PRICES_INVALID",
                    contract_ticker=contract_ticker)
                raise PrivateOptionQuoteError("OPTION_QUOTE_PRICES_INVALID", {
                    **self.last_diagnostics, "contract_ticker": contract_ticker})
            self.last_diagnostics["bid_ask_readable"] = True
            try:
                stamp, unit = self._validate_quote_time(quote.get("sip_timestamp"), window)
            except PrivateOptionQuoteError as exc:
                self.last_diagnostics.update(
                    status="BLOCKED", reason_code=exc.reason_code,
                    contract_ticker=contract_ticker)
                raise PrivateOptionQuoteError(exc.reason_code, {
                    **self.last_diagnostics, **exc.diagnostics}) from exc
            greeks = snapshot.get("greeks") or {}
            implied_volatility = snapshot.get("implied_volatility")
            missing_greeks = [name for name in ("delta", "gamma", "theta", "vega")
                              if greeks.get(name) is None]
            requested_at = pd.Timestamp(self.clock())
            if requested_at.tzinfo is None:
                requested_at = requested_at.tz_localize("UTC")
            else:
                requested_at = requested_at.tz_convert("UTC")
            output.append({
                "symbol": symbol, "contract_ticker": contract_ticker,
                "trade_date": window.session, "quote_as_of": stamp,
                "quote_timestamp_raw": quote.get("sip_timestamp"),
                "quote_timestamp_unit": unit, "request_as_of": requested_at,
                "expiration_date": str(pd.Timestamp(details["expiration_date"]).date()),
                "strike": details.get("strike_price"), "call_put": "P",
                "last": (snapshot.get("last_trade") or {}).get("price"),
                "bid": bid, "ask": ask,
                "bid_iv": None, "ask_iv": None,
                "snapshot_implied_volatility": implied_volatility,
                "iv_source": "snapshot.implied_volatility" if implied_volatility is not None else None,
                "iv_status": "AVAILABLE" if implied_volatility is not None else "MISSING",
                "open_interest": snapshot.get("open_interest"),
                "volume": (snapshot.get("day") or {}).get("volume"),
                "delta": greeks.get("delta"), "gamma": greeks.get("gamma"),
                "vega": greeks.get("vega"), "theta": greeks.get("theta"),
                "rho": greeks.get("rho"),
                "greeks_source": "snapshot.greeks" if greeks else None,
                "greeks_missing": tuple(missing_greeks),
                "quote_source": "quotes.sip_timestamp_bid_ask",
            })
        frame = pd.DataFrame(output)
        if frame.duplicated(["expiration_date", "strike", "call_put"]).any():
            raise PrivateOptionQuoteError("DUPLICATE_OPTION_CONTRACT_KEY", self.last_diagnostics)
        frame.attrs.update({"source": "private_massive_gateway", "quote_window": asdict(window),
                            "request_time_is_quote_time": False})
        self.last_diagnostics.update(status="READY", returned_contracts=len(frame),
                                     quote_time_verified=True, quote_time_status="VERIFIED")
        return frame


def build_private_gateway_options_reader(*, mode: str, decision_time: Any,
                                         env_file: str = ".env", **kwargs) -> PrivateGatewayOptionsReader:
    """Build the callback expected by the Pool runner without creating another client type."""
    config = GatewayConfig.from_environment(env_file)
    if config.rest_base_url.rstrip("/") != REST_BASE_URL:
        raise MarketGatewayError("private gateway endpoint mismatch")
    return PrivateGatewayOptionsReader(
        MassiveCompatibleClient(config), mode=mode, decision_time=decision_time, **kwargs)


__all__ = ["PrivateGatewayOptionsReader", "PrivateOptionQuoteError",
           "build_private_gateway_options_reader", "decode_quote_timestamp"]
