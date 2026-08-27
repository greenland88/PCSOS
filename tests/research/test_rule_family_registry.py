from pcs.research.rule_families.registry import RULE_FAMILIES, family_battery, get_rule_family
import json
from pathlib import Path


def test_registry_contains_only_the_two_frozen_research_families():
    assert set(RULE_FAMILIES) == {"PCS_TREND_CONTINUATION", "PCS_CONSTRUCTIVE_RECOVERY"}
    assert get_rule_family("PCS_TREND_CONTINUATION").discovery_version == "V2_H010"
    assert get_rule_family("PCS_CONSTRUCTIVE_RECOVERY").discovery_version == "V2_H027"


def test_new_ticker_battery_requires_transfer_evidence_and_is_not_production_action():
    battery = family_battery("COST")
    assert {row["rule_family_id"] for row in battery} == set(RULE_FAMILIES)
    assert all(row["target_ticker"] == "COST" for row in battery)
    assert all(row["transfer_status"] == "TRANSFER_TESTING_REQUIRED" for row in battery)
    assert all("action" not in row for row in battery)


def test_nvda_statuses_are_preserved_separately():
    assert get_rule_family("PCS_TREND_CONTINUATION").transfer_status_by_ticker["NVDA"] == "VALIDATED"
    assert get_rule_family("PCS_CONSTRUCTIVE_RECOVERY").transfer_status_by_ticker["NVDA"] == "INSUFFICIENT_EVIDENCE"
    assert get_rule_family("PCS_TREND_CONTINUATION").transfer_status_by_ticker["QQQ"] == "NO_TRANSFER"
    assert get_rule_family("PCS_CONSTRUCTIVE_RECOVERY").transfer_status_by_ticker["QQQ"] == "NO_TRANSFER"
    assert get_rule_family("PCS_TREND_CONTINUATION").transfer_status_by_ticker["AMD"] == "NO_TRANSFER"
    assert get_rule_family("PCS_CONSTRUCTIVE_RECOVERY").transfer_status_by_ticker["AMD"] == "NO_TRANSFER"


def test_persisted_registry_manifest_contains_auditable_family_fields():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "research_outputs/nvda_entry_discovery_agent_v2/rule_family_registry.json").read_text())
    assert {f["rule_family_id"] for f in manifest["families"]} == set(RULE_FAMILIES)
    required = {"human_name", "structural_logic", "pit_feature_requirements", "reference_implementation",
                "discovery_ticker", "discovery_version", "evidence_status", "validation_status", "transfer_status_by_ticker"}
    assert all(required.issubset(f) for f in manifest["families"])
