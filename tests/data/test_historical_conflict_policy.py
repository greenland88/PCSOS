from pathlib import Path
import yaml


def test_historical_vendor_conflict_policy_is_explicit():
    rules = yaml.safe_load(Path("config/pcs_rules.yaml").read_text(encoding="utf-8"))
    assert rules["data_quality"]["historical_vendor_conflict_policy"] == "VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW"
    assert rules["data_quality"]["incremental_conflict_source"] == "CLICKHOUSE"
