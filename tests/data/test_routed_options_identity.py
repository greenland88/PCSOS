from pathlib import Path
import hashlib

import pandas as pd
import pytest

from pcs.data.access import PCSDataAccess, DataAccessError
from pcs.data.control_plane import CanonicalDataCatalog, ImportEngine, MarketDataControlPlane, RequestLedger
from pcs.data.strategy_readiness import resolve_active_verified_options_handle
from pcs.data.incremental_update import update_options_frame


def option_frame(symbol, bid=1.0):
    return pd.DataFrame({
        "symbol": [symbol], "trade_date": pd.to_datetime(["2026-01-02"]),
        "expiration_date": pd.to_datetime(["2026-02-06"]), "strike": [90.0],
        "call_put": ["p"], "last": [bid], "bid": [bid], "ask": [bid + .1],
        "open_interest": [1000], "volume": [200],
    })


class RoutedFixtureAccess(PCSDataAccess):
    def __init__(self, routes, **kwargs):
        self._fixture_routes = routes
        super().__init__(**kwargs)

    def _resolve_route(self, dataset, symbol):
        route = self._fixture_routes.get((dataset, str(symbol).upper()))
        if route:
            return route
        return super()._resolve_route(dataset, symbol)


def make_access(tmp_path, symbol, physical):
    default_manifest = tmp_path / "default_manifest.csv"
    routed_manifest = tmp_path / f"{symbol.lower()}_manifest.csv"
    root = tmp_path / "parquet"
    routes = {("options", symbol): (physical, routed_manifest, root)}
    access = RoutedFixtureAccess(routes, manifest_path=default_manifest, parquet_root=root,
                                 routing_mode="isolated")
    return access, default_manifest, routed_manifest, root


@pytest.mark.parametrize("symbol,physical", [("NVDA", "options_v3"), ("QQQ", "options_v2"), ("AMZN", "options")])
def test_routed_options_stage_promote_handle_and_pinned_read(tmp_path, symbol, physical):
    access, default_manifest, routed_manifest, root = make_access(tmp_path, symbol, physical)
    engine = ImportEngine(access=access, staging_root=tmp_path / "staging",
                           catalog=CanonicalDataCatalog(tmp_path / "catalog.parquet"),
                           ledger=RequestLedger(tmp_path / "ledger.jsonl"))
    staged = engine.stage(option_frame(symbol), symbol=symbol, dataset="options",
                          partition="year=2026/quarter=1", source_id="fixture")
    result = engine.promote(staged, source_version="fixture")
    receipt = result["promotion_receipt"]
    handle = resolve_active_verified_options_handle(symbol, "2026-01-02", data_access=access)
    output = access.read_verified_dataset(handle)

    assert result["status"] == "IMPORTED"
    assert staged["logical_dataset"] == "options"
    assert staged["physical_dataset"] == physical
    assert receipt["dataset"] == physical
    assert receipt["logical_dataset"] == "options"
    assert receipt["manifest_identity"] == str(routed_manifest.resolve())
    assert receipt["parquet_root_identity"] == str(root.resolve())
    assert handle.dataset == physical
    assert handle.manifest_identity == str(routed_manifest.resolve())
    assert len(output) == 1
    assert not default_manifest.exists()
    assert routed_manifest.exists()
    assert list((root / physical / f"symbol={symbol}").rglob("*.parquet"))


def test_verified_routed_handle_does_not_read_wrong_default_manifest(tmp_path):
    access, default_manifest, routed_manifest, root = make_access(tmp_path, "NVDA", "options_v3")
    engine = ImportEngine(access=access, staging_root=tmp_path / "staging",
                           catalog=CanonicalDataCatalog(tmp_path / "catalog.parquet"),
                           ledger=RequestLedger(tmp_path / "ledger.jsonl"))
    staged = engine.stage(option_frame("NVDA", bid=1.0), symbol="NVDA", dataset="options",
                          partition="year=2026/quarter=1", source_id="fixture")
    engine.promote(staged, source_version="fixture")
    handle = resolve_active_verified_options_handle("NVDA", "2026-01-02", data_access=access)

    default = PCSDataAccess.isolated(manifest_path=default_manifest, parquet_root=root)
    default.promote_generation(option_frame("NVDA", bid=9.0), "options", "NVDA",
                               "year=2026/quarter=1", source_version="wrong")
    output = access.read_verified_dataset(handle)
    assert float(output.bid.iloc[0]) == 1.0


def test_routed_options_handle_requires_manifest_identity(tmp_path):
    access, _default, routed_manifest, _root = make_access(tmp_path, "QQQ", "options_v2")
    engine = ImportEngine(access=access, staging_root=tmp_path / "staging",
                           catalog=CanonicalDataCatalog(tmp_path / "catalog.parquet"),
                           ledger=RequestLedger(tmp_path / "ledger.jsonl"))
    staged = engine.stage(option_frame("QQQ"), symbol="QQQ", dataset="options",
                          partition="year=2026/quarter=1", source_id="fixture")
    engine.promote(staged, source_version="fixture")
    handle = resolve_active_verified_options_handle("QQQ", "2026-01-02", data_access=access)
    incomplete = handle.__class__(handle.dataset, handle.ticker, handle.generation_id,
        handle.partitions, handle.checksum, handle.row_count, handle.canonical_paths,
        handle.coverage, handle.source_lineage, dataset_fingerprint=handle.dataset_fingerprint,
        schema_version=handle.schema_version, price_basis=handle.price_basis,
        corporate_action_version=handle.corporate_action_version, min_date=handle.min_date,
        max_date=handle.max_date, partition_count=handle.partition_count)
    with pytest.raises(DataAccessError, match="ROUTED_MANIFEST_IDENTITY_MISSING"):
        access.read_verified_dataset(incomplete)


