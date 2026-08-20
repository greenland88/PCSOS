from datetime import datetime, timezone

from pcs.data.storage import ParquetStore
from pcs.providers.base import BaseBrokerProvider


class OptionChainSnapshotCollector:
    """Collects current option-chain snapshots. It never assumes historical chain access."""

    def __init__(self, provider: BaseBrokerProvider, store: ParquetStore):
        self.provider = provider
        self.store = store

    def collect_symbol(self, symbol: str):
        chain = self.provider.get_option_chain(symbol)
        rows = chain if isinstance(chain, list) else getattr(chain, "quotes", chain)
        normalized = []
        now = datetime.now(timezone.utc)
        for row in rows:
            item = row if isinstance(row, dict) else row.model_dump()
            item["ticker"] = item.get("ticker") or item.get("symbol") or symbol
            item["snapshot_at"] = now.isoformat()
            normalized.append(item)
        return self.store.write_snapshot("options", normalized, as_of=now, name=f"{symbol}_option_chain")
