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
