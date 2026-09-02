from pathlib import Path

from pcs.pool.models import EligibilityStatus
from pcs.pool.registry import UniverseSpec, evaluate_static_eligibility, persist_registry


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
    global_spec = UniverseSpec.from_global_candidates()
    assert global_spec.universe_role == "GLOBAL_CANDIDATE_UNIVERSE"
    assert len(global_spec.symbols) != len(spec.symbols)
