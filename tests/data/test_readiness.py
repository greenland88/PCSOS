import json
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from pcs.data.access import DataAccessError, PCSDataAccess
from pcs.data.readiness import discover_lifecycle_smoke_case, execute_lifecycle_smoke


def test_explicit_options_v2_does_not_fallback_to_legacy(tmp_path):
    access = PCSDataAccess(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet", source_routes={})
    with pytest.raises(DataAccessError, match="legacy_fallback_used=NO"):
        access.resolve_source("options_v2", "COST")


def test_lifecycle_smoke_discovery_is_deterministic_and_complete():
    access = PCSDataAccess()
    first, first_meta = discover_lifecycle_smoke_case(access, "COST", start_date="2020-01-01", end_date="2020-12-31")
    second, second_meta = discover_lifecycle_smoke_case(access, "COST", start_date="2020-01-01", end_date="2020-12-31")
    assert first is not None and second is not None
    assert first.identity == second.identity
    assert first_meta == second_meta
    result = execute_lifecycle_smoke(access, first)
    assert result["status"] == "COMPLETE"
    assert result["exit_reason"] in {"PROFIT_CAPTURE", "STOP", "TIME_EXIT"}
    assert result["case_identity"] == first.identity


def test_cross_ticker_rerun_is_idempotent_and_read_only():
    access = PCSDataAccess()
    before = {}
    after = {}
    manifest_paths = {}
    tickers = ("QQQ", "AMZN", "TSLA", "COST")
    def run(ticker):
        one, _ = discover_lifecycle_smoke_case(access, ticker, start_date="2020-01-01", end_date="2020-02-10")
        two, _ = discover_lifecycle_smoke_case(access, ticker, start_date="2020-01-01", end_date="2020-02-10")
        return ticker, one, two
    for ticker in tickers:
        evidence = __import__("pcs.data.readiness", fromlist=["canonical_route_evidence"]).canonical_route_evidence(access, ticker)
        manifest = Path(evidence["resolved_manifest"])
        manifest_paths[ticker] = manifest
        before[ticker] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=4) as pool:
        reruns = list(pool.map(run, tickers))
    for ticker, one, two in reruns:
        assert one is not None and two is not None
        assert one.identity == two.identity
        assert execute_lifecycle_smoke(access, one) == execute_lifecycle_smoke(access, two)
        after[ticker] = hashlib.sha256(manifest_paths[ticker].read_bytes()).hexdigest()
    assert before == after
