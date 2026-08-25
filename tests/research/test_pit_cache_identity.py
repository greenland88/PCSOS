import pandas as pd

from pcs.research.pit_cache_identity import build_pit_cache_identity, cache_identity_matches
from pcs.research.nvda_entry_discovery_v2_rebuild_pit_cache import _current_daily_days


def identity(**overrides):
    args = dict(symbol="NVDA", date_range={"start": "2020-01-01", "end": "2023-12-29"},
                daily_data_identity="daily-v1", feature_config={"sma": 200}, research_config={"mode": "NEW_ENTRY"})
    args.update(overrides)
    return build_pit_cache_identity(**args, corporate_action_path="missing-test-actions.csv")


def frame_for(i):
    return pd.DataFrame([{**i, "created_at": "now"}])


def test_same_identity_reusable_and_changed_identity_rejected():
    i = identity()
    assert cache_identity_matches(frame_for(i), i)
    changed = dict(i, identity_sha256="different")
    assert not cache_identity_matches(frame_for(i), changed)


def test_missing_identity_is_not_reusable():
    i = identity()
    incomplete = frame_for(i).drop(columns=["identity_sha256", "price_basis_version"])
    assert not cache_identity_matches(incomplete, i)


def test_identity_contract_excludes_outcomes_and_validation_data():
    i = identity()
    forbidden = {"realized_pnl", "lifecycle_pnl", "validation_data", "final_oos", "future_outcome"}
    assert not forbidden.intersection(i)


def test_price_basis_change_invalidates():
    i = identity()
    changed = dict(i, price_basis_version="price_basis.v2")
    changed["identity_sha256"] = "recomputed-not-needed-for-mismatch"
    assert not cache_identity_matches(frame_for(i), changed)


def test_corporate_action_change_invalidates():
    i = identity()
    changed = dict(i, corporate_action_registry_version="corporate_actions.registry.v2")
    changed["identity_sha256"] = "recomputed-not-needed-for-mismatch"
    assert not cache_identity_matches(frame_for(i), changed)


def test_feature_change_invalidates():
    i = identity()
    changed = dict(i, feature_implementation_version="pit_features.v3")
    changed["identity_sha256"] = "recomputed-not-needed-for-mismatch"
    assert not cache_identity_matches(frame_for(i), changed)


def test_date_range_daily_identity_and_research_config_invalidate():
    i = identity()
    for key, value in {
        "date_range_start": "2020-01-02",
        "daily_data_identity": "daily-v2",
        "research_config_hash": "different-config",
        "pit_context_schema_version": "pit_context.schema.v2",
    }.items():
        changed = dict(i, **{key: value})
        changed["identity_sha256"] = "recomputed-not-needed-for-mismatch"
        assert not cache_identity_matches(frame_for(i), changed), key


def test_identity_is_independent_of_current_working_directory(tmp_path, monkeypatch):
    first = identity()
    monkeypatch.chdir(tmp_path)
    second = identity()
    assert first == second


def test_pit_rebuild_domain_comes_from_current_daily_source_not_old_timeline():
    daily = pd.DataFrame({"date": ["2020-01-03", "2020-01-02", "2020-01-03", "2020-01-06"]})
    assert _current_daily_days(daily) == list(pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"]))