def test_incremental_options_update_uses_resolved_route(tmp_path):
    access, default_manifest, routed_manifest, root = make_access(tmp_path, "NVDA", "options_v3")
    result = update_options_frame("NVDA", option_frame("NVDA"), parquet_root=root,
                                  manifest_path=routed_manifest, source_version="fixture",
                                  physical_dataset="options_v3")
    assert result[0] == "UPDATED"
    assert routed_manifest.exists()
    assert not default_manifest.exists()
    assert list((root / "options_v3" / "symbol=NVDA").rglob("*.parquet"))


def test_exact_option_quote_repair_uses_resolved_route(tmp_path):
    access, default_manifest, routed_manifest, root = make_access(tmp_path, "NVDA", "options_v3")
    control_plane = MarketDataControlPlane(access=access)
    result = control_plane.repair_exact_option_quotes(
        "NVDA", option_frame("NVDA", bid=2.0), source_version="fixture",
        expected_keys=[("2026-01-02", "2026-02-06", 90.0, "p")],
    )
    assert result["options_update"] == "UPDATED"
    assert routed_manifest.exists()
    assert not default_manifest.exists()
    assert list((root / "options_v3" / "symbol=NVDA").rglob("*.parquet"))


def test_routed_promotion_is_idempotent_without_receipt_or_file_loss(tmp_path):
    access, default_manifest, routed_manifest, root = make_access(tmp_path, "NVDA", "options_v3")
    engine = ImportEngine(access=access, staging_root=tmp_path / "staging",
                          catalog=CanonicalDataCatalog(tmp_path / "catalog.parquet"),
                          ledger=RequestLedger(tmp_path / "ledger.jsonl"))
    first = engine.promote(engine.stage(option_frame("NVDA"), symbol="NVDA", dataset="options",
                                        partition="year=2026/quarter=1", source_id="fixture"),
                           source_version="fixture")
    active_path = Path(first["path"])
    parquet_bytes = active_path.read_bytes()
    parquet_hash = hashlib.sha256(parquet_bytes).hexdigest()
    manifest_bytes = routed_manifest.read_bytes()
    active_generation = first["promoted_generation_id"]

    second = engine.promote(engine.stage(option_frame("NVDA"), symbol="NVDA", dataset="options",
                                         partition="year=2026/quarter=1", source_id="fixture"),
                            source_version="fixture")

    active = access.active_generation_record("options_v3", "NVDA", "year=2026/quarter=1",
                                             manifest_identity=str(routed_manifest.resolve()))
    assert second["status"] == "ALREADY_COMPLETE"
    assert second["reason_codes"] == ["IDEMPOTENT_NO_OP"]
    assert second["promotion_receipt"] is None
    assert second["promoted_generation_id"] == active_generation
    assert active["active_generation"] == active_generation
    assert active_path.exists()
    assert hashlib.sha256(active_path.read_bytes()).hexdigest() == parquet_hash
    assert routed_manifest.read_bytes() == manifest_bytes
    assert int(active["row_count"]) == len(pd.read_parquet(active_path))
    assert access.semantic_content_hash(pd.read_parquet(active_path)) == str(active["content_hash"])
    assert len(pd.read_csv(routed_manifest)) == 1
    assert not default_manifest.exists()


def test_route_identity_failure_cannot_delete_preexisting_active_file(tmp_path, monkeypatch):
    access, default_manifest, routed_manifest, root = make_access(tmp_path, "NVDA", "options_v3")
    engine = ImportEngine(access=access, staging_root=tmp_path / "staging",
                          catalog=CanonicalDataCatalog(tmp_path / "catalog.parquet"),
                          ledger=RequestLedger(tmp_path / "ledger.jsonl"))
    first = engine.promote(engine.stage(option_frame("NVDA", bid=1.0), symbol="NVDA", dataset="options",
                                        partition="year=2026/quarter=1", source_id="fixture"),
                           source_version="fixture")
    old_path = Path(first["path"])
    old_bytes = old_path.read_bytes()
    old_generation = first["promoted_generation_id"]

    def fail_route_assertion(*_args):
        raise DataAccessError("CANONICAL_ROUTE_IDENTITY_MISMATCH")

    monkeypatch.setattr(engine, "_assert_route_identity", fail_route_assertion)
    result = engine.promote(engine.stage(option_frame("NVDA", bid=2.0), symbol="NVDA", dataset="options",
                                         partition="year=2026/quarter=1", source_id="fixture"),
                            source_version="fixture")

    assert result["status"] == "QUARANTINED"
    assert result["reason_codes"] == ["DataAccessError"]
    assert old_path.exists()
    assert old_path.read_bytes() == old_bytes
    active = access.active_generation_record("options_v3", "NVDA", "year=2026/quarter=1",
                                             manifest_identity=str(routed_manifest.resolve()))
    assert active["active_generation"] == old_generation
    assert not default_manifest.exists()
