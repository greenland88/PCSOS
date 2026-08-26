from pcs.data.control_plane import CanonicalDataCatalog, ImportCoordinator, ImportEngine, MarketDataControlPlane, MarketDataRequirements, PlanAction, RequestLedger, SourceResolver, repair_daily_session
import pandas as pd


def test_source_resolver_does_not_authorize_massive_options_contracts():
    sources = SourceResolver().resolve("options")
    assert all(item["source_id"] != "massive_option_contracts" for item in sources)


def test_registered_sources_have_minimum_audit_metadata():
    registry = SourceResolver().registry
    for dataset, sources in registry.get("sources", {}).items():
        for source in sources:
            assert {"source_id", "type", "enabled", "authorized", "adapter", "capabilities", "priority"}.issubset(source), (dataset, source)


def test_adapter_spec_fails_closed_for_reference_only_source():
    resolver = SourceResolver()
    try:
        resolver.adapter_spec("options", "massive_option_contracts")
    except Exception as exc:
        assert "SOURCE_NOT_AUTHORIZED" in str(exc)
    else:
        raise AssertionError("reference-only provider must not be executable")


def test_source_resolution_is_enabled_and_priority_ordered():
    resolver = SourceResolver()
    sources = resolver.resolve("daily")
    assert sources and sources[0]["source_id"] == "purchased_qfq"
    assert all(item["enabled"] and item["authorized"] for item in sources)


def test_registered_adapter_can_be_loaded():
    adapter = SourceResolver().load_adapter("daily", "private_massive_gateway")
    assert adapter.__name__ == "MassiveCompatibleClient"


def test_enabled_registry_adapters_pass_contract_validation():
    results = SourceResolver().validate_registry()
    assert results and all(item["status"] == "READY" for item in results)


