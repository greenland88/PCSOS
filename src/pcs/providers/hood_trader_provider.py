from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from .base import BaseBrokerProvider


@dataclass(frozen=True)
class HoodTraderSnapshot:
    """Stable, read-only boundary for exported or live Hood data."""
    as_of: str
    accounts: Any = None
    equity_quotes: Mapping[str, Any] = None
    equity_positions: Any = None
    option_positions: Any = None
    option_chains: Mapping[str, Any] = None
    option_quotes: Mapping[str, Any] = None
    portfolio: Any = None
    event_risk: Mapping[str, Any] = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "HoodTraderSnapshot":
        return cls(as_of=str(payload.get("as_of", "")),
                   accounts=payload.get("accounts", []),
                   equity_quotes=payload.get("equity_quotes", {}),
                   equity_positions=payload.get("equity_positions", payload.get("positions", [])),
                   option_positions=payload.get("option_positions", []),
                   option_chains=payload.get("option_chains", {}),
                   option_quotes=payload.get("option_quotes", {}),
                   portfolio=payload.get("portfolio", {}),
                   event_risk=payload.get("event_risk", {}))

    def to_dict(self):
        return {"as_of": self.as_of, "accounts": self.accounts,
                "equity_quotes": self.equity_quotes, "equity_positions": self.equity_positions,
                "option_positions": self.option_positions, "option_chains": self.option_chains,
                "option_quotes": self.option_quotes, "portfolio": self.portfolio,
                "event_risk": self.event_risk}


class HoodTraderProvider(BaseBrokerProvider):
    """Read-only Hood adapter.

    The local program can use this only when a read-only client is supplied.
    Codex's built-in Hood connector is not importable here, so this class
    deliberately accepts any client with matching read methods instead of
    depending on a private SDK.
    """

    def __init__(self, client=None):
        self.client = client

    def _call(self, method: str, *args, **kwargs):
        if self.client is None:
            raise NotImplementedError(
                "HoodTraderProvider requires a local read-only client. "
                "Codex connector access is not directly importable by Python."
            )
        fn = getattr(self.client, method, None)
        if fn is None:
            raise NotImplementedError(f"read-only client does not implement {method}()")
        return fn(*args, **kwargs)

    def get_accounts(self): return self._call("get_accounts")
    def get_portfolio(self): return self._call("get_portfolio")
    def get_positions(self): return self._call("get_positions")
    def get_equity_quote(self, symbol: str): return self._call("get_equity_quote", symbol)
    def get_option_chain(self, symbol: str): return self._call("get_option_chain", symbol)
    def get_option_quotes(self, ids: list[str]): return self._call("get_option_quotes", ids)
    def get_event_risk(self, symbol: str): return self._call("get_event_risk", symbol)


class JsonHoodClient:
    """Local read-only fixture/client for exported Hood snapshots."""

    def __init__(self, payload: dict | HoodTraderSnapshot):
        self.snapshot = payload if isinstance(payload, HoodTraderSnapshot) else HoodTraderSnapshot.from_mapping(payload)
        self.payload = self.snapshot.to_dict()

    def get_accounts(self): return self.payload.get("accounts", [])
    def get_portfolio(self): return self.payload.get("portfolio", {})
    def get_positions(self): return [*self.snapshot.equity_positions, *self.snapshot.option_positions]
    def get_equity_quote(self, symbol: str): return self.payload.get("equity_quotes", {}).get(symbol, {})
    def get_option_chain(self, symbol: str): return self.payload.get("option_chains", {}).get(symbol, [])
    def get_option_quotes(self, ids: list[str]):
        quotes = self.payload.get("option_quotes", {})
        if all(i in quotes for i in ids):
            return [quotes[i] for i in ids]
        flattened = {str(q.get("option_id", q.get("id", q.get("symbol")))): q
                     for rows in quotes.values() for q in (rows if isinstance(rows, list) else [])
                     if isinstance(q, Mapping)}
        return [flattened[i] for i in ids if i in flattened]

    def get_event_risk(self, symbol: str):
        return self.snapshot.event_risk.get(symbol, self.snapshot.event_risk) if isinstance(self.snapshot.event_risk, Mapping) else None

    def freshness(self, as_of: str, *, max_age_seconds: int = 300) -> bool:
        try:
            observed = datetime.fromisoformat(self.snapshot.as_of.replace("Z", "+00:00"))
            if len(str(as_of)) <= 10:
                return observed.date().isoformat() == str(as_of)[:10]
            requested = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            if requested.tzinfo is None:
                requested = requested.replace(tzinfo=timezone.utc)
            return observed.tzinfo is not None and abs((requested - observed).total_seconds()) <= max_age_seconds
        except (TypeError, ValueError):
            return False
