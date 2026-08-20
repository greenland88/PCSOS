from datetime import datetime, timezone

from pcs.data.storage import ParquetStore
from pcs.providers.base import HistoricalMarketProvider


class DailyMarketDataCollector:
    def __init__(self, provider: HistoricalMarketProvider, store: ParquetStore):
        self.provider = provider
        self.store = store

    def collect(self, symbols: list[str], start_date: str, end_date: str):
        rows = self.provider.get_daily_ohlcv(symbols, start_date, end_date)
        stamped = [dict(row, collected_at=datetime.now(timezone.utc).isoformat()) for row in rows]
        return self.store.write_snapshot("equities", stamped, name="daily_ohlcv")
