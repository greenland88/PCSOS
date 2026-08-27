"""Agent-ready, read-only market-data service over the private PCS gateway."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .massive_client import GatewayConfig, MarketGatewayError, MassiveCompatibleClient


class MarketDataStatus(str, Enum):
    READY = "READY"
    NO_DATA = "NO_DATA"
    UNAVAILABLE = "UNAVAILABLE"


class MarketDataReason(str, Enum):
    DATA_AVAILABLE = "DATA_AVAILABLE"
    DATA_NOT_FOUND = "DATA_NOT_FOUND"
    GATEWAY_UNAVAILABLE = "GATEWAY_UNAVAILABLE"
    PARTIAL_DATA = "PARTIAL_DATA"


class MarketDataResult(BaseModel):
    module: str = "pcs.market_data_gateway"
    version: str = "1.0"
    symbol: str
    as_of: str
    status: MarketDataStatus
    data_timestamp: str
    calculation_version: str = "private-gateway-v1"
    run_id: str
    request_id: str
    reason_codes: list[MarketDataReason] = Field(default_factory=list)
    data: Any = None
    source: str = "PCS_PRIVATE_MASSIVE_COMPATIBLE_GATEWAY"


class MarketDataService:
    """Compact deterministic reads for agents; never writes canonical PCS data."""

    def __init__(self, client: MassiveCompatibleClient | None = None):
        self.client = client or MassiveCompatibleClient(GatewayConfig.from_environment())

    def _result(self, symbol: str, data: Any, *, as_of: str | None, run_id: str | None,
                request_id: str | None) -> MarketDataResult:
        now = datetime.now(timezone.utc).isoformat()
        has_data = bool(data)
        return MarketDataResult(
            symbol=symbol.upper(), as_of=as_of or now,
            status=MarketDataStatus.READY if has_data else MarketDataStatus.NO_DATA,
            data_timestamp=now, run_id=run_id or f"run_{uuid4().hex}",
            request_id=request_id or f"req_{uuid4().hex}",
            reason_codes=[MarketDataReason.DATA_AVAILABLE if has_data else MarketDataReason.DATA_NOT_FOUND],
            data=data,
        )

    def _unavailable(self, symbol: str, exc: Exception, *, as_of: str | None,
                     run_id: str | None, request_id: str | None) -> MarketDataResult:
        now = datetime.now(timezone.utc).isoformat()
        return MarketDataResult(
            symbol=symbol.upper(), as_of=as_of or now, status=MarketDataStatus.UNAVAILABLE,
            data_timestamp=now, run_id=run_id or f"run_{uuid4().hex}",
            request_id=request_id or f"req_{uuid4().hex}",
            reason_codes=[MarketDataReason.GATEWAY_UNAVAILABLE],
            data={"error_type": type(exc).__name__},
        )

    def get_stock_realtime(self, symbol: str, *, run_id: str | None = None,
                           request_id: str | None = None) -> MarketDataResult:
        symbol = symbol.upper()
        data: dict[str, Any] = {}
        unavailable: list[str] = []
        for name, fetch in (("snapshot", self.client.stock_snapshot),
                            ("last_quote", self.client.stock_last_quote),
                            ("last_trade", self.client.stock_last_trade)):
            try:
                data[name] = fetch(symbol)
            except MarketGatewayError:
                unavailable.append(name)
        if not data:
            return self._unavailable(symbol, MarketGatewayError("all stock endpoints unavailable"),
                                     as_of=None, run_id=run_id, request_id=request_id)
        result = self._result(symbol, {**data, "unavailable_components": unavailable},
                              as_of=None, run_id=run_id, request_id=request_id)
        if unavailable:
            result.reason_codes.append(MarketDataReason.PARTIAL_DATA)
        return result

    def get_option_chain_realtime(self, symbol: str, *, limit: int = 250, max_pages: int = 4,
                                  run_id: str | None = None,
                                  request_id: str | None = None) -> MarketDataResult:
        symbol = symbol.upper()
        try:
            rows = self.client.option_chain_snapshot(symbol, limit=limit, max_pages=max_pages)
            return self._result(symbol, rows, as_of=None, run_id=run_id, request_id=request_id)
        except MarketGatewayError as exc:
            return self._unavailable(symbol, exc, as_of=None, run_id=run_id, request_id=request_id)

    def get_stock_daily(self, symbol: str, start_date: str, end_date: str, *, limit: int = 50000,
                        run_id: str | None = None, request_id: str | None = None) -> MarketDataResult:
        symbol = symbol.upper()
        try:
            frame = self.client.stock_daily(symbol, start_date, end_date, limit=limit)
            rows = frame.assign(date=frame["date"].dt.strftime("%Y-%m-%d")).to_dict("records")
            return self._result(symbol, rows, as_of=end_date, run_id=run_id, request_id=request_id)
        except Exception as exc:
            return self._unavailable(symbol, exc, as_of=end_date, run_id=run_id, request_id=request_id)
