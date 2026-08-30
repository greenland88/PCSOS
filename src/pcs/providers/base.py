from abc import ABC, abstractmethod


class BaseBrokerProvider(ABC):
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


class CoveredCallLiveProvider(ABC):
    """Read-only account/market contract for production covered-call decisions."""

    @abstractmethod
    def get_underlying_quote(self, symbol: str, as_of: str): ...

    @abstractmethod
    def get_share_position(self, symbol: str, as_of: str): ...

    @abstractmethod
    def get_open_option_positions(self, symbol: str, as_of: str): ...

    @abstractmethod
    def get_call_chain(self, symbol: str, expiration_window: tuple[int, int], as_of: str): ...

    @abstractmethod
    def get_event_risk(self, symbol: str, as_of: str): ...

    @abstractmethod
    def check_liquidity(self, symbol: str, contract: object) -> dict: ...

    @abstractmethod
    def check_ticker_risk(self, symbol: str, contract: object) -> dict: ...

    @abstractmethod
    def check_assignment(self, symbol: str, contract: object) -> dict: ...


class HistoricalMarketProvider(ABC):
    @abstractmethod
    def get_daily_ohlcv(self, symbols: list[str], start_date: str, end_date: str): ...


class HistoricalOptionsProvider(ABC):
    @abstractmethod
    def get_option_history(self, symbol: str, start_date: str, end_date: str): ...


class NewsProvider(ABC):
    @abstractmethod
    def get_news(self, symbols: list[str], start_date: str, end_date: str): ...