def test_status_is_machine_readable_and_fail_closed(tmp_path):
    access = __import__("pcs.data.access", fromlist=["PCSDataAccess"]).PCSDataAccess.isolated(
        manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    result = MarketDataControlPlane(access=access).get_market_data_status(
        MarketDataRequirements("PLTR", datasets=("daily",)))
    assert result.status == "PARTIAL"
    assert result.module == "pcs.data.control_plane"
    assert result.coverage_plan["actions"][0]["action"] == PlanAction.REPAIR_DAILY_FROM_GATEWAY


def test_require_blocks_partial_data(tmp_path):
    access = __import__("pcs.data.access", fromlist=["PCSDataAccess"]).PCSDataAccess.isolated(
        manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    try:
        MarketDataControlPlane(access=access).require_market_data("PLTR", {"datasets": ("daily",)})
    except Exception as exc:
        assert "DATA_NOT_READY" in str(exc)
    else:
        raise AssertionError("partial canonical data must not pass the consumer gate")


def test_requirement_mapping_and_request_reuse(tmp_path):
    req = MarketDataRequirements.from_mapping("PLTR", {"start": "2018-01-01", "end": "2026-08-26", "datasets": {"daily": {"required": True}, "options": {"required": False}}})
    assert req.datasets == ("daily",)
    ledger = RequestLedger(tmp_path / "requests.jsonl")
    ledger.record(source_id="gateway", symbol="PLTR", dataset="daily", requested_start=req.required_start, requested_end=req.required_end, query_version="1", status="API_COMPLETE")
    assert ledger.find_completed("gateway", "PLTR", "daily", req.required_start, req.required_end) is not None


def test_import_engine_stages_outside_canonical_and_quarantines_failed_promotion(tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    engine = ImportEngine(access=access, staging_root=tmp_path / "staging", catalog=CanonicalDataCatalog(tmp_path / "catalog.parquet"), ledger=RequestLedger(tmp_path / "ledger.jsonl"))
    frame = pd.DataFrame({"symbol": ["TEST"], "date": pd.to_datetime(["2025-01-02"]), "open": [1.], "high": [1.], "low": [1.], "close": [1.], "volume": [1.]})
    staged = engine.stage(frame, symbol="TEST", dataset="daily", partition="year=2025", source_id="fixture")
    assert str(tmp_path / "parquet") not in staged["path"]
    result = engine.promote(staged, source_version="fixture-v1")
    assert result["status"] == "IMPORTED"


def test_import_coordinator_dispatches_only_missing_dataset(tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    seen = []
    cp = MarketDataControlPlane(access=access)
    result = ImportCoordinator(cp, handlers={"daily": lambda plan: seen.append(plan.requirements.symbol)}).run({"start": "2018-01-01", "end": "2025-01-01", "datasets": {"daily": {"required": True}}}, "TEST")
    assert seen == ["TEST"] and result["outcomes"][0]["status"] == "IMPORTED"


def test_current_options_handler_reuses_when_requested_range_is_covered(tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    # No canonical source means the handler remains lazy and does not contact
    # the injected client during planning; this verifies the public factory is
    # constructible without provider credentials.
    class Client:
        def fetch_options_range(self, *args): raise AssertionError("must not fetch during planning")
    handlers = __import__("pcs.data.control_plane", fromlist=["default_import_handlers"]).default_import_handlers(clickhouse_client=Client(), access=access)
    assert "options" in handlers and callable(handlers["options"])


def test_options_handler_auto_wiring_reports_missing_clickhouse_credentials(tmp_path, monkeypatch):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    handlers = __import__("pcs.data.control_plane", fromlist=["default_import_handlers"]).default_import_handlers(access=access)
    result = handlers["options"](MarketDataRequirements("NVDL", "2023-01-01", "2023-12-31", ("options",)))
    assert result["status"] == "BLOCKED"
    assert result["reason_codes"] == ["CLICKHOUSE_CREDENTIALS_MISSING"]


def test_options_handler_uses_registered_client_and_exact_coverage(tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    class Client:
        def fetch_options_coverage(self, symbol, start, end):
            assert (symbol, start, end) == ("NVDL", "2023-09-26", "2023-12-31")
            return {"symbol": symbol, "requested_start": start, "requested_end": end,
                    "physical_rows": 1, "unique_contract_keys": 1, "call_rows": 1,
                    "put_rows": 0, "source_table": "test.options", "status": "READY", "reason_codes": []}
        def fetch_options_range(self, symbol, start, end):
            return pd.DataFrame({"symbol": [symbol], "trade_date": pd.to_datetime([start]),
                "expiration_date": pd.to_datetime(["2023-10-27"]), "strike": [100.], "call_put": ["c"],
                "last": [1.], "bid": [.9], "ask": [1.1], "bid_iv": [.2], "ask_iv": [.21],
                "open_interest": [100], "volume": [10], "delta": [.2], "gamma": [.01],
                "vega": [.1], "theta": [-.1], "rho": [.01]})
    handlers = __import__("pcs.data.control_plane", fromlist=["default_import_handlers"]).default_import_handlers(clickhouse_client=Client(), access=access)
    result = handlers["options"](MarketDataRequirements("NVDL", "2023-09-26", "2023-12-31", ("options",)))
    assert result["status"] == "IMPORTED"
    assert result["provider_coverage"]["symbol"] == "NVDL"
    assert len(access.read("options", "NVDL")) == 1


def test_ensure_market_data_auto_wires_registered_clickhouse(monkeypatch, tmp_path):
    from pcs.data.access import PCSDataAccess
    import pcs.data.clickhouse as clickhouse
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    class Client:
        def fetch_options_coverage(self, symbol, start, end):
            return {"symbol": symbol, "requested_start": start, "requested_end": end,
                    "source_min_date": start, "source_max_date": end, "physical_rows": 1,
                    "unique_contract_keys": 1, "put_rows": 0, "call_rows": 1,
                    "source_table": "test.options", "status": "READY", "reason_codes": []}
        def fetch_options_range(self, symbol, start, end):
            return pd.DataFrame({"symbol": [symbol], "trade_date": pd.to_datetime([start]),
                "expiration_date": pd.to_datetime(["2023-10-27"]), "strike": [100.], "call_put": ["c"],
                "last": [1.], "bid": [.9], "ask": [1.1], "bid_iv": [.2], "ask_iv": [.21],
                "open_interest": [100], "volume": [10], "delta": [.2], "gamma": [.01],
                "vega": [.1], "theta": [-.1], "rho": [.01]})
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "secret")
    monkeypatch.setattr(clickhouse, "PCSClickHouseClient", lambda *args, **kwargs: Client())
    result = __import__("pcs.data.control_plane", fromlist=["ensure_market_data"]).ensure_market_data(
        "NVDL", {"start": "2023-09-26", "end": "2023-12-31", "datasets": {"options": {"required": True}}}, access=access)
    assert result.status == "PARTIAL"
    assert result.selected_source == ("clickhouse_options",)
    assert result.promoted_partitions
    assert result.import_outcomes[0]["status"] == "IMPORTED"
    assert access.read("options", "NVDL").shape[0] == 1


def test_options_handler_zero_rows_is_authorized_source_block(monkeypatch, tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    class Client:
        def fetch_options_coverage(self, symbol, start, end):
            return {"symbol": symbol, "requested_start": start, "requested_end": end,
                    "source_min_date": None, "source_max_date": None, "physical_rows": 0,
                    "unique_contract_keys": 0, "put_rows": 0, "call_rows": 0,
                    "source_table": "test.options", "status": "BLOCKED",
                    "reason_codes": ["AUTHORIZED_SOURCE_NO_ROWS"]}
    result = __import__("pcs.data.control_plane", fromlist=["default_import_handlers"]).default_import_handlers(
        clickhouse_client=Client(), access=access)["options"](
            MarketDataRequirements("NVDL", "2023-09-26", "2023-12-31", ("options",)))
    assert result["status"] == "BLOCKED"
    assert result["reason_codes"] == ["AUTHORIZED_SOURCE_NO_ROWS"]


def test_options_handler_fails_closed_when_clickhouse_is_not_registered(tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    class Resolver:
        def resolve(self, dataset):
            return [{"source_id": "purchased_option_zip"}] if dataset == "options" else []
    handlers = __import__("pcs.data.control_plane", fromlist=["default_import_handlers"]).default_import_handlers(
        access=access, resolver=Resolver())
    result = handlers["options"](MarketDataRequirements("NVDL", "2023-09-26", "2023-12-31", ("options",)))
    assert result["status"] == "BLOCKED"
    assert result["reason_codes"] == ["SOURCE_NOT_AUTHORIZED"]


def test_massive_daily_handler_uses_canonical_incremental_writer(tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    class Gateway:
        def fetch_daily_range(self, symbol, start, end):
            return pd.DataFrame({"symbol": [symbol], "date": pd.to_datetime([start]), "open": [1.], "high": [1.], "low": [1.], "close": [1.], "volume": [1.]})
    handlers = __import__("pcs.data.control_plane", fromlist=["default_import_handlers"]).default_import_handlers(massive_client=Gateway(), access=access, parquet_root=tmp_path / "parquet", manifest_path=tmp_path / "manifest.csv")
    result = handlers["daily"](MarketDataRequirements("TEST", "2025-01-02", "2025-01-02", ("daily",)))
    assert result["daily_update"] == "UPDATED"


def test_promotion_failure_quarantines_and_removes_new_canonical(tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    access.record_provenance = lambda record, path=None: (_ for _ in ()).throw(RuntimeError("PROVENANCE_WRITE_FAILED"))
    engine = ImportEngine(access=access, staging_root=tmp_path / "staging", catalog=CanonicalDataCatalog(tmp_path / "catalog.parquet"), ledger=RequestLedger(tmp_path / "ledger.jsonl"))
    frame = pd.DataFrame({"symbol": ["TEST"], "date": pd.to_datetime(["2025-01-02"]), "open": [1.], "high": [1.], "low": [1.], "close": [1.], "volume": [1.]})
    staged = engine.stage(frame, symbol="TEST", dataset="daily", partition="year=2025", source_id="fixture")
    result = engine.promote(staged, source_version="fixture-v1")
    assert result["status"] == "QUARANTINED"
    assert not list((tmp_path / "parquet").rglob("*.parquet"))


def test_public_two_argument_status_contract(tmp_path):
    from pcs.data.access import PCSDataAccess
    from pcs.data.control_plane import get_market_data_status, ensure_market_data
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    requirements = {"start": "2018-01-01", "end": "2025-01-01", "datasets": {"daily": {"required": True}}}
    status = get_market_data_status("TEST", requirements, access=access)
    ensured = ensure_market_data("TEST", requirements, access=access)
    assert status.symbol == "TEST" and ensured.status == status.status


def test_daily_safety_repair_requires_unique_target_and_writes_canonical(tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    window = pd.DataFrame({"symbol": ["TEST", "TEST"], "date": pd.to_datetime(["2025-01-01", "2025-01-02"]), "open": [1., 1.], "high": [1., 1.], "low": [1., 1.], "close": [1., 1.], "volume": [1., 1.]})
    result = repair_daily_session("TEST", "2025-01-02", window, access=access)
    assert result["status"] == "AUTO_REPAIRED"
    assert list((tmp_path / "parquet").rglob("*.parquet"))


def test_ensure_uses_injected_importer_and_rechecks_status(tmp_path):
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess.isolated(manifest_path=tmp_path / "manifest.csv", parquet_root=tmp_path / "parquet")
    called = []
    result = MarketDataControlPlane(access=access).ensure_market_data({"datasets": {"daily": {"required": True}}}, importer=lambda plan: called.append(plan.requirements.symbol), symbol="TEST")
    assert called == ["TEST"] and result.status == "PARTIAL"
