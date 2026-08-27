from dataclasses import replace

import pytest

from pcs.research.research_framework import (
    ResearchMode, ResearchSpecError, load_spec, validate_rule_set,
)
from pcs.research.runner import ResearchRunner


def test_amd_rule_set_is_configured_and_isolated(tmp_path):
    spec = load_spec("config/research/templates/amd_current_strategy_replay.yaml")
    assert spec.research_mode == ResearchMode.CURRENT_STRATEGY_REPLAY
    result = ResearchRunner(spec, output_dir=tmp_path).rule_set_plumbing({
        "TRADING_DAYS": 10, "FEATURE_READY_DAYS": 8,
        "SETUP_ELIGIBLE_DAYS": 3, "CONTRACT_CANDIDATES": 6,
        "SELECTED_ENTRIES": 6, "LIFECYCLES_COMPLETED": 6,
    })
    assert result["final_oos_read"] is False
    assert result["production_changes_allowed"] is False
    assert result["rule_set"]["safe_strike_atr"] == 2.3
    assert result["rule_set"]["allowed_widths"] == [5.0, 10.0, 2.0]
    assert result["funnel"][0] == {"stage": "TRADING_DAYS", "count": 10}


def test_rule_set_supports_only_width_and_gate_switches(tmp_path):
    spec = load_spec("config/research/templates/amd_current_strategy_replay.yaml")
    spec = replace(spec, rules={**spec.rules, "allowed_widths": [5], "width_mode": "ONLY", "pullback_gate": False})
    runner = ResearchRunner(spec, output_dir=tmp_path)
    result = runner.rule_set_plumbing()
    assert result["rule_set"]["width_mode"] == "ONLY"
    pullback = next(x for x in result["funnel"] if x["stage"] == "PULLBACK_REJECTED")
    assert pullback["enabled"] is False


def test_rule_set_rejects_unknown_or_invalid_values():
    spec = load_spec("config/research/templates/amd_current_strategy_replay.yaml")
    with pytest.raises(ResearchSpecError, match="UNKNOWN_RULES"):
        validate_rule_set(replace(spec, rules={**spec.rules, "new_indicator": True}))
    with pytest.raises(ResearchSpecError, match="ONLY_REQUIRES_ONE_WIDTH"):
        validate_rule_set(replace(spec, rules={**spec.rules, "width_mode": "ONLY", "allowed_widths": [5, 10]}))
