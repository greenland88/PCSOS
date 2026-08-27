import pytest

from pcs.research.integrity_contract import (
    CandidateLedger, IntegrityError, LedgerContract, LedgerKind,
    validate_execution_cardinality, validate_reproducibility_manifest,
)


def test_candidate_ledger_cannot_enter_lifecycle_boundary():
    candidates = CandidateLedger.build(LedgerKind.CANDIDATE, [{"id": "c1"}], symbol="NVDA", run_id="r1")
    selected = LedgerContract.build(LedgerKind.SELECTED_TRADE, [{"id": "t1"}], symbol="NVDA", run_id="r1")
    with pytest.raises(IntegrityError, match="INVALID_LEDGER_BOUNDARY"):
        validate_execution_cardinality(signal_count=1, episode_count=1,
                                       selected=candidates, lifecycle=selected)


def test_one_entry_per_episode_rejects_candidate_explosion():
    selected = LedgerContract.build(LedgerKind.SELECTED_TRADE,
                                    [{"id": str(i)} for i in range(2)], symbol="NVDA", run_id="r1")
    lifecycle = LedgerContract.build(LedgerKind.LIFECYCLE,
                                     [{"id": str(i)} for i in range(2)], symbol="NVDA", run_id="r1")
    with pytest.raises(IntegrityError, match="(CARDINALITY_EXCEEDED|ONE_ENTRY_PER_EPISODE_VIOLATION)"):
        validate_execution_cardinality(signal_count=35, episode_count=1,
            selected=selected, lifecycle=lifecycle)


def test_cardinality_allows_multiple_trades_when_episode_constraint_disabled():
    selected = LedgerContract.build(LedgerKind.SELECTED_TRADE,
                                    [{"id": str(i)} for i in range(2)], symbol="NVDA", run_id="r1")
    lifecycle = LedgerContract.build(LedgerKind.LIFECYCLE,
                                     [{"id": str(i)} for i in range(2)], symbol="NVDA", run_id="r1")
    validate_execution_cardinality(signal_count=2, episode_count=1,
                                   selected=selected, lifecycle=lifecycle,
                                   one_entry_per_episode=False)


def test_incomplete_legacy_manifest_is_not_reproducible():
    with pytest.raises(IntegrityError, match="LEGACY_REFERENCE_INCOMPLETE"):
        validate_reproducibility_manifest({"git_commit_sha": "abc"})


def test_each_reproducibility_identity_is_required():
    from pcs.research.integrity_contract import REPRODUCIBILITY_REQUIRED

    complete = {key: f"value-{key}" for key in REPRODUCIBILITY_REQUIRED}
    validate_reproducibility_manifest(complete)
    for missing in REPRODUCIBILITY_REQUIRED:
        candidate = dict(complete)
        candidate.pop(missing)
        with pytest.raises(IntegrityError, match="LEGACY_REFERENCE_INCOMPLETE"):
            validate_reproducibility_manifest(candidate)
