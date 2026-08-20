from .base import BaseBrokerProvider


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


class JsonHoodClient:
    """Local read-only fixture/client for exported Hood snapshots."""

    def __init__(self, payload: dict):
        self.payload = payload

    def get_accounts(self): return self.payload.get("accounts", [])
    def get_portfolio(self): return self.payload.get("portfolio", {})
    def get_positions(self): return self.payload.get("positions", [])
    def get_equity_quote(self, symbol: str): return self.payload.get("equity_quotes", {}).get(symbol, {})
    def get_option_chain(self, symbol: str): return self.payload.get("option_chains", {}).get(symbol, [])
    def get_option_quotes(self, ids: list[str]):
        quotes = self.payload.get("option_quotes", {})
        return [quotes[i] for i in ids if i in quotes]
