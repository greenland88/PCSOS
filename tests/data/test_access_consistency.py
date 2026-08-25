import json
import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess, DataAccessError, SourceSpec
from pcs.data.ticker_registry import get_ticker_state


def test_explicit_routing_modes_fail_closed_for_canonical_physical_override(tmp_path):
    with pytest.raises(DataAccessError, match="CANONICAL_MODE_REQUIRES_DEFAULT_MANIFEST"):
        PCSDataAccess(manifest_path=tmp_path / "manifest.csv", routing_mode="canonical")
    isolated = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    assert isolated.routing_mode == "isolated"


def test_registry_rejects_readiness_from_different_options_identity(tmp_path):
    class FakeAccess:
        def resolve_source(self, dataset, symbol):
            return SourceSpec("options_v3" if dataset == "options" else "daily", symbol, "fixture", "fixture", "2020-01-01", "2026-01-01", 1, "manifest-v3", "1")

    readiness = {"checks": {"options": {"options_identity": {"dataset": "options_v2", "source_version": "manifest-v2"}}},
                 "OPTIONS_READY": "YES", "PCS_RESEARCH_READY": "YES"}
    path = tmp_path / "META.json"; path.write_text(json.dumps(readiness), encoding="utf-8")
    state = get_ticker_state("META", access=FakeAccess(), readiness_dir=tmp_path)
    assert state.PCS_RESEARCH_READY == "NO"
    assert "STALE_READINESS_SOURCE_IDENTITY" in state.reason_codes
