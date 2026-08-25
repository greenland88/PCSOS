from pcs.research.r1_frozen_validation import R1_FROZEN_V1, PROSPECTIVE_FIELDS

def test_r1_frozen_spec_matches_existing_definition():
    assert R1_FROZEN_V1["tier1_features"]==["atr_expansion","drawdown20","down_streak"]
    assert R1_FROZEN_V1["tier2_features"]==["atr_pct","move5_atr"]
    assert R1_FROZEN_V1["min_history"]==50
    assert "timestamp" in PROSPECTIVE_FIELDS and "r1_version" in PROSPECTIVE_FIELDS
