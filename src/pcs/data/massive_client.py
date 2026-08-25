"""Massive-compatible market-data client for the PCS private gateway.

The gateway intentionally uses the same resource shapes as Massive/Polygon,
but all requests are pinned to the configured private base URLs.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator
from urllib.parse import urlparse

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
    timeout_seconds: float = 10.0
    requests_per_minute: int = 90
    max_retries: int = 2

    @classmethod
    def from_environment(cls, env_file: str | Path = ".env") -> "GatewayConfig":
        key = os.getenv("PCS_MARKET_DATA_API_KEY")
        if not key:
            path = Path(env_file)
            if path.is_file():
                for raw_line in path.read_text(encoding="utf-8").splitlines():
                    name, separator, value = raw_line.partition("=")
                    if separator and name.strip() == "PCS_MARKET_DATA_API_KEY":
                        key = value.strip().strip("'\"")
                        break
        if not key:
            raise MarketGatewayError("PCS_MARKET_DATA_API_KEY is not set")
        return cls(api_key=key)


class SlidingWindowRateLimiter:
    """Thread-safe shared REST limiter with an injectable clock for tests."""

    def __init__(self, requests_per_minute: int, *, clock=time.monotonic, sleeper=time.sleep):
        if not 1 <= requests_per_minute <= 100:
            raise ValueError("requests_per_minute must be between 1 and 100")
        self.limit = requests_per_minute
        self.clock, self.sleeper = clock, sleeper
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self.clock()
                while self._calls and now - self._calls[0] >= 60:
                    self._calls.popleft()
                if len(self._calls) < self.limit:
                    self._calls.append(now)
                    return
                delay = max(0.01, 60 - (now - self._calls[0]))
            self.sleeper(delay)


class MassiveCompatibleClient:
    def __init__(self, config: GatewayConfig, session: requests.Session | None = None,
                 limiter: SlidingWindowRateLimiter | None = None, sleeper=time.sleep):
        self.config = config
        self.session = session or requests.Session()
        self.limiter = limiter or SlidingWindowRateLimiter(config.requests_per_minute)
        self._sleeper = sleeper

    def _private_url(self, path_or_url: str) -> str:
        base = self.config.rest_base_url.rstrip("/")
        if path_or_url.startswith(("http://", "https://")):
            expected, actual = urlparse(base), urlparse(path_or_url)
            if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
                raise MarketGatewayError("pagination URL escaped the configured private gateway")
            return path_or_url
        return base + "/" + path_or_url.lstrip("/")

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "apiKey": self.config.api_key}
        url = self._private_url(path)
        for attempt in range(self.config.max_retries + 1):
            self.limiter.acquire()
            try:
                response = self.session.get(url, params=query, timeout=self.config.timeout_seconds)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt < self.config.max_retries:
                        retry_after = float(response.headers.get("Retry-After", 2 ** attempt))
                        self._sleeper(min(max(retry_after, 0), 60))
                        continue
                response.raise_for_status()
                payload = response.json()
                break
            except (requests.RequestException, ValueError) as exc:
                if attempt < self.config.max_retries and isinstance(exc, requests.RequestException):
                    self._sleeper(min(2 ** attempt, 60))
                    continue
                safe_error = str(exc).replace(self.config.api_key, "<redacted>")
                raise MarketGatewayError(f"gateway request failed: {path}: {safe_error}") from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise MarketGatewayError(f"gateway retries exhausted: {path}")
        if not isinstance(payload, dict):
            raise MarketGatewayError(f"gateway returned a non-object payload: {path}")
        if payload.get("status") == "ERROR":
            raise MarketGatewayError(str(payload.get("error", payload)))
        return payload

    def iter_results(self, path: str, params: dict[str, Any], *, max_pages: int = 10) -> Iterator[dict[str, Any]]:
        next_url: str | None = path
        page_params = params
        for page_number in range(max_pages):
            payload = self._get(next_url, page_params)
            yield from payload.get("results", [])
            next_url = payload.get("next_url")
            if not next_url:
                return
            if page_number + 1 == max_pages:
                return
            page_params = {}

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

    def option_chain_snapshot(self, underlying_ticker: str, *, limit: int = 250,
                              max_pages: int = 10) -> list[dict[str, Any]]:
        return list(self.iter_results(f"/v3/snapshot/options/{underlying_ticker.upper()}",
                                      {"limit": limit}, max_pages=max_pages))

    def stock_snapshot(self, ticker: str) -> dict[str, Any]:
        return self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker.upper()}", {}).get("ticker", {})

    def stock_last_quote(self, ticker: str) -> dict[str, Any]:
        return self._get(f"/v2/last/nbbo/{ticker.upper()}", {}).get("results", {})

    def stock_last_trade(self, ticker: str) -> dict[str, Any]:
        return self._get(f"/v2/last/trade/{ticker.upper()}", {}).get("results", {})

    def market_status(self) -> dict[str, Any]:
        return self._get("/v1/marketstatus/now", {})

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
