import pytest
from pcs.data.price_basis import CorporateAction, CorporateActionRegistry, CorporateActionType
from pcs.research.current_strategy_replay import validate_lifecycle_corporate_action
from pcs.research.stage4a_lifecycle import LifecycleAdapterError


def test_cross_split_lifecycle_fails_closed_without_mapping():
    registry = CorporateActionRegistry([
        CorporateAction("NVDA", "2024-06-10", CorporateActionType.SPLIT, 10, "authoritative", True)
    ])
    with pytest.raises(LifecycleAdapterError, match="CORPORATE_ACTION_CONTRACT_MAPPING_UNAVAILABLE"):
        validate_lifecycle_corporate_action({"ticker": "NVDA", "date": "2024-06-07", "expiration": "2024-06-21"}, registry)


def test_non_crossing_lifecycle_remains_eligible():
    registry = CorporateActionRegistry([
        CorporateAction("NVDA", "2024-06-10", CorporateActionType.SPLIT, 10, "authoritative", True)
    ])
    validate_lifecycle_corporate_action({"ticker": "NVDA", "date": "2024-06-11", "expiration": "2024-06-21"}, registry)
