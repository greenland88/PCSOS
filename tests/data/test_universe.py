import pytest
from pcs.data.universe import load_market_universe, merge_symbols

def test_default_and_groups_are_stable_and_normalized():
    default = load_market_universe()
    assert default[:4] == ["QQQ", "SPY", "SOXX", "SMH"]
    assert load_market_universe(["benchmarks"]) == default[:4]
    assert len(default) == len(set(default))

def test_explicit_merge_deduplicates_and_uppercases():
    assert merge_symbols(["NVDA", "QQQ"], [" nvda ", "msft"]) == ["NVDA", "QQQ", "MSFT"]

def test_invalid_group_and_yaml_fail_loudly(tmp_path):
    with pytest.raises(ValueError, match="unknown universe group"):
        load_market_universe(["missing"])
    bad = tmp_path / "bad.yaml"
    bad.write_text("benchmarks: nope", encoding="utf-8")
    with pytest.raises(ValueError): load_market_universe(path=bad)
