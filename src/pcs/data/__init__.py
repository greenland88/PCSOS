from .daily_fetcher import YahooDailyFetcher, ensure_daily_data, update_live_daily
from .daily_provider import DailyDataError, DailyDataProvider, normalize_daily_frame
from .massive_client import GatewayConfig, MarketGatewayError, MassiveCompatibleClient
from .market_data_service import MarketDataResult, MarketDataService, MarketDataStatus
from .universe import load_market_universe, merge_symbols
from .covered_call_readiness import (CoveredCallReadiness, resolve_ticker_data_readiness,
                                      resolve_covered_call_universe)
from .control_plane import (CanonicalDataCatalog, CoveragePlan, ImportCoordinator, ImportEngine, MarketDataControlPlane, MarketDataRequirements,
                            MarketDataResult, MarketDataSourceAdapter, SourceResolver,
                            RequestLedger, default_import_handlers, ensure_market_data, get_market_data_status, repair_daily_session, require_market_data)

__all__ = ["DailyDataError", "DailyDataProvider", "DailySnapshotImportResult", "OptionArchiveImportResult", "GatewayConfig", "MarketDataResult", "MarketDataService", "MarketDataStatus", "MarketGatewayError", "MassiveCompatibleClient", "YahooDailyFetcher", "ensure_daily_data", "find_daily_snapshots", "find_latest_daily_snapshot", "import_daily_snapshot", "import_option_archives", "load_market_universe", "merge_symbols", "normalize_daily_frame", "update_live_daily", "CoveredCallReadiness", "resolve_ticker_data_readiness", "resolve_covered_call_universe"]


def __getattr__(name):
    if name in {"DailySnapshotImportResult", "find_daily_snapshots", "find_latest_daily_snapshot", "import_daily_snapshot"}:
        from .import_daily_snapshot import DailySnapshotImportResult, find_daily_snapshots, find_latest_daily_snapshot, import_daily_snapshot

        return {
            "DailySnapshotImportResult": DailySnapshotImportResult,
            "find_daily_snapshots": find_daily_snapshots,
            "find_latest_daily_snapshot": find_latest_daily_snapshot,
            "import_daily_snapshot": import_daily_snapshot,
        }[name]
    if name in {"OptionArchiveImportResult", "import_option_archives"}:
        from .import_option_archives import OptionArchiveImportResult, import_option_archives

        return {"OptionArchiveImportResult": OptionArchiveImportResult, "import_option_archives": import_option_archives}[name]
    raise AttributeError(name)
