from abc import ABC, abstractmethod


class BaseBrokerProvider(ABC):
    def get_trading_sessions(self):
        """Return canonical exchange sessions when historical gating needs them."""
        return None

    @abstractmethod
    def get_accounts(self): ...

    @abstractmethod
    def get_portfolio(self): ...

    @abstractmethod
    def get_positions(self): ...

    @abstractmethod
    def get_equity_quote(self, symbol: str): ...

    @abstractmethod
    def get_option_chain(self, symbol: str): ...

    @abstractmethod
    def get_option_quotes(self, ids: list[str]): ...


class HistoricalMarketProvider(ABC):
    @abstractmethod
    def get_daily_ohlcv(self, symbols: list[str], start_date: str, end_date: str): ...


class HistoricalOptionsProvider(ABC):
    @abstractmethod
    def get_option_history(self, symbol: str, start_date: str, end_date: str): ...


class NewsProvider(ABC):
    @abstractmethod
    def get_news(self, symbols: list[str], start_date: str, end_date: str): ...
