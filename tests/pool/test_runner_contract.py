import pytest
from pcs.pool.runner import run_pcs_pool


def test_runner_rejects_invalid_event_policy():
    with pytest.raises(ValueError, match="unsupported event policy"):
        run_pcs_pool(symbols=["AAA"], mode="EOD", event_policy="INVALID")


def test_runner_requires_planned_exit_buffer():
    with pytest.raises(ValueError, match="positive exit buffer"):
        run_pcs_pool(symbols=["AAA"], mode="EOD", event_policy="PLANNED_EARLY_EXIT")


def test_preparation_requires_explicit_authorization():
    with pytest.raises(ValueError, match="AUTO_PREPARE_DATA_REQUIRED"):
        run_pcs_pool(symbols=["AAA"], mode="EOD", data_mode="PREPARE_THEN_SCAN")


def test_read_only_rejects_contradictory_write_flag():
    with pytest.raises(ValueError, match="AUTO_PREPARE_DATA_REQUIRES_PREPARE_THEN_SCAN"):
        run_pcs_pool(symbols=["AAA"], mode="EOD", data_mode="READ_ONLY", auto_prepare_data=True)
