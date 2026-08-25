"""Massive-compatible market-data client for the PCS private gateway.

The gateway intentionally uses the same resource shapes as Massive/Polygon,
but all requests are pinned to the configured private base URLs.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

import pandas as pd
import requests

from .daily_provider import DailyDataError, normalize_daily_frame


REST_BASE_URL = "http://38.76.185.106:3000"
WEBSOCKET_BASE_URL = "ws://38.76.185.106:3000/stocks"


class MarketGatewayError(RuntimeError):
    """Raised when the private market-data gateway cannot satisfy a request."""


@dataclass(frozen=True)
class GatewayConfig:
    api_key: str
    rest_base_url: str = REST_BASE_URL
    websocket_base_url: str = WEBSOCKET_BASE_URL
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> "GatewayConfig":
        key = os.getenv("PCS_MARKET_DATA_API_KEY")
        if not key:
            raise MarketGatewayError("PCS_MARKET_DATA_API_KEY is not set")
        return cls(api_key=key)


class MassiveCompatibleClient:
    def __init__(self, config: GatewayConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "apiKey": self.config.api_key}
        url = self.config.rest_base_url.rstrip("/") + "/" + path.lstrip("/")
        try:
            response = self.session.get(url, params=query, timeout=self.config.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            safe_error = str(exc).replace(self.config.api_key, "<redacted>")
            raise MarketGatewayError(f"gateway request failed: {path}: {safe_error}") from exc
        if not isinstance(payload, dict):
            raise MarketGatewayError(f"gateway returned a non-object payload: {path}")
        if payload.get("status") == "ERROR":
            raise MarketGatewayError(str(payload.get("error", payload)))
        return payload

    def stock_daily(self, ticker: str, start_date: str, end_date: str, *, limit: int = 120) -> pd.DataFrame:
        payload = self._get(f"/v2/aggs/ticker/{ticker.upper()}/range/1/day/{start_date}/{end_date}", {
            "adjusted": "true", "sort": "asc", "limit": limit,
        })
        rows = [{"date": row.get("t"), "open": row.get("o"), "high": row.get("h"),
                 "low": row.get("l"), "close": row.get("c"), "volume": row.get("v")}
                for row in payload.get("results", [])]
        if not rows:
            raise DailyDataError(f"gateway returned no daily data for {ticker}")
        frame = pd.DataFrame(rows)
        frame["date"] = pd.to_datetime(frame["date"], unit="ms", errors="coerce")
        return normalize_daily_frame(frame)

    def option_contracts(self, underlying_ticker: str, *, contract_type: str = "call",
                         expiration_date_gte: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"underlying_ticker": underlying_ticker.upper(),
                                  "contract_type": contract_type, "limit": limit}
        if expiration_date_gte:
            params["expiration_date.gte"] = expiration_date_gte
        return self._get("/v3/reference/options/contracts", params).get("results", [])

    async def stock_stream(self, subscriptions: list[str]) -> AsyncIterator[dict[str, Any]]:
        try:
            import websockets
        except ImportError as exc:
            raise MarketGatewayError("websockets is required for stock_stream") from exc
        async with websockets.connect(self.config.websocket_base_url) as socket:
            await socket.send(json.dumps({"action": "auth", "params": self.config.api_key}))
            auth = json.loads(await socket.recv())
            if any(item.get("status") == "auth_failed" for item in (auth if isinstance(auth, list) else [auth])):
                raise MarketGatewayError(f"websocket authentication failed: {auth}")
            await socket.send(json.dumps({"action": "subscribe", "params": ",".join(subscriptions)}))
            async for message in socket:
                payload = json.loads(message)
                for item in payload if isinstance(payload, list) else [payload]:
                    yield item
