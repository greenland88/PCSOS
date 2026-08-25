from pathlib import Path
import json

from pcs.data.access import SourceSpec
from pcs.data.ticker_registry import get_ticker_state


class RegistryAccess:
    def resolve_source(self, dataset, symbol):
        if dataset == "daily":
            return SourceSpec("daily", symbol, "parquet", "data/parquet/daily/symbol=QQQ", "2000-01-01", "2026-01-01", 10, "daily-v1")
        return SourceSpec("options_v2", symbol, "parquet", "data/parquet/options_v2/symbol=QQQ", "2020-01-01", "2026-01-01", 10, "options-v1")


def test_registry_is_typed_view_over_readiness(tmp_path):
    (tmp_path / "qqq.json").write_text(json.dumps({
        "DATA_READY": "YES", "OPTIONS_READY": "YES", "PIT_READY": "YES",
        "CONTRACT_SELECTION_READY": "YES", "LIFECYCLE_READY": "YES",
        "PCS_RESEARCH_READY": "YES", "reason_codes": [], "blockers": [],
        "checks": {"daily": {"coverage_end": "2026-01-01"}, "pit": {"state_ready_rows": 1}},
    }), encoding="utf-8")
    state = get_ticker_state("QQQ", access=RegistryAccess(), readiness_dir=tmp_path)
    assert state.PCS_RESEARCH_READY == "YES"
    assert state.options_coverage_start == "2020-01-01"
    assert state.PIT_ready_through == "2026-01-01"
