from .base import BaseBrokerProvider


class CSVProvider(BaseBrokerProvider):
    """Future read-only CSV-backed provider hook."""

    def __init__(self, root: str):
        self.root = root

    def get_accounts(self): return {}
    def get_portfolio(self): return {}
    def get_positions(self): return []
    def get_equity_quote(self, symbol: str): raise NotImplementedError
    def get_option_chain(self, symbol: str): raise NotImplementedError
    def get_option_quotes(self, ids: list[str]): raise NotImplementedError

