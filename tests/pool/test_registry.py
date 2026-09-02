from pathlib import Path

from pcs.pool.models import EligibilityStatus
from pcs.pool.registry import UniverseSpec, build_global_pcs_universe, evaluate_static_eligibility, persist_registry
import pandas as pd


def test_universe_spec_normalizes_without_inventing_symbols():
    spec = UniverseSpec.from_symbols([" nvda ", "NVDA", "msft"])
    assert spec.symbols == ("NVDA", "MSFT")


def test_static_failures_are_reasoned_and_permanent_facts_only():
    result = evaluate_static_eligibility("abc", {"optionable": False})
    assert result.status == EligibilityStatus.HARD_EXCLUDED
    assert result.reason_codes == ("OPTIONS_NOT_LISTED",)

    result = evaluate_static_eligibility("abc", {"warmup_ready": False})
    assert result.status == EligibilityStatus.DATA_BLOCKED
    assert result.reason_codes == ("DAILY_WARMUP_INSUFFICIENT",)


def test_registry_persistence(tmp_path: Path):
    path = tmp_path / "registry.json"
    persist_registry([evaluate_static_eligibility("NVDA")], path)
    assert path.exists()
    assert "NVDA" in path.read_text()


def test_universe_spec_imports_explicit_csv_without_fixed_filename(tmp_path: Path):
    path = tmp_path / "authorized-universe.csv"
    path.write_text("symbol\n nvda \nMSFT\nNVDA\n", encoding="utf-8")
    spec = UniverseSpec.from_file(path)
    assert spec.symbols == ("NVDA", "MSFT")


def test_core_watchlist_is_not_global_candidate_universe():
    spec = UniverseSpec.from_config("config/market_universe.yaml")
    assert len(spec.symbols) == 25
    assert spec.universe_role == "CORE_WATCHLIST"
    try:
        UniverseSpec.from_global_candidates("missing-versioned-source.csv")
    except ValueError as exc:
        assert str(exc).startswith("GLOBAL_UNIVERSE_SOURCE_MISSING:")
    else:
        raise AssertionError("unversioned migration population must not be executable global universe")


def test_global_builder_writes_versioned_snapshot_and_pointer(tmp_path, monkeypatch):
    source = tmp_path / "migration.csv"
    pd.DataFrame({"symbol": ["AAA", "aaa", "BBB"], "status": ["SUCCESS", "SUCCESS", "FAILED"],
                  "rows_written": [1, 1, 0], "partitions": [1, 1, 0],
                  "source": ["x", "x", "x"], "source_size": [1, 1, 0]}).to_csv(source, index=False)
    import pcs.data.base_pool as base_pool
    monkeypatch.setattr(base_pool, "_underlying_rows", lambda *args: pd.DataFrame({
        "symbol": ["AAA"], "underlying_status": ["UNDERLYING_ELIGIBLE"]}))
    spec = build_global_pcs_universe(source=source, output_directory=tmp_path / "generated")
    assert spec.universe_id == "global_pcs_candidates"
    assert spec.universe_role == "GLOBAL_CANDIDATE_UNIVERSE"
    assert (tmp_path / "generated" / "active.json").exists()
    assert len(list((tmp_path / "generated").glob("*.parquet"))) == 1
